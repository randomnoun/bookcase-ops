#!/usr/bin/env python3
"""Tools for running and comparing the LLM backends ( ollama, sonar, tabbyapi, ninfer, mistralrs, sglang ) in this repo.

    python bin/llm.py --help
    python bin/llm.py speed langfuse --help
    python bin/llm.py engine list
    python bin/llm.py engine set sonar
    python bin/llm.py model load qwen3.8-27b-5bpw

Verbs are two levels: speed, metric, engine and model. To add one, write a function and decorate it with
@speed_group.command() ( or whichever group ). Needs click, PyYAML, hvac and requests
( pip install click pyyaml hvac requests ). Anything that talks to the cluster needs kubectl. The engine and model
verbs read secrets from vault ( run `. vault-login.sh` first, for VAULT_ADDR and VAULT_TOKEN ), call litellm directly
at LITELLM_URL ( default https://litellm.dev.randomnoun ), and reach the engines, which have no ingress, through a
temporary `kubectl port-forward`. `model load` runs ansible-playbook ( on Windows, run this from WSL ).
"""
import base64
import contextlib
import datetime
import functools
import json
import os
import re
import shutil
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import click
import hvac
import requests
import yaml

# prompt-size bands ( tokens ) used by the per-band summaries
BANDS = [("<15k", 0, 15_000), ("15-45k", 15_000, 45_000), ("45-65k", 45_000, 65_000), (">65k", 65_000, float("inf"))]


def num(text):
    return float(text.replace(",", ""))


def show_table(headers, rows, left=(0,)):
    rows = [[str(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def fmt(cells):
        return "  ".join(c.ljust(w) if i in left else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))

    click.echo(fmt(headers))
    for r in rows:
        click.echo(fmt(r))


def show_bands(recs, min_out, label):
    """Median speed by prompt size, over requests that generated at least min_out tokens."""
    rows = []
    for name, lo, hi in BANDS:
        v = [r for r in recs if lo <= r["prompt"] < hi and r["out"] >= min_out and r["tps"]]
        if v:
            speeds = [r["tps"] for r in v]
            outs = [r["out"] for r in v]
            rows.append([name, len(v), f"{statistics.median(speeds):.1f}", f"{min(speeds):.1f}-{max(speeds):.1f}",
                         f"{min(outs):,}-{max(outs):,}"])
    click.echo(f"\n{label}: requests with >= {min_out} tokens out, by prompt size")
    if rows:
        show_table(["prompt", "n", "median", "range", "tokens out"], rows)
    else:
        click.echo("  none")


def kubectl(*args):
    exe = shutil.which("kubectl")
    if not exe:
        raise click.ClickException("kubectl not found on PATH")
    p = subprocess.run([exe, *args], capture_output=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise click.ClickException(f"kubectl {' '.join(args)} failed: {p.stderr.strip()[:500]}")
    return p.stdout


FILE_OPT = click.option("--file", "file", type=click.File("r", encoding="utf-8", errors="replace"),
                        help="Read the text from a file ( or - for stdin ) instead of asking the cluster.")


def cluster_or_file(file, fetch):
    return file.read() if file else fetch()


# ---------------------------------------------------------------------------------------------------------------------

CLUSTER = "bnekub03"


@click.group()
@click.option("--cluster", default=CLUSTER, show_default=True, envvar="LLM_CLUSTER",
              help="Which cluster's vars files ( ansible/vars/*/<cluster>-models.vars.yml ) and vault paths to use.")
def cli(cluster):
    """Run and compare the LLM backends ( ollama, sonar, tabbyapi, ninfer, mistralrs, sglang ) in this repo."""
    global CLUSTER
    CLUSTER = cluster


@cli.group("speed")
def speed_group():
    """Per-request generation speeds."""


@cli.group("metric")
def metric_group():
    """Engine metrics."""


@cli.group("engine")
def engine_group():
    """Which engine has the GPUs ( only one engine runs at a time )."""


@cli.group("model")
def model_group():
    """The model the current engine serves, and its litellm entry."""


# --- langfuse ---------------------------------------------------------------------------------------------------------

def parse_since(text):
    """'90m', '2h', '1d' or '1h30m' as a timedelta, like kubectl --since."""
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text.strip())
    if not m or not any(m.groups()):
        raise click.BadParameter(f"'{text}' isn't a duration like 30m, 2h, 1d or 1h30m", param_hint="--since")
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    return datetime.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def langfuse_key(field):
    return vault(f"k8s/{CLUSTER}/secret/dev-langfuse/langfuse", field)


@speed_group.command("langfuse")
@click.argument("start", required=False)
@click.argument("end", required=False)
@click.option("--since", help="How far back to look ( 30m, 2h, 1d, 1h30m ), instead of a START time. "
                              "Without START or --since it's 1h.")
@click.option("--host", envvar="LANGFUSE_HOST", default="https://langfuse.dev.randomnoun", show_default=True)
@click.option("--public-key", envvar="LANGFUSE_PUBLIC_KEY",
              help="Or set LANGFUSE_PUBLIC_KEY. Default: the project's public_key in vault.")
@click.option("--secret-key", envvar="LANGFUSE_SECRET_KEY",
              help="Or set LANGFUSE_SECRET_KEY. Default: the project's secret_key in vault.")
@click.option("--model", help="Only models whose name contains this text ( case-insensitive ).")
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--basis", type=click.Choice(["e2e", "decode"]), default="e2e", show_default=True,
              help="e2e = output tokens / total latency ( includes prefill ). decode = output tokens / ( latency - "
                   "time to first token ), only for requests that have a first-token time.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
def langfuse_speeds(start, end, since, host, public_key, secret_key, model, min_out, basis, last):
    """Per-request speeds from langfuse, for generations since a time ago, or between START and END ( ISO 8601, UTC ).

    \b
    Examples:
      python bin/llm.py speed langfuse --since 2h --model ninfer
      python bin/llm.py speed langfuse 2026-10-29T12:30:00Z 2026-10-29T14:00:00Z --model sonar

    The project keys come from vault ( `. vault-login.sh` first ) unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    are set. The times in the table are UTC. Uses the v2 observations API ( langfuse v4 ). litellm v1.104 sends no
    first-token time, so for now only the e2e basis has data; the decode basis works once litellm sends it
    ( see setup/SETUP-LLM-MTP.md ).
    """
    if start and since:
        raise click.UsageError("give START or --since, not both")
    if not start:
        start = (datetime.datetime.now(datetime.timezone.utc) - parse_since(since or "1h")).strftime("%Y-%m-%dT%H:%M:%SZ")
    public_key = public_key or langfuse_key("public_key")
    secret_key = secret_key or langfuse_key("secret_key")
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    def get(cursor):
        q = {"type": "GENERATION", "fromStartTime": start, "limit": 1000,
             "fields": "core,basic,model,usage,metrics,time"}
        if end:
            q["toStartTime"] = end
        if cursor:
            q["cursor"] = cursor
        req = urllib.request.Request(f"{host.rstrip('/')}/api/public/v2/observations?{urllib.parse.urlencode(q)}",
                                     headers={"Authorization": f"Basic {auth}"})
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            hint = " ( this needs langfuse v4: the v1 /api/public/observations endpoint is gone )" if e.code == 404 else ""
            raise click.ClickException(f"langfuse returned HTTP {e.code} for {req.full_url.split('?')[0]}{hint}")
        except urllib.error.URLError as e:
            raise click.ClickException(f"cannot reach {host}: {e.reason}")

    rows, cursor = [], None
    while True:
        page = get(cursor)
        rows += page["data"]
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            break
    if not rows:
        raise click.ClickException("no generations in that window")

    def usage(o, *names):
        u = o.get("usageDetails") or {}
        return next((u[n] for n in names if u.get(n)), None)

    recs = []
    for o in rows:
        name = o.get("model") or o.get("name") or "?"
        if model and model.lower() not in name.lower():
            continue
        out = usage(o, "output", "completion_tokens", "output_tokens")
        prompt = usage(o, "input", "prompt_tokens", "input_tokens") or 0
        lat, ttft = o.get("latency"), o.get("timeToFirstToken")
        if not (out and lat):
            continue
        e2e = out / lat if lat > 0 else None
        decode = out / (lat - ttft) if ttft is not None and lat - ttft > 0 else None
        recs.append({"t": o["startTime"][5:19].replace("T", " "), "model": name, "prompt": prompt, "out": out,
                     "ttft": ttft, "e2e": e2e, "decode": decode, "tps": e2e if basis == "e2e" else decode})
    if not recs:
        raise click.ClickException(f"{len(rows)} generations, none matching --model or with usage + latency data")
    recs.sort(key=lambda r: r["t"])

    f1 = lambda v: "-" if v is None else format(v, ".1f")
    if last:
        click.echo(f"{len(recs)} requests ( times are UTC ), showing the last {min(last, len(recs))}:")
        show_table(["time", "model", "prompt", "out", "ttft s", "e2e tok/s", "decode tok/s"],
                   [[r["t"], r["model"][:34], f"{r['prompt']:,}", f"{r['out']:,}", f1(r["ttft"]), f1(r["e2e"]),
                     f1(r["decode"])] for r in recs[-last:]], left=(0, 1))
    for m in sorted({r["model"] for r in recs}):
        show_bands([r for r in recs if r["model"] == m], min_out, f"{m} ( {basis} tok/sec )")


# --- sonar ------------------------------------------------------------------------------------------------------------

SONAR_RE = re.compile(
    r"(\d\d:\d\d:\d\d) Request completed - E2E time: ([\d.]+)s, TTFT: ([\d.]+)s, "
    r"Prefill: ([\d,]+) tokens \(([\d.,]+) tokens/s\), Decode: ([\d,]+) tokens \(([\d.,]+) tokens/s\)"
    r"(?:, Cache hits: ([\d,]+) tokens \(([\d.]+)%\))?")


def parse_sonar(text):
    recs = []
    for m in SONAR_RE.finditer(text):
        t, e2e, ttft, prompt, _, out, tps, cached, cached_pct = m.groups()
        recs.append({"t": t, "prompt": int(num(prompt)), "out": int(num(out)), "ttft": float(ttft), "tps": num(tps),
                     "cached_pct": float(cached_pct) if cached_pct else None})
    return recs


@speed_group.command("sonar")
@click.option("--since", default="1h", show_default=True, help="How far back to read the pod log ( kubectl --since ).")
@click.option("--namespace", "-n", default="dev-sonar", show_default=True)
@click.option("--deployment", default="sonar", show_default=True)
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
@FILE_OPT
def sonar_speeds(since, namespace, deployment, min_out, last, file):
    """Per-request speeds from sonar's own "Request completed" log lines.

    The decode figure is output tokens / time after the first token, so it is the engine's decode speed. Times are
    as logged ( UTC ). Cached is the share of the prompt served from the prefix cache ( newer log lines only ).
    """
    recs = parse_sonar(cluster_or_file(file, lambda: kubectl("-n", namespace, "logs", f"deploy/{deployment}",
                                                              f"--since={since}")))
    if not recs:
        raise click.ClickException("no 'Request completed' lines found")
    if last:
        click.echo(f"{len(recs)} requests, showing the last {min(last, len(recs))}:")
        show_table(["time", "prompt", "cached", "out", "ttft s", "decode tok/s"],
                   [[r["t"], f"{r['prompt']:,}", "-" if r["cached_pct"] is None else f"{r['cached_pct']:.0f}%",
                     f"{r['out']:,}", f"{r['ttft']:.1f}", f"{r['tps']:.1f}"] for r in recs[-last:]])
    show_bands(recs, min_out, "decode tok/sec")


METRIC_RE = re.compile(r"^aphrodite:([A-Za-z0-9_:]+)(\{[^}]*\})?\s+(\S+)$")


def parse_counters(text):
    totals, by_pos = {}, {}
    for line in text.splitlines():
        m = METRIC_RE.match(line.strip())
        if not m or m.group(1).endswith("_created"):
            continue
        name, labels, value = m.groups()
        try:
            v = float(value)
        except ValueError:
            continue
        if name == "spec_decode_num_accepted_tokens_per_pos_total":
            pos = re.search(r'position="(\d+)"', labels or "")
            if pos:
                by_pos[int(pos.group(1))] = by_pos.get(int(pos.group(1)), 0) + v
        else:
            totals[name] = totals.get(name, 0) + v
    return totals, by_pos


@metric_group.command("sonar")
@click.option("--namespace", "-n", default="dev-sonar", show_default=True)
@click.option("--deployment", default="sonar", show_default=True)
@click.option("--port", default=2242, show_default=True)
@FILE_OPT
def sonar_metrics(namespace, deployment, port, file):
    """MTP draft acceptance and prefix-cache hit rate from sonar's /metrics.

    The counters are cumulative since the pod started, so restart it ( or note the numbers before and after a
    test ) to measure one run. Reads /metrics with kubectl exec, from inside the pod.
    """
    code = f"import urllib.request as u; print(u.urlopen('http://localhost:{port}/metrics').read().decode())"
    totals, by_pos = parse_counters(cluster_or_file(
        file, lambda: kubectl("-n", namespace, "exec", f"deploy/{deployment}", "--", "python3", "-c", code)))

    drafts = totals.get("spec_decode_num_drafts_total", 0)
    drafted = totals.get("spec_decode_num_draft_tokens_total", 0)
    accepted = totals.get("spec_decode_num_accepted_tokens_total", 0)
    if drafts:
        click.echo(f"drafts {drafts:,.0f}   draft tokens {drafted:,.0f}   accepted {accepted:,.0f} "
                   f"( {accepted / drafted:.1%} )")
        click.echo("accepted by draft position: " + "   ".join(
            f"{p + 1}: {by_pos[p] / drafts:.1%}" for p in sorted(by_pos)))
        click.echo(f"tokens per forward pass: {1 + accepted / drafts:.2f}   ( 1 + accepted / drafts )")
    else:
        click.echo("no speculative-decoding counters found ( is MTP enabled? )")
    queries = totals.get("prefix_cache_queries_total", 0)
    hits = totals.get("prefix_cache_hits_total", 0)
    if queries:
        click.echo(f"prefix cache: {hits:,.0f} of {queries:,.0f} prompt tokens hit ( {hits / queries:.1%} )")


# --- tabbyapi ---------------------------------------------------------------------------------------------------------

TABBY_RE = re.compile(
    r"(\d\d:\d\d:\d\d)\.\d+ INFO:\s+#(\d+) \S+ \([^)]*\): ([\d,]+) tokens generated at ([\d.,]+) T/s\s+\S+\s+"
    r"prompt ([\d,]+) tokens, (none|\d+%) cached, ([\d,]+) new in [\d.,]+ s \([\d.,]+ T/s\)\s+\S+\s+"
    r"first token ([\d.]+) s, total ([\d.]+) s(?:\s+\S+\s+draft ([\d,]+)/([\d,]+) accepted \((\d+)%\))?")


def parse_tabby(text):
    # tabby wraps each log entry over several lines; continuation lines are indented
    entries, cur = [], None
    for line in text.splitlines():
        if re.match(r"^\d\d:\d\d:\d\d", line):
            if cur:
                entries.append(cur)
            cur = line.strip()
        elif cur and line[:1].isspace():
            cur += " " + line.strip()
    if cur:
        entries.append(cur)
    recs = []
    for e in entries:
        m = TABBY_RE.search(e)
        if not m:
            continue
        t, n, out, tps, prompt, cached, _, ttft, _, acc, drafted, pct = m.groups()
        recs.append({"t": t, "n": int(n), "prompt": int(num(prompt)), "out": int(num(out)), "tps": num(tps),
                     "ttft": float(ttft), "cached": "0%" if cached == "none" else cached,
                     "draft": f"{pct}%" if pct else "-"})
    return recs


@speed_group.command("tabbyapi")
@click.option("--since", default="1h", show_default=True, help="How far back to read the pod log ( kubectl --since ).")
@click.option("--namespace", "-n", default="dev-tabbyapi", show_default=True)
@click.option("--deployment", default="tabbyapi", show_default=True)
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
@FILE_OPT
def tabbyapi_speeds(since, namespace, deployment, min_out, last, file):
    """Per-request speeds and MTP draft acceptance from tabbyapi's "tokens generated" log lines.

    The speed is tabby's own generation rate. Times are as logged ( UTC ). Draft is the share of drafted tokens
    accepted for that request.
    """
    recs = parse_tabby(cluster_or_file(file, lambda: kubectl("-n", namespace, "logs", f"deploy/{deployment}",
                                                              f"--since={since}")))
    if not recs:
        raise click.ClickException("no 'tokens generated' lines found")
    if last:
        click.echo(f"{len(recs)} requests, showing the last {min(last, len(recs))}:")
        show_table(["time", "#", "prompt", "cached", "out", "ttft s", "gen tok/s", "draft"],
                   [[r["t"], r["n"], f"{r['prompt']:,}", r["cached"], f"{r['out']:,}", f"{r['ttft']:.1f}",
                     f"{r['tps']:.1f}", r["draft"]] for r in recs[-last:]])
    show_bands(recs, min_out, "generation tok/sec")


# --- ninfer -----------------------------------------------------------------------------------------------------------

NINFER_DONE_RE = re.compile(r"^(?:\d{4}-\d\d-\d\d )?(\d\d:\d\d:\d\d)\.\d+\s+\w+\s+req#(\d+) done \| (.*)$")


def seconds(text):
    """'2.2s', '279 ms' or '1m 11.6s' as seconds, or None."""
    m = re.fullmatch(r"(?:(\d+)m\s*)?([\d.,]+)\s*(ms|s)", text.strip())
    if not m:
        return None
    mins, value, unit = m.groups()
    return num(value) / (1000 if unit == "ms" else 1) + 60 * int(mins or 0)


def per_second(text):
    """'412.6 tok/s' or '2.08k tok/s' as tokens per second, or None."""
    m = re.fullmatch(r"([\d.,]+)(k?)\s*tok/s", text.strip())
    return num(m.group(1)) * (1000 if m.group(2) else 1) if m else None


def parse_ninfer(text):
    recs = []
    for line in text.splitlines():
        m = NINFER_DONE_RE.match(line.strip())
        if not m:
            continue
        t, n, rest = m.groups()
        r = {"t": t, "n": int(n), "prompt": None, "out": None, "ttft": None, "prefill": None, "tps": None,
             "cached_pct": None, "draft": "-", "copies": "-"}
        for seg in (s.strip() for s in rest.split(" | ")):
            if x := re.fullmatch(r"prompt ([\d,]+)", seg):
                r["prompt"] = int(num(x.group(1)))
            elif x := re.fullmatch(r"output ([\d,]+)", seg):
                r["out"] = int(num(x.group(1)))
            elif x := re.match(r"cache ([\d,]+) \(([\d.]+)%", seg):
                r["cached_pct"] = float(x.group(2))
            elif x := re.fullmatch(r"TTFT (.+)", seg):
                r["ttft"] = seconds(x.group(1))
            elif x := re.fullmatch(r"prefill (.+)", seg):
                r["prefill"] = per_second(x.group(1))
            elif x := re.fullmatch(r"decode (.+)", seg):
                r["tps"] = per_second(x.group(1))
            elif x := re.fullmatch(r"(\w+)(?: speculation)? accepted [\d,]+/[\d,]+ \(([\d.]+)%\)", seg):
                r["copies" if x.group(1) == "copies" else "draft"] = f"{x.group(2)}%"
            elif x := re.fullmatch(r"ngram ([\d,]+)/([\d,]+) accepted(?:, [\d,]+ rounds?)?", seg):
                if num(x.group(2)):
                    r["copies"] = f"{num(x.group(1)) / num(x.group(2)):.1%}"
        if r["prompt"] is not None and r["out"] is not None and r["tps"]:
            recs.append(r)
    return recs


@speed_group.command("ninfer")
@click.option("--since", default="1h", show_default=True, help="How far back to read the pod log ( kubectl --since ).")
@click.option("--namespace", "-n", default="dev-ninfer", show_default=True)
@click.option("--deployment", default="ninfer", show_default=True)
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
@FILE_OPT
def ninfer_speeds(since, namespace, deployment, min_out, last, file):
    """Per-request speeds and speculative-decoding acceptance from ninfer's "req#N done" log lines.

    Prefill and decode are ninfer's own figures for the request. Cached is the share of the prompt served from the
    prefix cache. Draft is the share of drafted tokens accepted ( dflash2 or mtp ), and copies the same for n-gram
    copy drafting. Times are as logged. Agent traffic has short replies, so lower --min-out ( say 100 ) to see
    the per-band summary for it.
    """
    recs = parse_ninfer(cluster_or_file(file, lambda: kubectl("-n", namespace, "logs", f"deploy/{deployment}",
                                                              f"--since={since}")))
    if not recs:
        raise click.ClickException("no 'req#N done' lines found")
    dash = lambda v, fmt: "-" if v is None else format(v, fmt)
    if last:
        click.echo(f"{len(recs)} requests, showing the last {min(last, len(recs))}:")
        show_table(["time", "#", "prompt", "cached", "out", "ttft s", "prefill tok/s", "decode tok/s", "draft", "copies"],
                   [[r["t"], r["n"], f"{r['prompt']:,}", dash(r["cached_pct"], ".0f") + ("" if r["cached_pct"] is None else "%"),
                     f"{r['out']:,}", dash(r["ttft"], ".1f"), dash(r["prefill"], ",.0f"), f"{r['tps']:.1f}",
                     r["draft"], r["copies"]] for r in recs[-last:]])
    show_bands(recs, min_out, "decode tok/sec")


NINFER_METRIC_RE = re.compile(r"^((?:ninfer|llamacpp):\w+)(\{[^}]*\})?\s+(\S+)$")


def parse_ninfer_counters(text):
    totals = {}
    for line in text.splitlines():
        m = NINFER_METRIC_RE.match(line.strip())
        if not m:
            continue
        name, _, value = m.groups()
        try:
            totals[name] = totals.get(name, 0) + float(value)
        except ValueError:
            continue
    return totals


@metric_group.command("ninfer")
@FILE_OPT
def ninfer_metrics(file):
    """Decode speed and speculative-decoding acceptance from ninfer's /metrics.

    The counters are cumulative since the pod started, so restart it ( or note the numbers before and after a
    test ) to measure one run. The decode speed is tokens over the time spent in decode rounds, so it excludes
    prefill and waiting. Reads /metrics through a kubectl port-forward, with the API key from vault.
    """
    t = parse_ninfer_counters(cluster_or_file(file, lambda: engine_api("ninfer", "GET", "/metrics", ninfer_headers())))
    # ninfer-all's names ( docs/serving.md#metrics ): the llamacpp: series keep llama.cpp's meanings, ninfer: are its own
    tokens, secs = t.get("llamacpp:tokens_predicted_total", 0), t.get("llamacpp:tokens_predicted_seconds_total", 0)
    if not (tokens and secs):
        raise click.ClickException("no ninfer decode counters found ( has it served a request yet? )")
    click.echo(f"decode: {tokens:,.0f} tokens in {secs:,.1f}s of decode rounds = {tokens / secs:.1f} tok/s")

    rounds = t.get("llamacpp:n_decode_total", 0)
    drafted, accepted = t.get("ninfer:draft_tokens_total", 0), t.get("ninfer:draft_accepted_tokens_total", 0)
    if rounds and drafted:
        click.echo(f"speculation: {rounds:,.0f} decode rounds   draft tokens {drafted:,.0f}   accepted {accepted:,.0f} "
                   f"( {accepted / drafted:.1%} )")
        click.echo(f"tokens per decode round: {tokens / rounds:.2f}")
    else:
        click.echo("no speculative-decoding counters found ( NINFER_SPEC=none? )")
    p_tokens, p_secs = t.get("llamacpp:prompt_tokens_total", 0), t.get("llamacpp:prompt_seconds_total", 0)
    if p_tokens and p_secs:
        click.echo(f"prefill: {p_tokens:,.0f} uncached tokens in {p_secs:,.1f}s = {p_tokens / p_secs:,.0f} tok/s")


# --- mistralrs and sglang ---------------------------------------------------------------------------------------------

def parse_prefixed(text, prefix):
    """Sum each `<prefix>name{labels} value` metric over its labels. Returns ( totals, { name: { label value: value } } )
    where the second holds the `position` label of the per-position counters."""
    totals, by_pos = {}, {}
    pattern = re.compile(rf"^{re.escape(prefix)}(\w+)(\{{[^}}]*\}})?\s+(\S+)$")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m or m.group(1).endswith("_created"):
            continue
        name, labels, value = m.groups()
        try:
            v = float(value)
        except ValueError:
            continue
        pos = re.search(r'position="(\d+)"', labels or "")
        if pos:
            by_pos.setdefault(name, {})
            by_pos[name][int(pos.group(1))] = by_pos[name].get(int(pos.group(1)), 0) + v
        else:
            totals[name] = totals.get(name, 0) + v
    return totals, by_pos


SGLANG_LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) TP0\] (Prefill|Decode) batch, (.*)$")


def sglang_field(text, name):
    m = re.search(rf"{re.escape(name)}: ([\d.]+)", text)
    return float(m.group(1)) if m else None


def parse_sglang(text):
    """One record per generation, rebuilt from sglang's per-second 'Decode batch' lines ( it logs nothing per request ).

    A generation is a run of lines with one request running, from a prefill to the next prefill, a gap of 10s or more, or the
    context shrinking. Lines with other requests running are skipped, so only single-stream time is measured. The prompt is
    the context size on the run's first line, out is how far that context grew, and tps is the median of the lines' own
    "gen throughput". Cached is the share of the prompt the prefill lines before the run report as already cached."""
    recs, run, prompt, cached = [], None, 0, 0

    def close():
        nonlocal run
        if run and len(run["tps"]) >= 2:
            full = run["full"]
            step = statistics.median(b - a for a, b in zip(full, full[1:]))
            lens, rates = run["len"], run["rate"]
            recs.append({"t": run["t"], "prompt": int(full[0]), "out": int(full[-1] - full[0] + step),
                         "tps": statistics.median(run["tps"]),
                         "cached_pct": 100 * run["cached"] / run["prompt_total"] if run["prompt_total"] else None,
                         "accept_len": statistics.mean(lens) if lens else None,
                         "accept_rate": statistics.mean(rates) if rates else None})
        run = None

    for line in text.splitlines():
        m = SGLANG_LINE_RE.match(line)
        if not m:
            continue
        when = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        kind, rest = m.group(2), m.group(3)
        if run and (when - run["last"]).total_seconds() >= 10:
            close()
        if kind == "Prefill":
            new, hit = sglang_field(rest, "#new-token"), sglang_field(rest, "#cached-token")
            if new is None or hit is None or (new <= 1 and hit == 0):  # /health runs a one-token generation
                continue
            close()
            prompt += new + hit
            cached += hit
            continue
        running, full, tps = sglang_field(rest, "#running-req"), sglang_field(rest, "#full token"), \
            sglang_field(rest, "gen throughput (token/s)")
        if running != 1 or full is None or tps is None:
            continue
        if run and full < run["full"][-1]:
            close()
        if not run:
            run = {"t": m.group(1)[11:], "full": [], "tps": [], "len": [], "rate": [], "last": when,
                   "prompt_total": prompt, "cached": cached}
            prompt = cached = 0
        run["full"].append(full)
        run["tps"].append(tps)
        run["last"] = when
        for key, name in (("len", "accept len"), ("rate", "accept rate")):
            value = sglang_field(rest, name)
            if value is not None:
                run[key].append(value)
    close()
    return recs


@speed_group.command("sglang")
@click.option("--since", default="1h", show_default=True, help="How far back to read the pod log ( kubectl --since ).")
@click.option("--namespace", "-n", default="dev-sglang", show_default=True)
@click.option("--deployment", default="sglang", show_default=True)
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
@FILE_OPT
def sglang_speeds(since, namespace, deployment, min_out, last, file):
    """Per-generation speeds and MTP acceptance from sglang's "Decode batch" log lines.

    sglang logs nothing per request, so each row is rebuilt from the once-a-second decode lines: a run with one request
    running, from its prefill to the next one. The figures are approximate. Prompt is the context size when the run began,
    out is how far it grew, and decode is the median of sglang's own gen throughput for the run. Time spent with two
    requests running is left out, and prefill speed and time to first token aren't available. Accept len is the average
    tokens per decode step ( 1.00 = no draft was ever accepted ) and accept rate the share of drafted tokens accepted.
    Times are as logged ( UTC ).
    """
    recs = parse_sglang(cluster_or_file(file, lambda: kubectl("-n", namespace, "logs", f"deploy/{deployment}",
                                                              f"--since={since}")))
    if not recs:
        raise click.ClickException("no single-request 'Decode batch' runs found ( at least two lines in a row )")
    dash = lambda v, fmt: "-" if v is None else format(v, fmt)
    if last:
        click.echo(f"{len(recs)} generations, showing the last {min(last, len(recs))}:")
        show_table(["time", "prompt", "cached", "out", "accept len", "accept rate", "decode tok/s"],
                   [[r["t"], f"{r['prompt']:,}", dash(r["cached_pct"], ".0f") + ("" if r["cached_pct"] is None else "%"),
                     f"{r['out']:,}", dash(r["accept_len"], ".2f"),
                     "-" if r["accept_rate"] is None else f"{r['accept_rate']:.0%}", f"{r['tps']:.1f}"]
                    for r in recs[-last:]])
    show_bands(recs, min_out, "decode tok/sec")


@metric_group.command("mistralrs")
@FILE_OPT
def mistralrs_metrics(file):
    """Token counts, prefix-cache hit rate and MTP draft acceptance from mistralrs's /metrics.

    The counters are cumulative since the pod started, so restart it ( or note the numbers before and after a
    test ) to measure one run. mistralrs publishes no per-request speed, so there is no `speed mistralrs`. Reads
    /metrics through a kubectl port-forward.
    """
    t, by_pos = parse_prefixed(cluster_or_file(file, lambda: engine_api("mistralrs", "GET", "/metrics")), "mistralrs_")
    prefill, decode = t.get("prefill_tokens_processed_total", 0), t.get("decode_tokens_processed_total", 0)
    if not (prefill or decode):
        raise click.ClickException("no mistralrs token counters found ( has it served a request yet? )")
    click.echo(f"tokens: {prefill:,.0f} prompt   {decode:,.0f} generated")
    lookups, hits = t.get("prefix_cache_lookups_total", 0), t.get("prefix_cache_hits_total", 0)
    if lookups:
        click.echo(f"prefix cache: {hits:,.0f} of {lookups:,.0f} requests reused a cached prefix ( {hits / lookups:.1%} )")
    drafts, proposed = t.get("speculative_drafts_total", 0), t.get("speculative_draft_tokens_proposed_total", 0)
    accepted = t.get("speculative_draft_tokens_accepted_total", 0)
    if drafts and proposed:
        click.echo(f"speculation: {drafts:,.0f} drafts   proposed {proposed:,.0f}   accepted {accepted:,.0f} ( {accepted / proposed:.1%} )")
        per_pos = by_pos.get("speculative_draft_tokens_accepted_per_pos_total", {})
        if per_pos:
            click.echo("accepted by draft position: " + "   ".join(f"{p + 1}: {per_pos[p] / drafts:.1%}" for p in sorted(per_pos)))
        click.echo(f"tokens per forward pass: {1 + accepted / drafts:.2f}   ( 1 + accepted / drafts )")
    else:
        click.echo("no speculative-decoding counters found ( is --mtp on? )")
    preempted = t.get("paged_preemptions_total", 0)
    if preempted:
        click.echo(f"preemptions: {preempted:,.0f} requests were paused because the KV cache ran out")


@metric_group.command("sglang")
@FILE_OPT
def sglang_metrics(file):
    """Token counts, latency averages and cache hit rate from sglang's /metrics.

    The token and latency figures are cumulative since the pod started, so restart it ( or note the numbers before and
    after a test ) to measure one run. gen_throughput is a gauge of the speed right now, so read it while a request is
    running. For per-generation speeds use `speed sglang`. Reads /metrics through a kubectl port-forward ( the server
    is started with --enable-metrics ).
    """
    t, _ = parse_prefixed(cluster_or_file(file, lambda: engine_api("sglang", "GET", "/metrics")), "sglang:")
    prompt, gen = t.get("prompt_tokens_total", 0), t.get("generation_tokens_total", 0)
    if not (prompt or gen):
        raise click.ClickException("no sglang token counters found ( has it served a request yet? )")
    click.echo(f"tokens: {prompt:,.0f} prompt   {gen:,.0f} generated")
    if t.get("gen_throughput"):
        click.echo(f"generation throughput now: {t['gen_throughput']:.1f} tok/s")
    for label, name in (("time to first token", "time_to_first_token_seconds"), ("request latency", "e2e_request_latency_seconds"),
                        ("time per output token", "request_time_per_output_token_seconds")):
        count = t.get(f"{name}_count", 0)
        if count:
            click.echo(f"mean {label}: {t.get(f'{name}_sum', 0) / count:.2f}s over {count:,.0f} requests")
    if "cache_hit_rate" in t:
        click.echo(f"prefix cache hit rate: {t['cache_hit_rate']:.1%}   ( sglang's gauge )")
    # sglang's acceptance metrics are not in the docs I read, so they are shown only when the server emits them
    for name in sorted(n for n in t if n.startswith("spec_") and n not in ("spec_num_steps", "spec_num_draft_tokens")):
        click.echo(f"{name}: {t[name]:g}")
    if t.get("spec_num_steps"):
        click.echo(f"speculation: {t['spec_num_steps']:.0f} steps, {t.get('spec_num_draft_tokens', 0):.0f} draft tokens")


# --- engines ----------------------------------------------------------------------------------------------------------

ANSIBLE =Path(__file__).resolve().parent.parent / "ansible"
ENGINES = {"ollama": {"ns": "dev-ollama", "port": 11434},
           "sonar": {"ns": "dev-sonar", "port": 2242},
           "tabbyapi": {"ns": "dev-tabbyapi", "port": 5000},
           "ninfer": {"ns": "dev-ninfer", "port": 8080},
           "mistralrs": {"ns": "dev-mistralrs", "port": 1234},
           "sglang": {"ns": "dev-sglang", "port": 30000}}
# the engines with a model registry ( ansible/vars/<engine>/<cluster>-models.vars.yml ); ollama manages its own
CATALOGUED = ("sonar", "tabbyapi", "ninfer", "mistralrs", "sglang")
# the engines that serve one model per process, with no key, and list it at /v1/models
SINGLE_MODEL = ("sonar", "mistralrs", "sglang")
# the model_info key on the litellm models this script creates ( holding {"engine", "ansible-model"} ), so
# `model sync` never touches ones made in the UI
TAG = "bookcase-ops"
ENGINE_OPT = click.option("--engine", type=click.Choice(list(ENGINES)),
                          help="Default: the engine that is currently scaled up.")


def service_url(engine):
    return f"http://web.{ENGINES[engine]['ns']}.svc.cluster.local:{ENGINES[engine]['port']}"


def http(method, url, headers=None, body=None, timeout=60, session=None):
    """One JSON HTTP call. Returns the decoded JSON ( or text ); raises ClickException on a non-2xx or no connection."""
    try:
        r = (session or requests).request(method, url, headers=headers, json=body, timeout=timeout)
        r.raise_for_status()
    except requests.HTTPError as e:
        raise click.ClickException(f"{method} {url} returned HTTP {e.response.status_code}: {e.response.text[:300]}")
    except requests.exceptions.SSLError as e:
        raise click.ClickException(f"TLS error calling {url}: {str(e)[-200:]} ( if it's your private CA, set LITELLM_CACERT to its certificate )")
    except requests.Timeout:
        raise click.ClickException(f"{url} timed out after {timeout}s")
    except requests.ConnectionError:
        raise click.ClickException(f"cannot reach {url}")
    except requests.RequestException as e:
        raise click.ClickException(f"{method} {url} failed: {e}")
    try:
        return r.json()
    except ValueError:
        return r.text


@functools.lru_cache(maxsize=None)
def vault_client():
    # hvac reads VAULT_ADDR, VAULT_TOKEN ( or ~/.vault-token ) and VAULT_CACERT from the environment
    if not os.environ.get("VAULT_ADDR"):
        raise click.ClickException("VAULT_ADDR isn't set ( run `. vault-login.sh` first )")
    return hvac.Client()


@functools.lru_cache(maxsize=None)
def vault_secret(path):
    """A KV v2 secret under the `secret` mount, as a dict. Read once per run."""
    client = vault_client()
    try:
        return client.secrets.kv.v2.read_secret_version(path=path, mount_point="secret")["data"]["data"]
    except hvac.exceptions.InvalidPath:
        raise click.ClickException(f"vault has no secret/{path}")
    except hvac.exceptions.Forbidden:
        raise click.ClickException(f"vault refused secret/{path}: the token has expired or has no access ( `. vault-login.sh` )")
    except (hvac.exceptions.VaultError, requests.RequestException) as e:
        raise click.ClickException(f"cannot read secret/{path} from vault at {client.url}: {e}")


def vault(path, field):
    secret = vault_secret(path)
    if field not in secret:
        raise click.ClickException(f"vault secret/{path} has no field '{field}'")
    return secret[field]


def litellm_key():
    return vault("db/bnesql02/litellm", "master_key")


def tabbyapi_key(field):
    return vault(f"k8s/{CLUSTER}/secret/dev-tabbyapi/tabbyapi", field)


def tabbyapi_headers(admin=False):
    if admin:
        return {"x-admin-key": tabbyapi_key("admin_key")}
    key = tabbyapi_key("api_key")
    return {"x-api-key": key, "Authorization": f"Bearer {key}"}


def tabbyapi_folders():
    """The model folders on tabbyapi's PVC. Its /v1/models lists only the model that is loaded, so ask the filesystem."""
    out = kubectl("-n", ENGINES["tabbyapi"]["ns"], "exec", "deploy/tabbyapi", "-c", "tabbyapi", "--", "ls", "-1", "/app/models")
    return {line.strip() for line in out.splitlines() if line.strip()}


def ninfer_key():
    return vault(f"k8s/{CLUSTER}/secret/dev-ninfer/ninfer", "api_key")


def ninfer_headers():
    return {"Authorization": f"Bearer {ninfer_key()}"}


def engine_api_key(engine):
    """The key litellm sends to the engine ( sonar, mistralrs and sglang have none, but litellm wants a non-empty string )."""
    if engine == "tabbyapi":
        return tabbyapi_key("api_key")
    if engine == "ninfer":
        return ninfer_key()
    return "none"


# litellm has an ingress, so it's called directly. It's served with a certificate from the private CA, which requests
# doesn't know ( it uses certifi, not the OS store ): point LITELLM_CACERT ( or REQUESTS_CA_BUNDLE ) at the CA.
LITELLM_URL = os.environ.get("LITELLM_URL", "https://litellm.dev.randomnoun")


@functools.lru_cache(maxsize=None)
def litellm_session():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {litellm_key()}"
    if os.environ.get("LITELLM_CACERT"):
        s.verify = os.environ["LITELLM_CACERT"]
    return s


def litellm_api(method, path, body=None):
    return http(method, f"{LITELLM_URL.rstrip('/')}{path}", body=body, session=litellm_session())


@contextlib.contextmanager
def forward(engine):
    """kubectl port-forward to the engine's service on a free local port; yields the local base URL.

    The engines have no ingress, so this is the way in from outside the cluster.
    """
    exe = shutil.which("kubectl")
    if not exe:
        raise click.ClickException("kubectl not found on PATH")
    proc = subprocess.Popen([exe, "-n", ENGINES[engine]["ns"], "port-forward", "svc/web", f":{ENGINES[engine]['port']}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    try:
        line = proc.stdout.readline()
        m = re.search(r"127\.0\.0\.1:(\d+)", line)
        if not m:
            raise click.ClickException(f"kubectl port-forward to {engine} failed: {(line + proc.stdout.read()).strip()[:300]}")
        yield f"http://127.0.0.1:{m.group(1)}"
    finally:
        proc.terminate()
        proc.wait()


def engine_api(engine, method, path, headers=None, body=None, timeout=60):
    with forward(engine) as base:
        s = requests.Session()
        s.trust_env = False  # it's localhost: don't send it through an HTTP_PROXY
        return http(method, f"{base}{path}", headers, body, timeout, s)


def engine_state(engine):
    """Replicas and pod phase, or None if the engine isn't deployed."""
    ns = ENGINES[engine]["ns"]
    try:
        d = json.loads(kubectl("-n", ns, "get", f"deploy/{engine}", "-o", "json"))
    except click.ClickException as e:
        if "NotFound" in str(e) or "not found" in str(e):
            return None
        raise
    pods = json.loads(kubectl("-n", ns, "get", "pods", "-l", f"app={engine}-web", "-o", "json"))["items"]
    return {"replicas": d["spec"].get("replicas", 0), "ready": d["status"].get("readyReplicas", 0),
            "phase": pods[0]["status"]["phase"] if pods else "-"}


def current_engine(engine=None):
    if engine:
        return engine
    running = [e for e in ENGINES if (engine_state(e) or {}).get("replicas", 0) > 0]
    if len(running) != 1:
        raise click.ClickException(
            ("no engine is scaled up" if not running else f"more than one engine is scaled up ( {', '.join(running)} )")
            + ": pass --engine, or use `engine set`")
    return running[0]


def serving(engine):
    """What the engine says it is serving, asked from its own API."""
    if engine == "ollama":
        return [m["name"] for m in engine_api("ollama", "GET", "/api/ps")["models"]]
    if engine in SINGLE_MODEL:
        return [m["id"] for m in engine_api(engine, "GET", "/v1/models")["data"]]
    if engine == "ninfer":
        return [m["id"] for m in engine_api("ninfer", "GET", "/v1/models", ninfer_headers())["data"]]
    try:
        return [engine_api("tabbyapi", "GET", "/v1/model", tabbyapi_headers())["id"]]
    except click.ClickException as e:
        if "HTTP 404" in str(e):
            return []
        raise


def litellm_models():
    return litellm_api("GET", "/model/info")["data"]


def engine_of(model):
    base = (model.get("litellm_params") or {}).get("api_base") or ""
    return next((e for e in ENGINES if f"web.{ENGINES[e]['ns']}." in base), None)


# --- the model registries ---------------------------------------------------------------------------------------------

def catalogue_path(engine):
    return ANSIBLE / "vars" / engine / f"{CLUSTER}-models.vars.yml"


def catalogue(engine):
    """( active model name, { name: entry } ) from the engine's models vars file."""
    path = catalogue_path(engine)
    if not path.exists():
        raise click.ClickException(f"{path} not found")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get(f"{engine}_active_model") or "", data.get(f"{engine}_models") or {}


def set_active(engine, name):
    """Rewrite the one `<engine>_active_model:` line, so the comments in the file survive."""
    path = catalogue_path(engine)
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new, n = re.subn(rf'^({engine}_active_model:\s*)"[^"\n]*"', lambda m: f'{m.group(1)}"{name}"', text, flags=re.M)
    if n != 1:
        raise click.ClickException(f'expected one `{engine}_active_model: "..."` line in {path}')
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)


def run_ansible(engine):
    exe = shutil.which("ansible-playbook")
    cmd = ["ansible-playbook", "-e", f"deployments={CLUSTER}", "-e", f"app={engine}", f"k8s_llm_{CLUSTER}.yml"]
    if not exe:
        raise click.ClickException(f"ansible-playbook not found on PATH ( on Windows, run this from WSL ). To do it by hand:\n"
                                   f"  cd ansible && {' '.join(cmd)}")
    click.echo(f"+ cd ansible && {' '.join(cmd)}")
    if subprocess.run([exe, *cmd[1:]], cwd=ANSIBLE).returncode != 0:
        raise click.ClickException("ansible-playbook failed ( the registry file keeps the new active model; "
                                   "`git checkout` it to revert )")


# --- litellm sync -----------------------------------------------------------------------------------------------------

def desired_models():
    """The litellm model each registered engine should have: its active entry's `litellm:` block. No api_key yet."""
    out = {}
    for engine in CATALOGUED:
        active, models = catalogue(engine)
        if not active:
            continue
        if active not in models:
            raise click.ClickException(f"{engine}_active_model '{active}' is not in {engine}_models")
        entry = models[active]
        lit = entry.get("litellm")
        if not lit:
            continue
        upstream = entry["folder"] if engine == "tabbyapi" else lit.get(
            "upstream", {"sonar": "local-sonar", "sglang": "local-sglang", "mistralrs": entry.get("model")}.get(engine, engine))
        out[engine] = {
            "model_name": lit["public_name"],
            "litellm_params": {"model": f"openai/{upstream}", "api_base": f"{service_url(engine)}/v1",
                               **(lit.get("params") or {})},
            "model_info": {TAG: {"engine": engine, "ansible-model": active}}}
    return out


def differs(have, want):
    """Whether litellm's value differs from the registry's. /model/info masks values whose key looks secret
    ( "token", "key" ... ) as "****", so those can't be compared and are treated as equal."""
    if isinstance(have, str) and "****" in have:
        return False
    if isinstance(want, dict):
        # a PATCH replaces a top-level dict whole, so keys that are only in litellm count as a difference
        return not isinstance(have, dict) or bool(set(have) - set(want)) or any(differs(have.get(k), v) for k, v in want.items())
    return have != want


def same_model(have, want):
    # the UI may store "local-sonar" where the registry says "openai/local-sonar"; both reach the same endpoint
    strip = lambda m: m.removeprefix("openai/") if isinstance(m, str) else m
    return strip(have) == strip(want)


def sync_plan(existing, desired):
    """[ ( action, engine, wanted, existing model, [ ( key, litellm's value, registry's value ) ] ) ]: what it takes to
    make litellm match the registries."""
    actions = []
    for engine in CATALOGUED:
        want = desired.get(engine)
        if want:
            match = next((m for m in existing if m["model_name"] == want["model_name"]), None)
            if not match:
                actions.append(("create", engine, want, None, []))
            else:
                have_p, have_i = match.get("litellm_params") or {}, match.get("model_info") or {}
                changed = [(k, have_p.get(k), v) for k, v in want["litellm_params"].items()
                           if not same_model(have_p.get(k), v) and k == "model"
                           or k != "model" and differs(have_p.get(k), v)]
                changed += [(k, have_i.get(k), v) for k, v in want["model_info"].items() if have_i.get(k) != v]
                if changed:
                    actions.append(("update", engine, want, match, changed))
        for m in existing:
            tag = (m.get("model_info") or {}).get(TAG)
            if isinstance(tag, dict) and tag.get("engine") == engine \
                    and m["model_name"] != (want or {}).get("model_name"):
                actions.append(("delete", engine, None, m, []))
    return actions


def describe(action):
    kind, engine, want, match, changed = action
    if kind == "create":
        return f"create   {want['model_name']}  ( {engine} )"
    if kind == "update":
        return f"update   {want['model_name']}  ( {engine}: {', '.join(k for k, _, _ in changed)} )"
    return f"delete   {match['model_name']}  ( {engine}: no longer the active model )"


def describe_changes(action):
    """The lines under an update: litellm's value and the registry's, for each key that differs."""
    show = lambda v: "( not set )" if v is None else json.dumps(v)
    return [f"      {k}: {show(have)}  ->  {show(want)}" for k, have, want in action[4]]


def apply_sync(actions):
    for action in actions:
        kind, engine, want, match, changed = action
        if kind == "create":
            params = dict(want["litellm_params"])
            params["api_key"] = engine_api_key(engine)
            litellm_api("POST", "/model/new", {"model_name": want["model_name"], "litellm_params": params,
                                               "model_info": want["model_info"]})
        elif kind == "update":
            # PATCH merges at the top level, so send only what differs ( not the model or api_key that work today )
            keys = {k for k, _, _ in changed}
            body = {"model_info": want["model_info"]}
            params = {k: v for k, v in want["litellm_params"].items() if k in keys}
            if params:
                body["litellm_params"] = params
            litellm_api("PATCH", f"/model/{match['model_info']['id']}/update", body)
        else:
            litellm_api("POST", "/model/delete", {"id": match["model_info"]["id"]})
        click.echo("  done: " + describe(action))


def run_sync(dry_run):
    desired = desired_models()
    actions = sync_plan(litellm_models(), desired)
    if not actions:
        click.echo("litellm is in sync with the registries")
        return
    click.echo("litellm changes:")
    for a in actions:
        click.echo("  " + describe(a))
        for line in describe_changes(a):
            click.echo(line)
    if dry_run:
        return
    apply_sync(actions)
    after = {m["model_name"]: m for m in litellm_models()}
    lost = [w["model_name"] for w in desired.values()
            if TAG not in (after.get(w["model_name"], {}).get("model_info") or {})]
    if lost:
        click.echo(f"WARNING: litellm didn't keep model_info.{TAG} on {', '.join(lost)}; later syncs will update "
                   f"them by name but can't tell which entries are theirs to delete.")
    if any(a[0] == "update" and any(k == "num_ctx" for k, _, _ in a[4]) or a[0] == "create" for a in actions):
        click.echo("pi and opencode read the context window from litellm: run "
                   "`kubectl -n dev-pi exec deploy/pi -- pi-sync-models` and restart opencode.")


@engine_group.command("list")
def engine_list():
    """Replicas, pod phase, the configured and the serving model, and the litellm models of each engine.

    SERVING is what the engine itself reports ( asked through a kubectl port-forward ); CONFIG is the active model in
    the registry. LITELLM lists the litellm models whose api_base is the engine, and "!" marks an engine whose
    entry differs from the registry ( see `model sync` ).
    """
    try:
        models = litellm_models()
        plan = sync_plan(models, desired_models())
    except click.ClickException as e:
        click.echo(f"( litellm unavailable: {e.message} )", err=True)
        models, plan = None, []
    rows = []
    for engine in ENGINES:
        st = engine_state(engine)
        if not st:
            rows.append([engine, "not deployed", "-", "-", "-", "-"])
            continue
        config = catalogue(engine)[0] or "-" if engine in CATALOGUED else "-"
        try:
            now = ", ".join(serving(engine)) or "-" if st["ready"] > 0 else "-"
        except click.ClickException:
            now = "?"
        names = "?" if models is None else ", ".join(m["model_name"] for m in models if engine_of(m) == engine) or "-"
        if any(a[1] == engine for a in plan):
            names += "  !"
        rows.append([engine, f"{st['ready']}/{st['replicas']}", st["phase"], config, now, names])
    show_table(["engine", "ready", "pod", "config", "serving", "litellm"], rows, left=(0, 1, 2, 3, 4, 5))
    if any(r[5].endswith("!") for r in rows):
        click.echo("\n! - litellm entry does not match ansible; run llm.py model sync --dry-run")


@engine_group.command("set")
@click.argument("engine", type=click.Choice(list(ENGINES)))
@click.option("--timeout", default="15m", show_default=True, help="How long to wait for the rollout.")
@click.option("--dry-run", is_flag=True)
def engine_set(engine, timeout, dry_run):
    """Give the GPUs to ENGINE: scale the others to 0, wait for their pods to go, scale ENGINE to 1, wait for the rollout.

    ollama, sonar, tabbyapi, mistralrs and sglang each request both GPUs, and a GPU stays held until the old pod has
    finished terminating, so the others have to be gone before the new pod can schedule. ninfer asks for one GPU, but
    the others are scaled down for it too: it's only run on its own, and a second engine would cut into its memory.
    """
    target = engine_state(engine)
    if not target:
        raise click.ClickException(f"{engine} isn't deployed: run `ansible-playbook -e deployments={CLUSTER} -e app={engine} k8s_llm_{CLUSTER}.yml` first")
    if engine in CATALOGUED and engine != "tabbyapi" and not catalogue(engine)[0]:
        raise click.ClickException(f"{engine} has no active model, so its role deploys nothing: set {engine}_active_model")
    if engine == "tabbyapi" and not catalogue("tabbyapi")[0]:
        click.echo("note: tabbyapi has no active model, so it will start with nothing loaded")
    others = [e for e in ENGINES if e != engine and (engine_state(e) or {}).get("replicas", 0) > 0]
    for other in others:
        click.echo(f"scale {other} to 0")
        if not dry_run:
            kubectl("-n", ENGINES[other]["ns"], "scale", f"deploy/{other}", "--replicas=0")
    for other in others:
        if dry_run:
            continue
        deadline = time.time() + 600
        while kubectl("-n", ENGINES[other]["ns"], "get", "pods", "-l", f"app={other}-web", "-o", "name").strip():
            if time.time() > deadline:
                raise click.ClickException(f"{other}'s pod is still terminating after 10 minutes")
            time.sleep(3)
        click.echo(f"{other} is gone")
    click.echo(f"scale {engine} to 1")
    if not dry_run:
        kubectl("-n", ENGINES[engine]["ns"], "scale", f"deploy/{engine}", "--replicas=1")
        click.echo(kubectl("-n", ENGINES[engine]["ns"], "rollout", "status", f"deploy/{engine}", f"--timeout={timeout}").strip())


# --- models -----------------------------------------------------------------------------------------------------------

@model_group.command("list")
@ENGINE_OPT
def model_list(engine):
    """The models of the current engine: the registry entries ( every engine but ollama ) or what's pulled ( ollama )."""
    engine = current_engine(engine)
    st = engine_state(engine)
    up = bool(st and st["ready"] > 0)
    rows = []
    if engine == "ollama":
        if not up:
            raise click.ClickException("ollama isn't running")
        loaded = set(serving("ollama"))
        for m in engine_api("ollama", "GET", "/api/tags")["models"]:
            rows.append([m["name"], "loaded" if m["name"] in loaded else "", f"{m['size'] / 1e9:.1f} GB"])
        show_table(["model", "state", "size"], rows)
        return
    active, models = catalogue(engine)
    on_disk = None
    if engine == "tabbyapi" and up:
        on_disk = tabbyapi_folders()
    for name, e in models.items():
        if engine == "sonar":
            detail = f"{e['model']}@{e.get('revision', '-')}  ctx {e.get('max_model_len', '-')}"
        elif engine == "mistralrs":
            detail = f"{e['model']}  quant {e.get('quant', '-')}  ctx {e.get('max_model_len', '-')}"
        elif engine == "sglang":
            detail = f"{e['model']}@{e.get('revision', '-')}  ctx {e.get('context_length', '-')}"
        elif engine == "ninfer":
            detail = f"{e['profile']}  {e.get('spec', 'default spec')}  ctx {e.get('context', 'fit to free memory')}"
        else:
            detail = f"{e['folder']}  ctx {e.get('max_seq_len', '-')}"
            if on_disk is not None and e["folder"] not in on_disk:
                detail += "  ( not downloaded: `model download` )"
        rows.append([name, "active" if name == active else "", detail])
    if on_disk:
        known = {e["folder"] for e in models.values()}
        rows += [[f, "", "on the PVC, no registry entry"] for f in sorted(on_disk - known)]
    show_table(["model", "state", "detail"], rows)
    if not up:
        click.echo(f"( {engine} isn't running, so this is the registry only )")


@model_group.command("load")
@click.argument("name")
@ENGINE_OPT
@click.option("--no-apply", is_flag=True, help="Only edit the registry and print the ansible command.")
@click.option("--no-sync", is_flag=True, help="Don't update litellm afterwards.")
@click.option("--dry-run", is_flag=True)
def model_load(name, engine, no_apply, no_sync, dry_run):
    """Make NAME the current engine's model, persistently.

    Rewrites <engine>_active_model in the registry ( ansible/vars/<engine>/<cluster>-models.vars.yml ), re-runs the
    engine's ansible role so the pod restarts with the model's settings, then syncs litellm. The change shows up in git
    and survives pod restarts. An engine that is scaled to 0 picks the model up when you next `engine set` it.
    """
    engine = current_engine(engine)
    if engine not in CATALOGUED:
        raise click.ClickException(f"{engine} has no registry: pull models with `ollama pull`, and add them to litellm by hand")
    active, models = catalogue(engine)
    if name not in models:
        raise click.ClickException(f"{name} is not in {engine}_models ( {', '.join(models)} )")
    st = engine_state(engine)
    if engine == "tabbyapi" and st and st["ready"] > 0:
        if models[name]["folder"] not in tabbyapi_folders():
            raise click.ClickException(f"{models[name]['folder']} isn't on the PVC: run `model download {name}` first")
    click.echo(f"{engine}: {active or '-'} -> {name}" if name != active else f"{engine}: {name} is already the active model")
    if dry_run:
        return
    if name != active:
        set_active(engine, name)
    if no_apply:
        click.echo(f"now run: cd ansible && ansible-playbook -e deployments={CLUSTER} -e app={engine} k8s_llm_{CLUSTER}.yml")
        return
    run_ansible(engine)
    if not no_sync:
        run_sync(False)


@model_group.command("unload")
@ENGINE_OPT
def model_unload(engine):
    """Unload the current model from VRAM. Not persistent: tabbyapi loads its active model again when its pod restarts."""
    engine = current_engine(engine)
    if engine in ("sonar", "ninfer", "mistralrs", "sglang"):
        raise click.ClickException(f"{engine} serves one model per process and can't unload it: use `engine set` to scale it down")
    if engine == "tabbyapi":
        engine_api("tabbyapi", "POST", "/v1/model/unload", tabbyapi_headers(admin=True), {})
        click.echo("tabbyapi: unloaded")
        return
    for m in serving("ollama"):
        engine_api("ollama", "POST", "/api/generate", body={"model": m, "keep_alive": 0})
        click.echo(f"ollama: unloaded {m}")


@model_group.command("download")
@click.argument("name")
@click.option("--timeout", default=3600, show_default=True, help="Seconds to wait for tabbyapi to finish the download.")
def model_download(name, timeout):
    """Download registry entry NAME into tabbyapi's PVC ( from its `download:` block ), ready for `model load`."""
    _, models = catalogue("tabbyapi")
    if name not in models:
        raise click.ClickException(f"{name} is not in tabbyapi_models ( {', '.join(models)} )")
    entry = models[name]
    dl = entry.get("download")
    if not dl:
        raise click.ClickException(f"{name} has no download: block")
    st = engine_state("tabbyapi")
    if not st or st["ready"] < 1:
        raise click.ClickException("tabbyapi isn't running: `engine set tabbyapi` first")
    click.echo(f"downloading {dl['repo_id']}@{dl.get('revision', 'main')} to {entry['folder']} ( watch with "
               f"`kubectl -n dev-tabbyapi exec deploy/tabbyapi -- du -sh /app/models/{entry['folder']}` )")
    body = {"repo_id": dl["repo_id"], "folder_name": entry["folder"]}
    if dl.get("revision"):
        body["revision"] = dl["revision"]
    engine_api("tabbyapi", "POST", "/v1/download", tabbyapi_headers(admin=True), body, timeout=timeout)
    click.echo("done")


@model_group.command("sync")
@click.option("--dry-run", is_flag=True, help="Show what would change in litellm.")
def model_sync(dry_run):
    """Make litellm's models match the active model of each registered engine ( every engine but ollama ).

    Creates or updates one litellm model per engine from the active entry's `litellm:` block, and deletes the
    models this script made for entries that are no longer active ( sonar serves every model under one name, so a
    stale entry would answer with the wrong model ). Models made in the litellm UI are left alone, and so is ollama.
    """
    run_sync(dry_run)


if __name__ == "__main__":
    cli()

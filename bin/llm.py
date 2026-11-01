#!/usr/bin/env python3
"""Tools for comparing the LLM backends ( sonar, tabbyapi ) in this repo.

    python bin/llm.py --help
    python bin/llm.py langfuse-speeds --help

To add a verb, write a function and decorate it with @cli.command(). Only click and the standard library are
needed; the sonar-* and tabby-* verbs also need kubectl pointing at the cluster.
"""
import base64
import json
import re
import shutil
import statistics
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import click

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

@click.group()
def cli():
    """Compare and inspect the LLM backends ( sonar, tabbyapi ) in this repo."""


# --- langfuse ---------------------------------------------------------------------------------------------------------

@cli.command("langfuse-speeds")
@click.argument("start")
@click.argument("end", required=False)
@click.option("--host", envvar="LANGFUSE_HOST", default="https://langfuse.dev.randomnoun", show_default=True)
@click.option("--public-key", envvar="LANGFUSE_PUBLIC_KEY", required=True, help="Or set LANGFUSE_PUBLIC_KEY.")
@click.option("--secret-key", envvar="LANGFUSE_SECRET_KEY", required=True,
              help="Or set LANGFUSE_SECRET_KEY ( preferred: it doesn't end up in your shell history ).")
@click.option("--model", help="Only models whose name contains this text ( case-insensitive ).")
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--basis", type=click.Choice(["e2e", "decode"]), default="e2e", show_default=True,
              help="e2e = output tokens / total latency ( includes prefill ). decode = output tokens / ( latency - "
                   "time to first token ), only for requests that have a first-token time.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
def langfuse_speeds(start, end, host, public_key, secret_key, model, min_out, basis, last):
    """Per-request speeds from langfuse, for generations between START and END ( ISO 8601, UTC ).

    \b
    Example:
      python bin/llm.py langfuse-speeds 2026-10-29T12:30:00Z 2026-10-29T14:00:00Z --model sonar

    Uses the v2 observations API ( langfuse v4 ). litellm v1.104 sends no first-token time, so for now only the
    e2e basis has data; the decode basis works once litellm sends it ( see setup/SETUP-LLM-MTP.md ).
    """
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


@cli.command("sonar-speeds")
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


@cli.command("sonar-metrics")
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


@cli.command("tabby-speeds")
@click.option("--since", default="1h", show_default=True, help="How far back to read the pod log ( kubectl --since ).")
@click.option("--namespace", "-n", default="dev-tabbyapi", show_default=True)
@click.option("--deployment", default="tabbyapi", show_default=True)
@click.option("--min-out", default=1000, show_default=True, help="Minimum output tokens for the per-band summary.")
@click.option("--last", default=30, show_default=True, help="Show only the last N requests in the table ( 0 = summary only ).")
@FILE_OPT
def tabby_speeds(since, namespace, deployment, min_out, last, file):
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


if __name__ == "__main__":
    cli()

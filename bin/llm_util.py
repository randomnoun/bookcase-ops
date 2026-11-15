#!/usr/bin/env python3
"""Tests to run against an engine that is already serving ( the speed and metric verbs in llm.py only read what it logged ).

    python3 llm_util.py longprompt                 # one request at each of 8k, 190k and 250k tokens, to the engine that is scaled up
    python3 llm_util.py longprompt --tokens 120000 --engine strata

Needs what llm.py needs: kubectl, and `. vault-login.sh` for the engines that have an API key. It reaches the engine through llm.py's temporary
kubectl port-forward, so it doesn't go through litellm and nothing it sends is logged in langfuse.
"""

import json
import sys
import time
from pathlib import Path

import click
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()
PASSPHRASE = "marmalade-4471"


def filler(lines):
    """Repetitive numbered lines. The same text is produced for the same count, so a longer prompt starts with a shorter one's text and Strata's conversation cache can reuse it."""
    return "\n".join(f"Entry {i:05d}: " + " ".join(WORDS[(i + j) % len(WORDS)] for j in range(12)) + "." for i in range(lines))


def build_prompt(lines):
    return (f"The passphrase for this test is {PASSPHRASE}. Remember it.\n\n" + filler(lines) +
            "\n\nIgnore the entries above. What was the passphrase given at the very start? Reply with the passphrase only.")


def headers_for(engine):
    if engine == "strata":
        return llm.strata_headers()
    if engine == "ninfer":
        return llm.ninfer_headers()
    if engine == "tabbyapi":
        return llm.tabbyapi_headers()
    return {}


def restart_count(engine):
    pods = json.loads(llm.kubectl("-n", llm.ENGINES[engine]["ns"], "get", "pods", "-o", "json"))["items"]
    return sum(s["restartCount"] for p in pods for s in p["status"].get("containerStatuses", []))


def ask(session, base, headers, model, lines, max_tokens, budget, timeout):
    """One chat request. Returns a dict of what came back, or {'error': ...}."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": build_prompt(lines)}]}
    if budget is not None:
        body["reasoning_budget_tokens"] = budget
    t = time.time()
    try:
        r = session.post(f"{base}/v1/chat/completions", headers=headers, json=body, timeout=timeout)
    except requests.RequestException as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "seconds": time.time() - t}
    seconds = time.time() - t
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}", "seconds": seconds}
    j = r.json()
    msg = j["choices"][0]["message"]
    usage = j.get("usage") or {}
    return {"seconds": seconds, "prompt": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
            "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0, "finish": j["choices"][0].get("finish_reason"),
            "content": msg.get("content") or ""}


@click.group()
@click.option("--cluster", default=llm.CLUSTER, show_default=True, envvar="LLM_CLUSTER")
def cli(cluster):
    """Tests to run against a serving engine."""
    llm.CLUSTER = cluster


@cli.command("longprompt")
@click.option("--engine", type=click.Choice(list(llm.ENGINES)), help="Default: the engine that is currently scaled up.")
@click.option("--tokens", "sizes", multiple=True, type=int, help="Prompt size to aim for, in tokens; repeat for several. Default: 8000, 190000 and 250000.")
@click.option("--max-tokens", default=300, show_default=True, help="Reply limit. The answer is only the passphrase, so a small number does.")
@click.option("--reasoning-budget", type=int, default=None, help="reasoning_budget_tokens to send ( strata only; default 100 there, so the answer isn't spent on thinking ).")
@click.option("--model", help="Model name to send. Default: what the engine reports at /v1/models.")
@click.option("--timeout", default=1500, show_default=True, help="Seconds to wait for each reply.")
def longprompt(engine, sizes, max_tokens, reasoning_budget, model, timeout):
    """Send prompts of increasing size with a passphrase on the first line, and check the engine answers with it.

    It first sends a small prompt to find how many tokens a line of filler is for this model's tokenizer, then one request per
    size in order ( smallest first; each longer prompt starts with the shorter one's text, so a caching engine reuses it ).
    Per request it prints the prompt tokens the engine counted, how many came from its cache, the time, the reading speed of the
    uncached part, the reply speed, and whether the passphrase came back. It also says whether the pod restarted meanwhile: an
    out-of-memory restart during a long prompt is what this is for. Filler text and a one-word answer show that the cache and the
    attention fit, not that the model reads long documents well.
    """
    engine = llm.current_engine(engine)
    sizes = sorted(sizes) or [8000, 190000, 250000]
    if engine == "strata" and reasoning_budget is None:
        reasoning_budget = 100
    if reasoning_budget is not None and engine != "strata":
        click.echo(f"( reasoning_budget_tokens is only known to strata; sending it to {engine} anyway )", err=True)
    headers = headers_for(engine)
    before = restart_count(engine)
    with llm.forward(engine) as base:
        session = requests.Session()
        session.trust_env = False
        if model is None:
            try:
                model = llm.serving(engine)[0]
            except (click.ClickException, IndexError):
                model = "default"
        click.echo(f"{engine}: model {model}, restarts so far {before}, reply limit {max_tokens}"
                   + (f", reasoning budget {reasoning_budget}" if reasoning_budget is not None else ""))
        probe = ask(session, base, headers, model, 300, max_tokens, reasoning_budget, timeout)
        if "error" in probe or not probe.get("prompt"):
            raise click.ClickException(f"the calibration request failed: {probe.get('error', 'no usage in the reply')}")
        per_line = (probe["prompt"] - 60) / 300
        click.echo(f"calibration: 300 lines = {probe['prompt']:,} tokens ( about {per_line:.1f} per line )\n")
        rows, failed = [], False
        for target in sizes:
            lines = max(1, int((target - 60) / per_line))
            r = ask(session, base, headers, model, lines, max_tokens, reasoning_budget, timeout)
            if "error" in r:
                rows.append([f"{target:,}", "-", "-", f"{r['seconds']:.0f}", "-", "-", r["error"][:90]])
                failed = True
                continue
            fresh = max(r["prompt"] - r["cached"], 0)
            read = f"{fresh / r['seconds']:,.0f}" if fresh and r["seconds"] else "-"
            ok = PASSPHRASE in r["content"]
            rows.append([f"{target:,}", f"{r['prompt']:,}", f"{100 * r['cached'] / r['prompt']:.0f}%" if r["prompt"] else "-", f"{r['seconds']:.1f}",
                         read, str(r["out"]), "passphrase recalled" if ok else f"NOT recalled ( finish {r['finish']}: {r['content'][:60]!r} )"])
            failed = failed or not ok
    after = restart_count(engine)
    llm.show_table(["aimed", "prompt tokens", "cached", "seconds", "uncached tok/s ( incl. reply )", "out", "result"], rows, left=(6,))
    click.echo(f"\nrestarts: {before} before, {after} after" + ("  <- the pod restarted during the test" if after != before else ""))
    click.echo("see also: python3 llm.py speed " + engine + " --last " + str(len(sizes) + 1) + "   ( the engine's own read and decode speeds for these requests )")
    if failed or after != before:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    cli()

# bookcase-ops LLM backend: strata

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[Strata](https://github.com/Niko1221/Strata) ( MIT, by Niko1221 ) is a local inference engine built for large mixture-of-experts models on consumer hardware. Its main model is **Qwen3.8-Flash-Next**, 125 billion parameters, which it runs from
GGUF files by keeping the experts in system RAM ( or on the SSD ) and the dense layers and the hottest experts on the GPUs. It serves OpenAI and Anthropic APIs, one model per process here.

* **A different model from the other engines.** The others serve Qwen3.8-27B. Strata's models are the 125B Flash-Next ( and a few others, see its [MODELS.md](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md) ), so its speeds and answers aren't comparable with theirs.
* **Both GPUs, as a layer split.** Each card holds a range of layers and a cache of the hot experts; a token crosses once per verify window, so it needs no NVLink or peer access, which these 3090s don't have.
* **Lots of RAM.** The model needs RAM for its experts plus about 10 GB: 34 GB ( Q2_0 ) to 50 GB ( IQ3_S ) in the docs' figures. `bnenod05` has about 94 GB.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `ghcr.io/niko1221/strata:0.2.13-cuda13`. The CUDA 13 image needs NVIDIA driver 580 or newer, and `bnenod05` has 580.178.04.
* **Run on the 3090s.** It downloaded the 76 GB IQ3_XXS model overnight, loaded it in about 90 seconds and served a 63-request pi agent session at a median of about 159 tok/sec, with 98% of prompt tokens reused from its conversation cache ( [below](#what-was-found-running-it-2026-11-15) ). It is a different, larger model from the other engines', so the speeds don't compare directly.

## Install it

1. Put an API key in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). Strata won't serve beyond the machine without one, so the role refuses to run without a key of 16 or more characters.

   ```
   echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-strata/strata" api_key=-
   ```

   There is no Hugging Face token: Strata downloads anonymously and doesn't read or send one.

2. Check the node can pull the image: `sudo ctr -n k8s.io images pull ghcr.io/niko1221/strata:0.2.13-cuda13` ( about 640 MB to download, 2.2 GB on disk, from Strata's Docker doc ).

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=strata k8s_llm_bnekub03.yml
   python bin/llm.py engine set strata
   kubectl -n dev-strata logs -f deploy/strata
   ```

   The first start downloads the model: 76.7 GB for IQ3_XXS, at about 30 MB/s here ( faster than the 16-19 MB/s Strata's docs measured ), so 40-60 minutes, and it prepares a pack. The log shows `Downloading the model files: 7.7 of 76.7 GB`.
   The startup probe allows 3 hours. Strata is resumable: an interrupted download carries on when the pod restarts, and everything is kept on the PVC ( `/data/Strata-data`, checked: not the container's own disk ).
   Once downloaded it loads in about 90 seconds.

   To see what a choice would do, once the pod is running: `kubectl -n dev-strata exec deploy/strata -- strata launch --plan --family qwen --model IQ3_XXS --context 128k`. The plan for IQ3_XXS at 128k said 76 GB for the model, 1.2 GB left to download ( the DFlash2 drafter ), pictures on, and DFlash2 drafting.

The model and its settings are in the registry, `vars/strata/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `family` and `model`: Strata's `FAMILY` and `MODEL`. `qwen` with `Q2_0`, `IQ2_XS`, `IQ3_XXS` or `IQ3_S` are ISTA-DASLab's original sizes of Flash-Next, and the registry lists `IQ3_XXS` ( active ), `Q2_0` and `IQ3_S`.
* `context`: `CONTEXT`, such as `128k`. Left out, Strata takes the largest that fits.
* optionally `parallel`, `kv`, `vision`, `set` ( `STRATA_SET`, one engine setting per item ) and `env` ( any other variable the image reads ). They become environment variables on the container, and are only set when present.

| Size | RAM + VRAM for the model | Experts in RAM | Speed | Quality |
|---|---|---|---|---|
| `Q2_0` | 37.6 GB | 34 GB | fastest | good |
| `IQ2_XS` | 39.2 GB | 35.5 GB | fast | better |
| `IQ3_XXS` | 47.0 GB | 43 GB | slower | great |
| `IQ3_S` | 54.8 GB | 50 GB | slowest | best; matches the full model on Strata's published tests |

* **No memory or CPU limit.** Strata's Docker doc says not to put a memory limit on the container: it plans from the whole machine's RAM and loads tens of GB. So the pod has none, and only requests 48 Gi and 12 CPUs. It can crowd out everything else on `bnenod05`, so keep other engines scaled to 0.
* **Memory locking.** The Docker command uses `--ulimit memlock=-1:-1` so the engine can pin the RAM it holds the experts in. Kubernetes has no setting for that, and the node's default limit is 8 MB, which I measured inside another engine's pod. The deployment adds the `IPC_LOCK` capability instead,
  and that was enough: the engine process had 26.8 GiB locked ( `VmLck` in `/proc/<pid>/status` ). `ulimit -l` in a shell in the pod still says 8192, so it isn't a check of this; look at `VmLck`.
* **Context.** 128k is my choice, not Strata's: it started at 131072 ( `/health` says `max_context: 131072` ), so the litellm `num_ctx` of 131072 matches. Keep them equal.
* **Thinking.** Flash-Next's template thinks by default at `xhigh`. Strata has a `reasoning_budget_tokens` setting that ends the thinking at N tokens, but it is a key in the engine's own config file, and I haven't found a way to set it server-wide in the Docker image,
  so the registry sends it with every request through litellm ( `extra_body.reasoning_budget_tokens: 8192` ), where a request's own value wins. A direct request with `reasoning_budget_tokens: 40` was cut at 199 characters of thinking and followed by a correct answer.
  I haven't compared it with an uncapped request, or checked that litellm passes it through.
* **No `curl` or `wget` in the image.** It has its own Python at `/opt/strata/terminal/python/bin/python3`, which `urllib` works with, if you need to call the API from inside the pod. With Git Bash on Windows, set `MSYS_NO_PATHCONV=1` or kubectl is given a mangled `/opt/...` path.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-strata.svc.cluster.local:8080/v1`, with the vault `api_key`. Strata serves whatever model name it is sent,
so the name `strata` is only a label; `python bin/llm.py engine list` shows what it reports at `/v1/models`. By hand: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `strata`, Public Model Name `strata-qwen3.8-flash-next:iq3_xxs`, that API base and key,
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

## Metrics

Strata has a `/metrics` endpoint that returns JSON for its own monitor, and Prometheus text with vLLM's metric names when asked ( `?format=prometheus` ), plus its own `strata:` series. The role creates a `ServiceMonitor` that asks for that format and sends the API key as a bearer token.
`/health` answers without a key.

```
python3 ../bin/llm.py metric strata     # speed now, requests running, KV cache use, prefix-cache hits, draft acceptance, GPU and RAM load, tool-call repairs
```

It goes in through a `kubectl port-forward`, with the key from vault ( `. vault-login.sh` first ). Counters are totals since the pod started. `/metrics` has 38 names: `vllm:*` for requests, tokens, latencies, prefix cache and speculation, and `strata:*` for the engine ( state, slots, live speed, GPU busy and memory, host RAM ).
`GET /metrics?format=prometheus` and `Accept: text/plain` return the same thing; `/metrics/prometheus` is a separate, smaller set in llama-server's names ( `llamacpp:*` ) plus `strata:drafts_*` counters.
`metric strata` was checked against a real response.

Per-request speeds come from Strata's engine log, which is a file on the PVC and not the pod's output, and `llm.py` reads it:

```
python3 ../bin/llm.py speed strata --min-out 100     # per request: prompt, share reused from the conversation cache, tokens out, TTFT, read speed, decode tok/s, draft acceptance
```

Each request logs a line like `strata serve: prompt 6404 tokens = 5729 reused + 675 read in 704 ms (958.7 tok/s), 6144 generated in 48275 ms (127.3 tok/s), drafts accepted 3058 of 5891`. The lines have no timestamps, so there is no `--since`: rows are numbered in file order and `--last` takes the newest.
TTFT is estimated as the time to read the uncached part of the prompt. I haven't checked whether the log file is emptied when the pod restarts. For end-to-end figures use `python3 ../bin/llm.py speed langfuse ... --model strata`.

A Grafana dashboard for these metrics is in [SETUP-GRAFANA.md](SETUP-GRAFANA.md#strata--llm-backend-) ( `ansible/strata-dashboard.json` ), and Prometheus found the ServiceMonitor and was scraping it.

## What was found running it ( 2026-11-15 )

* **Speed.** Over a pi agent session of 63 requests ( prompts from 53 to 88,939 tokens ), decode was a median **158.6 tok/sec** for the 54 with 100+ tokens out ( range 127.3-178.3 ), barely changing with prompt size ( bands of 148-170 ), and 150 tok/sec on a 16,384-token reply.
  Draft acceptance averaged 72% ( 52-84% ), lowest on the longest replies. The full table and comparison are in [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 15. It is a larger, different model from the other engines', so it can't be compared with their figures directly.
* **Conversation cache.** 98.1% of prompt tokens were reused ( weighted by tokens ), the median prompt of 2k+ tokens was 100% reused, and the median first token took 0.5 s ( 1.3 s at the 90th percentile, 7.5 s for the one 24k-token cold prompt ).
* **Expert cache.** 95-99.6% hits ( median 99.1% ), with 96-99.9% of KV block reads from VRAM.
* **Drafting fell back to MTP.** The plan said DFlash2 drafting, but the log said `Drafting: MTP (DFlash2 isn't available in this mode)`. I haven't found what "this mode" is.
* **Memory is nearly full, by design.** With the model idle, each GPU is at about 97% ( 23.4 of 24 GiB ) and RAM is at about 83-89 GiB of 94, of which the engine locks 26.8 GiB. Keep every other engine at 0, and watch for out-of-memory errors on long prompts.
* **`/health`** returns JSON with `status`, `max_context`, `model`, `loaded` and `service`, and works without the key; `/v1/models` needs it. The model name it reports is `qwen3.8-flash-next-iq3_xxs`. Strata serves any name it is sent, and I haven't checked what litellm sends.
* **The probes.** The pod was `1/1` by the morning, so they didn't hold it back, but I didn't watch it go Ready, so I don't know what `/health` returned while it was downloading or loading.
* **Prefix cache.** `vllm:prefix_cache_hits_total` counts the conversation cache: 97.9% of prompt tokens after a pi agent session, against 98.1% from the engine log's own totals.

**Not confirmed:** output quality; tool calls and thinking through litellm and pi; that litellm passes `reasoning_budget_tokens` on; and any session longer than the 63 requests above.

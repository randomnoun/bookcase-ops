# bookcase-ops LLM backend: strata

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[Strata](https://github.com/Niko1221/Strata) ( MIT, by Niko1221 ) is a local inference engine built for large mixture-of-experts models on consumer hardware. Its main model is **Qwen3.8-Flash-Next**, 125 billion parameters, which it runs from
GGUF files by keeping the experts in system RAM ( or on the SSD ) and the dense layers and the hottest experts on the GPUs. It serves OpenAI and Anthropic APIs, one model per process here.

* **A different model from the other engines.** The others serve Qwen3.8-27B. Strata's models are the 125B Flash-Next ( and a few others, see its [MODELS.md](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md) ), so its speeds and answers aren't comparable with theirs.
* **Both GPUs, as a layer split.** Each card holds a range of layers and a cache of the hot experts; a token crosses once per verify window, so it needs no NVLink or peer access, which these 3090s don't have.
* **Lots of RAM.** The model needs RAM for its experts plus about 10 GB: 34 GB ( Q2_0 ) to 50 GB ( IQ3_S ) in the docs' figures. `bnenod05` has about 94 GB.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `ghcr.io/niko1221/strata:0.2.13-cuda13`. The CUDA 13 image needs NVIDIA driver 580 or newer, and `bnenod05` has 580.178.04.
* **Not run here yet.** Nothing on this page has been tried on the 3090s. It is written from Strata's docs, and where I couldn't confirm that something works it says so.

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

   The first start downloads the model, which is 66-76 GB for the three smaller sizes plus about 6 GB for the draft layer, and Strata's docs measured 16-19 MB/s from Hugging Face without a login, so allow one to two hours,
   then it prepares a pack. The startup probe allows 3 hours. Strata is resumable: an interrupted download carries on when the pod restarts, and everything is kept on the PVC ( `/data` ).

   To see what a choice would do before downloading, once the pod is running: `kubectl -n dev-strata exec deploy/strata -- strata launch --plan --family qwen --model IQ3_XXS --context 128k`.

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
* **Memory locking.** The Docker command uses `--ulimit memlock=-1:-1` so the engine can pin the RAM it holds the experts in. Kubernetes has no setting for that, and the node's default limit is 8 MB, which I measured inside another engine's pod. The deployment adds the `IPC_LOCK` capability instead.
  I haven't confirmed that this is enough, or what Strata does if it isn't. If startup fails or reports it can't pin memory, that is the first thing to look at.
* **Context.** 128k is my choice, not Strata's. Keep the litellm `num_ctx` equal to the context the server logs at startup.
* **Thinking.** Flash-Next's template thinks by default at `xhigh`. Strata has a `reasoning_budget_tokens` setting that ends the thinking at N tokens, but it is a key in the engine's own config file, and I haven't found a way to set it server-wide in the Docker image,
  so the registry sends it with every request through litellm ( `extra_body.reasoning_budget_tokens: 8192` ), where a request's own value wins. I haven't checked that the cap works that way.

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

It goes in through a `kubectl port-forward`, with the key from vault ( `. vault-login.sh` first ). Counters are totals since the pod started. Strata's docs name only some of the metrics ( `vllm:num_requests_running`, `vllm:kv_cache_usage_perc`,
the prefix-cache and speculative-decoding counters, `strata:live_tok_s`, `strata:last_hit_rate`, `strata:gpu_util`, `strata:ram_used_bytes` ), so `metric strata` prints those and then any token counters and time histograms it finds; I wrote it against the docs and
sample text, not a running server. There is no `speed strata`: its per-request log format isn't documented in what I read. For end-to-end figures use `python3 ../bin/llm.py speed langfuse ... --model strata`.

## What I haven't confirmed

* Everything on this page about the running engine: the first start, memory locking, whether the model fits at 128k, and its speed.
* What `/health` returns while the model downloads or loads. The probes use it with a 5 second timeout; if the pod goes Ready too early, or never, swap them for `tcpSocket` checks as tabbyapi does.
* Whether `GET /metrics?format=prometheus` is right through the `ServiceMonitor` ( `params` ), or whether it needs the `Accept: text/plain` header instead.
* Which model name Strata reports at `/v1/models`, and whether it needs to match the litellm `upstream`.
* Output quality, and tool calls and thinking through litellm and pi. Strata's docs say its tool-call parser repairs some malformed calls and that a quant below Q4 can break tool calls in some setups.

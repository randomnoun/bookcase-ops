# bookcase-ops LLM backend: ninfer

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[NInfer](https://github.com/Neroued/ninfer) is a single-GPU C++/CUDA engine that runs a few specific models from its own `.ninfer` files, and `ninfer-serve` is its server. Upstream targets Blackwell
GPUs; the RTX 3090 port is [ashalliants/ninfer-3090](https://github.com/ashalliants/ninfer-3090), which is what this uses. It serves OpenAI and Anthropic APIs, one model per process.

* **One GPU**, unlike the others here ( `ninfer_gpu_count: 1` ). It is fast because it doesn't split the model across the cards.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **No published image.** The role uses one you build from [docker/ninfer-3090](../docker/ninfer-3090/README.md), which wraps the port's prebuilt Linux release.

## Install it

1. Build the image and push it to nexus3, as for sonar; see the README in `docker/ninfer-3090`. The role pulls `docker-combined.nexus3.dev.randomnoun/ninfer-3090:0.17.1`. 
   Check the node can pull it: `sudo ctr -n k8s.io images pull docker-combined.nexus3.dev.randomnoun/ninfer-3090:0.17.1`

2. Put an API key in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). Without one the server is unauthenticated, on a LoadBalancer service. Set `ninfer_api_key: ""` in `vars/ninfer/bnekub03.vars.yml` to run without.

   ```
   echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-ninfer/ninfer" api_key=-
   ```

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=ninfer k8s_llm_bnekub03.yml
   python bin/llm.py engine set ninfer
   kubectl -n dev-ninfer logs -f deploy/ninfer
   ```

   The first start has an init container, `model`, download the model ( about 19GB ) onto the volume with the release's own `download-model.sh`; it resumes if interrupted and checks the size and SHA-256.
   `kubectl -n dev-ninfer logs -f deploy/ninfer -c model` shows it. The container then loads the model, which the release notes say takes under a minute once it is on disk.

The model and its settings are in the registry, `vars/ninfer/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry names a `profile`
( `qwen38-27b` or `qwen36-35b-a3b` ), which is the model `run.sh` serves and the artifact the init container downloads, a `spec` ( `dflash2` is the fastest at one stream, `mtp` gives two lanes ), and
optionally `context`, `concurrency`, `draft_tokens`, `kv_dtype`, `thinking_budget` and `env` for any other `NINFER_*` variable. They become the environment variables that `run.sh` reads, 
which are listed at the top of `run.sh` in the release.

* **Context length.** If the entry leaves `context` unset, `run.sh` starts at the largest context that fits the free GPU memory, up to 262144, and logs `context lowered to ...` when it has to cut it. 
  Setting `context` turns that off and the server fails if it doesn't fit. Keep the litellm `num_ctx` equal to the context the server logs at startup.
* **KV cache and memory.** The default profile uses a 4-bit-keys KV cache ( `rk4v4` ) and pins 8 GiB of host RAM for its context cache, so the pod asks for 24Gi ( limit 40Gi ). A failed pin retries with half.
* **Thinking.** `thinking_budget` is `--default-thinking-budget`, a server-wide cap. The litellm `reasoning_effort` values the other engines use aren't known to work here; ninfer's docs say `"none"` turns thinking off for Qwen.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-ninfer.svc.cluster.local:8080/v1`, called `ninfer` upstream ( `ninfer_served_model_name` ), 
with the vault `api_key`. By hand, it is the same as for the others: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `ninfer`, Public Model Name `ninfer-qwen3.8:27b-dflash2`, that API base and key, 
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

## Metrics

`ninfer-serve` has a Prometheus `/metrics` endpoint ( the role creates a `ServiceMonitor`, sending the API key as a bearer token ), and `/health` for the probes. It also logs one `req#N done` line per request, with the prompt and
output tokens, how much of the prompt came from the cache, the time to the first token, prefill and decode speed, and how many drafted tokens were accepted. `llm.py` reads both, as it does for sonar and tabbyapi:

```
python3 ../bin/llm.py speed ninfer --since 1h --min-out 100     # per request, from the log, then median decode tok/sec by prompt size
python3 ../bin/llm.py metric ninfer                              # decode speed and speculation acceptance since the pod started, from /metrics
```

`metric ninfer` reads the key from vault ( `. vault-login.sh` first ) and goes in through a `kubectl port-forward`. The speed is `ninfer_decode_tokens_total` over `ninfer_decode_seconds_total`, which only counts time in decode
rounds, and the acceptance is `ninfer_spec_decode_accepted_tokens_total` over `ninfer_spec_decode_draft_tokens_total`. From the same pod, with its own key:

```
kubectl -n dev-ninfer exec deploy/ninfer -c ninfer -- sh -c 'curl -s -H "Authorization: Bearer $NINFER_API_KEY" localhost:8080/metrics | grep -E "decode|spec_decode"'
```

The metric names changed in the release's 0.16.0, so check them if you change `ninfer_image`. The prefill counters are named in ninfer's docs but weren't in the output I checked, so `metric ninfer` shows prefill speed only when they are there.
A `speed` figure for ninfer varies a lot between requests ( 88-178 tok/sec in one coding session ) and follows the draft acceptance, so compare medians over many requests rather than single ones. The measured results are in [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md#7-ninfer--dflash2-one-gpu-).

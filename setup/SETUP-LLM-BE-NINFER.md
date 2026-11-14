# bookcase-ops LLM backend: ninfer

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[NInfer](https://github.com/Neroued/ninfer) is a single-GPU C++/CUDA engine that runs a few specific models from its own `.ninfer` files, and `ninfer-serve` is its server. Upstream targets Blackwell
GPUs. This uses [iamwavecut/ninfer-all](https://github.com/iamwavecut/ninfer-all), which continues the RTX 3090 port ( ashalliants/ninfer-3090, used here until 2026-11-09 ) and covers Volta to consumer Blackwell
and more models. It serves OpenAI and Anthropic APIs, one model per process.

* **One GPU**, unlike the others here ( `ninfer_gpu_count: 1` ). It is fast because it doesn't split the model across the cards.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Published image, nothing to build.** The role pulls `ghcr.io/iamwavecut/ninfer-all:cuda12.9-<commit12>`, pinned to one commit ( the floating `cuda12.9` tag moves with every commit to master ). 
  The `cuda12.9` image holds `sm_86` ( the 3090 ) as well as Volta to Ada; `cuda13.4` does too, but I haven't checked it against bnenod05's driver.
* **A small wrapper for the server.** ninfer-all's `run.sh` has no setting for `--api-key`, `--model-id` or `--default-thinking-budget`, so the role mounts a ConfigMap script ( `NINFER_SERVER` ) that adds them from `NINFER_API_KEY`, 
  `NINFER_MODEL_ID` and `NINFER_DEFAULT_THINKING_BUDGET` and then runs `/opt/ninfer/multi/ninfer-serve`. The container runs `/opt/ninfer/run.sh` directly, not the image's `entrypoint.sh`, whose `run` command would overwrite `NINFER_SERVER`.

## Install it

1. Check the node can pull the image: `sudo ctr -n k8s.io images pull ghcr.io/iamwavecut/ninfer-all:cuda12.9-bdc1b8d8970a`. To move to a newer one, pick a `cuda12.9-<commit12>` tag from the 
   [package page](https://github.com/iamwavecut/ninfer-all/pkgs/container/ninfer-all) and set `ninfer_image` in `vars/ninfer/bnekub03.vars.yml`.

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
   `kubectl -n dev-ninfer logs -f deploy/ninfer -c model` shows it. The container then loads the model, which took about half a minute on ninfer-3090.
   
   If the volume still holds the ninfer-3090 model ( `WarlaxZ/Qwen3.8-27B-NInfer-3090` ), the downloader sees it fails the size and SHA-256 check for ninfer-all's pinned `neroued/Qwen3.8-27B-NInfer` and downloads that beside it, replacing it once the new file has verified. 
   Both files are on the disk for a while, which doesn't matter on the dedicated models disk.

The model and its settings are in the registry, `vars/ninfer/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry names a `profile`
( `qwen38-27b` or `qwen36-35b-a3b` ), which is the model `run.sh` serves and the artifact the init container downloads, a `spec` ( `dflash2` is the fastest at one stream, `mtp` gives two lanes ), and
optionally `context`, `concurrency`, `draft_tokens`, `kv_dtype`, `thinking_budget` and `env` for any other `NINFER_*` variable. They become the environment variables that `run.sh` reads, 
which are listed at the top of `run.sh` in the release.

* **Context length.** If the entry leaves `context` unset, `run.sh` starts at 262144 and, if the server is refused for lack of GPU memory, steps down an eighth at a time ( up to five times ), saying `Not enough free GPU memory to start at context ... Retrying at ...`. 
  Setting `context` turns that off and the server fails if it doesn't fit. Keep the litellm `num_ctx` equal to the context the server ends up with. 
  This replaces ninfer-3090's `--fit-context`; ninfer-all's release notes say a 3090 ended up at 196,608 after two step-downs, and its model is about 1.3 GiB larger than the one ninfer-3090 used, so expect a lower context than the 236,544 seen before.
* **KV cache and memory.** The default profile uses a 4-bit-keys KV cache ( `rk4v4` ) and pins 8 GiB of host RAM for its context cache ( `--host-kv-mib 8192` ), so the pod asks for 24Gi ( limit 40Gi ). It also keeps 32 host state slots, a few GiB more. 
  If the log says `failed to pin`, the container's memlock limit is too low ( the project's docker examples pass `--ulimit memlock=-1` ); the launcher steps down on that as well.
* **Vision is on.** The `tuned` profile loads the vision tower in `overlay` residency ( about 10 MiB of runtime reservation ); `NINFER_VISION=off` in `env` drops it.
* **Thinking.** `thinking_budget` is `--default-thinking-budget`, a server-wide cap. The litellm `reasoning_effort` values the other engines use aren't known to work here; ninfer's docs say `"none"` turns thinking off for Qwen.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-ninfer.svc.cluster.local:8080/v1`, called `ninfer` upstream ( `ninfer_served_model_name` ), 
with the vault `api_key`. By hand, it is the same as for the others: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `ninfer`, Public Model Name `ninfer-qwen3.8:27b-dflash2`, that API base and key, 
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

## Metrics

`ninfer-serve` has a Prometheus `/metrics` endpoint ( the role creates a `ServiceMonitor`, sending the API key as a bearer token ), and `/health` for the probes. It also logs one `req#N done` line per request, with the prompt and
output tokens, how much of the prompt came from the cache, the time to the first token, prefill and decode speed, and how many drafted tokens were accepted. `llm.py` reads both, as it does for sonar and tabbyapi.
**The `req#N done` format is from ninfer-3090 0.17.1; I haven't seen ninfer-all's, so check `speed ninfer` finds requests after the first start** ( ninfer-all's docs describe `request_done` records and a stats panel, not that line ):

```
python3 ../bin/llm.py speed ninfer --since 1h --min-out 100     # per request, from the log, then median decode tok/sec by prompt size
python3 ../bin/llm.py metric ninfer                              # decode speed and speculation acceptance since the pod started, from /metrics
```

`metric ninfer` reads the key from vault ( `. vault-login.sh` first ) and goes in through a `kubectl port-forward`. The speed is `llamacpp:tokens_predicted_total` over `llamacpp:tokens_predicted_seconds_total`, which only counts time in decode
rounds, and the acceptance is `ninfer:draft_accepted_tokens_total` over `ninfer:draft_tokens_total` ( these count completed requests ). Prefill speed comes from `llamacpp:prompt_tokens_total` over `llamacpp:prompt_seconds_total`. From the same pod, with its own key:

```
kubectl -n dev-ninfer exec deploy/ninfer -c ninfer -- sh -c 'curl -s -H "Authorization: Bearer $NINFER_API_KEY" localhost:8080/metrics | grep -E "tokens_predicted|draft"'
```

The metric names changed between ninfer-3090 and ninfer-all ( `ninfer_decode_tokens_total` became `llamacpp:tokens_predicted_total`, and so on ), and `llm.py` follows ninfer-all's, from its `docs/serving.md#metrics`. Check them if you change `ninfer_image`. 
The n-gram copy counters `metric ninfer` used to show are gone: I found no equivalent series in the docs.
A `speed` figure for ninfer varies a lot between requests ( 88-178 tok/sec in one coding session ) and follows the draft acceptance, so compare medians over many requests rather than single ones. The measured results are in [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md#7-ninfer--dflash2-one-gpu-).

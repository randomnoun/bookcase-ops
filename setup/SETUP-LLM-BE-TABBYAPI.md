# bookcase-ops LLM backend: tabbyapi

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[TabbyAPI](https://github.com/theroyallab/tabbyAPI) is an ExLlama server: it runs **EXL3** ( and FP16/BF16 ) models, not GGUF or EXL2. Unlike sonar it has a prebuilt
image ( `ghcr.io/theroyallab/tabbyapi` ), so there's nothing to build. Like ollama it's pinned to `bnenod05`, requests the GPUs, has no Ingress/TLS and is consumed via `litellm`.
Its own README describes it as a hobby project, which is ok for bookcase-ops.

* **It competes with ollama and sonar for the GPUs**, and starts scaled to 0. See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **One model at a time.** Loading another model unloads the first. Unlike sonar, the pod doesn't fetch the model when it starts: you download it into the PVC through tabbyapi's API, then load it.

## Install the model

Everything specific to the model is in the registry, `vars/tabbyapi/bnekub03-models.vars.yml` ( with comments ); the steps below describe the keys of an entry ( `folder`, `max_seq_len`, `cache_mode`,
`tensor_parallel`, `draft_*`, and optionally `gpu_count`, `vision` and `vision_offload` ), not the flat `tabbyapi_*` variables they're copied onto.
`gpu_count: 1` asks kubernetes for one GPU, so tabbyapi sees a single device and doesn't split the model, and the other 3090 stays free ( ninfer can then use it, once its own pod is up ).
`vision` loads the model's vision tower, and `vision_offload` keeps its weights in system RAM instead of VRAM. To change model, add an entry, `python bin/llm.py model download NAME` and `model load NAME` 
( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ), which replaces steps 3 and 4 below for later models. Only the keys and the volume size are still in `vars/tabbyapi/bnekub03.vars.yml`.

Prerequisite: the tabbyapi keys in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md). The `api_key` is what litellm uses to call tabbyapi, and the `admin_key` is needed to download and load models.
The role refuses to run without them.

1. Set the context length and cache:

   ```
   tabbyapi_max_seq_len: 262144
   tabbyapi_cache_mode: "8,8"
   tabbyapi_tensor_parallel: true
   ```

   `tabbyapi_max_seq_len` is the context length. `tabbyapi_cache_mode: "8,8"` quantizes the KV cache to 8 bits ( the closest match to sonar's `--kv-cache-dtype=fp8` ), which halves its VRAM
   at a small quality cost; the role default is `FP16`. For this model a full 262144-token cache at 8 bits is around 8GiB, on top of about 20GB of weights, and it loads fine on the two 3090s. 
   A model that doesn't fit fails to load with an out-of-memory error; lowering `tabbyapi_max_seq_len` is the first thing to try.

   `tabbyapi_tensor_parallel: true` runs both GPUs on every token, as sonar does. With `false` the model is split across the GPUs by filling GPU 0 and then GPU 1 ( autosplit ), so only one card works at a time. 
   For this model that is the difference between about 36-37 tokens/sec ( autosplit ) and about 47-48 ( tensor-parallel ), against about 52 for sonar. The cards are connected over PCIe only, with no NVLink, 
   and it still helps. The first few requests after loading are slow while it warms up.

2. Deploy it with `tabbyapi_model_name` still empty. It comes up at 0 replicas; then give it the GPUs, scaling down whichever engine is running first:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=tabbyapi k8s_llm_bnekub03.yml
   kubectl -n dev-sonar scale deploy/sonar --replicas=0
   kubectl -n dev-tabbyapi scale deploy/tabbyapi --replicas=1
   kubectl -n dev-tabbyapi logs -f deploy/tabbyapi
   ```

   ( swap `dev-sonar` / `sonar` for `dev-ollama` / `ollama` if that's what's running ). It starts with no model loaded.

3. Download the model into the PVC. The `/v1/download` endpoint pulls from HuggingFace:

   ```
   ADMIN=$(vault kv get -mount=secret -field=admin_key k8s/bnekub03/secret/dev-tabbyapi/tabbyapi)
   kubectl -n dev-tabbyapi port-forward svc/web 5000:5000 &
   sleep 5   # port-forward returns before the port is open, and the curl fails if it runs too soon
   curl -s http://localhost:5000/v1/download -H "x-admin-key: $ADMIN" -H "Content-Type: application/json" \
     -d '{"repo_id": "turboderp/Qwen3.8-27B-exl3", "revision": "5.00bpw", "folder_name": "Qwen3.8-27B-exl3-5.00bpw"}'
   ```

   EXL3 repos publish each bits-per-weight as its own branch, which `revision` selects ( I've checked that `5.00bpw` exists for this repo ). 
   `folder_name` is the directory it's saved under in `/app/models`, and what you load the model by. It's about 20GB. 
   You can monitor the download progress via:

   ```
   kubectl -n dev-tabbyapi exec deploy/tabbyapi -- du -sh /app/models/Qwen3.8-27B-exl3-5.00bpw
   ```

4. Set the model in the vars file and re-run the role. The pod restarts and loads it at startup, using the settings from step 1, and loads it again every time the pod starts:

   ```
   tabbyapi_model_name: "Qwen3.8-27B-exl3-5.00bpw"
   ```

   Only set `tabbyapi_model_name` once the download has finished; if the folder doesn't exist, tabbyapi fails at startup. 
   `kubectl -n dev-tabbyapi logs -f deploy/tabbyapi` shows the load. With tensor-parallel on, the load line should no longer end with `(autosplit)`.

   You can also load a model without restarting, through the API. Pass the settings in the request, because a load request that only names the model doesn't pick up the values in the config file ( tabbyapi 
   falls back to the model folder's own config, the config's `use_as_default` keys, then its built-in defaults, so you'd get autosplit and its default cache ):

   ```
   curl http://localhost:5000/v1/model/load -H "x-admin-key: $ADMIN" -H "Content-Type: application/json" \
     -d '{"model_name": "Qwen3.8-27B-exl3-5.00bpw", "tensor_parallel": true, "cache_mode": "8,8", "max_seq_len": 262144}'
   ```

   The call returns a stream of progress messages while the model loads. If it returns straight away with `already loaded` in tabbyapi's log, nothing happened: the model is loaded already, with the old settings. 
   Unload it first ( `curl -X POST http://localhost:5000/v1/model/unload -H "x-admin-key: $ADMIN"` ), or restart the pod. I haven't tried the unload call.

Tool calls and thinking: unlike sonar there are no flags to set. tabbyapi's own config has `tool_format: auto` and `reasoning: true`, and the role leaves both at their defaults, 
so it should pick the tool-call parser and split the `<think>` text out of the answer from the model's chat template. I haven't confirmed that for this model; 
if tool calls come back as plain text in pi, that's the place to look.

## Configure the model in litellm

`python bin/llm.py model sync` does all of this from the registry entry's `litellm:` block. What follows is the same thing by hand.

Add it as an OpenAI-compatible provider:

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * LiteLLM Model Name(s): select **Custom Model Name**, then enter the folder name of the loaded model, e.g. `Qwen3.8-27B-exl3-5.00bpw`
   * Model Mappings - Public Model Name: `tabbyapi-<something>`, e.g. `tabbyapi-qwen3.8:27b-5bpw` ( what litellm clients ask for )
   * API Base: `http://web.dev-tabbyapi.svc.cluster.local:5000/v1`
   * API Key: the `api_key` from vault, via `vault kv get -mount=secret -field=api_key k8s/bnekub03/secret/dev-tabbyapi/tabbyapi` ( run `vault login` first )
   * Try 'test connection' and then save
* Then select the model, click 'Edit Settings', and add these to the 'LiteLLM Params' at the bottom of the page, and 'Save changes':
   * `"num_ctx": 262144` - the context window, equal to `tabbyapi_max_seq_len`. It's only used so pi and opencode know the window ( tabbyapi itself ignores it ); 
     without it pi falls back to its default. Run `pi-sync-models` afterwards ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi) ), and restart opencode.
   * `"reasoning_effort": "xhigh"` - written into the prompt, so the model knows to keep its thinking short. tabbyapi's config says it passes this top-level field to the chat template. 
     The model's template accepts `low`, `medium` and `xhigh` ( the default ); `high` and `max` are rejected.
   * `"extra_body": {"reasoning_budget_tokens": 8192}` - a hard limit on thinking tokens, so a long thought can't use up the whole output limit and leave no answer ( clients report that as "Response was truncated" ). 
     This is tabbyapi's own name for the field; it lists `thinking_token_budget` ( what sonar uses ) as an alias, but litellm hides any value whose key has a `token` segment as `****` in its API, 
     which stops `model sync` seeing it. `reasoning_budget_tokens` isn't hidden. I haven't tested that tabbyapi honours it from litellm. 
     tabbyapi can also apply a budget server-wide with `reasoning_budget_tokens` in its `config.yml`, which the role doesn't expose yet.

Don't use `max_tokens` for this: the model can't see it, so it just truncates the output.

## Metrics

tabbyapi has no Prometheus support, so there's nothing to scrape; see [SETUP-GRAFANA.md](SETUP-GRAFANA.md#tabbyapi--no-metrics-) for what to use instead. 
It logs a line per request with the tokens generated and tokens/sec, which is the number to compare with sonar's `Request completed` line:

```
kubectl -n dev-tabbyapi logs -f deploy/tabbyapi | grep -i "tokens generated"
```

The wording of that line is from reading tabbyapi's source, not from seeing it run, so adjust the `grep` if it doesn't match.

# bookcase-ops LLM backends

Part of the [LLM setup](SETUP-LLM.md).

The backends run the models: `ollama` ( GGUF ), `sonar` and `tabbyapi` ( both EXL3 ). All three are pinned to `bnenod05`, request both GPUs, have no Ingress/TLS and are
consumed only by `litellm`. They each need the `nvidia-device-plugin` and `local-path-provisioner` system components.

## Switching between engines

`ollama`, `sonar` and `tabbyapi` each request both GPUs, so only one can run at a time. Whichever starts second would sit `Pending`, and after a
node reboot they'd race for the GPUs, so only `ollama` starts running ( `ollama_initial_replicas: 1` ); `sonar` and `tabbyapi` are created at 0 replicas.

Switch by scaling the one that's running down to 0 *first* ( a GPU stays held until the old pod has finished terminating ), then the one you want up:

```
kubectl -n dev-ollama   scale deploy/ollama   --replicas=0
kubectl -n dev-tabbyapi scale deploy/tabbyapi --replicas=1
kubectl -n dev-tabbyapi rollout status deploy/tabbyapi --timeout=15m
```

( swap the namespace and name for `dev-sonar` / `sonar` or `dev-ollama` / `ollama` ). Check what's running with `kubectl get deploy -A | grep -E 'ollama|sonar|tabbyapi'`.

Re-running a role does **not** reset the replica count: it reads the current value from the cluster and keeps it, so `initial_replicas` only applies
the first time the deployment is created. To change a role's behaviour permanently, scale it with `kubectl` as above.

Things to note:

* A scaled-down engine keeps its PVC and models; only the pod goes away.
* litellm returns an error for a model whose engine is scaled to 0. It doesn't try to start it.
* Loading a big model takes minutes after scaling up ( tabbyapi/sonar load the weights into VRAM; ollama loads on first request ).
* The `sonar` role does nothing until `sonar_model` is set, whatever its replica count.

## ollama 

Unlike the other apps here, `ollama` is pinned to `bnenod05` (`nodeSelector: gpu-vendor=nvidia`) and requests both GPUs (`nvidia.com/gpu: 2`).
Ollama needs the `nvidia-device-plugin` and `local-path-provisioner` system components to be installed, and has no Ingress/TLS.
It's an internal API consumed by other in-cluster services like `litellm`, so uses a plain `LoadBalancer` Service on its native port (11434).

Once it's runing, download and install models via:

```
kubectl -n dev-ollama exec deploy/ollama -- ollama pull qwen3-coder:30b
kubectl -n dev-ollama exec deploy/ollama -- ollama show qwen3-coder:30b

kubectl -n dev-ollama exec deploy/ollama -- ollama pull llama3.1:8b
kubectl -n dev-ollama exec deploy/ollama -- ollama pull llama3.3:70b
kubectl -n dev-ollama exec deploy/ollama -- ollama pull deepseek-coder-v2:16b
```

Configure the models these in litellm via:

* Models → Add New Model
   * Provider: **Ollama Chat** ( not plain `Ollama` )
   * Model Name: local-qwen3-coder:30b
   * LiteLLM Model Name: qwen3-coder:30b
   * API Base: http://web.dev-ollama.svc.cluster.local:11434
   * Try 'test connection' and then save
* Repeat for all models
* Once you've created them, you can specify per-model context windows
   * I'm just trying this in `local-qwen3:30b` for now; select that and click 'Edit Settings'
   * In the 'LiteLLM Params' at the bottom of the page, add `"num_ctx": 65536` to the JSON
   * 'Save changes' 

**A note on naming:** Model name is what litellm clients ask for, and LiteLLM model name is what's installed in ollama.
I'm using `local-*` to differentiate local models from paid models, when I add those I'll add service-provider prefixes. 

Use the `local-*` names in `pi_default_model` and `opencode_default_model` in the vars files.

**A note on context window size:** The context window ( `ollama_context_length` in `ansible/host_vars/localhost.yml` ) is set to 32768. 

pi and opencode have small prompts and can work with a 8192 length, but openhands sends a 15K+ token prompt. A bigger window costs VRAM for the KV cache. 

`ollama ps` shows the `CONTEXT` and the `PROCESSOR` split for whatever is loaded, if the context window is
too large then the model spills from GPU to the CPU (and is much slower).

`ollama_context_length` sets the server-wide default, litellm values override this. 

The KV cache for the whole window is reserved when the model loads, not as it fills, so a big window costs VRAM straight away, even for a short prompt.

Some defaults have been changed:

* `ollama_flash_attention` ( `OLLAMA_FLASH_ATTENTION` ) is on; enables memory-efficient attention ( memory use grows more slowly, longer prompts )
* `ollama_keep_alive` ( `OLLAMA_KEEP_ALIVE` ) is 30m rather than ollama's 5m; keeps models in memory longer 

**A note on tool usage:** Not all models can run tools; some models that advertise tools are poor at them (e.g. qwen2.5-coder:32b).

`ollama show <model>` lists `tools` under `Capabilities` for the models with tool capabilities:

```
kubectl -n dev-ollama exec deploy/ollama -- ollama show deepseek-coder-v2:16b
```

A session that has already 'gone wrong' due to config can keep going wrong. If an earlier reply in the conversation showed a tool call as text, 
the model may copy that pattern even after you switch to a model that handles tools fine. 
Start a new session ( `/new` in pi ) before concluding a model doesn't work.

To check a model, ask for a tool call with streaming on, and look for `"tool_calls"` in the chunks ( not JSON inside `"content"` ):

```
KEY=<a litellm virtual key>
kubectl -n dev-litellm run toolcheck --rm -i --restart=Never --image=curlimages/curl -- \
  curl -sN http://web.dev-litellm.svc.cluster.local:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"local-qwen3-coder:30b","stream":true,"messages":[{"role":"user","content":"list the files in the current directory"}],"tools":[{"type":"function","function":{"name":"bash","description":"run a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}]}'
```

To test models through litellm, try the following ( uses the virtual key for the 'pi' service account ):

```
KEY=$(vault kv get -mount=secret -field=litellm_virtual_key k8s/bnekub03/secret/dev-pi/pi)
for m in local-qwen3-coder:30b local-llama3.1:8b; do
  echo "== $m"
  curl -s -w '\n%{time_total}s total\n' https://litellm.dev.randomnoun/v1/chat/completions \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain what a mutex is in two sentences\"}]}"
done

== local-qwen3-coder:30b
0.710104s total
A mutex (short for "mutual exclusion") is a synchronization primitive that ensures only one thread or process can access a shared resource or critical section of code at a time. When a thread locks a mutex, other threads attempting to acquire the same lock will be blocked until the first thread releases it, preventing race conditions and data corruption.
tokens: 68
== local-llama3.1:8b
0.883475s total
A mutex (short for "mutual exclusion") is a synchronization primitive that allows only one thread to access a shared resource at a time, preventing other threads from accessing it until the mutex is released. By acquiring and releasing a mutex, threads can coordinate access to shared data, preventing data corruption and ensuring that multiple threads don't try to modify the same data simultaneously.
tokens: 74
```

## sonar

Sonar is an alternative to `ollama` that can serve EXL3 quantizations as well as GGUF, with an OpenAI-compatible API. 
It was formerly called "Aphrodite Engine"; some components still use the old name as the project is mid-rename.  
Like ollama it's pinned to `bnenod05`, requests the GPUs, has no Ingress/TLS and is consumed via `litellm`.

* **It competes with ollama and tabbyapi for the GPUs**, and starts scaled to 0. See [Switching between engines](#switching-between-engines).
  ( Alternatively set two of them to 1 GPU ( and `sonar_tensor_parallel_size: 1` ) and run a small model in each. )
* **One model per deployment.** Sonar loads a single model at startup and keeps it resident; there's no `pull`. The role does nothing until `sonar_model` is set.

### Build the image

There's no prebuilt image of current Sonar on Docker Hub or ghcr.io, so to use it you build one yourself from
[dphnAI/sonar](https://github.com/dphnAI/sonar) and push it to nexus3

To build and push, bump up the memory in `%USERPROFILE%/.wslconfig` first:

```
[wsl2]
memory=26GB
swap=8GB
``` 

then run this on WSL. It takes forever. Well, 7 hours or so.

Note we're pushing to `docker-releases` because `docker-combined` is a read-only group of releases + snapshots and can't be pushed to.

```
git clone --branch v0.24.1 https://github.com/dphnAI/sonar.git
cd sonar

# single job, so the CUDA compile fits in memory; this took about 7 hours to complete
BUILDKIT_MAX_PARALLEL_WORKERS=1 docker build --build-arg BUILDKIT_JOBS=1 -f docker/Dockerfile --target aphrodite-openai -t docker-releases.nexus3.dev.randomnoun/sonar:0.24.1 .

docker login docker-releases.nexus3.dev.randomnoun
docker push docker-releases.nexus3.dev.randomnoun/sonar:0.24.1
```

When the push fails on the largest layer with `failed commit on ref "layer-sha256:..." ... timeout awaiting response headers`, push with `skopeo` instead, which doesn't have that timeout:

```
docker save -o sonar.tar docker-releases.nexus3.dev.randomnoun/sonar:0.24.1
skopeo copy --dest-creds USER:PASS docker-archive:sonar.tar docker://docker-releases.nexus3.dev.randomnoun/sonar:0.24.1
```

Try this from `bnenod05` to check that worked

```
sudo ctr -n k8s.io images pull docker-combined.nexus3.dev.randomnoun/sonar:0.24.1
```

### Install the model

Everything specific to the model is in `vars/sonar/bnekub03.vars.yml` ( with comments ). To change model, edit it and re-run the role.

Prerequisite: the sonar secret in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md). That's a HuggingFace token, read as `sonar_hf_token`; it isn't needed for public repos, 
but unauthenticated downloads are slower and rate limited.

1. Choose the model:

   ```
   sonar_model: "turboderp/Qwen3.8-27B-exl3"
   sonar_model_revision: "5.00bpw"
   ```

   EXL3 repos publish each bits-per-weight as its own branch, which `sonar_model_revision` selects. Leave `sonar_quantization` empty; sonar works out EXL3 from the model files.
   For a GGUF model, set `sonar_model` to the path of a `.gguf` file on the PVC ( copy it in via `kubectl -n dev-sonar cp` ), and `sonar_tokenizer` to the HF repo of the original model.

2. Set the context length and memory:

   ```
   sonar_max_model_len: "262144"
   sonar_gpu_memory_utilization: "0.92"
   ```

   Unlike ollama, sonar reserves `sonar_gpu_memory_utilization` of each GPU up front. If the model doesn't fit it fails to start, rather than spilling onto the CPU.
   Two 24GB 3090s give around 41GB usable at 0.92, so a 70b at 4.0bpw is tight and needs a shorter `sonar_max_model_len`.
   `sonar_max_model_len` only sets the longest single request; the size of the KV cache pool comes from whatever VRAM is left after the weights.

3. Set the tool-call and reasoning flags in `sonar_extra_args`:

   ```
   sonar_extra_args:
     - "--trust-remote-code"
     - "--kv-cache-dtype=fp8"
     - "--enable-auto-tool-choice"
     - "--tool-call-parser=qwen3_coder"
     - "--reasoning-parser=qwen3"
   ```

   A model that calls tools needs `--enable-auto-tool-choice` and a parser matching its tool-call format ( `qwen3_coder` for Qwen's `<tool_call><function=...>` ). 
   Without them, the first request with tools fails with `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`.
   `--reasoning-parser=qwen3` splits the `<think>` text out of the answer so clients don't show it as part of the reply, and it's also what enables `thinking_token_budget`, below.
   The valid parser names are in `aphrodite run --help=all`, run inside the pod; I haven't confirmed that every parser works correctly with every model.

4. Deploy, then give sonar the GPUs:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=sonar k8s_apps_bnekub03.yml
   kubectl -n dev-ollama scale deploy/ollama --replicas=0
   kubectl -n dev-sonar scale deploy/sonar --replicas=1
   kubectl -n dev-sonar logs -f deploy/sonar
   ```

The weights are downloaded from HuggingFace on first start into the PVC, so the first start takes a while; sonar's startup probe allows 2 hours for it. 
You can monitor the download progress via:

```
kubectl -n dev-sonar exec deploy/sonar -- du -shL /root/.cache/huggingface/hub/models--turboderp--Qwen3.8-27B-exl3
```

The finished download is about 20GB. The `-L` matters: HuggingFace keeps the finished files outside the model folder, which only links to them, so without it `du` undercounts and the number can even go down. 
To see which files are still in progress ( they end in `.incomplete` ):

```
kubectl -n dev-sonar exec deploy/sonar -- sh -c 'ls -l /root/.cache/huggingface/hub/models--*/blobs/ | grep incomplete'
```

If a download was interrupted, the abandoned `.incomplete` files stay on the PVC. Once nothing has them open and the finished shards are the right size, they can be deleted by hand.

### Configure the model in litellm

Add it as an OpenAI-compatible provider ( not Ollama Chat ):

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * Model Name: `local-sonar-<something>`, e.g. `local-sonar-qwen3.8:27b-5bpw`
   * LiteLLM Model Name: `local-sonar` ( sonar serves every model under this name; it's `sonar_served_model_name` in the role defaults )
   * API Base: `http://web.dev-sonar.svc.cluster.local:2242/v1`
   * API Key: any non-empty string
   * Try 'test connection' and then save
* Then select the model, click 'Edit Settings', and add these to the 'LiteLLM Params' at the bottom of the page, and 'Save changes'. 
  They have to go here, because sonar has no server-wide default for them:
   * `"num_ctx": 262144` - the context window, equal to `sonar_max_model_len`. It's only used so pi and opencode know the window ( sonar itself ignores it ); 
     without it pi falls back to its default. Run `pi-sync-models` afterwards ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi) ), and restart opencode.
   * `"reasoning_effort": "medium"` - written into the prompt, so the model knows to keep its thinking short. 
     The model's chat template accepts `low`, `medium` and `xhigh` ( the default ); `high` and `max` are rejected with a 400.
   * `"extra_body": {"thinking_token_budget": 8192}` - a hard limit on thinking tokens. When it's reached, sonar forces the thinking to end and the model goes on to its answer, so the answer isn't lost ( the thinking is cut off mid-sentence ). 
     I tested it at 50 tokens: the answer still came back, with about 100 characters of thinking. 8192 is a guess based on the longest legitimate turn seen so far ( about 5,300 tokens ); adjust it once you've seen more.

Qwen thinks by default, and left alone it can spend its whole output limit thinking and never answer, which clients report as "Response was truncated". 
That's what the last two settings are for. Don't use `max_tokens` instead: the model can't see it, so it just truncates the output.
None of these need a model reload in pi, apart from the `pi-sync-models` for `num_ctx`.

### Metrics

Sonar serves Prometheus metrics at `/metrics`, and the role creates a `ServiceMonitor` for them. For tokens/sec, latency and cache usage dashboards see [SETUP-GRAFANA.md](SETUP-GRAFANA.md#sonar--llm-backend-).
It also logs one line per request, with decode and prefill speed, which is the number to use when comparing it with another backend:

```
kubectl -n dev-sonar logs -f deploy/sonar | grep "Request completed"
```

## tabbyapi

[TabbyAPI](https://github.com/theroyallab/tabbyAPI) is an ExLlama server: it runs **EXL3** ( and FP16/BF16 ) models, not GGUF or EXL2. Unlike sonar it has a prebuilt
image ( `ghcr.io/theroyallab/tabbyapi` ), so there's nothing to build. Like ollama it's pinned to `bnenod05`, requests the GPUs, has no Ingress/TLS and is consumed via `litellm`.
Its own README describes it as a hobby project, which is ok for bookcase-ops.

* **It competes with ollama and sonar for the GPUs**, and starts scaled to 0. See [Switching between engines](#switching-between-engines).
* **One model at a time.** Loading another model unloads the first. Unlike sonar, the pod doesn't fetch the model when it starts: you download it into the PVC through tabbyapi's API, then load it.

### Install the model

Everything specific to the model is in `vars/tabbyapi/bnekub03.vars.yml` ( with comments ).

Prerequisite: the tabbyapi keys in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md). The `api_key` is what litellm uses to call tabbyapi, and the `admin_key` is needed to download and load models.
The role refuses to run without them.

1. Set the context length and cache:

   ```
   tabbyapi_max_seq_len: 262144
   tabbyapi_cache_mode: "8,8"
   tabbyapi_tensor_parallel: false
   ```

   `tabbyapi_max_seq_len` is the context length. `tabbyapi_cache_mode: "8,8"` quantizes the KV cache to 8 bits ( the closest match to sonar's `--kv-cache-dtype=fp8` ), which halves its VRAM
   at a small quality cost; the role default is `FP16`. For this model a full 262144-token cache at 8 bits should be around 8GiB, on top of about 20GB of weights. 
   I haven't measured that on tabbyapi, so if the model fails to load with an out-of-memory error, lower `tabbyapi_max_seq_len` first.

   `tabbyapi_tensor_parallel: false` splits the model across both GPUs by filling GPU 0 and then GPU 1 ( autosplit ), where sonar runs tensor-parallel. 
   Try `true` as a benchmark; it can be faster, but I haven't verified it works well on 3090s without NVLink.

2. Deploy it with `tabbyapi_model_name` still empty. It comes up at 0 replicas; then give it the GPUs, scaling down whichever engine is running first:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=tabbyapi k8s_apps_bnekub03.yml
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

4. Load it, either without a restart:

   ```
   curl http://localhost:5000/v1/model/load -H "x-admin-key: $ADMIN" -H "Content-Type: application/json" -d '{"model_name": "Qwen3.8-27B-exl3-5.00bpw"}'
   ```

   or, so it's loaded every time the pod starts, set it in the vars file and re-run the role ( the pod restarts and loads it ):

   ```
   tabbyapi_model_name: "Qwen3.8-27B-exl3-5.00bpw"
   ```

   Only set `tabbyapi_model_name` once the download has finished; if the folder doesn't exist, tabbyapi fails at startup. `kubectl -n dev-tabbyapi logs -f deploy/tabbyapi` shows the load.

Tool calls and thinking: unlike sonar there are no flags to set. tabbyapi's own config has `tool_format: auto` and `reasoning: true`, and the role leaves both at their defaults, 
so it should pick the tool-call parser and split the `<think>` text out of the answer from the model's chat template. I haven't confirmed that for this model; 
if tool calls come back as plain text in pi, that's the place to look.

### Configure the model in litellm

Add it as an OpenAI-compatible provider:

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * Model Name: `local-tabbyapi-<something>`, e.g. `local-tabbyapi-qwen3.8:27b-5bpw`
   * LiteLLM Model Name: the folder name of the loaded model, e.g. `Qwen3.8-27B-exl3-5.00bpw`
   * API Base: `http://web.dev-tabbyapi.svc.cluster.local:5000/v1`
   * API Key: the `api_key` from vault
   * Try 'test connection' and then save
* Then select the model, click 'Edit Settings', and add these to the 'LiteLLM Params' at the bottom of the page, and 'Save changes':
   * `"num_ctx": 262144` - the context window, equal to `tabbyapi_max_seq_len`. It's only used so pi and opencode know the window ( tabbyapi itself ignores it ); 
     without it pi falls back to its default. Run `pi-sync-models` afterwards ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi) ), and restart opencode.
   * `"reasoning_effort": "medium"` - written into the prompt, so the model knows to keep its thinking short. tabbyapi's config says it passes this top-level field to the chat template. 
     The model's template accepts `low`, `medium` and `xhigh` ( the default ); `high` and `max` are rejected.
   * `"extra_body": {"thinking_token_budget": 8192}` - a hard limit on thinking tokens, so a long thought can't use up the whole output limit and leave no answer ( clients report that as "Response was truncated" ). 
     tabbyapi's config lists `thinking_token_budget` as an alias of its `reasoning_budget_tokens` request field, so the same setting as sonar should work, but I haven't tested it on tabbyapi. 
     tabbyapi can also apply a budget server-wide with `reasoning_budget_tokens` in its `config.yml`, which the role doesn't expose yet.

Don't use `max_tokens` for this: the model can't see it, so it just truncates the output.

### Metrics

tabbyapi has no Prometheus support, so there's nothing to scrape; see [SETUP-GRAFANA.md](SETUP-GRAFANA.md#tabbyapi--no-metrics-) for what to use instead. 
It logs a line per request with the tokens generated and tokens/sec, which is the number to compare with sonar's `Request completed` line:

```
kubectl -n dev-tabbyapi logs -f deploy/tabbyapi | grep -i "tokens generated"
```

The wording of that line is from reading tabbyapi's source, not from seeing it run, so adjust the `grep` if it doesn't match.

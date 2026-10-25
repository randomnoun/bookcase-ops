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
   * I'm just trying this in `local-qwen3:30b` for now; selet that and click 'Edit Settings'
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
* **One model per deployment.** Sonar loads a single model at startup and keeps it resident; there's no `pull`. Set `sonar_model` in
  `vars/sonar/bnekub03.vars.yml` and re-run the role to change it ( until it's set, the role does nothing ).
  Weights are downloaded from HuggingFace on first start into the PVC, so the first start takes a while; `kubectl -n dev-sonar logs -f deploy/sonar`.
* **EXL3:** set `sonar_model` to the repo and `sonar_model_revision` to the bits-per-weight branch ( e.g. `5.00bpw` ). Leave `sonar_quantization` empty;
  sonar works out EXL3 from the model files ( `"exl2"`, the role default, is wrong for an EXL3 repo ).
* **GGUF:** set `sonar_model` to a path to a `.gguf` file on the PVC, and `sonar_tokenizer` to the HF repo of the original model.  Copy the file in via `kubectl -n dev-sonar cp`.
* **The VRAM budget is different to ollama's.** Sonar reserves `sonar_gpu_memory_utilization` ( 0.92 in the vars file ) of each GPU and
  pre-allocates the KV cache at startup. If the model doesn't fit it fails to start rather than spilling onto the CPU.
  Two 24GB 3090s give around 41GB usable at 0.92, so a 70b at 4.0bpw is tight and needs a shorter `sonar_max_model_len`.
  `sonar_max_model_len` only sets the longest single request; the size of the KV cache pool comes from whatever VRAM is left after the weights.
* **A HuggingFace token** ( `sonar_hf_token`, read from vault ) isn't needed for public repos, but unauthenticated downloads are slower and rate limited.
  Store a read-only token with the command in [SETUP-DATABASE.md](SETUP-DATABASE.md).
* **The first download is slow, and sonar's startup probe allows a long time for it** ( 2 hours ). Watch progress by listing the `.incomplete` files in the model cache,
  not with `du` on the model folder, which doesn't count finished files:

  ```
  kubectl -n dev-sonar exec deploy/sonar -- sh -c 'ls -l /root/.cache/huggingface/hub/models--*/blobs/ | grep incomplete'
  kubectl -n dev-sonar exec deploy/sonar -- du -sh /root/.cache/huggingface/hub
  ```

  If a download was interrupted, the abandoned `.incomplete` files stay on the PVC. Once nothing has them open and the finished shards are the right size, they can be deleted by hand.

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

`bnenod05` has to trust the CA that signed the nexus3 certificate ( see [SETUP-LLM-KUBERNETES.md](SETUP-LLM-KUBERNETES.md) ), and containerd has to be restarted after installing it.
Try this from `bnenod05` to check that worked

```
sudo ctr -n k8s.io images pull docker-combined.nexus3.dev.randomnoun/sonar:0.24.1
```

Configure the model in litellm as an OpenAI-compatible provider ( not Ollama Chat ):

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * Model Name: `local-sonar-<something>`, e.g. `local-sonar-qwen3.8:27b-5bpw`
   * LiteLLM Model Name: the `sonar_served_model_name` value
   * API Base: `http://web.dev-sonar.svc.cluster.local:2242/v1`
   * API Key: any non-empty string

### Tool calls and thinking

A reasoning model that calls tools needs a few server flags, which are in `sonar_extra_args` in `vars/sonar/bnekub03.vars.yml` ( with comments ). Without them, 
the first request with tools fails with `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`:

* `--enable-auto-tool-choice` and `--tool-call-parser=qwen3_coder` ( the parser matching Qwen's `<tool_call><function=...>` format )
* `--reasoning-parser=qwen3`, which splits the `<think>` text out of the answer so clients don't show it as part of the reply. It also enables `thinking_token_budget`, below.

The valid parser names are in `aphrodite run --help=all`, run inside the pod; I haven't confirmed that every parser works correctly with every model.

Qwen thinks by default, and left alone it can spend its whole output limit thinking and never answer, which clients report as "Response was truncated". Don't use `max_tokens` to fix this:
the model can't see it, so it just truncates the output. Two settings do help, and both go in the model's **LiteLLM Params** ( Models → Edit Settings ), not on the server:

```
"reasoning_effort": "medium"
"extra_body": {"thinking_token_budget": 8192}
```

* `reasoning_effort` is written into the prompt, so the model knows to keep its thinking short. The model's chat template accepts `low`, `medium` and `xhigh` ( the default ); `high` and `max` are rejected with a 400.
* `thinking_token_budget` is a hard limit on thinking tokens. When it's reached sonar forces the thinking to end, and the model goes on to its answer, so the answer isn't lost. The thinking is cut off mid-sentence.
  I tested it at 50 tokens: the answer still came back, with about 100 characters of thinking. 8192 is a guess based on the longest legitimate turn seen so far ( about 5,300 tokens ); adjust it once you've seen more.

Neither needs a model reload in pi. If you also want pi and opencode to know the context window, add `"num_ctx"` to the same params, equal to `sonar_max_model_len`, then run `pi-sync-models` ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi) ).
Sonar itself ignores `num_ctx`.

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

Before you create, will need a litellm key stored in vault, see SETUP-DATABASE.md for details.


Deploy it first with `tabbyapi_model_name` empty ( it comes up at 0 replicas; see [Switching between engines](#switching-between-engines) ), then download an EXL3 model into the PVC. The `/v1/download` endpoint pulls from HuggingFace ( `revision` picks the
bits-per-weight branch, which EXL3 repos usually publish as separate branches ):

```
ADMIN=$(vault kv get -mount=secret -field=admin_key k8s/bnekub03/secret/dev-tabbyapi/tabbyapi)
kubectl -n dev-tabbyapi port-forward svc/web 5000:5000 &
curl -s http://localhost:5000/v1/download -H "x-admin-key: $ADMIN" -H "Content-Type: application/json" \
  -d '{"repo_id": "<hf-org>/<model>-exl3", "revision": "4.0bpw", "folder_name": "<model>-exl3-4.0bpw"}'
```

Then set `tabbyapi_model_name` to that folder name in `vars/tabbyapi/bnekub03.vars.yml` and re-run the role ( the pod restarts and loads it ),
or load it without a restart: `curl http://localhost:5000/v1/model/load -H "x-admin-key: $ADMIN" -H "Content-Type: application/json" -d '{"model_name": "<folder>"}'`.
`kubectl -n dev-tabbyapi logs -f deploy/tabbyapi` shows the load. I haven't verified the exact EXL3 repo names or branch layout, so check the repo's HuggingFace page first.

Things that affect speed and fit, all in `defaults/main.yml`:

* `tabbyapi_tensor_parallel` is off ( autosplit across the two GPUs ). Try `true` as a benchmark; it can be faster, but I haven't verified it works well on 3090s without NVLink.
* `tabbyapi_cache_mode: "8,8"` quantizes the KV cache to save VRAM, at a small quality cost. `tabbyapi_max_seq_len` sets the context length.

Configure the model in litellm as an OpenAI-compatible provider:

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * Model Name: `local-tabby-<something>`
   * LiteLLM Model Name: the folder name of the loaded model
   * API Base: `http://web.dev-tabbyapi.svc.cluster.local:5000/v1`
   * API Key: the `api_key` from vault

# bookcase-ops LLM backend: ollama

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

Like the other backend apps, `ollama` is pinned to `bnenod05` (`nodeSelector: gpu-vendor=nvidia`) and requests both GPUs (`nvidia.com/gpu: 2`).
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
   * LiteLLM Model Name(s): select **Custom Model Name**, then enter `qwen3-coder:30b` ( the name it has in ollama )
   * Model Mappings - Public Model Name: `ollama-qwen3-coder:30b` ( what litellm clients ask for )
   * API Base: http://web.dev-ollama.svc.cluster.local:11434
   * Try 'test connection' and then save
* Repeat for all models
* Once you've created them, you can specify per-model context windows
   * I'm just trying this in `ollama-qwen3:30b` for now; select that and click 'Edit Settings'
   * In the 'LiteLLM Params' at the bottom of the page, add `"num_ctx": 65536` to the JSON
   * 'Save changes' 

**A note on naming:** The public model name is what litellm clients ask for, and the LiteLLM model name is what's installed in ollama.
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
  -d '{"model":"ollama-qwen3-coder:30b","stream":true,"messages":[{"role":"user","content":"list the files in the current directory"}],"tools":[{"type":"function","function":{"name":"bash","description":"run a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}]}'
```

To test models through litellm, try the following ( uses the virtual key for the 'pi' service account ):

```
KEY=$(vault kv get -mount=secret -field=litellm_virtual_key k8s/bnekub03/secret/dev-pi/pi)
for m in ollama-qwen3-coder:30b ollama-llama3.1:8b; do
  echo "== $m"
  curl -s -w '\n%{time_total}s total\n' https://litellm.dev.randomnoun/v1/chat/completions \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain what a mutex is in two sentences\"}]}"
done

== ollama-qwen3-coder:30b
0.710104s total
A mutex (short for "mutual exclusion") is a synchronization primitive that ensures only one thread or process can access a shared resource or critical section of code at a time. When a thread locks a mutex, other threads attempting to acquire the same lock will be blocked until the first thread releases it, preventing race conditions and data corruption.
tokens: 68
== ollama-llama3.1:8b
0.883475s total
A mutex (short for "mutual exclusion") is a synchronization primitive that allows only one thread to access a shared resource at a time, preventing other threads from accessing it until the mutex is released. By acquiring and releasing a mutex, threads can coordinate access to shared data, preventing data corruption and ensuring that multiple threads don't try to modify the same data simultaneously.
tokens: 74
```

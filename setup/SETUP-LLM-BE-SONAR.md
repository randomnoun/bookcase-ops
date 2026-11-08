# bookcase-ops LLM backend: sonar

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

Sonar is an alternative to `ollama` that can serve EXL3 quantizations as well as GGUF, with an OpenAI-compatible API. 
It was formerly called "Aphrodite Engine"; some components still use the old name as the project is mid-rename.  
Like ollama it's pinned to `bnenod05`, requests the GPUs, has no Ingress/TLS and is consumed via `litellm`.

* **It competes with ollama and tabbyapi for the GPUs**, and starts scaled to 0. See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
  ( Alternatively set two of them to 1 GPU ( and `sonar_tensor_parallel_size: 1` ) and run a small model in each. )
* **One model per deployment.** Sonar loads a single model at startup and keeps it resident; there's no `pull`. The role does nothing until `sonar_model` is set.

## Build the image

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

## Install the model

Everything specific to the model is in the registry, `vars/sonar/bnekub03-models.vars.yml` ( with comments ); the steps below describe the keys of an entry ( `model`, `revision`, `max_model_len`, `extra_args` ),
not the flat `sonar_*` variables they're copied onto. To change model, add an entry and `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). 
Only the memory settings and the image are still in `vars/sonar/bnekub03.vars.yml`.

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
   ansible-playbook -vvv -e deployments=bnekub03 -e app=sonar k8s_llm_bnekub03.yml
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

## Configure the model in litellm

`python bin/llm.py model sync` does all of this from the registry entry's `litellm:` block. What follows is the same thing by hand.

Add it as an OpenAI-compatible provider ( not Ollama Chat ):

* Models → Add New Model
   * Provider: **OpenAI-Compatible Endpoints**
   * LiteLLM Model Name(s): select **Custom Model Name**, then enter `local-sonar` ( sonar serves every model under this name; it's `sonar_served_model_name` in the role defaults )
   * Model Mappings - Public Model Name: `sonar-<something>`, e.g. `sonar-qwen3.8:27b-5bpw` ( what litellm clients ask for )
   * API Base: `http://web.dev-sonar.svc.cluster.local:2242/v1`
   * API Key: any non-empty string
   * Try 'test connection' and then save
* Then select the model, click 'Edit Settings', and add these to the 'LiteLLM Params' at the bottom of the page, and 'Save changes'. 
  They have to go here, because sonar has no server-wide default for them:
   * `"num_ctx": 262144` - the context window, equal to `sonar_max_model_len`. It's only used so pi and opencode know the window ( sonar itself ignores it ); 
     without it pi falls back to its default. Run `pi-sync-models` afterwards ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi) ), and restart opencode.
   * `"reasoning_effort": "xhigh"` - written into the prompt, so the model knows to keep its thinking short. 
     The model's chat template accepts `low`, `medium` and `xhigh` ( the default ); `high` and `max` are rejected with a 400.
   * `"extra_body": {"thinking_token_budget": 8192}` - a hard limit on thinking tokens. When it's reached, sonar forces the thinking to end and the model goes on to its answer, so the answer isn't lost ( the thinking is cut off mid-sentence ). 
     I tested it at 50 tokens: the answer still came back, with about 100 characters of thinking. 8192 is a guess based on the longest legitimate turn seen so far ( about 5,300 tokens ); adjust it once you've seen more.

Qwen thinks by default, and left alone it can spend its whole output limit thinking and never answer, which clients report as "Response was truncated". 
That's what the last two settings are for. Don't use `max_tokens` instead: the model can't see it, so it just truncates the output.
None of these need a model reload in pi, apart from the `pi-sync-models` for `num_ctx`.

## Metrics

Sonar serves Prometheus metrics at `/metrics`, and the role creates a `ServiceMonitor` for them. For tokens/sec, latency and cache usage dashboards see [SETUP-GRAFANA.md](SETUP-GRAFANA.md#sonar--llm-backend-).
It also logs one line per request, with decode and prefill speed, which is the number to use when comparing it with another backend:

```
kubectl -n dev-sonar logs -f deploy/sonar | grep "Request completed"
```

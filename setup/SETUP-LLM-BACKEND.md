# bookcase-ops LLM backends

Part of the [LLM setup](SETUP-LLM.md).

## The engines

The backend engines run the models: 

* `ollama` ( GGUF )
   * is the easiest one to get going. It can swap models in and out on demand so it doesn't have a model registry in this project's source code
   * developed by Ollama Inc
   * built on llama.cpp, written by ggerganov ( Georgi Gerganov )
* `sonar` ( EXL3 )
   * has the most features: batching, Prometheus, and flags for tool-call and reasoning parsers.
   * developed by dphnAI
   * built on the 'Aphrodite Engine' fork of vLLM, written at UC Berkeley   
   * it needs a docker image to be built ( takes 7+ hours ), and to get MTP working might have to manually patch the downloaded models
   * use llm.py to manage the model registry 
* `tabbyapi` ( EXL3 )
   * is the simplest way to run EXL3. MTP worked first time
   * developed by the-royal-lab
   * built on ExLlamaV3, written by turboderp
   * also has a model registry managed by llm.py ( we can swap models at runtime, but it's a bit fiddly )
* `ninfer` ( custom .ninfer format )
   * runs on one GPU, fastest so far, but uses lower quantisation to fit in the VRAM, so not an apples-to-apples comparison
   * developed by by Neroued
   * also has a model registry managed by llm.py

All four are pinned to `bnenod05`, have no Ingress/TLS and are consumed only by `litellm`. 

`ollama` and `sonar` request both GPUs; `ninfer` requests one, and `tabbyapi` can request 1 or 2 depending on the model. 
They all need the `nvidia-device-plugin` component to access the GPU and `local-path-provisioner` to read/write large models on the locally-attached NVMe drive.

## The models

Models are mostly hosted on huggingface, get an account there as it can speed up downloads. 

Some things affect model speed / quality:

* The model variant
   * After experimenting with a few different models on ollama, I settled on qwen3.8 but might be worth checking again now that I've modified some of the other settings
* Engine
   * Different engines support different model formats, which support different math operations on the model, which can speed them up.
   * **tabbyapi** serves models through ExLlamaV3, the library that defines the EXL3 format, so EXL3 improvements appear here first. 
      * EXL3 is a quantisation format based on QTIP, a research method for compressing weights to a chosen number of bits each, which ExLlamaV3 converts and loads at fractional bit rates ( 5.00 bpw, 4.00 bpw ) with a custom GPU kernel.
   * **sonar** is built on vLLM, so it gets continuous batching, and has added EXL3 loading and other formats. 
     Of the four, it's the only one here with batching, which helps when several agents share the model.
   * **ninfer** is an engine for the RTX 5090 ( with the NVidia Blackwell architecture `sm_120` ), and ninfer-3090 is a port of it to the 3090 ( `sm_86` ).
      * It handles one request stream on one GPU very fast, but only for the specific models it supports. 
      * Its `.ninfer` files are single self-contained model files ( weights, tokenizer and chat template ) in its own 4-5 bit grouped quantisation, which only NInfer reads, so a model has to be converted for it first.
* Model file format
   * How the weights are stored in files, which is separate from how many bits each weight has ( see Quantisation, below ). Each engine may only support a subset of these formats.
   * **safetensors**: the usual way the upstream models are published. 
      * A container that stores tensors safely (unlike older pickle-based `.bin` files, which can run code when loaded) and loads fast (it can be memory-mapped).
      * A 'tensor' is a named array of numbers. Most are the model's weights, usually with 1 or 2 dimensions ( a 1D vector for a norm layer, a 2D matrix for a projection ), although some, like a vision tower's patches or a quantised weight's packed data, have more dimensions. 
      * It holds weights of any precision, so the BF16 original and the EXL3 quants are both safetensors; the format doesn't say how many bits the weights use.
      * A safetensors model is a **folder** of files, not one file: several weight shards (`model-00001-of-00002.safetensors`, ...), an index of which tensor is in which shard, and the config, tokenizer and chat template as separate files. 
        The Swift quant on tabbyapi is 19 files. Engines load the folder, which is why tabbyapi's `folder` is a directory name and the downloads are repo-sized.
      * Read by tabbyapi and sonar ( EXL3 and the original models ).
   * **GGUF**: supposedly the "GPT-Generated Unified Format", but created by Georgi Gerganov (creator of llama.cpp) so that's probably the origin of the 'GG' in 'GGUF'.  
      * It's meant to be a single file. 
      * The spec says the file should need no external files, and it stores the tensors, hyperparameters, tokenizer and chat template together. 
      * The exceptions are sidecar files for some features ( a vision projector `mmproj`, a separate MTP head ), and shards for very large models. 
      * Read by ollama and sonar.
   * **`.ninfer`**: an Nvidia-specific file format, also one file.
      * Only NInfer reads it. 
      * It bundles the weights, configuration, tokenizer, chat template and extras like the vision tower and speculative-decoding heads.  
      * The larger models ( Flash-Next, GLM ) are split across an entry file plus part files that must sit beside it.
* Quantisation
   * Different engines support different model formats, which then support different quantisations. 
   * Model is usually published as `BF16` ( 16-bit weights ), but to cram it onto 2x 3090s we need to reduce the number of bits
      * **BF16** stands for 'Brain Floating Point' (after 'Google Brain' who created the format). 16 bits is split across 8 bits exponent (magnitude), 7 bits fraction/mantissa (precision).
      * **FP16** (floating point) is also 16-bit weights but has lower range: 5 bits exponent (magnitude), 10 bits fraction/mantissa (precision).
   * Fewer bits per weight means less memory to read for every token, so faster decoding, and room for a larger context, at some cost in accuracy.
   * The 27B Qwen3.8 model is about 54GB at BF16 and 17-20GB at 4-5 bits, which is how it can fit on one 24GB card.
   * What you want to do is to minimise **Mean KL**, which is the average KL divergence between the quantised model's predicted next-token probabilities and the unquantised BF16 model's, given the same inputs.
      * 0 means identical, and lower is closer to the original.
      * A lot of the "model cards" on huggingface have charts showing KL on a vertical axis and bits per weight (or memory use) on the horizontal axis.
   * Some standard quantisations, from the least to the most compressed. 
      * Sizes below are the actual GGUF file sizes for Qwen3.8-27B, as listed on [bartowski's Hugging Face repo](https://huggingface.co/bartowski/Qwen3.8-27B-GGUF). 
        Other quantisers' files, such as unsloth's, differ a little in size.
      * **"file bpw"** is bits per weight for the whole file: the file size in bits divided by the number of weights, so it includes everything in the file. Not to be confused with **"bpw"** as it appears in a quant's name or an EXL3 target ( `5.00bpw` ), which is a setting, not a measurement.
         * These are the "File bits/weight" figures on the same page ( its "bpw" column ), which imply about 27.8 billion weights.
         * It's higher than the number in the quant's name for two reasons: the K-quant blocks also store scale values ( llama.cpp's `Q4_K` is 4.5 bpw, not 4 ), and the `_M` and `_S` names are mixtures that give some tensors a higher-precision type ( llama.cpp's notes say `Q4_K_M` uses `Q6_K` for half of the attention-value and feed-forward-down tensors ).
      * **FP16 / BF16** ( 54.7GB, 16 file bpw ): the unquantised model. The reference everything else is judged against.
      * **GGUF** ( ollama, sonar ), llama.cpp's quantisations, stored in a GGUF file. Named like `Q4_K_M`: the number is the rough bits per weight, `K` means the "K-quant" scheme, and `S` / `M` / `L` is small, medium or large mix of which tensors keep more bits. 
        The `IQ` ones are "I-quants", which use a lookup table to get better quality at very low bit rates but decode more slowly on a CPU.
         * `Q8_0`: 29.1GB, 8.38 file bpw. Closest to the original ( mean KL 0.0009 ), and too big for one 3090 with any context.
         * `Q6_K`: 23.9GB, 6.87 file bpw. Mean KL 0.0030.
         * `Q5_K_M`: 20.9GB, 6.03 file bpw. Mean KL 0.0053.
         * `Q4_K_M`: 17.4GB, 5.02 file bpw. Mean KL 0.0139; bartowski's recommended default for most users.
         * `IQ4_XS`: 15.5GB, 4.46 file bpw. Mean KL 0.0188: about 2GB smaller than `Q4_K_M` for a somewhat larger KL.
         * `Q3_K_M`: 13.4GB, 3.86 file bpw. Mean KL 0.0564.
         * `Q2_K`: 10.8GB, 3.12 file bpw. Mean KL 0.161. And `IQ2_XXS`: 8.9GB, 2.56 file bpw, is 0.283. These are noticeably degraded, for models that wouldn't otherwise fit.
         * The KL figures are bartowski's own measurement for these files, on `wiki.test.raw` over 100 chunks, so they're the best like-for-like quality comparison here. They say how close the output probabilities are, not how well the model codes.
      * **EXL3** ( tabbyapi, sonar ), ExLlamaV3's format. Named by bits per weight, to two decimals ( `5.00bpw`, `4.00bpw` ), so any rate in between is possible, not just the ones above.
         * Unlike the GGUF figures, the number in the name is the converter's *target* average ( `-b` ), not measured from the file. 
         * The "embedding table" is still stored as BF16 by default ( 2.54GB in the 5.00bpw repo ), and the "output head" has its own bit width ( `head_bits`, 6 there ), so the file is bigger than the name suggests.
         * I'm using `turboderp/Qwen3.8-27B-exl3` at 5.00bpw ( 19.9GB; sonar and tabbyapi ), and the SC quant of Swift at 4.00bpw ( 16.35GB; tabbyapi ).
         * By size, the 5.00bpw file sits between `Q5_K_S` ( 19.6GB ) and `Q5_K_M` ( 20.9GB ), and the 4.00bpw Swift file is close to `Q4_K_S` ( 16.4GB ). The GGUF sizes are from the list above, and bartowski's table for `Q5_K_S`. That says how big they are, not how good.
      * **ninfer** ( ninfer )
         * The weights use ninfer's own grouped quantisation of 4 to 5 bits ( 64 weights per group, with 6 bits for the output head and 4 for the embedding ), so a model has to be converted for it first.
         * I'm using `Qwen3.8-27B-NInfer-3090`( about 19GB ), which by size is close to a GGUF `Q5_K_S` ( 19.6GB ). It also carries the vision tower and the speculative-decoding heads.
      * **Others you'll see**, which none of these engines here use: GPTQ and AWQ ( 4-bit, for vLLM-style engines ), FP8 and NVFP4 ( hardware floating-point formats; NVFP4 needs a Blackwell GPU, not a 3090 ), and EXL2 ( EXL3's predecessor ).
   * File size is the one number that means the same thing in every format, so compare by that. A quant's name, or its bpw, doesn't compare across formats ( and a GGUF's file bpw isn't the same measure as an EXL3's `5.00bpw` target ), and the KL figures above are only for the GGUF files.
     I haven't measured quality across formats, and sizes aren't quality: the formats spend their bits differently.
* Size of context window
   * Context windows grow as you converse with the LLM or the LLM produces code, the bigger the window the more tools you can use, and the larger the inputs / outputs you can send.
   * An agent that reads code resends its whole conversation each turn: some sample pi sessions had prompts of 20,000 to 75,000 tokens, so a window below about 64k is quickly full. Qwen3.8 supports up to 262,144.
   * It's measured in tokens and can also be quantised: the KV cache that holds the conversation has its own precision ( `fp8` and 8-bit on sonar and tabbyapi, 4-bit on ninfer ), which trades quality for memory, and for decode speed at long context.
   * The window competes with the weights for VRAM. ninfer's log showed it starting at 236,544 tokens, not 262,144, because only part of the card was free.
* MTP
   * Multi-token prediction. The model has a small extra layer that guesses the next few tokens, and the full model checks the guesses in one pass, keeping the ones that match. It's speculative decoding without a second draft model.
   * It roughly doubled decode speed on tabbyapi and sonar ( see [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) ), but only if the quant kept the MTP layer. The gain depends on how often the guesses are right.
   * ninfer has its own variant: DFlash2 ( the default, fastest here ), MTP, and n-gram copying, which drafts text the model has just read.
* MoE model variants
   * Mixture of experts. Only some of the model's weights are active for each token, so a 35B-A3B model has 35B parameters in memory but about 3B doing the work per token, which makes it much faster than a dense 27B. 
   * All the experts still have to be stored, so VRAM use follows the total size. The trade is memory for speed, and usually somewhat lower quality than a dense model of the same total size.
* 'Swift' model variants
   * Swift 1.5 is a community fine-tune of Qwen3.8-27B by UkisAI. Its model cards and write-ups say it reasons with far fewer tokens ( claims up to 58.5% fewer thinking tokens ) and scores better on coding benchmarks. 
   * It's the same size as the base model, so speed gains come from fewer tokens, not faster decoding.
* 'Flash' model variants
   * Qwen3.8-Flash-Next is a 125B mixture of experts with about 6B active, and too big for VRAM, so it needs its experts offloaded to system RAM. It's a model name, not a technique.
   * Unrelated to DFlash2, which is the speculative-decoding method in ninfer.
* EXL-SC model variants
   * "Self-calibrated" EXL3 quants made by community builders rather than turboderp. The quantiser is calibrated partly on the model's own output and agentic traces, and bits are allocated tensor by tensor. 
   * Reportedly has a 16% lower KL divergence than a turboderp build at the same bit rate, on one model. 
   * In a name like `SC_4.00bpw_H5_V6`, `4.00bpw` is the average bits per weight, `H5` is a 5-bit output head (`lm_head`), and `V6` is a 6-bit vision tower. 
   * I'm running `Jon-Nielsen/Swift-Qwen3.8-27B-exl3-SC_4.00bpw_H5_V6` on tabbyapi, a 4.00 bpw quant of the Swift fine-tune, 16.3GB, with a 4-bit MTP head. See the `swift-27b-sc-4bpw-*` entries in the tabbyapi model registry. 
     The card says its author tested it on one 3090 with a 262,144 context and a Q6 KV cache, and ran no quality or speed measurements. The bit plan was copied from the base model's published quant, not re-measured for the fine-tune, 
     so quality is untested. The card says it was verified on ExLlamaV3 1.5.0, and some other SC builds need a newer ExLlamaV3 or a fork.


## Switching between engines

`ollama`, `sonar` and `tabbyapi` usually request both GPUs, so only one can run at a time. 
`ninfer` needs only one GPU, so it could in principle run beside another one-GPU pod, but for now `engine set` still scales everything else down for it.

Switch with `bin/llm.py`. It needs `pip install click pyyaml hvac requests`, `kubectl`, and `. vault-login.sh` first for the verbs that call APIs: secrets are read from vault with
[hvac](https://python-hvac.org) ( `VAULT_ADDR` and `VAULT_TOKEN`, plus `VAULT_CACERT` if vault uses the private CA ), litellm is called directly at `LITELLM_URL` ( default `https://litellm.dev.randomnoun`;
`requests` doesn't use the OS certificate store, so set `LITELLM_CACERT` to the CA certificate ), and the engines, which have no ingress, are reached through a temporary `kubectl port-forward`:

```
. vault-login.sh                           # set env vars to access secrets in vault
python3 ../bin/llm.py engine list              # replicas, pod phase, configured / serving model, litellm models
python3 ../bin/llm.py engine set tabbyapi      # scale the others to 0, wait for their pods to go, scale tabbyapi to 1, wait for the rollout
```

That's the same as scaling the one that's running down to 0 *first* ( a GPU stays held until the old pod has finished terminating ), then the one you want up:

```
kubectl -n dev-ollama   scale deploy/ollama   --replicas=0
kubectl -n dev-tabbyapi scale deploy/tabbyapi --replicas=1
kubectl -n dev-tabbyapi rollout status deploy/tabbyapi --timeout=15m
```

( swap the namespace and name for `dev-sonar` / `sonar` or `dev-ollama` / `ollama` ). Check what's running with `kubectl get deploy -A | grep -E 'ollama|sonar|tabbyapi'`.

### Switching models

`sonar` and `tabbyapi` each have a model registry, in `ansible/vars/<engine>/bnekub03-models.vars.yml`: a `<engine>_models` map, and `<engine>_active_model` naming the one that's deployed.
Each entry holds the engine settings for that model ( sonar: model, revision, context length, flags; tabbyapi: folder, context length, cache, tensor-parallel, MTP ) and a `litellm:` block with its litellm
name and params. The roles copy the active entry onto their usual variables, so the choice survives pod restarts and re-running the role.

```
python3 ../bin/llm.py model list                     # the current engine's registry ( tabbyapi: marks what's on the PVC ); ollama: pulled models
python3 ../bin/llm.py model download NAME            # tabbyapi: fetch the entry's download: repo onto the PVC
python3 ../bin/llm.py model load NAME                # edit <engine>_active_model, re-run the role ( ansible-playbook ), then sync litellm
python3 ../bin/llm.py model sync --dry-run           # show what would change in litellm
python3 ../bin/llm.py model unload                   # tabbyapi / ollama only; not persistent
```

"Current engine" is whichever of ollama, sonar and tabbyapi is scaled up; pass `--engine` to pick one. `model load` leaves a one-line change in the registry file for you to commit. Run it from WSL,
since it runs `ansible-playbook` ( or use `--no-apply` and run the printed command yourself ).

`model sync` makes litellm match the active models: it creates or updates one litellm model per engine and deletes the ones it created for entries that are no longer active ( sonar serves every model under
the same name, so a stale entry would answer with the wrong model ). It marks its models with a `bookcase-ops` entry in `model_info` ( `{"engine": ..., "ansible-model": ...}`, the engine and the registry entry it was built from ) and leaves everything else alone, so models you added in the litellm UI are untouched. 
Ollama's models aren't synced; add them in the UI. After a sync that changes `num_ctx`, run `pi-sync-models` and restart opencode as before.

Re-running a role does **not** reset the replica count: it reads the current value from the cluster and keeps it, so `initial_replicas` only applies
the first time the deployment is created. To change a role's behaviour permanently, scale it with `kubectl` as above.

Things to note:

* A scaled-down engine keeps its PVC and models; only the pod goes away.
* litellm returns an error for a model whose engine is scaled to 0. It doesn't try to start it.
* Loading a big model takes minutes after scaling up ( tabbyapi/sonar load the weights into VRAM; ollama loads on first request ).
* The `sonar` role does nothing until `sonar_model` is set, whatever its replica count.

## ollama 

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

Everything specific to the model is in the registry, `vars/sonar/bnekub03-models.vars.yml` ( with comments ); the steps below describe the keys of an entry ( `model`, `revision`, `max_model_len`, `extra_args` ),
not the flat `sonar_*` variables they're copied onto. To change model, add an entry and `python bin/llm.py model load NAME` ( see [Switching models](#switching-models) ). 
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

### Configure the model in litellm

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

Everything specific to the model is in the registry, `vars/tabbyapi/bnekub03-models.vars.yml` ( with comments ); the steps below describe the keys of an entry ( `folder`, `max_seq_len`, `cache_mode`,
`tensor_parallel`, `draft_*`, and optionally `gpu_count`, `vision` and `vision_offload` ), not the flat `tabbyapi_*` variables they're copied onto.
`gpu_count: 1` asks kubernetes for one GPU, so tabbyapi sees a single device and doesn't split the model, and the other 3090 stays free ( ninfer can then use it, once its own pod is up ).
`vision` loads the model's vision tower, and `vision_offload` keeps its weights in system RAM instead of VRAM. To change model, add an entry, `python bin/llm.py model download NAME` and `model load NAME` 
( see [Switching models](#switching-models) ), which replaces steps 3 and 4 below for later models. Only the keys and the volume size are still in `vars/tabbyapi/bnekub03.vars.yml`.

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

### Configure the model in litellm

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

### Metrics

tabbyapi has no Prometheus support, so there's nothing to scrape; see [SETUP-GRAFANA.md](SETUP-GRAFANA.md#tabbyapi--no-metrics-) for what to use instead. 
It logs a line per request with the tokens generated and tokens/sec, which is the number to compare with sonar's `Request completed` line:

```
kubectl -n dev-tabbyapi logs -f deploy/tabbyapi | grep -i "tokens generated"
```

The wording of that line is from reading tabbyapi's source, not from seeing it run, so adjust the `grep` if it doesn't match.

## ninfer

[NInfer](https://github.com/Neroued/ninfer) is a single-GPU C++/CUDA engine that runs a few specific models from its own `.ninfer` files, and `ninfer-serve` is its server. Upstream targets Blackwell
GPUs; the RTX 3090 port is [ashalliants/ninfer-3090](https://github.com/ashalliants/ninfer-3090), which is what this uses. It serves OpenAI and Anthropic APIs, one model per process.

* **One GPU**, unlike the others here ( `ninfer_gpu_count: 1` ). It is fast because it doesn't split the model across the cards.
* **It starts scaled to 0.** See [Switching between engines](#switching-between-engines).
* **No published image.** The role uses one you build from [docker/ninfer-3090](../docker/ninfer-3090/README.md), which wraps the port's prebuilt Linux release.

### Install it

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

The model and its settings are in the registry, `vars/ninfer/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](#switching-models) ). Each entry names a `profile`
( `qwen38-27b` or `qwen36-35b-a3b` ), which is the model `run.sh` serves and the artifact the init container downloads, a `spec` ( `dflash2` is the fastest at one stream, `mtp` gives two lanes ), and
optionally `context`, `concurrency`, `draft_tokens`, `kv_dtype`, `thinking_budget` and `env` for any other `NINFER_*` variable. They become the environment variables that `run.sh` reads, 
which are listed at the top of `run.sh` in the release.

* **Context length.** If the entry leaves `context` unset, `run.sh` starts at the largest context that fits the free GPU memory, up to 262144, and logs `context lowered to ...` when it has to cut it. 
  Setting `context` turns that off and the server fails if it doesn't fit. Keep the litellm `num_ctx` equal to the context the server logs at startup.
* **KV cache and memory.** The default profile uses a 4-bit-keys KV cache ( `rk4v4` ) and pins 8 GiB of host RAM for its context cache, so the pod asks for 24Gi ( limit 40Gi ). A failed pin retries with half.
* **Thinking.** `thinking_budget` is `--default-thinking-budget`, a server-wide cap. The litellm `reasoning_effort` values the other engines use aren't known to work here; ninfer's docs say `"none"` turns thinking off for Qwen.

### Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-ninfer.svc.cluster.local:8080/v1`, called `ninfer` upstream ( `ninfer_served_model_name` ), 
with the vault `api_key`. By hand, it is the same as for the others: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `ninfer`, Public Model Name `ninfer-qwen3.8:27b-dflash2`, that API base and key, 
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

### Metrics

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

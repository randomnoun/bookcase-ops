# bookcase-ops LLM backends

Part of the [LLM setup](SETUP-LLM.md).

## The engines

The backend engines run the models: 

* `ollama` ( GGUF )
   * is the easiest one to get going. It can swap models in and out on demand so it doesn't have a model registry in this project's source code
   * developed by Ollama Inc
   * built on llama.cpp, written by ggerganov ( Georgi Gerganov )
   * setup: [SETUP-LLM-BE-OLLAMA.md](SETUP-LLM-BE-OLLAMA.md)
* `sonar` ( EXL3 )
   * has the most features: batching, Prometheus, and flags for tool-call and reasoning parsers.
   * developed by dphnAI
   * built on the 'Aphrodite Engine' fork of vLLM, written at UC Berkeley   
   * it needs a docker image to be built ( takes 7+ hours ), and to get MTP working might have to manually patch the downloaded models
   * use llm.py to manage the model registry 
   * setup: [SETUP-LLM-BE-SONAR.md](SETUP-LLM-BE-SONAR.md)
* `tabbyapi` ( EXL3 )
   * is the simplest way to run EXL3. MTP worked first time
   * developed by the-royal-lab
   * built on ExLlamaV3, written by turboderp
   * also has a model registry managed by llm.py ( we can swap models at runtime, but it's a bit fiddly )
   * setup: [SETUP-LLM-BE-TABBYAPI.md](SETUP-LLM-BE-TABBYAPI.md)
* `ninfer` ( custom .ninfer format )
   * runs on one GPU, fastest so far, but uses lower quantisation to fit in the VRAM, so not an apples-to-apples comparison
   * developed by by Neroued
   * also has a model registry managed by llm.py
   * setup: [SETUP-LLM-BE-NINFER.md](SETUP-LLM-BE-NINFER.md)

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
      * **Others you'll see**, which none of these engines here use: 
         * GPTQ and AWQ ( 4-bit, for vLLM-style engines )
         * FP8 and NVFP4 ( hardware floating-point formats; NVFP4 needs a Blackwell GPU, not a 3090 )
         * EXL2 ( EXL3's predecessor )
   * File size is the one number that means the same thing in every format, so compare by that. A quant's name, or its bpw, doesn't compare across formats ( and a GGUF's file bpw isn't the same measure as an EXL3's `5.00bpw` target ), and the KL figures above are only for the GGUF files.
   * Quality can differ across formats, and sizes aren't quality: the formats spend their bits differently.
* Size of context window
   * Context windows grow as you converse with the LLM or the LLM produces code, the bigger the window the more tools you can use, and the larger the inputs / outputs you can send.
   * An agent that reads code resends its whole conversation each turn: some sample pi sessions had prompts of 20,000 to 75,000 tokens, so a window below about 64k is quickly full. Qwen3.8 supports up to 262,144.
   * The working state for the tokens in the context window is stored in the "KV cache" (key/value cache); the memory for the cache is reserved up front. It is quantised separately to the model
      * This trades quality for memory, and for decode speed at long context.
      * sonar uses `fp8` which uses 8 bits for the key (K) and 8 bits for the value (V).
        * memory use is (K size + V size) * (# full-attention layers) * (head size) * (# KV heads) / 8
        * which for qwen3.8 is (8 bits + 8 bits) * (16) * (256) * (4) / 8 = 32KB per token, which = 8GB for the full 262,144-token window.
      * tabbyapi uses k_bits,v_bits pairs 
         * 8,8 for the 5.00bpw run = 32KB per token = 8GB window
         * 6,6 for the one-GPU Swift run = 24KB per token = 6GB window
         * and 8,8 for two GPUs run = 32KB per token = 8GB window
      * ninfer's default is rk4v4, which is 4-bit keys and 4-bit values 
         * is 16KB per token = 4GB for the window; slightly more in practice
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
     The card says its author tested it on one 3090 with a 262,144 context and a Q6 KV cache, and ran no quality or speed measurements.


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


## Engine setup

Each engine has its own page, covering deployment, model installation, the litellm configuration and metrics:

* [SETUP-LLM-BE-OLLAMA.md](SETUP-LLM-BE-OLLAMA.md) - `ollama`
* [SETUP-LLM-BE-SONAR.md](SETUP-LLM-BE-SONAR.md) - `sonar`, including building its image
* [SETUP-LLM-BE-TABBYAPI.md](SETUP-LLM-BE-TABBYAPI.md) - `tabbyapi`
* [SETUP-LLM-BE-NINFER.md](SETUP-LLM-BE-NINFER.md) - `ninfer`

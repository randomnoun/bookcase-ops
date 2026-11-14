# bookcase-ops LLM backend: sglang

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[SGLang](https://github.com/sgl-project/sglang) is a Python serving engine from LMSYS, with continuous batching, a radix-tree prefix cache and built-in tool-call and reasoning parsers. It serves OpenAI-compatible
APIs, one model per process here.

* **Both GPUs**, with tensor parallelism ( `--tp-size 2` ).
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `lmsysorg/sglang` from Docker Hub. Nothing to build.
* **Reads AWQ and GPTQ, not EXL3.** The registry's entry is a 4-bit GPTQ quant of Qwen3.8-27B. sglang's own Qwen3.8 recipe is for FP8 and NVFP4 checkpoints on Hopper and Blackwell cards; the 3090s ( `sm_86` ) have neither.
* **Run on the 3090s.** It serves Qwen3.8-27B at about 95 tok/sec in an agent session with MTP working ( [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 13 ), but only after a fix to the checkpoint's quantization config, see below. The first start took about 15 minutes ( 10 of them loading the weights from a cold disk ); restarts with the weights cached took about 2 minutes.

## Install it

1. Check the image. The role default is `lmsysorg/sglang:latest`, which moves, so `vars/sglang/bnekub03.vars.yml` pins `sglang_image` to `lmsysorg/sglang:v0.5.23` by digest: sglang 0.5.23, the CUDA 13.0 build, which is what everything on this page and in
   [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 13 was run on ( on 2026-11-14 Docker Hub's `v0.5.23`, `v0.5.23-cu130`, `latest` and `latest-cu130` all pointed at it ). To move to a newer one, pull it, check that the server still starts and that MTP still accepts drafts, and replace the pin.
   The image is 16 GB and took about 7 minutes to pull. Check the node can pull it: `sudo ctr -n k8s.io images pull lmsysorg/sglang:v0.5.23`.

2. Put the HuggingFace token in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). It's read as `sglang_hf_token`; the playbook fails if the secret is missing, even though a public repo doesn't need the token.

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=sglang k8s_llm_bnekub03.yml
   python bin/llm.py engine set sglang
   kubectl -n dev-sglang logs -f deploy/sglang
   ```

   The first start downloads the model onto the PVC ( `HF_HOME` is under `/root/.cache` ). The startup probe allows 30 minutes. The first start took about 15 minutes, most of it loading the weights ( about 10 minutes ) and capturing CUDA graphs ( about 4 minutes ); later restarts, with the weights in the page cache, were up in about 2.

The model and its settings are in the registry, `vars/sglang/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `model`: a HuggingFace repo id, or a path under `/root/.cache` ( `--model-path` )
* `revision`: `--revision`, a branch, tag or commit
* `quantization`: `--quantization`; leave it out and sglang reads the method from the checkpoint
* `context_length`: `--context-length`; left out, the model's own maximum
* `thinking_budget`: a server-wide cap on thinking tokens. The role starts the server with `--enable-strict-thinking` and sets `SGLANG_MAX_THINK_TOKENS`; sglang then forces `</think>` at the cap. It needs the reasoning parser
* `extra_args`: any other `sglang.launch_server` flag. The registry's entry sets `--kv-cache-dtype=fp8_e4m3`, `--tool-call-parser=qwen3_coder`, `--reasoning-parser=qwen3` and four `--speculative-*` flags for MTP.

`--tp-size` and `--mem-fraction-static` are `sglang_tensor_parallel_size` and `sglang_mem_fraction_static` in the role defaults. The server is started with `--enable-metrics`, and the pod gets a 16Gi `/dev/shm`, 
which sglang's docs say it needs on docker and kubernetes.

## What was found running it ( 2026-11-14 )

* **MTP needs a patched quantization config.** palmfuture's exclusion rule `-:.*\.mtp\..*` misses sglang's `mtp.layers.N` names, so the draft head was built as GPTQ, seven of its weights couldn't load, and it accepted nothing ( ~40 tok/sec ). The registry's `--json-model-override-args` adds the rule
  `-:(?:.*\.)?mtp\..*`; it replaces the whole `quantization_config`, so regenerate it from the checkpoint's `config.json` if palmfuture re-quantizes. With it, accepted length averages about 2.9 of 4 and decode about 95 tok/sec in an agent session ( [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 13 ).
  The draft head's `Load weight end` line still says `quant=gptq` afterwards, so check the "not found in params_dict" warnings and `accept len` instead.
* **The quant.** `palmfuture/Qwen3.8-27B-GPTQ-Int4` loads and serves; `bernhardbrieger/Qwen3.8-27B-GPTQ-Int4` doesn't load on sglang 0.5.23 ( it doesn't exclude `linear_attn.in_proj_ba` and `gptq_marlin_repack` fails ). I haven't compared palmfuture's output quality with the EXL3 and `.ninfer` quants.
* **Probe timeout.** `/health` takes about 1.0 s to answer, which is the probe default timeout, so the role sets `timeoutSeconds: 5`. Without it the pod stayed `0/1` after the server was up ( its endpoint showed `ready=false` ). litellm logged 15 "Connection error" 500s from pi during that time while sglang's own log showed none, so the unready endpoint is the likely cause, but I didn't confirm it.
* **Parser names.** `qwen3_coder` and `qwen3` are accepted by the image ( the server started with them ), but I haven't checked end to end that tool calls and the reasoning split come out right in pi.
* **Memory.** About 1.65 GB per GPU is free after startup, the KV pool holds 268,377 tokens at fp8 ( 141,683 at bf16, which didn't change MTP ), and `max_running_requests` is about 8, capped by the Mamba state cache.
* **Not confirmed:** whether the 8192 thinking cap is enforced ( the server starts and speculates with `--enable-strict-thinking`, but I haven't seen a request run past the cap or be cut at it ); the quality of the output; and what `/health` returns while the model is still loading.
* **No authentication.** No API key is set, so the server is open to the network on its LoadBalancer service, as sonar's is.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-sglang.svc.cluster.local:30000/v1`, called `local-sglang` upstream
( `sglang_served_model_name` ), with a placeholder `api_key`. By hand: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `local-sglang`, Public Model Name `sglang-qwen3.8:27b-gptq-int4`, that API base,
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

## Metrics

sglang has a Prometheus `/metrics` endpoint, enabled by `--enable-metrics` ( the role creates a `ServiceMonitor` for it ). `llm.py` reads it:

```
python3 ../bin/llm.py metric sglang     # prompt and generated tokens, mean latencies, the throughput gauge, cache hit rate
```

It goes in through a `kubectl port-forward`. The token counts and latencies are totals since the pod started; `gen_throughput` is the speed right now, so read it while a request is running. 
sglang's documented metrics don't include speculative-decoding acceptance, so `metric sglang` prints any `spec_*` figures the server emits and nothing else. 
sglang logs nothing per request, so `python3 ../bin/llm.py speed sglang --since 2h --min-out 100` rebuilds one row per generation from its once-a-second `Decode batch` lines ( prompt size, share cached, tokens out, `accept len` and `accept rate` for MTP, decode tok/s ).
It's approximate: only time with one request running is measured, and there is no time to first token or prefill speed. For end-to-end figures use `python3 ../bin/llm.py speed langfuse ... --model sglang`.

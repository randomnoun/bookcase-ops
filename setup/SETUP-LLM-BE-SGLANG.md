# bookcase-ops LLM backend: sglang

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[SGLang](https://github.com/sgl-project/sglang) is a Python serving engine from LMSYS, with continuous batching, a radix-tree prefix cache and built-in tool-call and reasoning parsers. It serves OpenAI-compatible
APIs, one model per process here.

* **Both GPUs**, with tensor parallelism ( `--tp-size 2` ).
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `lmsysorg/sglang` from Docker Hub. Nothing to build.
* **Reads AWQ and GPTQ, not EXL3.** The registry's entry is a 4-bit GPTQ quant of Qwen3.8-27B. sglang's own Qwen3.8 recipe is for FP8 and NVFP4 checkpoints on Hopper and Blackwell cards; the 3090s ( `sm_86` ) have neither.
* **Not tested here yet.** Nothing in this setup has been run on the 3090s. The flags come from sglang's documentation; where I couldn't confirm that something works it says so in the registry's comments.

## Install it

1. Check the image. The role defaults to `lmsysorg/sglang:latest`, which moves; pin a version tag once one works. Check the node can pull it: `sudo ctr -n k8s.io images pull lmsysorg/sglang:latest` ( the image is large ).

2. Put the HuggingFace token in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). It's read as `sglang_hf_token`; the playbook fails if the secret is missing, even though a public repo doesn't need the token.

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=sglang k8s_llm_bnekub03.yml
   python bin/llm.py engine set sglang
   kubectl -n dev-sglang logs -f deploy/sglang
   ```

   The first start downloads the model onto the PVC ( `HF_HOME` is under `/root/.cache` ). The startup probe allows 30 minutes.

The model and its settings are in the registry, `vars/sglang/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `model`: a HuggingFace repo id, or a path under `/root/.cache` ( `--model-path` )
* `revision`: `--revision`, a branch, tag or commit
* `quantization`: `--quantization`; leave it out and sglang reads the method from the checkpoint
* `context_length`: `--context-length`; left out, the model's own maximum
* `extra_args`: any other `sglang.launch_server` flag. The registry's entry sets `--kv-cache-dtype=fp8_e4m3`, `--tool-call-parser=qwen3_coder`, `--reasoning-parser=qwen3` and four `--speculative-*` flags for MTP.

`--tp-size` and `--mem-fraction-static` are `sglang_tensor_parallel_size` and `sglang_mem_fraction_static` in the role defaults. The server is started with `--enable-metrics`, and the pod gets a 16Gi `/dev/shm`, 
which sglang's docs say it needs on docker and kubernetes.

## What I haven't confirmed

* **MTP on the 3090s.** The four `--speculative-*` flags are sglang's recipe for the in-checkpoint MTP head, written for Blackwell cards. If the server fails at startup, try adding `--attention-backend=triton`, or remove them.
* **The quant.** `palmfuture/Qwen3.8-27B-GPTQ-Int4` was chosen because its description says it uses a group size of 32 for sglang compatibility and ships MTP weights. I haven't run it, or compared its quality with the EXL3 and `.ninfer` quants.
* **The parser names.** `qwen3_coder` and `qwen3` are the names sglang lists. Check them against `python3 -m sglang.launch_server --help` in the image you pin.
* **Thinking budget.** No cap is set. sglang has a thinking-budget feature, but I haven't worked out the flag, so the model can spend its whole output budget thinking.
* **`/health`.** As for mistralrs, I haven't checked what it returns while the model is loading.
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
There is no `speed sglang`; compare engines on the speed seen by litellm / langfuse ( `python3 ../bin/llm.py speed langfuse ... --model sglang` ).

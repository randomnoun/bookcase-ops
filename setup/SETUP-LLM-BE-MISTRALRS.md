# bookcase-ops LLM backend: mistralrs

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[mistral.rs](https://github.com/EricLBuehler/mistral.rs) is a Rust inference engine, developed by Eric Buehler, built on the Candle ML framework. It serves OpenAI and Anthropic APIs, one model per process here.

* **Both GPUs**, like sonar and tabbyapi ( `mistralrs_gpu_count: 2` ). It maps the layers across them automatically.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `ghcr.io/ericlbuehler/mistral.rs`, with a tag in the form `cuda<CUDA version>-sm<compute capability>-<release>`; the 3090 is `sm86`. Nothing to build.
* **Reads the original weights.** It loads safetensors and GGUF and can quantize as it loads ( `--quant`, in-situ quantization ), or load a prebuilt UQFF file. It doesn't read EXL3 or `.ninfer`.
* **Not tested here yet.** Unlike the other engines, nothing in this setup has been run on the 3090s. The flags come from mistral.rs's documentation; where I couldn't confirm that something works it says so in the registry's comments.

## Install it

1. Check the image tag. The role defaults to `cuda128-sm86-latest`; pick the CUDA version your node's driver supports ( `nvidia-smi` shows it ) and pin a release once one works.
   Check the node can pull it: `sudo ctr -n k8s.io images pull ghcr.io/ericlbuehler/mistral.rs:cuda128-sm86-latest`

2. Put the HuggingFace token in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). It's read as `mistralrs_hf_token`; the playbook fails if the secret is missing, even though a public repo doesn't need the token.

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=mistralrs k8s_llm_bnekub03.yml
   python bin/llm.py engine set mistralrs
   kubectl -n dev-mistralrs logs -f deploy/mistralrs
   ```

   The first start downloads the model from HuggingFace onto the PVC ( `HF_HOME` is `/data` ) and, if the entry sets `quant`, quantizes it as it loads, so it's slow. 
   The startup probe allows 30 minutes.

The model and its settings are in the registry, `vars/mistralrs/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `model`: a HuggingFace repo id ( `--model-id` )
* `quant`: `--quant`, a bit width ( `4` ), a named type ( `q4k` ), or `auto`. It uses a prebuilt UQFF file when the repo has one, and quantizes at load time otherwise.
* `max_model_len`: `--max-model-len`, the longest single request. Left out, mistralrs uses whatever the model and the KV cache allow.
* `extra_args`: any other `mistralrs serve` flag. The registry's entry uses `--pa-cache-type=f8e4m3` ( an 8-bit float KV cache ) and `--mtp` ( multi-token prediction, using the draft head in the checkpoint ).

`--gpu-memory-utilization` is `mistralrs_gpu_memory_utilization` in the role defaults.

## What I haven't confirmed

* **MTP on Qwen3.8.** `--mtp` is a documented flag, but I haven't checked that this release supports MTP for this architecture. If the server fails at startup with an MTP error, remove it from `extra_args`.
* **The model name.** The litellm entry's `upstream` is the repo id. If `engine list` shows mistralrs serving it under a different name, change `upstream`.
* **Tool calls and thinking.** mistralrs has no flags for a tool-call or reasoning parser, and no thinking budget. If tool calls come back as plain text in pi, or the `<think>` text isn't separated, that is the cause.
* **`/health`.** The probes use `/health`; I haven't checked what it returns while the model is still loading. If the pod goes Ready too early, or never, swap them for `tcpSocket` checks as tabbyapi does.
* **No authentication.** I didn't find an API key option, so the server is open to the network on its LoadBalancer service, as sonar's is.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-mistralrs.svc.cluster.local:1234/v1`, with a placeholder `api_key`
( litellm wants one ). By hand: Provider **OpenAI-Compatible Endpoints**, Custom Model Name the repo id, Public Model Name `mistralrs-qwen3.8:27b-q4k`, that API base, then `"num_ctx"` in the LiteLLM Params.
Run `pi-sync-models` afterwards and restart opencode.

## Metrics

mistralrs has a Prometheus `/metrics` endpoint ( the role creates a `ServiceMonitor` for it ). `llm.py` reads it, from the counters since the pod started:

```
python3 ../bin/llm.py metric mistralrs     # prompt and generated tokens, prefix-cache hit rate, MTP draft acceptance
```

It goes in through a `kubectl port-forward`. There is no `speed mistralrs`: mistralrs doesn't publish a speed per request, so compare engines on the speed seen by litellm / langfuse
( `python3 ../bin/llm.py speed langfuse ... --model mistralrs` ).

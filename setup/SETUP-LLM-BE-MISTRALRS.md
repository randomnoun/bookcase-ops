# bookcase-ops LLM backend: mistralrs

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[mistral.rs](https://github.com/EricLBuehler/mistral.rs) is a Rust inference engine, developed by Eric Buehler, built on the Candle ML framework. It serves OpenAI and Anthropic APIs, one model per process here.

* **Both GPUs**, like sonar and tabbyapi ( `mistralrs_gpu_count: 2` ). It chose tensor parallelism over NCCL by itself, with about 15 GiB on each card.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `ghcr.io/ericlbuehler/mistral.rs`, with a tag in the form `cuda<CUDA version>-sm<compute capability>-<release>`; the 3090 is `sm86`. Nothing to build.
* **Reads the original weights.** It loads safetensors and GGUF and can quantize as it loads ( `--quant`, in-situ quantization ), or load a prebuilt UQFF file. It doesn't read EXL3 or `.ninfer`.
* **Run on the 3090s, and slow.** It serves Qwen3.8-27B with MTP working, at a median of about 37 tok/sec over 7 requests of 5.5k tokens or less, and 18 tok/sec on one long reply. That is well under sglang's ~95 ( [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 14 ), and with MTP on its prefix cache never hits.

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

   The first start downloads the model from HuggingFace onto the PVC and, if the entry sets `quant`, quantizes it as it loads. It ignores `HF_HOME` and writes to `/root/.cache` on the container's own disk, which a restart throws away,
   so the role passes `--hf-cache=/data/hub`. The BF16 weights are about 52 GB, which took about 55 minutes at 14-18 MiB/s, and the startup probe allows 2 hours.
   **Every start re-quantizes** ( about 2.5 minutes with the weights on disk ) because nothing keeps the result; see the UQFF note below.

The model and its settings are in the registry, `vars/mistralrs/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `model`: a HuggingFace repo id ( `--model-id` )
* `quant`: `--quant`, a bit width ( `4` ), a named type ( `q4k` ), or `auto`. It uses a prebuilt UQFF file when the repo has one, and quantizes at load time otherwise.
* `max_model_len`: `--max-model-len`, the longest single request. It is 65,536 here, because 262,144 didn't start ( below ), and the KV cache holds only 63,840 tokens.
* `extra_args`: any other `mistralrs serve` flag. The registry's entry uses `--pa-cache-type=f8e4m3` ( an 8-bit float KV cache ), `--max-seqs=2` and `--mtp` ( multi-token prediction, using the draft head in the checkpoint ).

`--gpu-memory-utilization` is `mistralrs_gpu_memory_utilization` in the role defaults.

## What was found running it ( 2026-11-14 )

* **`--max-seqs` has to be small.** At its default of 32 the start failed with `runtime reservations need 36131 MiB ... only 4904 MiB remains usable`, and cutting the context to a quarter changed that by under 1%. `--max-seqs=2` brought the recurrent-state reservation from `capacity=33` to `9` and it started.
  I take it the Gated DeltaNet layers reserve state per concurrent request, as sglang's Mamba cache does, but I haven't confirmed that from the source. It means at most 2 requests at once.
* **The KV cache is small.** 1,062 MiB per GPU, 63,840 tokens at fp8, with about 9 GiB still free on each card. Raising `--gpu-memory-utilization` to 0.95 should give about 136k tokens by my estimate; not tried.
* **MTP works.** `Speculative decoding enabled: MTP assistant built-in`, accepting 2.0-2.75 tokens per step ( 49-79% ). But it recomputes cached prompt prefixes, so the prefix cache hit rate is always 0%.
* **It hung once.** After a request was cancelled mid-generation the next one was accepted and never ran, with one GPU at 100% and one at 0%, while `/health` kept answering. A restart cleared it and the next request completed. I didn't see it happen without a cancel, so I don't know the cause.
* **`/health`** answers in about 1 ms with a 200 once the server is up, so the 5 second probe timeout isn't needed, but it also doesn't notice a hung engine. I haven't seen what it returns while the model is loading; the pod stayed `0/1` until the server was listening.
* **The model name.** `/v1/models` lists `default` and `Qwen/Qwen3.8-27B` ( `status: loaded` ), which matches the litellm `upstream`.
* **Not confirmed:** tool calls and thinking through litellm and pi ( there is no tool-call or reasoning parser flag and no thinking budget ), output quality, and any session longer than 9 requests.
* **No authentication.** I didn't find an API key option, so the server is open to the network on its LoadBalancer service, as sonar's is.
* **UQFF.** `mistralrs quantize -m Qwen/Qwen3.8-27B --isq q4k -o <dir>` saves the quantized model, and `--from-uqff` serves it without re-quantizing. I haven't tried it.

## Startup times

Measured on `bnenod05` with `qwen3.8-27b-q4k`, on 2026-11-14, from the pod log and the pull I ran by hand. `( est. )` is arithmetic, not a measurement.
**\*** marks a figure measured before the ethernet cable on `bnellm01` was replaced that night, so it should be measured again.
The layout is the same on every engine's page; the summary is in [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md#startup-times).

**Sizes**

| What | Size |
|---|---|
| Image `ghcr.io/ericlbuehler/mistral.rs:cuda128-sm86-latest` | 4.04 GB on the node |
| Model, `Qwen/Qwen3.8-27B` ( BF16 safetensors ) | 55.6 GB in 32 files, 52 GiB; the quantized result is never saved |

**One-time, per node** ( repeats when the image tag changes )

| Phase | Size | Time | Rate | Scales with |
|---|---|---|---|---|
| Pull the image | 4.04 GB | **5 min 40 s\*** | 11.9 MB/s\* ( est. from the two figures ) | image size |

**One-time, per model**

| Entry | Download | Download time | Rate | Pod created to serving |
|---|---|---|---|---|
| `qwen3.8-27b-q4k` | 55.6 GB ( 52 GiB ) | **about 55 min\*** | **14-18 MiB/s\*** | about 58 min\* ( est.: the download plus the every-start time below ); the startup probe allows 2 hours |

**Every start** ( weights on the PVC; **the quantization is in here, because it happens every time** )

| Phase | Size processed | Time | Rate | Scales with | Why every start |
|---|---|---|---|---|---|
| Load the weights and quantize to q4k | 52 GiB read, about 15 GiB on each GPU afterwards | about 2 min 23 s | - | model size ( both the read and the quantization ) | nothing keeps the quantized model; a UQFF file would, and I haven't tried it |
| Plan memory and capture graphs | - | about 39 s | - | not measured | each time |
| **Pod created to Ready** | | **3 min 06 s** ( 186 s ) | | | |

`python bin/llm.py engine list --times` prints each engine's pod age and created-to-Ready time ( while that pod still exists ).

**Limits:** the startup probe allows 2 hours ( 360 × 20 s ) and `engine set` waits 15 minutes unless given `--timeout`.

**Normal in the log:** the log has ANSI colour codes, and `/health` isn't answering until the server is listening, so the pod stays `0/1` through the whole load.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-mistralrs.svc.cluster.local:1234/v1`, with a placeholder `api_key`
( litellm wants one ). By hand: Provider **OpenAI-Compatible Endpoints**, Custom Model Name the repo id, Public Model Name `mistralrs-qwen3.8:27b-q4k`, that API base, then `"num_ctx"` in the LiteLLM Params.
Run `pi-sync-models` afterwards and restart opencode.

## Metrics

mistralrs has a Prometheus `/metrics` endpoint ( the role creates a `ServiceMonitor` for it ). `llm.py` reads it, from the counters since the pod started:

```
python3 ../bin/llm.py metric mistralrs     # prompt and generated tokens, prefix-cache hit rate, MTP draft acceptance
```

It goes in through a `kubectl port-forward`. For per-request speeds, mistralrs logs a `request completed` line with the prompt and completion tokens and its own prefill and decode speeds, and `llm.py` reads it:

```
python3 ../bin/llm.py speed mistralrs --since 2h --min-out 100     # per request: prompt, tokens out, TTFT, prefill and decode tok/s, MTP acceptance
```

TTFT there is estimated as prompt tokens over prefill speed, and the acceptance columns are means over the engine's 5-second log lines during the request, so a request shorter than 5 seconds shows a dash. Requests the client cancelled have no token counts and are skipped.
mistralrs colours its log with ANSI escape codes, which the parser strips. For end-to-end figures use `python3 ../bin/llm.py speed langfuse ... --model mistralrs`.

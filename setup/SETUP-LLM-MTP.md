# MTP ( multi-token prediction ) on tabbyapi and sonar

Notes from 2026-10-29, enabling MTP self-speculative decoding for `turboderp/Qwen3.8-27B-exl3` (5.00bpw) on both EXL3 backends.
See [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md) for how the engines are deployed and switched.

## Background

The model ships one MTP layer (`mtp_num_hidden_layers: 1`). The 5.00bpw branch of the EXL3 quant keeps it:
`quantization_config.json` has `mtp_bits: 4`, and `model-00003-of-00003.safetensors` holds 39 `mtp.*` tensors (`mtp.fc`, one full transformer layer, norms).
The layer is full attention, so it adds a little KV cache ( ~2KiB/token at 8-bit, ~0.5GiB at 262144 ) plus ~0.2GiB of weights. The 262144 context still fits.

Because there is only one MTP layer, drafting more than one token reuses it, and acceptance drops at later positions.

To check another quant before trying it: look for `mtp_num_hidden_layers` in `config.json` and `mtp_bits` in `quantization_config.json`,
and count the `mtp.*` keys in `model.safetensors.index.json`.

## tabbyapi

Worked first time. Vars in `ansible/vars/tabbyapi/bnekub03.vars.yml`:

```yaml
tabbyapi_draft_mode: "mtp"
tabbyapi_draft_num_tokens: 4
tabbyapi_dynamic_draft: true     # num_tokens is then the ceiling
```

The role (`roles/tabbyapi`) renders these into a `draft_model:` section of tabby's `config.yml`, only when `tabbyapi_draft_mode` is non-empty.
No draft model is needed. Startup log: `Using main model MTP component for drafting`.

Results ( tensor parallel, warm, short replies ): **95-128 tok/sec** against 47-48 before. Long generations ( ~8000 tokens ) settle at **74-76 tok/sec**.
Draft acceptance was 60-81% on short replies and 47-50% on long ones. Prefix reuse works ( 64-87% of repeated prompts cached ).

To read speeds and draft acceptance from the logs ( tabby wraps each entry over several lines; the script joins them ):

```
python bin/llm.py tabby-speeds --since 1h
```

See [Tooling](#tooling) for the other verbs.

## sonar

Needed four changes. Sonar here is `0.24.2.dev0+g82b0cd8c2`.

### 1. Enable it, and give the MTP config the quant branch

```yaml
sonar_extra_args:
  - '--speculative-config={"method":"mtp","num_speculative_tokens":3,"revision":"5.00bpw"}'
```

Without `"revision"` startup fails with `Invalid repository ID or local directory specified: 'turboderp/Qwen3.8-27B-exl3'`:
the speculative config reuses the target's repo id but not its `--revision`, and the repo's `main` branch has no `config.json`.
It has to match `sonar_model_revision`.

### 2. Render args as JSON ( `roles/sonar/templates/sonar.j2.yml` )

Extra args are now written as `- {{ arg | to_json }}` instead of `- "{{ arg }}"`, because the JSON contains double quotes.
Existing args render the same as before.

### 3. Patch the model's `quantization_config.json` ( manual, on the PVC )

Next failure: `ValueError: There is no module or parameter named 'fc.mul1' in Qwen3_5MultiTokenPredictor ... available parameters ... {'fc.weight'}`.

Cause: sonar's EXL3 loader only treats a layer as EXL3 if it has an entry in `tensor_storage` in `quantization_config.json`,
and turboderp's file has 707 entries and none for `mtp.*`. The MTP layers were therefore built as plain layers while the checkpoint holds EXL3 tensors
( `trellis` / `suh` / `svh` / `mul1` ). Sonar only checks `quant_format == "exl3"` on an entry, so adding entries is enough.

The patch adds 15 entries built from the safetensors header of shard 3 ( 8 EXL3 at 4 bits, 7 plain norms ), copying `mul1_multiplier: 2212286765`
( the value in the main model's entries; the `mul1` tensors in the MTP layers hold the same number ). Generator:

```python
import json, struct, urllib.request, copy
base = 'https://huggingface.co/turboderp/Qwen3.8-27B-exl3/resolve/5.00bpw/'
def rng(u, a, b):
    return urllib.request.urlopen(urllib.request.Request(u, headers={'Range': f'bytes={a}-{b}'})).read()
u = base + 'model-00003-of-00003.safetensors'
n = struct.unpack('<Q', rng(u, 0, 7))[0]
hdr = json.loads(rng(u, 8, 8 + n - 1)); hdr.pop('__metadata__', None)
cfg = json.loads(urllib.request.urlopen(base.replace('resolve', 'raw') + 'quantization_config.json').read())
DT = {'F16': 'torch.float16', 'BF16': 'torch.bfloat16', 'I32': 'torch.int32', 'I16': 'torch.int16'}
groups = {}
for name, m in hdr.items():
    if name.startswith('mtp.'):
        mod = name.rsplit('.', 1)[0]
        groups.setdefault(mod, {})[name] = {'shape': m['shape'], 'n_bytes': m['data_offsets'][1] - m['data_offsets'][0], 'dtype': DT[m['dtype']]}
new = {}
for mod, st in groups.items():
    e = {'stored_tensors': st}
    if mod + '.trellis' in st:
        e.update({'quant_format': 'exl3', 'bits_per_weight': st[mod + '.trellis']['shape'][2] // 16, 'mul1_multiplier': 2212286765})
    new[mod] = e
out = copy.deepcopy(cfg); out['tensor_storage'].update(new)
json.dump(out, open('quantization_config.patched.json', 'w'), indent=2)
```

Apply it to the HF cache on the sonar PVC. Sonar reads the file at startup, so run this while the pod is up ( e.g. during a crash loop ) and the next start picks it up.
The cache file is a blob ( `snapshots/<hash>/quantization_config.json` is a symlink to it ). The original is **629045 bytes**; the command aborts if the size differs,
and keeps a backup next to the cache:

```
cd <dir with the patched file>
kubectl -n dev-sonar exec -i deploy/sonar -- sh -c 'B=/root/.cache/huggingface/hub/models--turboderp--Qwen3.8-27B-exl3/blobs/047fe12e203bababf55ea729c7c9b85890046a9b; cat > /root/.cache/quantization_config.patched.json; [ "$(wc -c < $B)" = 629045 ] || { echo "blob size unexpected, aborting"; exit 1; }; cp -p $B /root/.cache/quantization_config.orig.json; sha256sum $B /root/.cache/quantization_config.orig.json; cp /root/.cache/quantization_config.patched.json $B; python3 -c "import json,sys; ts=json.load(open(sys.argv[1]))[\"tensor_storage\"]; print(\"entries\", len(ts), \"mtp\", sum(1 for k in ts if k.startswith(\"mtp.\")))" $B' < quantization_config.patched.json
```

Expect two identical sha256 lines ( blob and backup ) and `entries 722 mtp 15`. The blob name above is the one at the time of writing; list `blobs/` to confirm.
Restore with `cp /root/.cache/quantization_config.orig.json <blob>`.

**This edit lives only on the PVC.** A fresh PVC, a re-download or a different quant needs it repeated. A durable fix would be an upstream change ( sonar treating `mtp.*` as EXL3
when `mtp_bits` is set, or turboderp adding the entries ) or an init container that applies the patch.

After the patch: both weight loads finish, the KV pool is **399,806 tokens ( 1.53x concurrency at 262144 )** against ~1.7x before, and the server starts.

### 4. Results with fp8 KV + FlashInfer ( the default selection )

Short replies **104-112 tok/sec**, long outputs **69-72 tok/sec** ( against ~52 before MTP, and tabby's 121-128 / 74-76 ).
Acceptance from `/metrics`: 47.5% overall; by draft position 67.8% / 44.7% / 30.1%; ~2.4 tokens per forward pass.
Prefix cache hits were 9% of prompt tokens in this short-request test ( real long agentic prompts reuse far more, see section 5 ).

Sonar does not log acceptance; it is in the engine's `/metrics` ( counters since the pod started ):

```
python bin/llm.py sonar-metrics
```

Per-request speed ( from the `Request completed` log lines ): `python bin/llm.py sonar-speeds --since 1h`.

### Why sonar trails tabby ( from the startup log and source )

With an fp8 KV cache on sm86 only FlashInfer is eligible: FlashAttention needs SM90+ for fp8 KV, and Triton rejects `fp8*` below SM89. With MTP, FlashInfer gives:

* `CUDAGraphMode.FULL_AND_PIECEWISE is not supported with spec-decode ... setting cudagraph_mode=PIECEWISE`
* `Fused multi-step draft decode is not supported ... falling back to rebuilding attention metadata between draft steps`
* `Speculative decoding (method=mtp) is enabled but no KV cache group could be identified as the draft model's ...` which makes all Mamba groups count as draft groups. The log says this disables prefix reuse across requests, but in practice reuse still works ( whole 1584-token blocks, 76% of prompt tokens over the coding run in section 5 ), so treat the warning as overstated

Also in the log: custom all-reduce is off ( no GPU P2P, probably the Proxmox passthrough ) and the symmetric-memory all-reduce does not support SM 8.6.

### 5. Experiment: Triton attention + int8 KV ( much slower at long context; reverted )

```yaml
  - "--kv-cache-dtype=int8_per_token_head"
  - "--attention-backend=TRITON_ATTN"
```

Triton's backend supports the draft-metadata update ( fused drafting ) and full CUDA graphs, and its SM89 check only fires for dtypes starting with `fp8`.
First attempt: Triton was selected and the speculator and prefill FULL CUDA graphs were captured, but startup then failed with
`ValueError: max_num_seqs (256) exceeds available Mamba cache blocks (250)` ( full graph capture needs one Mamba cache block per sequence ).
Fixed by adding `--max-num-seqs=64`. The second attempt started: `Using AttentionBackendEnum.TRITON_ATTN backend`, no `Fused multi-step` or
`FULL_AND_PIECEWISE ... PIECEWISE` warnings, FULL CUDA graphs captured, `GPU KV cache size: 406,974 tokens` ( 1.55x at 262144; fp8 gave 399,806 ).
The draft-group warning is still there.

Results, from `Request completed` lines and `/metrics`:

* Short replies ( ~100-150 tokens ): 80-113 tok/sec, about the same as or a little below fp8 + FlashInfer ( 104-112 ).
* The first long generation ( 4,930 token prompt, 7,139 tokens out ): **53.2 tok/sec**, against 68.9 for fp8 + FlashInfer ( 4,282 prompt, 6,413 out ) and 73.8-75.7 for tabby
  ( ~8,300 out ). So roughly 22% slower on this first long request even with fused drafting and full graphs on, and the gap widens with context ( see the langfuse comparison below ).
* MTP acceptance was no worse than with fp8 KV, which suggests int8 KV is not degrading output: 49.9% in the short test, then **62.6% overall** after a long coding session
  ( by draft position 78.4% / 61.0% / 48.5%, ~2.9 tokens per forward pass; higher than chat because code is more predictable ).
  This is an indirect signal; output quality was not otherwise tested.
* Prefix caching works well in a real agentic session: **76% of prompt tokens were cache hits** ( 396,000 of 519,764 ), typically 78-95% on the long follow-up requests,
  in whole 1584-token blocks ( the attention block size sonar picked to match the Mamba page size ). A cold 28k-token prompt took 25 s to first token; the same prompt with
  93% cached took 2.1 s.
* Decode slows sharply as the context grows ( same coding task, one request at a time ):

| Prompt tokens | Tokens out | Decode tok/sec |
|---|---|---|
| ~4.9k | 7,139 | 53.2 |
| ~11.9k | 16,384 ( hit the output cap ) | 43.9 |
| ~28.9k | 10,807 | 30.7 |
| ~41k | 3,519 | 25.0 |
| ~47k | 460 | 21.2 |
| ~58.6k | 4,424 | 21.3 |

Langfuse ( litellm sends every request there ) has the same coding task from the earlier fp8 + FlashInfer run, so the two can be compared at matching prompt sizes.
Speeds below are output tokens / total request latency: litellm's traces carry no first-token time, so they include prefill and read lower than the engine-logged decode
figures above, especially for short or uncached requests. Both columns use the same basis. Only requests with >= 1000 output tokens, paired by rough prompt size
( for two rows a different FlashInfer request was slightly closer: 61.0 at 10.9k would make the ~12k row 70%, and 90.8 at 39.9k would make the ~41k row 27% ):

| Prompt tokens | fp8 + FlashInfer | int8 + Triton | Triton as % of FlashInfer |
|---|---|---|---|
| ~4-5k ( 6.4k / 7.1k out ) | 66.6 | 52.2 | 78% |
| ~12-13k ( both hit the 16,384 output cap ) | 82.1 | 42.8 | 52% |
| ~29k ( 7.4k / 10.8k out ) | 69.1 | 30.5 | 44% |
| ~41-43k ( 3.3k / 3.5k out ) | 74.3 | 24.3 | 33% |
| ~55-59k ( 1.2k / 4.4k out ) | 68.0 | 19.4 | 29% |

* FlashInfer stays flat at 61-91 tok/sec from 4k to 55k of context ( median 69.1, 9 requests ). Triton falls steadily from 52 to 19 ( median 30.5, 5 requests ).
  So the slowdown belongs to this configuration; it is not inherent to the model or to MTP. The attention backend and the KV dtype were changed together, so which of the two is responsible is unknown.
* The two runs generated almost the same amount of text ( 49.1k and 49.5k output tokens over 24 and 25 requests ) and took about 12 and 28 minutes.
* A fixed prefill time is a bigger share of a faster engine's total time, so the end-to-end basis probably understates FlashInfer more than Triton and the real decode gap is likely larger.
  That is an inference. For the three Triton requests that appear in both the engine log and langfuse the two tok/sec figures agree to within 0.7-2.5%, and for tabby's 8,372-token request
  to within 5% ( 70.4 vs 73.8 ); I do not have engine logs for the long FlashInfer requests.
* The langfuse script's overall "median" line mixes both runs ( same model name ); use per-run windows.

Verdict: no gain at similar acceptance, 2-3.5x slower at long context, and a quantisation change for no benefit. Reverted: `vars/sonar/bnekub03.vars.yml` is back on `"--kv-cache-dtype=fp8"`
with the `--attention-backend` and `--max-num-seqs` lines removed. The sonar role has to be re-run for the running pod to pick that up.

Other ideas, not tried: `--dtype float16` ( EXL3 casts bf16 to fp16 and back at every linear layer ); an fp16 KV cache so FlashAttention is eligible
( KV doubles, so `max-model-len` would have to drop to ~190k ); patching sonar's draft KV-group detection to restore prefix reuse ( risky ).

### 6. Long-context comparison including tabbyapi ( langfuse, same coding task )

tabbyapi + MTP ran the task on 2026-10-29 11:27-11:49 UTC ( 25 requests, prompts growing to 101k tokens ). Median end-to-end tok/sec ( output tokens / total latency ) by prompt-size band,
requests with >= 1000 output tokens only, with the range of output lengths in each cell:

| Prompt tokens | tabbyapi + MTP | sonar fp8 + FlashInfer | sonar int8 + Triton |
|---|---|---|---|
| < 15k | 69.2 ( n=2, 8.3-8.4k out ) | 66.6 ( n=3, 2.3-16.4k out ) | 47.5 ( n=2, 7.1-16.4k out ) |
| 15-45k | 90.8 ( n=2, 16.0-22.7k out ) | 77.3 ( n=4, 1.6-7.4k out ) | 27.4 ( n=2, 3.5-10.8k out ) |
| 45-65k | 92.6 ( n=1, 17.8k out ) | 67.5 ( n=2, 1.2-2.5k out ) | 19.4 ( n=1, 4.4k out ) |
| > 65k | 67.6 ( n=7, 1.0-5.7k out ) | - | - |

These cells are not like-for-like. Prefill time is a fixed cost per request, so it weighs far less on tabby's 16-23k-token outputs than on FlashInfer's 1.2-3.3k-token ones, which flatters tabby in the
middle rows. The fairest cut is requests with >= 6000 output tokens: tabby median **86.8** ( n=5: 70.4, 67.9, 94.9, 86.8, 92.6 ), FlashInfer **69.1** ( n=3: 66.6, 82.1, 69.1 ),
Triton **42.8** ( n=3 ). Even there the prompt sizes differ ( tabby 5.8k-61k, FlashInfer 4k-30k ) and each cell has only 3-5 requests.

Reading: tabbyapi holds roughly 60-95 tok/sec from 6k to 100k tokens of context with no decay; sonar + FlashInfer holds 61-91 over 4k-55k; the Triton configuration decays steeply.
tabby looks ahead of sonar + FlashInfer by something like 10-25% on long generations, which is consistent with the 5-15% gap in engine-reported short-reply speed, but the sample is too small and
the output lengths too different to put a tighter number on it. The sonar rows come from the same task; tabby's later turns diverged from sonar's because the model's output differs run to run.

Why langfuse and engine figures differ: both count only tokens that reached the client, so rejected MTP draft tokens are in neither. The difference is the denominator. The engine's
decode tok/sec is output tokens / ( total time - time to first token ); langfuse has no first-token time from litellm, so it uses total latency, which includes prefill. On long requests the
two agree to 0.7-4.6%; on a ~100-token reply with a 1.6 s prefill the engine says 112 tok/sec and langfuse 38.

## Comparison ( Qwen3.8-27B 5.00bpw, tensor parallel on both 3090s, 262144 context, single stream )

| Setup | Short replies | ~7-8k token generation | 29-59k context, >=1k tokens out | Notes |
|---|---|---|---|---|
| tabbyapi, no MTP | 47-48 tok/sec | - | - | |
| sonar, no MTP | ~52 tok/sec | - | - | |
| **tabbyapi + MTP** | 95-128 tok/sec | 74-76 tok/sec | 87-95 tok/sec ( n=3, but 16-23k tokens out; see section 6 ) | acceptance 47-81%; prefix reuse 64-87% on repeat prompts |
| sonar + MTP, fp8 KV, FlashInfer | 104-112 tok/sec | 69-72 tok/sec | 67-91 tok/sec | acceptance 47.5% ( short test ); needs the PVC config patch |
| sonar + MTP, int8 KV, Triton | 80-113 tok/sec | 53 tok/sec | 19-31 tok/sec | acceptance 50-63%; much slower, see section 5 |

The first three speed columns are engine-reported decode tok/sec; the 29-59k column is end-to-end tok/sec from langfuse ( output tokens / total latency, includes prefill ), so only compare within it.
All numbers are single runs, not averaged. The short-reply and 7-8k figures used different prompts; the 29-59k column is the same coding task in both sonar rows.
tabbyapi + MTP is the simplest ( no patching ) and at least as fast as sonar wherever both were measured; its long-context cell comes from longer outputs than the sonar rows, so treat it as indicative ( section 6 ).
Sonar's advantages are its Prometheus metrics and batching several requests; neither helps a single stream.

## Things that did not work

* `--speculative-model mtp`, `--speculative-draft-n-max`, `--quantization exllamav3`, `--kv-cache-dtype int8`: flags from other engines. Sonar uses `--speculative-config '{...}'`,
  the quant method is `exl3` ( auto-detected ) and the KV option is `int8_per_token_head`.
* Pointing the speculative config at the BF16 `Qwen/Qwen3.8-27B` repo for the MTP head ( `"model": ...` ): not tried. From the source it would likely hit the same problem on `lm_head`
  ( the draft inherits the target's EXL3 config and `head_bits` is set ) and download all 51.7GiB of BF16 shards.
* TensorRT-LLM / Triton Inference Server: not viable for this model on 3090s. The `.engine` build path has no Qwen3.5 model, EXL3 isn't loadable, NVFP4/FP8 are gated to sm89+ / Hopper+,
  and the dense `Qwen3_5ForConditionalGeneration` isn't in its tested model list.

## Tooling

`bin/llm.py` is a [click](https://click.palletsprojects.com) CLI with one verb per task; add new ones as `@cli.command()` functions. It needs Python 3 and `click`
( standard library otherwise ), and `kubectl` for the verbs that read the cluster.

```
python bin/llm.py --help
python bin/llm.py sonar-speeds --since 2h          # decode tok/sec per request, from sonar's log
python bin/llm.py sonar-metrics                    # MTP acceptance by draft position, prefix cache hit rate ( kubectl exec )
python bin/llm.py tabby-speeds --since 2h          # generation tok/sec, cached share and draft acceptance, from tabby's log
python bin/llm.py langfuse-speeds 2026-10-29T12:30:00Z 2026-10-29T14:00:00Z --model sonar
```

The three log/metrics verbs also take `--file <path>` to read saved text instead of asking the cluster. Each prints a per-request table and then median speeds by
prompt size ( `<15k`, `15-45k`, `45-65k`, `>65k` ) for requests with at least `--min-out` tokens out ( default 1000 ).

`langfuse-speeds` needs the project keys: `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` ( in vault under `k8s/bnekub03/secret/dev-langfuse/langfuse` ). From WSL running a Windows Python,
also `export WSLENV=LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY:$WSLENV`, or the variables are not passed through. Langfuse is v4.70, where the old `/api/public/observations`
returns 404; the verb uses `/api/public/v2/observations` with cursor paging. Its speed is `--basis e2e` ( output tokens / total latency, so it includes prefill ) because
`timeToFirstToken` is empty ( next section ).

Which numbers to compare: sonar's and tabby's own figures are decode speed ( after the first token ); langfuse's e2e figure agrees with them to within a few percent on long requests but reads far lower
on short ones ( 38 vs 112 tok/sec for a ~100-token reply with a 1.6 s prefill ). Neither counts rejected MTP draft tokens.

## Loose ends

* litellm v1.104.0 does not send the first-token time to langfuse, so langfuse's `timeToFirstToken` is empty. litellm records it ( `completion_start_time`, used for its Prometheus metrics ) but its `langfuse_otel`
  exporter never put it on the span; langfuse reads the span attribute `langfuse.observation.completion_start_time` and derives the field from it. Fixed upstream by "fix(langfuse_otel)!: send the completion start time
  (#45597)", merged 2026-10-20. Checked 2026-10-30: the attribute is absent from every stable tag through v1.106.6 and from v1.107.0-rc.2 and v1.108.0-rc.1, and present in `v1.109.0-dev.1`.
  So it needs a stable litellm release newer than 1.106.6 that includes that commit; when `litellm_image` is next bumped, re-run `langfuse-speeds --basis decode` over a streaming request to confirm it fills in.
  Streaming requests only. Requests already stored will stay empty.

* Other MoE Qwens to try on tabby: `turboderp/Qwen3.5-35B-A3B-exl3` ( fits in VRAM ) and `turboderp/Qwen3.8-Flash-Next-exl3` ( 125B, ~6B active; needs CPU expert offload,
  so tabby's 48Gi memory limit would need raising; the node has ~96GiB allocatable ). Not tried.

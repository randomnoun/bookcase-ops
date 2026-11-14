# Note to humans

This is all claude-written, I'll rewrite this whole doc at some stage once I've got a known set of LLM engines and models.

# MTP ( multi-token prediction ) on tabbyapi and sonar

Notes from 2026-10-29, enabling MTP self-speculative decoding for `turboderp/Qwen3.8-27B-exl3` (5.00bpw) on both EXL3 backends.
See [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md) for how the engines are deployed and switched.
Section 7 adds `ninfer` ( 2026-11-07 ), which is a different engine with its own speculative decoding.

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
python bin/llm.py speed tabbyapi --since 1h
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
python bin/llm.py metric sonar
```

Per-request speed ( from the `Request completed` log lines ): `python bin/llm.py speed sonar --since 1h`.

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

### 7. ninfer ( DFlash2, one GPU )

Measured 2026-11-07. This is not the same setup as the rows above: ninfer serves its own 4-5 bit mix of Qwen3.8-27B ( `WarlaxZ/Qwen3.8-27B-NInfer-3090`, 17.0 GiB loaded, against ~20GB for the EXL3 5.00bpw )
on **one** 3090, not both. It ran the image from `docker/ninfer-3090` ( NInfer-3090 0.17.1; since replaced by ninfer-all, measured in sections 10 and 11 ) with run.sh's default profile: DFlash2 speculation with K=7 and a draft head, n-gram copy drafting, `rk4v4` KV cache, one lane.
Startup: weights ready in 22.8 s, engine ready in 29.4 s. The log also says `context lowered to 236,544 tokens (asked for 262,144)`: only 6.30 GiB of the card was free beside the model.

Results are engine-reported decode tok/sec from 44 tool-calling requests in one session ( the conversation grew from 2.2k to 73k tokens, `python bin/llm.py speed ninfer --since 6h --min-out 100` ).
Only requests with >= 100 tokens out are in the bands, not the >= 1000 used above, since agent replies are short:

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out |
|---|---|---|---|---|
| < 15k | 11 | 131.2 | 97.0-178.3 | 110-16,384 |
| 15-45k | 6 | 142.4 | 119.3-164.0 | 798-16,384 |
| 45-65k | 19 | 128.5 | 88.0-155.4 | 129-1,877 |
| > 65k | 3 | 91.2 | 89.9-100.8 | 129-931 |

* From `/metrics` over the pod's life ( 79,682 decode tokens in 612.6 s of decode rounds ): **130.1 tok/sec**. Draft acceptance 42.3% of drafted tokens, ~4.09 tokens per forward pass; n-gram copies accepted 72.9%.
  `python bin/llm.py metric ninfer` prints these ( the counters are cumulative since the pod started ).
* Prefix caching: **95.7% of prompt tokens were cache hits** ( 1,591,792 of 1,663,260 ). Uncached prefill ran at 1,683 tok/sec; a cold 13k-token prompt took 8.2 s to the first token and a cold 2.2k-token one 2.2 s.
* Speed varies a lot from request to request ( 88-178 ) and follows the draft acceptance. The slowest requests were beyond 60k tokens of context. Output length does not explain it: a 6,917-token reply ran at 97.0
  ( acceptance 26.3% ) but three 16,384-token replies ran at 119.6, 136.9 and 164.0 ( 38-55% ). What the model was writing probably matters, but that is a guess.
* Not controlled: different weights, one GPU against two, a single run, and I cannot show that these prompts are the same task as the sonar and tabbyapi runs. These are not langfuse end-to-end figures,
  so they are not comparable with the langfuse columns in sections 5 and 6.

### 8. tabbyapi, Swift 4.00bpw SC quant, one GPU

Measured 2026-11-08. The model is `Jon-Nielsen/Swift-Qwen3.8-27B-exl3-SC_4.00bpw_H5_V6` ( the Swift 1.5 fine-tune of Qwen3.8-27B; 16.3GB, 4.00 bits per weight on average, 5-bit head, 6-bit vision tower, 4-bit MTP head ),
registry entry `swift-27b-sc-4bpw-1gpu`. It runs on **one** 3090 with a 6-bit KV cache at 262144 context, the vision tower held in system RAM ( `vision_offload` ), and MTP drafting from the model's own head ( `draft_num_tokens: 4`, dynamic ).
The log says `Disabling GPU split because one GPU is in use` and `Model loaded in 33.8 s`. GPU 0 was using 22,546 of 24,576 MiB, so it fits at the full window with about 2GiB spare.

Results are tabbyapi's own generation tok/sec from 54 requests with >= 100 tokens out in one tool-calling session ( the conversation grew from about 1k to 80k tokens;
`python bin/llm.py speed tabbyapi --since 3h --min-out 100` ):

| Prompt tokens | n | Median tok/sec | Range |
|---|---|---|---|
| < 15k | 6 | 71.8 | 17.5-93.4 |
| 15-45k | 21 | 74.5 | 61.8-103.2 |
| 45-65k | 9 | 72.0 | 59.8-75.8 |
| > 65k | 18 | 70.2 | 56.2-92.2 |

* **Flat with context.** The median stays at 70-75 tok/sec from 15k to 80k tokens. Individual requests ran 56-103. A 4,735-token reply ran at 59.8.
* **Draft acceptance** was 52-88% per request, mostly in the 60s. The fastest requests ( 87-95 tok/sec ) were the ones with 80%+ acceptance, as with ninfer.
* **Prefix cache:** 96-100% hits on nearly every request. First token took about 1-3 s, with an occasional cold one ( 14.2 s on an 82%-cached 57k prompt ).
* The lowest figures in the < 15k band ( 17.5 ) are the first requests after loading, while the model warms up.
* Against the 5.00bpw run in the comparison table: long generations are about the same ( 56-75 here against 74-76 ), and short replies are lower ( median ~72 against 95-128 ), but these are agent turns with tool calls, not the short test prompts of the earlier run.
* Not controlled: one GPU against two, 4.00bpw against 5.00bpw, a 6-bit KV cache against 8-bit, and a different session and prompts. A single run. Quality was not measured, and the quant's author says the bit plan was copied from the base model, not re-measured for the fine-tune.

To compare it with two GPUs on the same model, run the `swift-27b-sc-4bpw-2gpu` registry entry ( tensor parallel, 8-bit KV cache ) through a similar session. Those steps are in [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md#switching-models).
The result is in section 9.

### 9. tabbyapi, Swift 4.00bpw SC quant, two GPUs ( tensor parallel )

Measured 2026-11-08, shortly after section 8, on registry entry `swift-27b-sc-4bpw-2gpu`: the same model, the same folder on the PVC, tensor parallel across both 3090s, an **8-bit** KV cache ( section 8 used 6-bit ), no vision tower, MTP with `draft_num_tokens: 4`.
`Model loaded in 30.4 s` ( 33.8 s on one GPU ). After loading, GPU 0 used 13,681 MiB and GPU 1 12,489 MiB, so about 25.6 GiB across the two against 22 GiB on one.

Results are tabbyapi's own generation tok/sec from 47 requests with >= 100 tokens out in a tool-calling session on this pod ( prompts growing from 2k to 70k tokens;
`python bin/llm.py speed tabbyapi --since 2h --min-out 100` ). The pod was 17 minutes old, so every request in the log is from this entry:

| Prompt tokens | n | Median tok/sec | Range | One GPU ( section 8 ) |
|---|---|---|---|---|
| < 15k | 4 | 92.8 | 20.7-143.0 | 71.8 |
| 15-45k | 23 | 115.7 | 88.9-151.3 | 74.5 |
| 45-65k | 15 | 99.4 | 83.8-129.4 | 72.0 |
| > 65k | 2 | 85.8 | 79.3-92.4 | 70.2 |
| all | 47 | **105.2** ( mean 107.5 ) | | |

* **Faster than one GPU in every band:** the median is +29% below 15k tokens of context ( 4 requests ), +55% at 15-45k, +38% at 45-65k and +22% above 65k ( 2 requests ). The two outer bands have too few requests to lean on.
  The earlier 5.00bpw run measured 95-128 tok/sec on short replies with tensor parallel, so this lands in the same range.
* **Draft acceptance** had a median of 66%, in line with section 8's 52-88%.
* **Time to first token:** a cold prompt took 37.1 s for 2,145 tokens ( the first request, while the model warmed up ), 11.4 s for 7,923 and 15.0 s for 16,272. Requests with >= 90% of the prompt cached ( 42 of 49 ) had a median of **0.95 s**, a 90th percentile of 2.3 s and a worst case of 14.5 s.
* **Long outputs:** the requests with >= 1000 tokens out ran at 88.9-145.6 tok/sec ( ten of them, including 3,724, 4,716, 6,011, 8,261 and 9,348 tokens out ), so a long reply didn't slow it the way it did on one GPU ( 59.8 for 4,735 tokens ).
* **Not a clean test:** the KV cache is 8-bit here and 6-bit in section 8, so the gap isn't purely the second GPU. The session is a different one, with different prompts, and I cannot show that the two are the same kind of work. Each is a single run, and the first-band figures are skewed by the warm-up requests.
  Quality was not compared. Tensor parallel without NVLink works fine here, even with GPU-to-GPU traffic going through host memory.

### 10. ninfer-all ( MTP3, one GPU )

Measured 2026-11-14, the first session after moving from ninfer-3090 to [ninfer-all](https://github.com/iamwavecut/ninfer-all) ( image `cuda12.9-bdc1b8d8970a`, build 2026-11-09.1 ), on registry entry `qwen3.8-27b-mtp`:
run.sh's `tuned` profile with MTP, three drafts and a draft head, a Q6 output head, two lanes, `rk4v4` KV and vision on. The weights are `neroued/Qwen3.8-27B-NInfer` ( 16.0 GiB loaded, against 17.0 GiB for ninfer-3090's ), on **one** 3090.
It started at the full 262,144 context on its first attempt, with no step-down; GPU 0 used 22,188 of 24,576 MiB and the log said 1.90 GiB free. `engine ready` took 1m 8.6s ( weights 58.4 s ), against 29.4 s on ninfer-3090.

Results are engine-reported decode tok/sec from 33 tool-calling requests in one session ( prompts growing from 2.1k to 65.8k tokens, `python bin/llm.py speed ninfer --since 2h --min-out 100` ).
The bands are as in section 7, requests with >= 100 tokens out ( 32 of the 33 ), computed from the per-request table:

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out | Section 7 ( ninfer-3090, DFlash2 ) |
|---|---|---|---|---|---|
| < 15k | 8 | 115.2 | 84.6-159.9 | 103-4,870 | 131.2 |
| 15-45k | 7 | 127.9 | 107.6-198.1 | 531-16,384 | 142.4 |
| 45-65k | 15 | 98.8 | 87.4-167.1 | 111-5,642 | 128.5 |
| > 65k | 2 | 100.0 | 99.5-100.6 | 573-1,090 | 91.2 |
| all | 32 | **107.3** | | | |

* **Not directly comparable with section 7.** Every band's median is lower than ninfer-3090's, but that run was DFlash2 with K=7 and one lane, and this is MTP3 with two lanes, in a different session. The ninfer-3090 release notes put MTP about 14% below DFlash2
  ( 115 against 133 ), but section 11 ran DFlash2 on ninfer-all and it was no faster overall ( ~102 against ~107 ), so MTP against DFlash2 doesn't explain the gap to section 7.
* **Draft acceptance** had a median of 61.5% ( range 40-81%, 33 requests ). N-gram copy acceptance varied from 7% to 96% per request.
* **Prefix cache:** 26 of 33 requests had >= 90% of the prompt cached, and the 21 with >= 99% got their first token in 0.1-0.5 s. Cold prompts were slower: 14,375 tokens with nothing cached took 6.7 s ( prefill 2,140 tok/sec ), 14,664 took 7.2 s,
  and a cold 2.1k one 0.9 s. Partly cached ones took 3.6-4.2 s at 24k, 48k and 56k tokens ( 65%, 85% and 90% cached ).
* **Langfuse agrees.** `python bin/llm.py speed langfuse --since 2h --model ninfer` ( the model is recorded as `ninfer-qwen3.8:27b-mtp` ) gives end-to-end medians of 87.0, 121.8, 96.6 and 97.1 tok/sec for requests with >= 1000 tokens out,
  against engine decode of 87.9, 127.9, 97.2 and 100.6 for the same bands ( n = 2, 5, 3, 1 ), so within 0.6-5%. Short cold requests are far lower end to end: 23.5 tok/sec for a cold 14.4k prompt with 186 tokens out, since the 6.7 s prefill dominates.
  Langfuse still has no first-token time.
* Not controlled: a single session, a different speculation setup from section 7, and prompts that are not the same task as the sonar and tabbyapi runs.

### 11. ninfer-all ( DFlash2, one GPU )

Measured 2026-11-14, straight after section 10, on registry entry `qwen3.8-27b-dflash2`: the same image and weights file, with run.sh's `tuned` profile using DFlash2 ( seven drafts and a draft head ), one lane, `rk4v4` KV and vision on.
The weights load as 18.0 GiB, against 16.0 GiB for MTP. It did **not** start at 262,144: `Not enough free GPU memory` at 262,144 ( prefill chunk 4096 ), then at 229,376, and it started at **196,608** with a 2048 chunk and 16 host state slots instead of 32.
GPU 0 used 22,864 of 24,576 MiB with 1.24 GiB free. Each failed attempt reloaded the weights, so the start took about 2m 15s, against 51 s for the final attempt alone.

Results are engine-reported decode tok/sec from 35 tool-calling requests in one session ( prompts growing from about 3k to 76k tokens, `python bin/llm.py speed ninfer --since 2h --min-out 100` ), requests with >= 100 tokens out ( 31 of the 35 ):

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out | Section 10 ( MTP3 ) |
|---|---|---|---|---|---|
| < 15k | 7 | 152.9 | 90.1-182.1 | 107-16,384 | 115.2 ( n=8 ) |
| 15-45k | 5 | 124.2 | 102.4-127.9 | 2,688-11,952 | 127.9 ( n=7 ) |
| 45-65k | 6 | 116.5 | 94.2-161.8 | 209-2,053 | 98.8 ( n=15 ) |
| > 65k | 13 | 96.4 | 78.8-101.8 | 141-1,808 | 100.0 ( n=2 ) |
| all | 31 | **102.4** | | | 107.3 ( n=32 ) |

* **No faster overall than MTP3 here.** DFlash2's median is 102.4 against 107.3, but the two sessions put their requests in different places: 13 of DFlash2's are above 65k tokens against 2 of MTP's, and 15 of MTP's are at 45-65k against 6.
  So only the bands with several requests on both sides mean anything, and they disagree: DFlash2 is ahead at < 15k and 45-65k and behind at 15-45k. It also ran one lane at 196,608 tokens, where MTP ran two at 262,144.
* **ninfer-all's own 3090 figures** ( `docs/performance.md`, Qwen3.8-27B, `rk4v4`, one request, a 258,048 window ) have DFlash2 5 ahead of MTP 3 on short chat ( 115.9 against 95.9 ), at 1k ( 88.5 against 79.0 ) and at 32k ( 93.2 against 89.4 ), level at 8k ( 86.1 against 88.1 ),
  and behind from 65k ( 70.6 against 77.5; 60.5 against 66.3 at 131k; 54.1 against 61.5 at 196k ). That fits an agent session that spends most of its time deep in context. Our figures at 65-76k ( 96-100 ) are well above theirs, and I haven't worked out why; the n-gram copy drafting
  that the benchmark may not use is a guess.
* **Against section 7.** ninfer-3090's DFlash2 session had a median of ~130, against ~102 here. The engine build, the weights file ( 17.0 GiB loaded then, 17.7-18.0 now ), the context ( 236,544 then, 196,608 now ) and the session all differ, so I can't say which accounts for it.
  It was one GPU in both cases; ninfer has never used two.
* **Draft acceptance** had a median of 36% ( range 25-56%, 35 requests ), against 61.5% for MTP3's three drafts. DFlash2 proposes seven per round, so the two percentages aren't comparable.
* **Langfuse reads lower, and the gap is time to first token.** For requests with >= 1000 tokens out, langfuse's end-to-end medians were 97.9, 105.6, 126.3 and 88.8 against engine decode of 101.0, 124.2, 128.2 and 95.1 ( n = 2, 5, 1, 4 ): within 1.5-7% except the 15-45k band, which is 15% lower.
  That band has two cold or partly cached prompts ( 22,811 tokens with nothing cached took 11.2 s to a first token, 34,796 with 66% cached took 6.0 s ). Estimating end to end as `output / ( output / decode speed + TTFT )` from the pod's log reproduces langfuse's own figures:

  | Prompt | Cached | TTFT | Engine decode | Estimate | Langfuse |
  |---|---|---|---|---|---|
  | 14,378 | 0% | 7.2 s | 112.0 | 106.7 | 106.3 |
  | 22,811 | 0% | 11.2 s | 117.6 | 105.9 | 105.6 |
  | 34,796 | 66% | 6.0 s | 125.8 | 98.8 | 98.6 |
  | 43,912 | 100% | 0.2 s | 102.4 | 102.0 | 101.6 |

  So the two measures agree. Langfuse includes prefill, so compare engine with engine or langfuse with langfuse, and expect it to be lower when prompts are cold. Section 10's "within 0.6-5%" held only because that session had fewer cold prompts in those bands.
* **Log format:** a request where only DFlash2 drafts ( no n-gram copying ) logs `dflash2 accepted 516/2,009 (25.7%)`, not `mixed speculation accepted ...`; `speed ninfer` handles both.
* Not controlled: a single session, MTP and DFlash2 sessions that differ in lanes and context, and prompts that are not the same task as the sonar and tabbyapi runs or, as far as I can show, as each other.

### 12. ninfer-all ( DFlash2, one GPU, CUDA 13.4 image )

Measured 2026-11-14, straight after section 11: the same commit ( `bdc1b8d8970a` ), registry entry and weights, but the `cuda13.4` image in place of `cuda12.9` ( `CUDA 13 backend`, `nvcc 13.4.92` in the log ). The driver reports CUDA 13.0, the image's minimum, and it started without complaint.
Startup was the same as section 11: the same two step-downs to **196,608** ( prefill chunk 2048, 16 host state slots ), 1.26 GiB free against 1.24, weights loading in 41-43 s per attempt, and `engine ready` in 50.5 s against 50.7 s. The toolkit made no visible difference to memory or load time.

Results are engine-reported decode tok/sec from 63 tool-calling requests ( prompts growing from 2k to 104k tokens ), requests with >= 100 tokens out ( 57 of the 63 ):

| Prompt tokens | n | Median decode tok/sec | Range | Section 11 ( CUDA 12.9 ) |
|---|---|---|---|---|
| < 15k | 7 | 126.9 | 94.0-164.6 | 152.9 ( n=7 ) |
| 15-45k | 8 | 100.7 | 91.4-151.0 | 124.2 ( n=5 ) |
| 45-65k | 15 | 104.9 | 85.6-153.6 | 116.5 ( n=6 ) |
| > 65k | 27 | 106.3 | 78.8-146.9 | 96.4 ( n=13 ) |
| all | 57 | **108.5** | | 102.4 ( n=31 ) |

* **No measurable difference between the toolkits.** The overall median is 6% higher, and restricted to the prompts section 11 covered ( up to 76k tokens ) it is 108.8 over 38 requests, but the bands go both ways: lower below 65k ( -17%, -19%, -10% ) and higher above it ( +10% ).
  Within any one band the requests span about 80-165 tok/sec, and each session was a different piece of agent work, so differences of this size can't be told apart from the sessions' own variation.
* **The sessions aren't matched.** This one went to 104k tokens against 76k, and 27 of its requests are above 65k, where decode is slowest, against 13. That lifts the CUDA 13 > 65k median relative to section 11's, because most of those requests were short replies to long, fully cached prompts.
* **Draft acceptance** had a median of 38.9% ( range 26-56%, 63 requests ), against 36.0% in section 11.
* **Time to first token:** only two requests took 5 s or more ( 5.8 s at 12k tokens and 9.7 s at 20.5k, both cold ), against four in section 11, so this session leans less on prefill, which helps its end-to-end figures more than its decode ones.
* To tell the toolkits apart, replay one fixed prompt set against each image, rather than comparing two live sessions. I haven't done that.

## Comparison ( Qwen3.8-27B 5.00bpw, tensor parallel on both 3090s, 262144 context, single stream )

| Setup | Short replies | ~7-8k token generation | 29-59k context, >=1k tokens out | Notes |
|---|---|---|---|---|
| tabbyapi, no MTP | 47-48 tok/sec | - | - | |
| sonar, no MTP | ~52 tok/sec | - | - | |
| **tabbyapi + MTP** | 95-128 tok/sec | 74-76 tok/sec | 87-95 tok/sec ( n=3, but 16-23k tokens out; see section 6 ) | acceptance 47-81%; prefix reuse 64-87% on repeat prompts |
| sonar + MTP, fp8 KV, FlashInfer | 104-112 tok/sec | 69-72 tok/sec | 67-91 tok/sec | acceptance 47.5% ( short test ); needs the PVC config patch |
| sonar + MTP, int8 KV, Triton | 80-113 tok/sec | 53 tok/sec | 19-31 tok/sec | acceptance 50-63%; much slower, see section 5 |
| tabbyapi + MTP, Swift 4.00bpw SC, **one** GPU | median ~72 tok/sec in a tool-calling session ( range 17-103 ) | 60 tok/sec ( one 4.7k request ) | 70-75 median at 15-80k context, engine decode, not end-to-end ( n=54; see section 8 ) | acceptance 52-88%; 96-100% of prompt tokens cached; 6-bit KV; different weights ( 4.00bpw ) |
| tabbyapi + MTP, Swift 4.00bpw SC, **two** GPUs ( tensor parallel ) | median ~105 tok/sec in a tool-calling session ( range 21-151 ) | 89-130 tok/sec ( 10 requests of 1,087-9,348 tokens ) | 86-116 median at 15-70k context, engine decode, not end-to-end ( n=47; see section 9 ) | acceptance median 66%; 8-bit KV; TTFT median 0.95 s on cached prompts, 11-15 s cold |
| ninfer-3090, DFlash2, **one** GPU | 121-178 tok/sec | 97 tok/sec ( one 6.9k request ) | 116-164 engine decode, not end-to-end ( n=8, median 142; see section 7 ) | acceptance 42%; 96% of prompt tokens cached; different weights ( 4-5 bit, 17 GiB ) |
| ninfer-all, MTP3, **one** GPU | median ~107 tok/sec in a tool-calling session ( range 85-198 ) | 109 tok/sec ( one 7.2k-token reply ) | 97-180 engine decode, not end-to-end ( n=5, median 153; see section 10 ) | acceptance median 61%; 26 of 33 requests >= 90% cached; two lanes; weights 16.0 GiB |
| ninfer-all, DFlash2, **one** GPU | median ~102 tok/sec in a tool-calling session ( range 79-182 ) | 90 tok/sec ( one 9.3k-token reply ) | 102-128 engine decode, not end-to-end ( n=5, median 126; see section 11 ) | acceptance median 36% ( seven drafts a round ); one lane; context stepped down to 196,608; weights 18.0 GiB |

The first three speed columns are engine-reported decode tok/sec; the 29-59k column is end-to-end tok/sec from langfuse ( output tokens / total latency, includes prefill ), so only compare within it.
All numbers are single runs, not averaged. The short-reply and 7-8k figures used different prompts; the 29-59k column is the same coding task in both sonar rows.
tabbyapi + MTP is the simplest ( no patching ) and at least as fast as sonar wherever both were measured; its long-context cell comes from longer outputs than the sonar rows, so treat it as indicative ( section 6 ).
Sonar's advantages are its Prometheus metrics and batching several requests; neither helps a single stream.
ninfer's decode speed is higher than the other rows, on half the GPUs, but the margin depends on the cell: on short replies its range ( 121-178 ) overlaps tabbyapi's top end ( 95-128 ), and the 7-8k cell is one request.
The ninfer row is also a different quantisation, a different task and a different measure in its last speed cell ( section 7 ), so read it as "faster", not as a precise ratio.
The two ninfer-all rows ( sections 10 and 11 ) are closer to the others: their session medians of ~107 ( MTP3 ) and ~102 ( DFlash2 ) tok/sec on one GPU are about the same as tabbyapi's two-GPU Swift run ( ~105, section 9 ), so the figures above describe the ninfer-3090 DFlash2 setup, not ninfer in general.

## The four engines compared

All four serve models to litellm from `bnenod05`, and only one runs at a time ( `python bin/llm.py engine set` ). Speeds are in the next section, with their qualifications; they are not a like-for-like benchmark.

| | **ollama** | **sonar** | **tabbyapi** | **ninfer** |
|---|---|---|---|---|
| **Model format** | GGUF | EXL3 ( also GGUF ) | EXL3 ( and FP16/BF16 ) | its own `.ninfer`, specific models only |
| **Models at once** | many, loaded on demand and unloaded after `keep_alive` ( 30m ) | one per process, set in the registry | one; load and unload through the API | one per process |
| **GPUs** | both ( layer split ) | both ( tensor parallel ) | both ( autosplit, or tensor parallel ) | **one** |
| **Speed seen, short replies** | not measured | 52 without MTP; 104-112 with | 47-48 without MTP; 95-128 with | 121-178 on ninfer-3090 ( speculation always on ); 79-198 on ninfer-all, session medians ~102-107 |
| **Speed seen, long generations** | not measured | 69-72 with MTP | 74-76 with MTP | 97 ( one 6.9k request ); median ~130 over a 44-request session on ninfer-3090; 90-109 ( one 7-9k reply each ) on ninfer-all |
| **Metrics** | none set up | Prometheus `/metrics` | none, logs only | Prometheus `/metrics`, `/health`, and a per-request log line |
| **Image** | stock | you build it ( the CUDA compile took about 7 hours ) | stock, from ghcr | stock, from ghcr ( `ninfer-all`; the figures here are from the ninfer-3090 image you had to build ) |
| **Starts** | scaled up | scaled to 0 | scaled to 0 | scaled to 0 |
| **Switch model** | `ollama pull`, then add it to litellm by hand | edit the registry, `model load` | registry, `model download`, `model load` | registry, `model load` |

* **ollama** is the flexible one: the only engine that swaps many models in and out on demand, which is why it runs by default. It is also the only one without a model registry or `model sync`.
* **sonar** has the most features ( batching, Prometheus, tool-call and reasoning parser flags ) but was the hardest to set up; MTP needed a manual patch on the PVC.
* **tabbyapi** is the simplest way to run EXL3: MTP worked first time and models can be swapped at runtime. No metrics.
* **ninfer** matched tabbyapi's two-GPU speed on one GPU ( session medians ~102-107 on ninfer-all against ~105 ), with the earlier ninfer-3090 session higher ( ~130 ), at the cost of a narrower, riskier setup: a community port of a Blackwell engine, maintained best-effort, that only runs the models it supports.

### Qualifications on the speed rows

* **Different weights.** sonar and tabbyapi ran the EXL3 5.00bpw quant ( ~20GB ) with an 8-bit KV cache. ninfer ran a 4-5 bit mix ( 17.7 GiB ) with a 4-bit KV cache ( `rk4v4` ). Fewer bits means less to move per token, so some of ninfer's speed
  is a quality trade, not only a better engine. I have no measurement of how its output compares with EXL3; its model card compares it only with other ninfer and GGUF files.
* **Different hardware use.** ninfer ran on one 3090 and the others on two with tensor parallelism, so ninfer pays no cross-GPU traffic, and on these PCIe-only cards that traffic is a real cost.
* **Different speculation.** sonar and tabbyapi use the model's own MTP layer. ninfer's default profile uses DFlash2 with a draft head, plus n-gram copy drafting that helps when the model rewrites text it has just read ( 73% of copied
  tokens were accepted against 42% of the others ). `NINFER_SPEC=mtp` ( the `qwen3.8-27b-mtp` registry entry ) is the closer match to the other two, and the release's own numbers have it slower than DFlash2; sections 10 and 11 measured MTP and DFlash2 on ninfer-all ( session medians ~107 and ~102 tok/sec ), so DFlash2 was not faster there.
* **Different prompts and measures.** The sonar and tabbyapi figures are short test prompts and one coding task, and the ninfer ones are a different tool-calling session. All are single runs. ninfer's figures are engine-reported decode speed
  from `speed ninfer`; none of the four were measured on the same prompts, and none are langfuse end-to-end figures. ollama was not measured at all.
* **Large variation.** ninfer ran 88-178 tok/sec between requests, following its draft acceptance ( 26-56% ). A median of many requests is more meaningful than any one, and none of these medians is a benchmark.
* **Output differs too.** ninfer's `run.sh` adds a min-p and a presence penalty by default as a loop guard, which the others don't, so the engines are not producing the same text from the same weights.

To compare them properly: one prompt set, run through litellm against each engine in turn, with the same sampling settings, and read both the speed and the output.

## The models behind each engine ( are they comparable? )

Every engine serves Qwen3.8-27B, but not the same weights. These are the registry entries as of 2026-11-14 ( `ansible/vars/<engine>/bnekub03-models.vars.yml` ); sglang and mistralrs have not been measured yet, so there are no speed rows for them above.

| Engine | Registry entry | Weights | GPUs | Speculation | KV cache | Context | Thinking cap |
|---|---|---|---|---|---|---|---|
| sonar | `qwen3.8-27b-5bpw` | EXL3 5.00bpw ( `turboderp/Qwen3.8-27B-exl3`, ~19.9GB ) | 2, tensor parallel | MTP, 3 drafts | fp8 | 262144 | 8192 |
| tabbyapi | `qwen3.8-27b-5bpw` | the same EXL3 5.00bpw | 2, tensor parallel | MTP, up to 4 drafts, dynamic | 8-bit | 262144 | 8192 |
| tabbyapi | `swift-27b-sc-4bpw-2gpu` ( active ), `-1gpu` | EXL3 4.00bpw of **Swift**, a fine-tune ( 16.3GB ) | 2 ( tensor parallel ) or 1 | MTP, up to 4 drafts, dynamic | 8-bit ( 6-bit on one GPU ) | 262144 | 8192 |
| ninfer | `qwen3.8-27b-dflash2` ( active ), `-mtp` | `.ninfer`, own 4-5 bit mix ( 18.0 GiB loaded with DFlash2, 16.0 with MTP ) | **1** | DFlash2 with n-gram copying, or MTP | `rk4v4` ( 4-bit keys and values ) | 196608 ( DFlash2, pinned ), 262144 ( MTP ) | 8192 |
| sglang | `qwen3.8-27b-gptq-int4` | GPTQ 4-bit, group size 32 ( `palmfuture/Qwen3.8-27B-GPTQ-Int4` ) | 2, tensor parallel | MTP via EAGLE, 3 steps, 4 drafts | fp8 | 262144 | 8192 ( strict thinking ) |
| mistralrs | `qwen3.8-27b-q4k` | the original BF16 weights, quantized to `q4k` as they load | 2, automatic device mapping | `--mtp` ( support for this model unconfirmed ) | fp8 | 262144 | none |

* **Closest pair.** sonar and tabbyapi `qwen3.8-27b-5bpw` are the only exact match: same EXL3 weights, same context, similar speculation, and they differ in the KV format ( fp8 against 8-bit ) and the engine. Every other row has different weights.
* **sglang against tabbyapi Swift.** sglang's GPTQ 4-bit is nearest in size to the Swift 4.00bpw quant ( 16.3GB ) on two GPUs with MTP, but Swift is a fine-tune, so the output is not the same model. Against the 5.00bpw rows sglang has about one bit per weight less.
* **GPTQ and q4k are not EXL3.** At the same nominal bits, the three formats lose different amounts of quality. I haven't measured any of them; only the speed rows above exist.
* **mistralrs quantizes at load.** Its `q4k` weights come from the original BF16 model, so unlike the others there is no pre-made quant to check against, and its first start is slow.
* **No cap on mistralrs.** The other engines stop thinking at 8192 tokens, so a long think on mistralrs can use more of the output budget. Compare tok/sec rather than the length of a reply.
* **Limits seen on sglang.** `max_running_requests` is capped at 5 by its Mamba state cache, and its KV pool held 283,366 tokens at fp8 on these cards. That is above the 262144 context but leaves room for only about one full-length request.

To make the comparison fair, either run the same registry entry's weights on the engines that can read them, or accept that this is a comparison of engine and quant together, as the earlier rows are.

## Things that did not work

* `--speculative-model mtp`, `--speculative-draft-n-max`, `--quantization exllamav3`, `--kv-cache-dtype int8`: flags from other engines. Sonar uses `--speculative-config '{...}'`,
  the quant method is `exl3` ( auto-detected ) and the KV option is `int8_per_token_head`.
* Pointing the speculative config at the BF16 `Qwen/Qwen3.8-27B` repo for the MTP head ( `"model": ...` ): not tried. From the source it would likely hit the same problem on `lm_head`
  ( the draft inherits the target's EXL3 config and `head_bits` is set ) and download all 51.7GiB of BF16 shards.
* TensorRT-LLM / Triton Inference Server: not viable for this model on 3090s. The `.engine` build path has no Qwen3.5 model, EXL3 isn't loadable, NVFP4/FP8 are gated to sm89+ / Hopper+,
  and the dense `Qwen3_5ForConditionalGeneration` isn't in its tested model list.

## Tooling

`bin/llm.py` is a [click](https://click.palletsprojects.com) CLI with two-level verbs ( `speed`, `metric`, `engine`, `model` ); add new ones as commands on one of those groups.
It needs Python 3 and `pip install click pyyaml hvac requests`, and `kubectl` for the verbs that read the cluster. The `engine` and `model` verbs, which switch engines and models, are described in
[SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md#switching-between-engines).

```
python bin/llm.py --help
python bin/llm.py speed sonar --since 2h          # decode tok/sec per request, from sonar's log
python bin/llm.py metric sonar                    # MTP acceptance by draft position, prefix cache hit rate ( kubectl exec )
python bin/llm.py speed tabbyapi --since 2h          # generation tok/sec, cached share and draft acceptance, from tabby's log
python bin/llm.py speed ninfer --since 2h --min-out 100   # prefill and decode tok/sec, cached share, draft and n-gram acceptance, from ninfer's log
python bin/llm.py metric ninfer                   # decode speed and speculation acceptance since the pod started ( vault key, kubectl port-forward )
python bin/llm.py speed langfuse --since 2h --model ninfer     # or START [END] as UTC times: 2026-10-29T12:30:00Z 2026-10-29T14:00:00Z
```

The log and metrics verbs also take `--file <path>` to read saved text instead of asking the cluster. Each prints a per-request table and then median speeds by
prompt size ( `<15k`, `15-45k`, `45-65k`, `>65k` ) for requests with at least `--min-out` tokens out ( default 1000 ).

`speed langfuse` reads the project keys from vault ( `k8s/bnekub03/secret/dev-langfuse/langfuse`, so `. vault-login.sh` first ). `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` override them if set.
`--since 2h` looks back from now; `START` and `END` are still UTC, and so are the times in the table. Langfuse is v4.70, where the old `/api/public/observations`
returns 404; the verb uses `/api/public/v2/observations` with cursor paging. Its speed is `--basis e2e` ( output tokens / total latency, so it includes prefill ) because
`timeToFirstToken` is empty ( next section ).

Which numbers to compare: sonar's, tabby's and ninfer's own figures are decode speed ( after the first token ); langfuse's e2e figure agrees with them to within a few percent on long requests but reads far lower
on short ones ( 38 vs 112 tok/sec for a ~100-token reply with a 1.6 s prefill ). Neither counts rejected MTP draft tokens.

## Loose ends

* litellm v1.104.0 does not send the first-token time to langfuse, so langfuse's `timeToFirstToken` is empty. litellm records it ( `completion_start_time`, used for its Prometheus metrics ) but its `langfuse_otel`
  exporter never put it on the span; langfuse reads the span attribute `langfuse.observation.completion_start_time` and derives the field from it. Fixed upstream by "fix(langfuse_otel)!: send the completion start time
  (#45597)", merged 2026-10-20. Checked 2026-10-30: the attribute is absent from every stable tag through v1.106.6 and from v1.107.0-rc.2 and v1.108.0-rc.1, and present in `v1.109.0-dev.1`.
  So it needs a stable litellm release newer than 1.106.6 that includes that commit; when `litellm_image` is next bumped, re-run `speed langfuse --basis decode` over a streaming request to confirm it fills in.
  Streaming requests only. Requests already stored will stay empty.

* Other MoE Qwens to try on tabby: `turboderp/Qwen3.5-35B-A3B-exl3` ( fits in VRAM ) and `turboderp/Qwen3.8-Flash-Next-exl3` ( 125B, ~6B active; needs CPU expert offload,
  so tabby's 48Gi memory limit would need raising; the node has ~96GiB allocatable ). Not tried.

# Note to humans

This is all claude-written, I'll rewrite this whole doc at some stage once I've got a known set of LLM engines and models.

# MTP ( multi-token prediction ) on tabbyapi and sonar

Notes from 2026-10-29, enabling MTP self-speculative decoding for `turboderp/Qwen3.8-27B-exl3` (5.00bpw) on both EXL3 backends.
See [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md) for how the engines are deployed and switched.
Section 7 adds `ninfer` ( 2026-11-07 ), which is a different engine with its own speculative decoding. Section 13 adds `sglang` and section 14 `mistralrs` ( both 2026-11-14 ), and sections 15, 16 and 17 `strata` ( 2026-11-15, IQ3_XXS, Q2_0 and IQ3_S ), which runs a different, much larger model. Section 18 is the reading on Unsloth quants plus one measured file, UD-Q2_K_XL. Section 19 ( 2026-11-16 ) is the first measurements after an NVLink bridge was fitted between the 3090s: everything in sections 1-18 is from before it.

## How the sessions were run

Every "pi session", "agent session" or "coding session" in this doc is the **same pi prompt**, sent to a different engine, model or setting. The sessions are not identical runs, though: the model's output differs from run to run and from engine to engine, so each agent
takes its own path, with different tool calls, different numbers of requests, different output lengths and context sizes, and results that vary in quality ( some good, some bad ). So the starting prompt is controlled and the trajectory isn't.
Where this doc says a session was "a different task" or that it "can't show the prompts are the same task", read that as "the same starting prompt, a different trajectory" ( written before this was recorded here; sections 5-6 and 15-18 were not all re-checked ). The effect is that request counts, prompt-size bands and output lengths aren't matched between runs, which is why the bands
with few requests are not worth leaning on. A fixed prompt set avoids this, see [SETUP-LLM-TIMING.md](SETUP-LLM-TIMING.md).

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
**Update 2026-11-16:** with the NVLink bridge fitted the same engine logs `Using ['CUSTOM', 'PYNCCL'] all-reduce backends` and no longer warns, and decodes about 13% faster than with custom all-reduce and P2P forced off ( [SETUP-LLM-TIMING.md](SETUP-LLM-TIMING.md#sonar-nvlink-on-against-off-2026-11-16), section 19 ). The figures in this section are from before the bridge.

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

### 13. sglang ( MTP, two GPUs, GPTQ 4-bit )

Measured 2026-11-14 11:59-12:08 UTC, the first test requests and then one agent session after the MTP fix below, on sglang 0.5.23 ( the `v0.5.23` release image, CUDA 13.0 ). The weights are `palmfuture/Qwen3.8-27B-GPTQ-Int4` ( 4-bit, group size 32 ) on **both** 3090s with tensor parallelism,
an fp8 KV cache, 262144 context and the 8192 thinking cap ( `--enable-strict-thinking` ). Speculation is MTP through EAGLE, 3 steps and 4 draft tokens, with `--enable-linear-replayssm-spec` and `--mamba-ssm-dtype=float32`.

**MTP only worked after patching the quantization config.** As published, the checkpoint excludes its MTP head from quantization with the rule `-:.*\.mtp\..*`, which needs a dot before `mtp`, but sglang names the draft layers `mtp.layers.N...`. So the rule missed them, the head was built as 4-bit GPTQ layers, seven of its weights had nowhere to load, and nothing it drafted was accepted
( `accept len 1.00`, ~40 tok/sec ). The registry's `--json-model-override-args` replaces the quantization config with a copy that has one more rule, `-:(?:.*\.)?mtp\..*`. That took the "not found in params_dict" warnings from 7 to 0 and decode from ~40 to 87-118 tok/sec on the first requests.
The head's `Load weight end` line still says `quant=gptq` after the fix, so it is not a usable check; the warnings and `accept len` are.

Results are sglang's own `gen throughput` from its once-a-second `Decode batch` log lines, rebuilt into 31 generations ( `python bin/llm.py speed sglang --since 2h --min-out 100` ), prompts growing from 4.1k to 67.5k tokens. All 480 decode lines had one request running, so no batching is mixed in.

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out |
|---|---|---|---|---|
| < 15k | 4 | 100.3 | 88.5-117.6 | 236-8,230 |
| 15-45k | 11 | 97.5 | 78.1-143.9 | 196-11,067 |
| 45-65k | 13 | 92.8 | 73.5-138.8 | 202-3,263 |
| > 65k | 3 | 102.1 | 74.1-115.5 | 289-982 |
| all | 31 | **94.6** | 73.5-143.9 | |

* **Other cuts of the same data.** Weighting by decode line instead of by generation gives a median of 99.0 ( mean 102.6 ), because long generations count for more lines. With only the requests of 1,000+ tokens out the bands are 90.3 ( < 15k, n=2 ), 122.9 ( 15-45k, n=4 ) and 91.0 ( 45-65k, n=6 ), too few to compare.
* **Speed follows acceptance.** Over the 480 decode lines accepted length averaged **2.89** tokens per step ( range 1.68-4.00, out of a maximum of 4 ) and the accept rate 63% ( 23-100% ); the server's own `avg_spec_accept_length` agrees at 2.89. Averaged per generation instead they are 2.98 and 66%.
  The three fastest generations ( 130-144 tok/sec ) had 89-97% acceptance and the three slowest ( 73.5-78.1 ) had 52-57%.
* **Prefix cache:** 97.5% of prompt tokens were cached ( 1,966,208 of 2,017,055 over 54 prefill batches ). Only the two large first prompts ( 11.9k and 20.2k tokens ) were cold. The server's counters give a mean time to first token of 0.84 s over 48 requests since the pod started, which includes the test requests.
* **No errors.** The sglang log has no 5xx apart from `/health` returning 503 while it started. The litellm 500s seen earlier were from the pod being unready under a 1-second probe timeout, which `timeoutSeconds: 5` fixed.
* **Memory:** the GPUs were at 23.2 and 22.9 of 24 GiB, 1.65 GB per GPU was free after startup, and the KV pool held 268,377 tokens at fp8 ( one full 262144 request ). `max_running_requests` is about 8, capped by the Mamba state cache.
* **Against the others.** The session median of 94.6 is about 7-12% below ninfer-all ( ~102-108, sections 10-12 ) and about 10% below tabbyapi Swift on two GPUs ( ~105, section 9 ). I can't say the gap is real: this is 31 requests in about ten minutes against 57-63 per session, the sessions are different agent work,
  and the weights ( GPTQ, EXL3, `.ninfer` ), the GPU count ( two against ninfer's one ) and the speculation setup all differ.
* **What may limit it, untested:** the log says custom all-reduce is off because the 3090s lack peer access, so tensor-parallel traffic goes over PCIe; the Gated DeltaNet kernels run through Triton; and the draft length is fixed at 3 steps while the average accepted is 2.89 of 4. A static `--speculative-num-steps=2` and `--speculative-adaptive` haven't been tried.
  **Update 2026-11-16:** an NVLink bridge has since been fitted to the 3090s, and the all-reduce no longer goes over PCIe. Measured on and off in [SETUP-LLM-TIMING.md](SETUP-LLM-TIMING.md#sglang-nvlink-on-against-off-2026-11-16): about 16% faster decode with it on ( median 112.0 against 96.2 tok/sec ), one run each. The figures in this section are from before the bridge.
* **Not measured:** output quality, langfuse end-to-end figures, per-request time to first token, and whether the 8192 thinking cap is actually enforced. The generation lengths are estimated from how far the context grew, and the times are UTC.

### 14. mistralrs ( MTP, two GPUs, q4k quantized at load )

Measured 2026-11-14 14:02-14:12 UTC, a few test prompts from pi and one long generation, on mistral.rs 0.9.5 ( the `cuda128-sm86-latest` image, CUDA 12.8 on a driver that supports 13.0, FlashAttention v2 ). The weights are the original `Qwen/Qwen3.8-27B` BF16 files, quantized to **`q4k`** ( sensitive tensors at `q6k` )
every time the server starts, over **both** 3090s with tensor parallelism ( NCCL, picked by mistralrs itself ). Speculation is the model's built-in MTP head ( `--mtp` ), with an fp8 KV cache, `--max-seqs=2` and a 65,536-token `--max-model-len`. This is **9 requests, not a session**: nothing like the 31 in section 13.

**Getting it to start took three changes.**
- **The download went to the wrong disk.** mistralrs ignores `HF_HOME` and wrote the 52 GB of BF16 weights to `/root/.cache` on the container's own disk, where the 30-minute startup probe was about to kill the container and throw it away. `--hf-cache=/data/hub` puts it on the PVC, the probes allow 2 hours, and 21 GB already downloaded were copied across by hand.
- **`runtime reservations need 36131 MiB ... only 4904 MiB remains usable`.** The model loaded, but a reservation about twice the size of a card didn't fit. Cutting the context from 262,144 to 65,536 moved it by 0.8% ( 36,131 to 35,856 MiB ), so it wasn't the KV cache. The log line before it was `Recurrent state pool reserved capacity=33`, which is `--max-seqs`' default of 32 for `serve` plus one,
  and `--max-seqs=2` brought it to `capacity=9` and the start succeeded. I take it that the Gated DeltaNet layers reserve recurrent state per concurrent sequence, which is the same kind of limit as sglang's Mamba cache ( section 13 ), but I haven't confirmed it from the source.
- **The KV cache is small:** 1,062 MiB per GPU, 63,840 tokens at fp8. The log shows about 9 GiB free on each card afterwards, so it could be bigger ( `--gpu-memory-utilization`, or a fixed `--kv-cache` ), but that isn't tried.

Results are mistralrs's own figures from its `request completed` log line ( `python bin/llm.py speed mistralrs --since 2h --min-out 100` ). TTFT is estimated as prompt tokens over prefill speed, and accept length and rate are means over the engine's 5-second log lines during each request.

| Prompt tokens | n | Decode tok/sec | Prefill tok/sec | Accepted length | Tokens out |
|---|---|---|---|---|---|
| 1.5k-3.2k | 5 | 36.8-43.1 | 1,512-1,554 | 2.4-2.75 ( 59-79% ) | 90-299 |
| 3.5k | 1 | 36.5 | 1,545 | 2.35 ( 67% ) | 84 |
| 4.8k-5.5k | 3 | 18.0-29.1 | 1,533-1,536 | 2.0-2.5 ( 49-72% ) | 104-6,850 |
| all ( >= 100 tokens out, n=7 ) | 7 | median **36.8** | | | |

The rows are by prompt size and include the three requests with fewer than 100 tokens out, so the last row's median covers a different set; the 7 that have 100 or more are the ones the rows above it don't separate.

* **Prefill is fine:** a steady 1,510-1,550 tok/sec, so 60k uncached tokens would take about 40 seconds.
* **Decode is slow and gets slower:** 43 tok/sec at 1.5k tokens, 29 at 5k, and **18.0 for the one long generation** ( 5.5k prompt, 6,850 tokens out, 6.3 minutes ). The slowdown with prompt size is large for such short contexts, and I can't explain it.
  With the long reply the acceptance also fell to 2.0 accepted per step ( 49% ), against 2.4-2.75 on the short ones, the same pattern as the other engines on long outputs.
* **Against the others.** The median of 36.8 is about 39% of sglang's 94.6 ( section 13 ) and about 34-36% of ninfer-all's ~102-108, but the prompts are 5k tokens against 4-67k, so this isn't like-for-like. sglang's first, unpatched run decoded at about 40 tok/sec with no speculation working at all, which is the nearest comparison for these short ones.
* **The prefix cache never hits** ( 0% on every request ). The docs say that with the built-in MTP head, cached prompt prefixes are recomputed. sglang cached 97.5% of prompt tokens in section 13 and ninfer-all 90%+, so on agent work, where every turn re-sends a long prompt, this costs a full prefill every time: about 3 seconds at 5k tokens, but about 40 seconds at 60k.
* **One hang.** After a request was cancelled mid-generation, the next request was accepted and never ran: one GPU sat at 100% utilisation, the other at 0%, nothing was running or waiting, and the engine logged nothing for 4 minutes while `/health` still answered. The pod was restarted ( I didn't see by whom ), and the next request completed normally.
  My guess is that the cancel left the two tensor-parallel ranks out of step; I didn't see it happen without a cancel, and it isn't reproduced.
* **Not measured:** output quality, tool calls and thinking through litellm and pi, langfuse end-to-end figures, whether the model needs a thinking cap ( there is none, and no tool-call or reasoning parser flag ), and any session longer than 9 requests.

**What to try next**, cheapest first. Each is one restart of about 3 minutes ( the re-quantisation ), and none has been tried:
0. **The CPU limit.** mistralrs's pod was limited to 8 CPUs ( `mistralrs_cpu_limit` ) and was throttled in 12.6% of CPU periods over 24 hours, against 2.8% for ninfer and 0.2% for sglang ( Prometheus, `container_cpu_cfs_throttled_periods_total` ), and its server process
   was using 222-331% CPU. The role default is now **16**, which is applied on the next deploy and hasn't been run. After a session, compare the throttled share again and `speed mistralrs`. It is a restart, but no re-download.
   strata's pod has no CPU limit at all, and peaked at 22.3 cores in the hour I measured, so this isn't what limits it.
1. **`--mtp-n-predict=3`.** Left alone it picks 2, 3, 4 or 6 as it runs and mostly chose 2; sglang ran a fixed 3 steps and accepted 2.9. This removes the adaptive logic.
2. **Drop `--pa-cache-type=f8e4m3`**, and raise `--gpu-memory-utilization` to 0.95 to make up the cache. The warning says the fp8 scales are uncalibrated, and the slowdown with prompt length points at attention. This is my best guess for that slowdown, and only a guess.
3. **`--mtp` off, as a control.** It shows what speculation is worth here, and it brings the prefix cache back, which may matter more than decode speed for agent work. Decode would probably fall; whether the cached prompts make up for it is the thing to find out.
4. **Layer mapping instead of tensor parallelism**, `-n "0:32;1:32"`. NCCL all-reduce goes through host memory on these cards, and a pipeline split avoids it, and might also avoid the hang. It usually decodes slower per token on one stream, so it is a trade, not a fix.
5. **A bigger KV cache** ( `--gpu-memory-utilization 0.95`, or `--kv-cache 6GiB` ): about 136k tokens by my estimate, and the 63,840 now is too small for a real agent session ( sglang's reached 67k, ninfer-all's 104k ). That is about fitting, not speed.
6. **Save the quantized model as UQFF** ( `mistralrs quantize -m Qwen/Qwen3.8-27B --isq q4k -o <dir>`, then serve it with `--from-uqff` ) so a restart skips the 3 minutes of quantizing. Convenience only.
7. **A different quant.** I expect most of the gap to sglang to be the `q4k` kernels against GPTQ's Marlin ones, and none of 1-6 changes that, so I'd expect them to give tens of percent, not 2x. I haven't checked whether mistralrs has a Marlin-style 4-bit path.

### 15. strata ( Qwen3.8-Flash-Next 125B, IQ3_XXS, two GPUs )

Measured 2026-11-15, one pi agent session of 63 requests with prompts growing from 53 to 88,939 tokens, on Strata 0.2.13 ( engine 0.1.59, the `0.2.13-cuda13` image ). **This is a different model from sections 1-14**: Qwen3.8-Flash-Next is a 125-billion-parameter mixture of experts,
run from ISTA-DASLab's IQ3_XXS GGUF ( 76 GB ), with the experts held mostly in system RAM ( the engine pins 26.8 GiB ) and the dense layers and hottest experts on the GPUs as a layer split across both 3090s. Speculation is MTP; the plan asked for DFlash2 and the log said `Drafting: MTP (DFlash2 isn't available in this mode)`.
Context was 128k ( 131,072 ) for this session ( it has since been raised to 256k, see [SETUP-LLM-BE-STRATA.md](SETUP-LLM-BE-STRATA.md#what-was-found-running-it-2026-11-15) ), pictures are on. So the figures below are not a like-for-like comparison with the 27B engines; they say what this setup does, not that strata is faster.

Results are strata's own per-request figures from its engine log ( `python bin/llm.py speed strata --min-out 100` ). That log is a file on the PVC and its lines have no timestamps, so the requests are in file order. TTFT is estimated as the time to read the uncached part of the prompt.

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out |
|---|---|---|---|---|
| < 15k | 7 | 170.3 | 127.3-176.8 | 109-6,144 |
| 15-45k | 7 | 162.8 | 150.1-168.2 | 1,628-16,384 |
| 45-65k | 16 | 148.5 | 128.8-166.1 | 105-2,510 |
| > 65k | 24 | 158.7 | 131.9-178.3 | 155-2,447 |
| all ( >= 100 tokens out ) | 54 | **158.6** | 127.3-178.3 | |

* **Decode barely depends on context here.** The bands stay between 148.5 and 170.3, a spread of about 15%, from under 15k to 89k tokens, unlike mistralrs ( section 14 ), where decode fell from 43 to 18 tok/sec over 1.5k-5.5k. The longest generation, 16,384 tokens, ran at 150.1 tok/sec. With only 1,000+ tokens out the bands are 139.9, 162.8, 145.3 and 144.4 ( n = 2, 7, 5, 4 ), a little lower, as the long replies are slower.
* **Draft acceptance** averaged 72% ( median 73%, range 52-84% ) over those 54 requests, and was lowest on the longest replies ( 52% on a 6,144-token one ), the same pattern as the other engines.
* **Prefix reuse is the standout.** 98.1% of all prompt tokens were reused from its conversation cache ( 3.22 million of 3.28 million, weighted by tokens ), and the median prompt of 2k+ tokens was 100% reused ( 59 requests ). The first token took a median 0.5 s, 1.3 s at the 90th percentile, 7.5 s at worst.
  Only 3 of those 59 were cold: 2,100 tokens ( 1.7 s ), 15,895 ( 5.8 s ) and 24,106 ( 7.5 s ), reading at 1,256, 2,764 and 3,218 tok/sec. Prometheus's `vllm:prefix_cache_hits_total` agreed: 97.9%.
* **Expert cache:** 95.1-99.6% hits ( median 99.1% over 63 lines ), and 96.3-99.9% of KV block reads were served from VRAM ( median 99.3% ), with the rest read from RAM. That is the thing this engine is built around, and it held up at 89k tokens.
* **Against the others.** The median of 158.6 tok/sec is 1.7x sglang's 94.6 ( section 13 ) and 1.5-1.6x ninfer-all's ~102-108, on a model with far more parameters. I think that is because only a few of its experts run per token, so it reads little per token, and the speculation is on top, but I haven't tested that.
  What I can say is that the comparison is between different models, different quants ( IQ3_XXS against GPTQ, EXL3 and `.ninfer` ) and different sessions, each on one run. Whether the 125B model's answers are better, I haven't looked at.
* **Memory is nearly full.** Both GPUs sat at about 97% ( 23.4 of 24 GiB ) and host RAM at 83-89 of 94 GiB with the model idle, so nothing else can run beside it, and a longer prompt or a second request could run out. I saw no out-of-memory error.
* **First start:** the 76.7 GB downloaded in 30 minutes at about 42 MB/s, and the pod went from created to serving in about 37 minutes, with 87 seconds of that loading the engine ( a restart is Ready in 2 minutes ). The pod had no restarts overnight. Memory locking worked with only the `IPC_LOCK` capability, with no ulimit change.
* **Later runs ( 2026-11-15 ).** The context was raised to 192k and then 256k, and the same engine log grew to 183 pi requests with MTP ( my synthetic long-prompt tests and the 53-token warm-ups left out ): median decode **149.8 tok/sec** over the 172 with 100+ tokens out ( range 122-198; the first session's 158.4 was a little higher ), draft acceptance 71%, 98.3% of prompt tokens reused, prompts up to 102,583 tokens.
  After fetching Strata's DFlash2 drafter and restarting, 86 more requests ( 76 with 100+ out ) gave a median **149.2 tok/sec** ( range 114-204 ), 141.1 on the 22 replies of 1,000+ tokens against 158.1 for MTP's 57, 61% acceptance ( windows of up to 8 tokens ) and the same 98.4% reuse: no faster than MTP in an agent session, and it takes one request at a time.
  Back on MTP ( IQ3_XXS, 64 requests, 46 with 100+ out ) the median was 140.7, with reuse down to 93.6% because Strata had sized its conversation cache at 1,024 MiB on that start.
  The smaller Q2_0 size is in section 16 and the larger IQ3_S ( which only starts with `ple-io=direct` ) in section 17.
  Single runs of different tasks each, so the differences are within noise except possibly the long replies. Details in [SETUP-LLM-BE-STRATA.md](SETUP-LLM-BE-STRATA.md#what-was-found-running-it-2026-11-15).
* **Not measured:** output quality, tool calls and thinking through litellm and pi ( the thinking cap went with each request, and I only tested that it works on a direct one ), langfuse end-to-end figures, and a comparison on the same prompts as the other engines.

### 16. strata ( Qwen3.8-Flash-Next 125B, Q2_0, two GPUs )

Measured 2026-11-15, one pi agent session of 45 requests ( 38 with 100+ tokens out; the 53-token warm-up left out ) with prompts of 2,100 to 63,267 tokens, then `bin/llm_util.py longprompt`, on the same Strata 0.2.13 as section 15 with **context 256k ( 262,144 )**, pictures on and MTP drafting.
Only the quant changed from section 15: `Q2_0` is ISTA-DASLab's smallest size of the same 125B model ( 37.6 GB of RAM and VRAM for the model against 47.0 GB for IQ3_XXS, and Strata's docs rate it "good" against "great" ), and 99% of its experts are resident in VRAM against 77% for IQ3_XXS with MTP. So this is the same model at lower precision, not another model;
the sessions were different tasks, one run each.

| Prompt tokens | n | Median decode tok/sec | Range |
|---|---|---|---|
| < 15k | 5 | 155.7 | 137.9-182.0 |
| 15-45k | 12 | 163.7 | 138.3-200.2 |
| 45-65k | 21 | 167.6 | 147.5-222.8 |
| all ( >= 100 tokens out ) | 38 | **166.2** | 137.9-222.8 |

* **Against IQ3_XXS with MTP ( section 15, 172 requests ):** 166.2 against 149.8 tok/sec overall, 11% higher ( p10-p90 146-195 against 131-170 ). By prompt size the lead is only in the 45-65k band ( 167.6 against 148.5 ); under 45k the two are level or Q2_0 is lower ( 155.7 against 163.2, 163.7 against 165.3 ), and I have no Q2_0 data over 65k. Replies of 1,000+ tokens: 165.6 ( n = 16 ) against 158.1 ( n = 57 ); the longest reply was 11,835 tokens at 194.9 tok/sec.
  Against the second IQ3_XXS MTP session ( 140.7 median, 64 requests ) the lead is 18%, which shows how far a session's task alone moves the figure.
* **Draft acceptance** 73% mean ( range 54-94% ), against 71% for IQ3_XXS with MTP.
* **Conversation cache:** 96.9% of prompt tokens reused ( 1.80 of 1.86 million ), median first token 0.38 s ( p90 1.5 s, 6.6 s at worst ). Strata had sized the cache at 4,352 MiB from the free RAM at start-up, and it never logged `skip parking`.
* **Long prompts:** one request each of 7,985, 189,289 and 249,031 tokens ( the last 72% cached ), a passphrase on the first line and the question at the end. All three answered correctly in 3.8, 43.8 and 22.6 s, the engine's read speed was 2,282, 4,378 and 3,122 tok/sec, decode 167.0, 151.7 and 137.2 tok/sec, with no pod restart and no out-of-memory, "too long" or truncation lines.
  The same prompts on IQ3_XXS ( 189,083 and 252,093 tokens, 256k ) decoded at 121.3 and 135.7 tok/sec with MTP and at 139.6 and 138.3 with DFlash2 ( 113.0 at 8,493 tokens, DFlash2 only ), and read the cold 189k prompt in 47.9 s with MTP and 41.2 s with DFlash2; Q2_0 took 43.8 s. It is filler text and a 1-word answer, so it shows the 256k KV and the prompt path fit, not quality at depth, a long reply after a 250k prompt, or two requests together.
* **Download and start:** 37.6 GB ( its second shard is the same file as IQ3_XXS's, so the 28.8 GB was already on the volume ) in 13 min 14 s at 47 MB/s, pod created to serving in 15 min, with 75 s of loading the engine.
* **Memory:** GPUs 23.5 and 23.7 GiB with the model idle, host RAM 8.3 GiB available, 26.8 GiB locked as before. Q2_0 isn't smaller in what it takes: the engine sizes its expert cache to the free VRAM, so a smaller model keeps more of its experts on the cards ( 12,288 slots against 7,107 ) and nothing else could run beside it.
* **Not measured:** output quality ( no comparison of Q2_0's answers with IQ3_XXS's ), prompts over 65k in a real session, tool calls through litellm and pi, and DFlash2 on Q2_0.

### 17. strata ( Qwen3.8-Flash-Next 125B, IQ3_S, two GPUs, PLE table read from disk )

Measured 2026-11-15, one pi agent session of 59 requests ( 56 with 100+ tokens out; the 53-token warm-up and my test prompts left out ) with prompts of 2,100 to 122,347 tokens, then `bin/llm_util.py longprompt`, on the same Strata 0.2.13 as sections 15 and 16, **context 256k ( 262,144 )**, pictures on and MTP drafting.
`IQ3_S` is ISTA-DASLab's largest size of the same 125B model ( 54.8 GB of RAM and VRAM for the model against 47.0 for IQ3_XXS and 37.6 for Q2_0; Strata's docs rate it "best, matches the full model on the published tests" ). **It only starts with `ple-io=direct`**, which reads the PLE table from the SSD instead of locking its 26.8 GiB in RAM: without it the node's OOM killer killed the engine 70 seconds into every load ( "Things that did not work" ).
So every figure below has the setting, the larger model and a different task changed together, and no run separates them. This is the same model at higher precision, not another model; the session was a different task from the other two ( the agent built on the results of an earlier IQ3_S test ).

| Prompt tokens | n | Median decode tok/sec | Range | Tokens out |
|---|---|---|---|---|
| < 15k | 3 | 138.6 | 129.8-143.9 | 103-167 |
| 15-45k | 17 | 134.8 | 100.3-156.8 | 110-8,194 |
| 45-65k | 6 | 150.2 | 108.3-158.8 | 178-16,384 |
| > 65k | 30 | 126.4 | 107.9-150.9 | 104-6,512 |
| all ( >= 100 tokens out ) | 56 | **134.0** | 100-159 | |

* **Against IQ3_XXS with MTP ( section 15 ) and Q2_0 ( section 16 ):** 134.0 against 149.8 and 166.2 tok/sec overall, so 10.5% and 19.4% lower ( p10-p90 113-152 against 131-170 and 146-195 ). It is lower in the 15-45k band ( 134.8 against 165.3 and 163.7 ) and above 65k ( 126.4 against 144.8; Q2_0 has no data there ). The 45-65k band ( 150.2 against 148.5 and 167.6 ) has only 6 requests. Replies of 1,000+ tokens: 134.1 ( n = 26 ) against 158.1 and 165.6; the longest was 16,384 tokens at 152.5 tok/sec, the same length as IQ3_XXS's ( 150.1 ).
* **Draft acceptance** 72% mean ( median 73%, range 53-89% ), the same as the other two ( 71% and 73% ).
* **Conversation cache:** 97.5% of prompt tokens reused ( 4.05 of 4.15 million ), against 98.3% and 96.9%; no `skip parking` line. **First token** a median 0.71 s ( p90 2.65 s, 8.7 s at worst ) against 0.45 s and 0.38 s. The three cold prompts over 5k tokens took 6.0 s ( 16.5k tokens, 25% cached ), 8.7 s ( 26.4k ) and 8.6 s ( 27.5k ), reading at 2,065 to 3,187 tok/sec, against 5.9 s and 5.8 s medians for the cold prompts of the other two ( 6 and 2 of them ).
* **Why it may be slower, three guesses I haven't separated:** a bigger model with fewer of its experts in VRAM ( 6,109 expert slots against 7,107 for IQ3_XXS and 12,288 for Q2_0, because its arena takes 46.9 GiB of RAM ), the table read from disk, and the task. The disk reads probed at 59,611 rows/sec at start-up, so I don't think they stall; the cold-prompt times are the only figure that would show their cost, and 3 requests is too few.
  One run would separate the first two: IQ3_XXS ( which starts both ways ) with `ple-io=direct` through a session like the 149.8 one. I haven't done it.
* **Long prompts:** one request each of 7,985, 189,289 and 249,031 tokens ( the last 72% cached ). All three answered correctly in 4.2, 48.1 and 25.1 s; the engine's read speed was 2,171, 3,991 and 2,819 tok/sec and decode 150.5, 135.4 and 136.3 tok/sec, with no pod restart and no out-of-memory, "too long", stall or truncation lines. The cold 189k read took 48.1 s against 43.7 s ( Q2_0 ) and 47.9 s ( IQ3_XXS with MTP ).
* **Download and start:** 54.8 GB for its own shard ( the 28.8 GB second shard is the same shared file again, which is why Strata's plan said 83.6 GB ) in 19 min 39 s at 46.5 MB/s, about 30 s of prep, and then **2 min 00 s** from pod created to Ready once the files were there, with 835 MiB of VRAM for the MTP draft layer.
* **Memory:** GPUs 22.9 and 23.0 GiB ( 23,451 and 23,575 MiB ) with the model idle; host RAM 70.3 GiB used and 26.1 GiB available right after the start, **76.2 and 20.3 after the long prompts, and 78.9 and 17.6 after the pi session**, with nothing locked ( `VmLck` 0 kB ), against about 60 GiB used for Q2_0 with the same setting. It fits with about 17 GiB to spare, but the figure fell through the session and I watched it for only about 35 minutes.
* **Not measured:** output quality ( Strata rates IQ3_S above the other two; I haven't compared answers ), whether `ple-io=direct` itself costs speed on this task, tool calls through litellm and pi, DFlash2 on IQ3_S, and a longer session's memory use.

### 18. Unsloth quants ( UD-Q2_K_XL measured in a pi session; the rest is reading )

Written 2026-11-15 from reading to decide what to test, then **one Unsloth file, UD-Q2_K_XL, was downloaded and run** ( results in the bullet starting "UD-Q2_K_XL was run" below ); nothing else from Unsloth has been. The reading is from a web search and Strata's own docs ( [MODELS.md](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md), [UNSLOTH_Q4.md](https://github.com/Niko1221/Strata/blob/main/docs/UNSLOTH_Q4.md) ); I haven't checked these against a second source unless it says so.

* **What they are.** Unsloth's "Dynamic 2.0" GGUFs choose a quant type for each layer instead of one for the whole model, calibrated on chat data ( [docs](https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs) ). Their page makes size and accuracy claims ( on Gemma 3 27B, Q4_K_XL scored 71.47 MMLU at 15.6 GB against 70.64 for Google's QAT at 17.2 GB ), **not speed claims**. Those numbers are Unsloth's own.
* **There is a newer version: Dynamic 3.0, and 3.1 ( Nov 2026 ).** From its [page](https://unsloth.ai/docs/basics/dynamic-3.0-ggufs), which is Unsloth's own and which I read as text only ( its charts aren't in what I got ): a better calibration set ( agentic coding, chat, multilingual ), "improved layer selection" and "many more quantization techniques", post-training only ( no QAT ), with the same `UD-` names. It says 3.1 is the latest algorithm.
  **Claims, not measurements here:** for Qwen3.8-27B, ">10% top-1 better accuracy at the same size" than other providers; `UD-Q2_K_XL` 9.83 GB and "about 8% more accurate on top-1 than the next best"; `UD-IQ1_S` 6.2 GB keeping "around 72% top-1". Like the 2.0 page, it makes **no speed claim**; its one "50% less token usage" figure for 3.1 is about tokens consumed, not tokens per second.
  It says the MTP module was removed from `UD-Q2_K_XL` and smaller files ( about 500 MB saved ) and is available as a separate `Q4_0` file. It says 3.0 files work with "most inference engines including llama.cpp", but doesn't say whether a build with Unsloth's changes is needed. For Flash-Next it names only a 1-bit 3.1 quant ( 54.0% on DeepSWE v1.1 and 68.3% on Terminal Bench 2.1 ).
  **What I can't tell:** which files in Unsloth's repos are 2.0, 3.0 or 3.1; the file listing doesn't say. That matters for "which Unsloth quant is quickest", because the 2.0 and 3.0 claims are different claims.
* **"Quick" is not shown, for 2.0 or 3.x.** The one direct comparison I found ( on 2.0 files ), a Qwen3.6-35B test on one machine ( [source](https://github.com/nabe2030/qwen36-evo-x2) ), had bartowski's Q4_K_M faster ( 67.5 against 60.9 tok/s ) and smaller. One independent KL test ( on a Japanese corpus, by someone who made their own quants ) had UD-Q4_K_XL at 0.0102 against 0.0162 for Q4_K_M ( [source](https://huggingface.co/utautako/Qwen3.6-35B-A3B-JA-GGUF) ).
  My reading, not a measurement: the value is quality per GB, plus MTP draft heads that ship with the files, not decode speed. The speed gain people report with Unsloth's Flash-Next files is more likely the MTP head: one DGX Spark test had 47.1 against 24.5 tok/s on code but 24.2 against 23.0 on prose, with Unsloth's own llama.cpp branch ( [source](https://github.com/Weschera/Qwen3.8-Flash-Next-1x-DGX-Spark) ).
* **What exists for our models.**
  * **Flash-Next 125B** ( `family: unsloth` in Strata ). Strata's docs list five sizes: UD-IQ4_XS 94 GB ( 88.9% same top token as Q8_0, about 72 GB of RAM for the experts ), UD-Q4_K_XL 111 GB ( 93.1%, about 88 GB ), UD-Q5_K_XL 158 GB, UD-Q6_K_XL 169 GB and Q8_0, against 87.7% for ISTA-DASLab's IQ3_S. Strata measured no Unsloth speeds on NVIDIA 3090s with this much RAM; the closest is 45 tok/s on a 3090 plus a 3080 Ti with 91 GB of RAM, quant not stated.
    **Unsloth's [repo](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) has more, and smaller, sizes than that**, by the file sizes the Hugging Face API gives ( each includes the shards that hold the embedding tables, so these are totals to download ): UD-IQ1_S 66.4 GB, UD-IQ1_M 68.0, UD-Q2_0 70.5, UD-IQ2_XS 75.8, UD-Q2_K_XL 79.6, UD-IQ3_XXS 83.6, UD-Q3_K_XL 89.5, UD-IQ4_XS 93.7, UD-Q4_K_XL 111.3, UD-Q5_K_XL 158.3, UD-Q6_K_XL 169.2, Q8_0 188.2, BF16 354.0, plus a separate Q8_0 MTP file ( 4.1 GB ) and an `MTP` folder ( 24.6 GB, 6 files ).
    **Strata's own list ( checked 2026-11-15 with `strata launch --plan --family unsloth --model X`, which only reports ):** UD-IQ4_XS, UD-Q4_K_XL, UD-Q5_K_XL, UD-Q6_K_XL, Q8_0, UD-IQ1_M, UD-Q2_0, UD-IQ2_XS, UD-Q2_K_XL, UD-IQ3_XXS, UD-Q3_K_XL and **UD-IQ4_XS-v31**. So it takes the extra sizes, but **not UD-IQ1_S**: that name gets "has no size UD-IQ1_S".
    Its plan download sizes match the repo's ( UD-IQ1_M 68.0 GB, UD-Q2_0 70.5, UD-IQ2_XS 75.8, UD-Q2_K_XL 79.6, UD-IQ3_XXS 83.6, UD-Q3_K_XL 89.5, UD-IQ4_XS 93.7, UD-Q4_K_XL 111.3 ). The `-v31` size gives the same 93.7 GB; I don't know what differs from UD-IQ4_XS, or whether it is Unsloth's 3.1 build, and the repo listing I pulled has no such name. Every plan also said `drafting DFlash2` until given `--drafting mtp`, which it accepted.
  **Checks on UD-Q2_K_XL before downloading it ( 2026-11-15 )**, from the GGUF headers in Unsloth's repo ( read with HTTP range requests, about 6 MB per shard, nothing downloaded ) and Strata's plan:
  * **Layout:** four shards of 0.01, 49.73, 1.04 and 28.80 GB ( 79.6 GB ). Shard 4 holds one tensor, `per_layer_token_embd.weight`, IQ4_NL, 160 x 320,001,536, which is **28,800,138,432 bytes: the same as the PLE shard already on the volume**, and 160 IQ4_NL values are 90 bytes a row, the row size Strata's help gives. So it is almost certainly the same table, and `ple-io=direct` should read it the same way; the match in size and shape is the evidence, I haven't compared contents.
    Shard 3 ( the odd 1.04 GB one ) is just the last layer, block 47, with 20 tensors. The earlier question of what it was is answered.
  * **K-quants:** that last layer's routed experts are **IQ2_S** ( gate and up ) and **IQ4_NL** ( down ), not K-quants, and its shared experts are Q8_0. Strata's engine log says a model "with K-quant experts ( Unsloth UD-Q4/Q5/Q6_K_XL )" reads prompts on llama.cpp's MMQ kernels; **from this layer, UD-Q2_K_XL probably isn't one of those**, but I read one layer of 48 ( shard 2, 49.7 GB with 1,203 tensors, I didn't parse ), so the other layers may differ.
  * **MTP:** Strata's `mtp` folder holds the draft layer ( 31 tensors, 5.2 GB ) cut from the **original BF16 checkpoint** ( the manifest lists `model-000NN-of-00131.safetensors` shards ), not from any GGUF, and it is shared by every size. So it doesn't depend on which quant is loaded. The plan for UD-Q2_K_XL with `--drafting mtp` listed only the model as a download ( 79.6 GB ) and said `drafting MTP`. That is good evidence, not a test; Strata's docs only say the MTP is "chosen by the file" for Qwen3.6, not Qwen3.8.
  * **Download:** the plan says 79.6 GB. For Q2_0 it said 66.4 GB and fetched 37.6 GB, because Strata hard-linked the PLE shard from the ISTA-DASLab repo it was already in. Unsloth's copy comes from another repo, so Strata may fetch it again: 50.8 GB if it links it, 79.6 GB if not ( 17 or 28 minutes at 47 MB/s ). The volume has 454 GB free either way.
  * **Memory estimate** ( unchanged: about 43 GiB of experts from the arena-to-file-size ratio of the three ISTA-DASLab sizes, 0.90-0.92 ): with the PLE table in RAM about 70 GiB, 74% of what the engine sees, between IQ3_XXS ( fit, 71% ) and IQ3_S ( killed, 78% ); with `ple-io=direct` about 43 GiB, 46%. **Still an estimate**: the ratio comes from three files of a different quantizer, and the other layers' quant types are unknown.
  **What happened when it was run ( same day ):** it started, served and answered long prompts. Details below the list.
  * **Not checkable without running it ( before the run ):** whether `ple-io=direct` is accepted with an Unsloth file ( `launch --plan` accepts anything, as it did `drafting=mtp` ), what the engine does with the expert quant types, and the speed. The registry has `flash-next-unsloth-ud-q2-k-xl`, with `ple-io=direct`, at 256k.
  * **Qwen3.8-27B** ( [repo](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF), 30 files ): UD-IQ1_S 6.2 GB, UD-IQ2_XXS 7.2, UD-Q2_K_XL 9.9, UD-IQ3_XXS 10.9, UD-Q3_K_XL 13.1, UD-IQ4_XS 14.4, UD-Q4_K_S 15.4, plain Q4_0 16.1, UD-Q4_K_M 16.6, UD-Q4_K_XL 17.7, UD-Q5_K_XL 21.0, UD-Q6_K_XL 25.3, UD-Q8_K_XL 31.5, Q8_0 29.0, BF16 54.7 GB, and a separate 1.4 GB MTP file. For comparison the EXL3 5.00bpw quant the other engines run is about 19.9 GB, so UD-Q4_K_XL ( 17.7 GB ) and UD-Q5_K_XL ( 21 GB ) bracket it.
* **Which engines.** **Strata** reads the Flash-Next Unsloth sizes already, so it is the least work; the registry has a `flash-next-unsloth-ud-iq4-xs` entry, untried. The 27B files would need llama.cpp or ik_llama.cpp ( reported to load them, ik_llama.cpp with MTP per one blog; single sources, unverified ), which this repo has no role for; I didn't check Ollama's or sglang's GGUF support for Qwen3.8.
* **The main risk is RAM, and it has already bitten.** UD-IQ4_XS needs about 72 GB for its experts, more than ISTA-DASLab's IQ3_S, which was OOM-killed on this node ( see "Things that did not work" ). Strata falls back to reading experts from the SSD when RAM is short, which Strata's docs say is much slower, so even a start that works may not be quick.
  **A lower-risk first Unsloth test** is the smallest size Strata offers, **UD-IQ1_M ( 68.0 GB )**; UD-IQ1_S, which I'd suggested, isn't offered. It is about Q2_0's size ( 66.4 GB with the shared shard ), so it is the one most likely to start here: from the file sizes alone I'd guess about 35 GB of experts, between Q2_0's 31.6 GiB arena and IQ3_XXS's 40 GiB, both of which start with the PLE table in RAM. That is an estimate; Strata's plan prints no arena size.
  It is also near the bottom for quality, so it says little about whether Unsloth beats ISTA-DASLab at a size where the choice matters; the fairer pair is an Unsloth file near IQ3_XXS's 75.8 GB, UD-IQ2_XS ( 75.8 GB ) or UD-Q2_K_XL ( 79.6 GB ), once IQ1_M shows the engine and its MTP draft layer cope. The registry has `flash-next-unsloth-ud-iq1-m` ( 256k, nothing set ) for the first.
  **Update, same day:** IQ3_S now starts with `ple-io=direct` ( first step below done ), with about 20 GiB of RAM left after use. UD-IQ4_XS's experts need roughly 25 GiB more RAM than IQ3_S's 46.8 GiB arena ( 72 against about 47 by Strata's docs ), which would take that to nothing, so it is the next test only if Strata's SSD mode copes; I'd try the smaller Unsloth sizes first.
  **Order to try for the larger ones ( the first step is done ):** get IQ3_S started first ( `ple-io=direct`, [SETUP-LLM-BE-STRATA.md](SETUP-LLM-BE-STRATA.md#what-was-found-running-it-2026-11-15) ), then UD-IQ4_XS with the same setting at 128k context, and compare it with IQ3_XXS and Q2_0 on the same kind of pi session. UD-Q4_K_XL ( 111 GB ) would stream from the SSD, so leave it until a cheaper size has shown whether Strata's SSD mode is usable here.
* **UD-Q2_K_XL was run ( 2026-11-15 ): it starts at 256k with `ple-io=direct` and MTP, answers prompts up to 249k tokens, and ran a pi session of 72 requests at a median of 128.7 tok/sec ( table at the end of this bullet ).**
  * **The download fetched the PLE shard again** instead of linking it: 79.6 GB ( 80.5 by Strata's progress count ) in 30 min 21 s from the first byte to the files being ready ( pod created 06:51:10 UTC, model ready 07:21:46 ), about 44 MB/s, with 30 s of prep. The shared-shard saving I had hoped for ( 50.8 GB ) didn't happen; Strata keeps Unsloth's copy in its own folder ( `models/unsloth-UD-Q2_K_XL`, its own inode ). The volume went from 454 to 379 GB free.
  * **Start:** Ready 1 min 53 s after the model was prepared ( pod created to Ready 32 min 31 s in all, the first start ); loading took about 90 s. The log said `PLE direct read probe: 50459 rows/s (fine)`, `strata mtp: draft layer loaded, 835 MiB of VRAM`, an expert arena of **21,952 huge pages, 42.9 GiB** ( my estimate was about 43 ), 6,779 expert slots in VRAM ( IQ3_S 6,109, IQ3_XXS 7,107 ).
    So `ple-io=direct` is accepted with an Unsloth file, and the MTP draft layer loads with one. The K-quant banner printed here too, but it printed on every start, so it says nothing about the file.
  * **Memory:** 67.4 GiB used and 29.1 GiB available just after the start, 73.1 used and 23.4 available after the long prompts, nothing locked, GPUs 22.9 and 23.0 GiB ( 23,443 and 23,569 MiB ). That is more headroom than IQ3_S had at the same point ( 20.3 available ), as the estimate said.
  * **Long prompts** ( `llm_util.py longprompt`, same filler; engine's own figures ):

    | Prompt tokens | Cached | Seconds | Read speed | Decode | Draft accepted | Result |
    |---|---|---|---|---|---|---|
    | 7,985 | 0% | 4.3 | 2,126 tok/s | 145.5 tok/s ( 131.3 on the first, 8,493-token request ) | 80% ( 84% ) | passphrase recalled |
    | 189,289 | 0% | 49.2 | 3,895 tok/s | **106.5 tok/s** | **50%** | recalled |
    | 249,031 | 72% | 25.2 | 2,798 tok/s | **111.7 tok/s** | 64% | recalled |

    No restart and no out-of-memory, stall, "too long" or truncation lines. The cold 189k read took 49.2 s ( Q2_0 43.7, IQ3_XXS 47.9 with MTP, IQ3_S 48.1 ). **Decode at depth is the lowest of any size on the same prompts** ( 106.5 and 111.7 against 145.8 and 142.6 for Q2_0, 121.3 and 135.7 for IQ3_XXS, 135.4 and 136.3 for IQ3_S ), and acceptance at 189k was 50% against 68-82%.
    The replies were only 48 and 58 tokens, so each decode figure there is a short, noisy run. I first read the 50% acceptance on the 189k request as the drafter agreeing less with this quant; **the pi session below says that was wrong** ( 71% mean acceptance over 64 requests, the same as the ISTA-DASLab sizes ), so it was the short replies.
  * **A pi session ( 72 requests, 64 with 100+ tokens out; the warm-up and my test prompts left out ), same task type as the others but not the same task:**

    | Run | Decode median ( p10-p90 ) | 1,000+ out | Draft accepted | Prompt tokens reused | Median first token ( p90 ) | Largest prompt |
    |---|---|---|---|---|---|---|
    | IQ3_XXS ISTA-DASLab, MTP | 149.8 ( 131-170 ) | 158.1, n = 57 | 71% | 98.3% | 0.45 s ( 1.3 s ) | 102,583 |
    | Q2_0 ISTA-DASLab, MTP | 166.2 ( 146-195 ) | 165.6, n = 16 | 73% | 96.9% | 0.38 s ( 1.5 s ) | 63,267 |
    | IQ3_S ISTA-DASLab, MTP, `ple-io=direct` | 134.0 ( 113-152 ) | 134.1, n = 26 | 72% | 97.5% | 0.71 s ( 2.65 s ) | 122,347 |
    | **UD-Q2_K_XL Unsloth, MTP, `ple-io=direct`** | **128.7** ( 111-148 ) | **137.2, n = 19** | **71%** | **98.1%** | **0.35 s** ( 2.69 s ) | 93,996 |

    By prompt size UD-Q2_K_XL's median decode was 133.6 ( under 15k, n = 9 ), 145.2 ( 15-45k, n = 7 ), 128.3 ( 45-65k, n = 14 ) and 120.7 ( over 65k, n = 34 ), against IQ3_XXS's 163.2, 165.3, 148.5 and 144.8. The longest reply was 8,197 tokens at 137.2 tok/sec.
    * **It is 14% slower than IQ3_XXS ISTA-DASLab ( 128.7 against 149.8 ), the closest in file size ( 79.6 against 75.8 GB ), and 4% slower than IQ3_S ( 134.0 ), which has the same `ple-io=direct` setting.** I can't say how much of the gap to IQ3_XXS is the quant and how much is the disk-read setting: the one run that would separate them ( IQ3_XXS with `ple-io=direct` ) hasn't been done, and the sessions are different tasks.
    * **Acceptance ( 71% ), reuse ( 98.1% ) and the median first token ( 0.35 s, the lowest of the four ) are as good as the ISTA-DASLab sizes.** The p90 first token ( 2.7 s ) is the same as IQ3_S's, which has the same setting, and both are about twice the other two's; the 2 cold prompts over 5k tokens took 6.1 and 6.9 s, in line with the others ( 5.8-5.9 s ).
    * So by this session an Unsloth file of about IQ3_XXS's size does **not** decode faster than the ISTA-DASLab one here, and the "quick" claim isn't shown for it, which is what section 18's reading predicted. Whether its answers are better is the claim Unsloth makes and I haven't tested it.
  * **Not yet measured:** quality ( any comparison of answers ), the effect of `ple-io=direct` alone, UD-IQ1_M, and larger Unsloth sizes.
* **What would show the claim.** Same prompts, same context, MTP on, one Unsloth size against one ISTA-DASLab size of the same file size, decode and acceptance from `speed strata`, and some check on answers. Without the last, a speed number says nothing about whether the quant is better.

### 19. NVLink bridge: tabbyapi, sglang and sonar ( 2026-11-16 )

A bridge was fitted between the two 3090s on 2026-11-16, so the tensor-parallel all-reduce can use NVLink instead of PCIe. The figures and method are in [SETUP-LLM-TIMING.md](SETUP-LLM-TIMING.md#results); this is the summary. Each pi session used the same prompt as the rest of this doc ( see "How the sessions were run" ), and each result is one run.

* **sglang: about 16% faster decode with NVLink on** ( median 112.0 against 96.2 tok/sec, 12 and 25 requests ), and 8-25% faster `longprompt` prefill ( 189k cold: 156.6 s against 178.0 s ). Acceptance was *lower* with it on ( 2.91 against 3.21 accepted per step ), so the gain isn't the draft. The off run's 96.2 matches section 13's pre-bridge 94.6. NVLink was forced off with `sglang_nvlink: false`, which sets `NCCL_P2P_DISABLE=1`, `NCCL_SHM_DISABLE=0` and `--disable-custom-all-reduce`; the NVLink byte counters ( `nvidia-smi nvlink -gt d` ) rose by about 111 GiB per link direction with it on ( `longprompt` and the session ) and did not move with it off.
* **sonar: about 13% faster decode with NVLink on** ( median 91.7 against 81.1 tok/sec, 43 and 45 requests, about 50 requests and prompts to 72-74k in each session ), the same in every prompt-size band ( 12-16% ), and 11-27% faster `longprompt` prefill ( 189k cold: 175.1 s against 199.7 s ). Acceptance was the same ( 65.1% against 64.5% ), so the draft doesn't explain it. Switched with `sonar_nvlink: false`, as for sglang; the byte counters rose by about 240 GiB per link direction with it on and stayed flat with it off. There is no pre-bridge pi-session median for sonar to check the off run against.
* **tabbyapi: no measurable gain** ( median 106.2 with the `nccl` backend, against 105.2 for `native` in section 9 ). That comparison is against a session from before the bridge, so it doesn't isolate the bridge. The `native` run on the bridged box is still to do. Tabby has no way to disable P2P under `nccl`, so `tabbyapi_nvlink` switches the backend instead. tabby's `native` backend stages the all-reduce through host memory and doesn't use NVLink at all.
* **sglang's cache-hit figures were wrong in this deployment**: `longprompt`'s cached column read 0% because the server wasn't started with `--enable-cache-report` ( now in the registry entry; it makes sglang return cached-token counts in each reply ). Its prefill log shows the real counts ( the 249k `longprompt` request had 189,248 tokens cached ). The same goes for the earlier `longprompt` runs on sglang. sglang's hit-rate gauge also read 0.0% throughout; I don't know whether the flag fixes that, and haven't tried it.
* **Not tested:** the other engines ( mistralrs does tensor parallel over NCCL and should be affected; ollama and strata split layers across the cards and ninfer uses one GPU, so I'd expect little or no effect, but none of that is measured ), a repeat of any run, `native` tabbyapi on the bridged box, and decode at depth with NVLink on for sglang ( its on session stopped at 34k tokens; sonar's covered up to 74k ).

## Comparison ( Qwen3.8-27B 5.00bpw, tensor parallel on both 3090s, 262144 context, single stream )

| Setup | Short replies | ~7-8k token generation | 29-59k context, >=1k tokens out | Notes |
|---|---|---|---|---|
| tabbyapi, no MTP | 47-48 tok/sec | - | - | |
| sonar, no MTP | ~52 tok/sec | - | - | |
| **tabbyapi + MTP** | 95-128 tok/sec | 74-76 tok/sec | 87-95 tok/sec ( n=3, but 16-23k tokens out; see section 6 ) | acceptance 47-81%; prefix reuse 64-87% on repeat prompts |
| sonar + MTP, fp8 KV, FlashInfer, **before the NVLink bridge** | 104-112 tok/sec | 69-72 tok/sec | 67-91 tok/sec | acceptance 47.5% ( short test ); needs the PVC config patch |
| sonar + MTP, int8 KV, Triton | 80-113 tok/sec | 53 tok/sec | 19-31 tok/sec | acceptance 50-63%; much slower, see section 5 |
| tabbyapi + MTP, Swift 4.00bpw SC, **one** GPU | median ~72 tok/sec in a tool-calling session ( range 17-103 ) | 60 tok/sec ( one 4.7k request ) | 70-75 median at 15-80k context, engine decode, not end-to-end ( n=54; see section 8 ) | acceptance 52-88%; 96-100% of prompt tokens cached; 6-bit KV; different weights ( 4.00bpw ) |
| tabbyapi + MTP, Swift 4.00bpw SC, **two** GPUs ( tensor parallel, `native` backend, before the NVLink bridge ) | median ~105 tok/sec in a tool-calling session ( range 21-151 ) | 89-130 tok/sec ( 10 requests of 1,087-9,348 tokens ) | 86-116 median at 15-70k context, engine decode, not end-to-end ( n=47; see section 9 ) | acceptance median 66%; 8-bit KV; TTFT median 0.95 s on cached prompts, 11-15 s cold |
| tabbyapi + MTP, Swift 4.00bpw SC, **two** GPUs, **NVLink bridge on** ( `nccl` backend ) | median 106.2 tok/sec in a pi session ( range 50-157, n=23 ) | not separately measured ( the pi session's longest reply was 17,141 tokens, in the under-15k band; its own speed wasn't pulled out ) | 98.8 median at 15-45k ( n=10 ) and 107.0 at 45-65k ( n=7 ), engine decode, not end-to-end ( see section 19 ) | acceptance median 69%; no measurable gain over the `native` row above ( 105.2 ), but that row is from before the bridge and the `native` control on this box hasn't been run; counters confirm the bridge carried the traffic; model load 46.7 s against 30.4 s |
| ninfer-3090, DFlash2, **one** GPU | 121-178 tok/sec | 97 tok/sec ( one 6.9k request ) | 116-164 engine decode, not end-to-end ( n=8, median 142; see section 7 ) | acceptance 42%; 96% of prompt tokens cached; different weights ( 4-5 bit, 17 GiB ) |
| ninfer-all, MTP3, **one** GPU | median ~107 tok/sec in a tool-calling session ( range 85-198 ) | 109 tok/sec ( one 7.2k-token reply ) | 97-180 engine decode, not end-to-end ( n=5, median 153; see section 10 ) | acceptance median 61%; 26 of 33 requests >= 90% cached; two lanes; weights 16.0 GiB |
| ninfer-all, DFlash2, **one** GPU | median ~102 tok/sec in a tool-calling session ( range 79-182 ) | 90 tok/sec ( one 9.3k-token reply ) | 102-128 engine decode, not end-to-end ( n=5, median 126; see section 11 ) | acceptance median 36% ( seven drafts a round ); one lane; context stepped down to 196,608; weights 18.0 GiB |
| sglang, MTP via EAGLE, GPTQ 4-bit, **two** GPUs ( tensor parallel ), **before the NVLink bridge** | median ~95 tok/sec in a tool-calling session ( range 73.5-144 ) | 88.5 and 92.1 tok/sec ( 6.6k and 8.2k tokens out ) | 123 ( 15-45k, n=4 ) and 91 ( 45-65k, n=6 ) median, engine decode, not end-to-end ( see section 13 ) | accept length 2.9 of 4, rate 63%; 97.5% of prompt tokens cached; fp8 KV; needed a patched quantization config for MTP to work at all |
| sglang, as above, **NVLink bridge on** ( `sglang_nvlink: true` ) | median 112.0 tok/sec in a pi session ( range 58-145, n=12 ) | 130.7 tok/sec ( one 15.8k-token reply, 10.6k prompt ); 102.3 on a 6.2k-token one | 114.3 median at 15-45k ( n=9 ), engine decode, not end-to-end; the session stopped at 34k tokens ( see section 19 ) | accept length 2.91 ( about 64% ); NVLink counters rose by about 111 GiB per link direction over `longprompt` and the session; one run, ON before OFF |
| sglang, as above, **NVLink forced off** ( `sglang_nvlink: false`, same box ) | median 96.2 tok/sec in a pi session ( range 79-129, n=25 ) | 118.3 tok/sec ( one 16.4k-token reply, 12.9k prompt ) | 92.3 at 15-45k ( n=8 ), 116.4 at 45-65k ( n=5 ), 96.8 above 65k ( n=9 ), engine decode ( see section 19 ) | accept length 3.21 ( about 74% ); counters flat for the whole run; matches the pre-bridge 94.6 |
| sonar + MTP, fp8 KV, FlashInfer, **NVLink bridge on** ( `sonar_nvlink: true` ) | median 91.7 tok/sec in a pi session ( range 77-126, n=43 ) | not separately measured ( 11 replies of 1,000+ tokens: median 89.1 ) | 89.6 median at 45-65k ( n=20 ) and 87.3 above 65k ( n=9 ), engine decode, not end-to-end ( see section 19 ) | acceptance 65.1%, 2.95 tokens per forward pass; NVLink counters rose by about 240 GiB per link direction over `longprompt` and the session; 50 requests, prompts to 74k; one run, ON before OFF |
| sonar + MTP, fp8 KV, FlashInfer, **NVLink forced off** ( `sonar_nvlink: false`, same box ) | median 81.1 tok/sec in a pi session ( range 68-111, n=45 ) | not separately measured ( 12 replies of 1,000+ tokens: median 78.6; the longest, 16,384 tokens, ran at 86.4 ) | 77.5 median at 45-65k ( n=22 ) and 77.7 above 65k ( n=9 ), engine decode ( see section 19 ) | acceptance 64.5%, 2.93 tokens per forward pass; counters flat for the whole run; 51 requests, prompts to 72k |
| mistralrs, MTP, `q4k` quantized at load, **two** GPUs ( tensor parallel ) | median 36.8 tok/sec over 7 requests of 5.5k tokens or less ( range 18.0-43.1 ) | 18.0 tok/sec ( one 6,850-token reply, 5.5k prompt ) | not measured; prompts stopped at 5.5k tokens ( see section 14 ) | accept length 2.0-2.75, rate 49-79%; **0% prefix cache** ( MTP recomputes prefixes ); 63,840-token KV cache; one hang after a cancelled request |
| strata, MTP, Qwen3.8-**Flash-Next 125B** IQ3_XXS ( a different model ), **two** GPUs ( layer split ) | median 158.6 tok/sec in a pi agent session ( range 127-178, n=54 ) | 127.3 tok/sec ( one 6,144-token reply ); 150.1 on a 16,384-token one | 158.4 median above 15k tokens, up to 89k ( bands 148.5-162.8 ), engine decode, not end-to-end ( n=47; see section 15 ) | acceptance mean 72%; **98% of prompt tokens reused**; median TTFT 0.5 s; experts held in RAM ( 26.8 GiB pinned ); both GPUs at 97% memory; **not comparable with the rows above** |
| strata, MTP, Qwen3.8-**Flash-Next 125B** **Q2_0** ( a different model; the smaller quant of the row above ), **two** GPUs ( layer split ), 256k | median 166.2 tok/sec in a pi agent session ( range 138-223, n=38 ) | 194.9 tok/sec ( one 11,835-token reply ) | 167.6 median at 45-65k tokens ( n=21 ), engine decode, not end-to-end; 137-152 on 189k and 249k filler prompts with 52-63 tokens out ( see section 16 ) | acceptance mean 73%; 96.9% of prompt tokens reused; median TTFT 0.38 s; 99% of experts resident in VRAM; no pod restart on a 249k-token prompt; **not comparable with the 27B rows** |
| strata, MTP, Qwen3.8-**Flash-Next 125B** **IQ3_S** with `ple-io=direct` ( a different model; the largest ISTA-DASLab size, which only starts with the PLE table read from disk ), **two** GPUs ( layer split ), 256k | median 134.0 tok/sec in a pi agent session ( range 100-159, n=56 ) | not separately measured ( 26 replies of 1,000+ tokens: median 134.1 ) | 126.4 median above 65k tokens ( n=30 ), engine decode, not end-to-end; 135-150 on 189k and 249k filler prompts ( see the strata page ) | acceptance mean 72%; 97.5% of prompt tokens reused; median TTFT 0.71 s ( 8.6 s on the 3 cold prompts over 5k ); 6,109 expert slots in VRAM; the session continued from an earlier IQ3_S test, so it isn't the same work as the other rows; **not comparable with the 27B rows** |
| strata, MTP, Qwen3.8-**Flash-Next 125B** **Unsloth UD-Q2_K_XL** with `ple-io=direct` ( a different model; Unsloth's quant, 79.6 GB against IQ3_XXS's 75.8 ), **two** GPUs ( layer split ), 256k | median 128.7 tok/sec in a pi agent session ( range 94-158, n=64 ) | not separately measured ( 19 replies of 1,000+ tokens: median 137.2 ) | 120.7 median above 65k tokens ( n=34 ), engine decode, not end-to-end; 106-112 on 189k and 249k filler prompts with 48-58 tokens out ( see section 18 ) | acceptance mean 71%; 98.1% of prompt tokens reused; median TTFT 0.35 s; 6,779 expert slots in VRAM; the PLE table is read from disk; the session was a different task from the other rows; **not comparable with the 27B rows** |

The first three speed columns are engine-reported decode tok/sec; the 29-59k column is end-to-end tok/sec from langfuse ( output tokens / total latency, includes prefill ), so only compare within it.
All numbers are single runs, not averaged. The short-reply and 7-8k figures used different prompts; the 29-59k column is the same coding task in both sonar rows.
tabbyapi + MTP is the simplest ( no patching ) and at least as fast as sonar wherever both were measured; its long-context cell comes from longer outputs than the sonar rows, so treat it as indicative ( section 6 ).
Sonar's advantages are its Prometheus metrics and batching several requests; neither helps a single stream.
ninfer's decode speed is higher than the other rows, on half the GPUs, but the margin depends on the cell: on short replies its range ( 121-178 ) overlaps tabbyapi's top end ( 95-128 ), and the 7-8k cell is one request.
The ninfer row is also a different quantisation, a different task and a different measure in its last speed cell ( section 7 ), so read it as "faster", not as a precise ratio.
The two ninfer-all rows ( sections 10 and 11 ) are closer to the others: their session medians of ~107 ( MTP3 ) and ~102 ( DFlash2 ) tok/sec on one GPU are about the same as tabbyapi's two-GPU Swift run ( ~105, section 9 ), so the figures above describe the ninfer-3090 DFlash2 setup, not ninfer in general.

## The seven engines compared

All seven serve models to litellm from `bnenod05`, and only one runs at a time ( `python bin/llm.py engine set` ). Speeds are in the next section, with their qualifications; they are not a like-for-like benchmark, and **the strata column is a different, larger model** ( Qwen3.8-Flash-Next 125B, not the 27B ), so its speeds can't be set against the others'.

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

| | **mistralrs** | **sglang** | **strata** ( Flash-Next 125B ) |
|---|---|---|---|
| **Model format** | safetensors ( or GGUF ), **quantized as it loads** ( `q4k` ); a prebuilt UQFF file would skip that | AWQ / GPTQ safetensors ( not EXL3 ) | GGUF, for the Flash-Next family only ( ISTA-DASLab and Unsloth sizes ) |
| **Models at once** | one per process | one per process | one per process |
| **GPUs** | both ( tensor parallel over NCCL ) | both ( tensor parallel ) | both ( layer split; the experts mostly in system RAM ) |
| **Speed seen, short replies** | median 36.8 over 7 requests of 5.5k tokens or less ( range 18-43 ) | session median ~95 ( range 73.5-144 ) | session medians 140-166 ( IQ3_XXS 150, Q2_0 166 ) |
| **Speed seen, long generations** | 18.0 ( one 6,850-token reply ) | 88.5 and 92.1 ( 6.6k and 8.2k tokens out ) | 127 on a 6,144-token reply and 150 on a 16,384-token one ( IQ3_XXS ); 195 on an 11,835-token one ( Q2_0 ) |
| **Metrics** | Prometheus `/metrics`, a ServiceMonitor, `metric mistralrs` | Prometheus `/metrics` ( `--enable-metrics` ), a ServiceMonitor, `metric sglang` | Prometheus `/metrics?format=prometheus`, a ServiceMonitor, a Grafana dashboard, `metric strata`; per-request speeds from a log file on the volume |
| **Image** | stock, from ghcr ( 4.04 GB ) | stock, from Docker Hub ( 16.1 GB ), pinned by digest | stock, from ghcr ( 0.86 GB ) |
| **Starts** | scaled to 0 | scaled to 0 | scaled to 0 |
| **Switch model** | registry, `model load` | registry, `model load` | registry, `model load` |

* **ollama** is the flexible one: the only engine that swaps many models in and out on demand, which is why it runs by default. It is also the only one without a model registry or `model sync`.
* **sonar** has the most features ( batching, Prometheus, tool-call and reasoning parser flags ) but was the hardest to set up; MTP needed a manual patch on the PVC.
* **tabbyapi** is the simplest way to run EXL3: MTP worked first time and models can be swapped at runtime. No metrics.
* **ninfer** matched tabbyapi's two-GPU speed on one GPU ( session medians ~102-107 on ninfer-all against ~105 ), with the earlier ninfer-3090 session higher ( ~130 ), at the cost of a narrower, riskier setup: a community port of a Blackwell engine, maintained best-effort, that only runs the models it supports.
* **mistralrs** was the slowest of the 27B engines by a wide margin ( 37 tok/sec against ~95 for sglang ), has no prefix cache hits with MTP on, and hung once after a cancelled request. It quantizes the original weights on every start ( about 3 minutes ), which is its one real convenience: no separate quant to find. The untested speed ideas are in section 14.
* **sglang** is the fastest of the engines that read a stock 4-bit quant on both GPUs ( ~95 ), and has the most complete serving features ( tool-call and reasoning parsers, a strict thinking cap, metrics ), but MTP only worked after patching the checkpoint's quantization config through `--json-model-override-args`, and its 16 GB image is the largest here.
* **strata** isn't an alternative to the others for the 27B: it runs a different, 125B model that doesn't fit on the GPUs, by keeping its experts in system RAM, and it decodes faster than every 27B engine on it ( 150-166 tok/sec ), with 93-98% of prompt tokens reused from its conversation cache. The price is RAM ( 26.8 GiB locked, 83-89 of 94 GiB in use ) and two GPUs at ~96% memory, so nothing else can run beside it, and a first-start download of 38-76 GB. Whether its answers are better I haven't tested.

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

Every engine except strata serves Qwen3.8-27B, but not the same weights. These are the registry entries as of 2026-11-15 ( `ansible/vars/<engine>/bnekub03-models.vars.yml` ); sglang is measured in section 13 and mistralrs, on 9 requests only, in section 14. strata ( sections 15, 16 and 17 ) serves Qwen3.8-Flash-Next 125B instead, so its rows are at the bottom and are a different model, not another quant of the 27B.

| Engine | Registry entry | Weights | GPUs | Speculation | KV cache | Context | Thinking cap |
|---|---|---|---|---|---|---|---|
| sonar | `qwen3.8-27b-5bpw` | EXL3 5.00bpw ( `turboderp/Qwen3.8-27B-exl3`, ~19.9GB ) | 2, tensor parallel | MTP, 3 drafts | fp8 | 262144 | 8192 |
| tabbyapi | `qwen3.8-27b-5bpw` | the same EXL3 5.00bpw | 2, tensor parallel | MTP, up to 4 drafts, dynamic | 8-bit | 262144 | 8192 |
| tabbyapi | `swift-27b-sc-4bpw-2gpu` ( active ), `-1gpu` | EXL3 4.00bpw of **Swift**, a fine-tune ( 16.3GB ) | 2 ( tensor parallel ) or 1 | MTP, up to 4 drafts, dynamic | 8-bit ( 6-bit on one GPU ) | 262144 | 8192 |
| ninfer | `qwen3.8-27b-dflash2` ( active ), `-mtp` | `.ninfer`, own 4-5 bit mix ( 18.0 GiB loaded with DFlash2, 16.0 with MTP ) | **1** | DFlash2 with n-gram copying, or MTP | `rk4v4` ( 4-bit keys and values ) | 196608 ( DFlash2, pinned ), 262144 ( MTP ) | 8192 |
| sglang | `qwen3.8-27b-gptq-int4` | GPTQ 4-bit, group size 32 ( `palmfuture/Qwen3.8-27B-GPTQ-Int4` ) | 2, tensor parallel | MTP via EAGLE, 3 steps, 4 drafts | fp8 | 262144 | 8192 ( strict thinking ) |
| mistralrs | `qwen3.8-27b-q4k` | the original BF16 weights, quantized to `q4k` as they load | 2, tensor parallel ( NCCL ) | `--mtp`, built-in head, adaptive depth | fp8 | 65536 ( 262144 didn't start ) | none |
| strata | `flash-next-iq3-xxs` | **Qwen3.8-Flash-Next 125B**, ISTA-DASLab IQ3_XXS GGUF ( 47.0 GB for the model, 75.8 GB to download ) | 2, layer split; experts mostly in RAM ( 26.8 GiB locked ) | MTP ( `drafting: mtp` ); DFlash2 was tried and wasn't faster | int8 ( Strata's default ) with KV streaming | 262144 | 8192, sent by litellm with each request |
| strata | `flash-next-q2-0` | the same model, ISTA-DASLab Q2_0 ( 37.6 GB for the model, 37.6 GB to download once IQ3_XXS's shared shard is there ) | the same | MTP | the same | 262144 | 8192 |
| strata | `flash-next-iq3-s` | the same model, ISTA-DASLab IQ3_S ( 54.8 GB for the model ); **needs `ple-io=direct` ( PLE table read from disk ) to start at 256k; without it the node's OOM killer killed it, see "Things that did not work"** | the same | MTP | the same | 262144 | 8192 |

* **Closest pair.** sonar and tabbyapi `qwen3.8-27b-5bpw` are the only exact match: same EXL3 weights, same context, similar speculation, and they differ in the KV format ( fp8 against 8-bit ) and the engine. Every other row has different weights.
* **sglang against tabbyapi Swift.** sglang's GPTQ 4-bit is nearest in size to the Swift 4.00bpw quant ( 16.3GB ) on two GPUs with MTP, but Swift is a fine-tune, so the output is not the same model. Against the 5.00bpw rows sglang has about one bit per weight less.
* **GPTQ and q4k are not EXL3.** At the same nominal bits, the three formats lose different amounts of quality. I haven't measured any of them; only the speed rows above exist.
* **mistralrs quantizes at load.** Its `q4k` weights come from the original BF16 model, so unlike the others there is no pre-made quant to check against, and its first start is slow.
* **No cap on mistralrs.** The other engines stop thinking at 8192 tokens, so a long think on mistralrs can use more of the output budget. Compare tok/sec rather than the length of a reply.
* **strata isn't comparable.** It is a different model with about 4.6 times the parameters, run from GGUF at 2-3 bits with most experts in system RAM, so its higher tok/sec says what that setup does, not that the engine is better. The three entries are sizes of the one model: All three are measured ( 150, 166 and 134 tok/sec session medians for IQ3_XXS, Q2_0 and IQ3_S, sections 15, 16 and 17 ), but IQ3_S's session had the PLE table read from disk and a different task.
  Strata's cap on thinking isn't a server setting I could find, so litellm sends `reasoning_budget_tokens: 8192` with each request; I tested that on a direct request and not through litellm and pi.
* **Limits seen on sglang.** `max_running_requests` is capped by its Mamba state cache, at 5 at first and about 8 with `--enable-linear-replayssm-spec` and float32 state. Its KV pool holds 268,377 tokens at fp8 on these cards ( 141,683 at bf16 ), which is above the 262144 context but leaves room for only about one full-length request.

To make the comparison fair, either run the same registry entry's weights on the engines that can read them, or accept that this is a comparison of engine and quant together, as the earlier rows are.

## Things that did not work

* `--speculative-model mtp`, `--speculative-draft-n-max`, `--quantization exllamav3`, `--kv-cache-dtype int8`: flags from other engines. Sonar uses `--speculative-config '{...}'`,
  the quant method is `exl3` ( auto-detected ) and the KV option is `int8_per_token_head`.
* Pointing the speculative config at the BF16 `Qwen/Qwen3.8-27B` repo for the MTP head ( `"model": ...` ): not tried. From the source it would likely hit the same problem on `lm_head`
  ( the draft inherits the target's EXL3 config and `head_bits` is set ) and download all 51.7GiB of BF16 shards.
* TensorRT-LLM / Triton Inference Server: not viable for this model on 3090s. The `.engine` build path has no Qwen3.5 model, EXL3 isn't loadable, NVFP4/FP8 are gated to sm89+ / Hopper+,
  and the dense `Qwen3_5ForConditionalGeneration` isn't in its tested model list.

Found on sglang 0.5.23 ( 2026-11-14 ), as engine / model / config. None of these produced a working MTP draft.

* **sglang / `palmfuture/Qwen3.8-27B-GPTQ-Int4`, as published:** loads and serves, but MTP accepts nothing ( `accept len 1.00`, ~40 tok/s ). Its exclusion rule `-:.*\.mtp\..*` needs a dot before `mtp`, but sglang names the draft layers `mtp.layers.N...`, so they are built as 4-bit GPTQ layers and the bf16 weights can't load ( the seven "not found in params_dict" warnings ). Fixed with `--json-model-override-args`, see section 13.
* **sglang / `bernhardbrieger/Qwen3.8-27B-GPTQ-Int4`:** doesn't load. Its MTP rule is right, but it doesn't exclude `linear_attn.in_proj_ba`, so every GDN layer warns "not found in params_dict" and `gptq_marlin_repack` dies with `size_n = 48 is not divisible by tile_n_size = 64`.
* **sglang / `--speculative-draft-model-quantization=unquant`:** no effect. The server_args still showed `None`, and the draft head still loaded as `quant=gptq`.
* **sglang / bf16 KV cache instead of fp8:** MTP acceptance unchanged ( still 0.00 ); it only halved the KV pool ( 283,366 to 141,683 tokens ).
* **sglang / `--enable-linear-replayssm-spec` and `--mamba-ssm-dtype=float32`:** start fine on the 3090s and raised `max_running_requests` from 5 to 8, but did not change MTP acceptance.

Found on strata 0.2.13 ( 2026-11-15 ):

* **strata / `set: ["drafting=mtp"]`:** `--set` passes the key to the engine binary as `--drafting`, which exits with `unknown argument: --drafting`, so the pod crash-looped. Strata also saved it in the launch preset ( `extra` ) and the run config ( `user_args` ), so removing it from the registry didn't help; both files had to be edited by hand. The drafter is chosen with `strata launch --drafting`, which the image's entrypoint has no variable for, so the role patches the entrypoint ( see SETUP-LLM-BE-STRATA.md ).
* **strata / DFlash2 instead of MTP on IQ3_XXS:** works ( after a 10 minute `strata drafting fetch` ), but no faster in a pi session ( median 149.2 against 149.8 tok/sec, slower on replies of 1,000+ tokens ) and it answers one request at a time.
* **strata / `IQ3_S` at 256k with MTP, two GPUs:** the node's kernel OOM killer killed the engine about 70 seconds into the load, on every attempt ( four `SystemOOM` events and `OOMKilled`, exit 137, by the time I looked; six restarts by the end ), even though the pod has no memory limit. The log's plan was a 46.8 GiB expert arena in RAM on top of the 26.8 GiB PLE table locked in RAM, with 88 GiB available at the start, against 20,464 pages ( about 40 GiB ) for IQ3_XXS and 16,201 ( 31 GiB ) for Q2_0, which fit. IQ3_XXS and Q2_0 are fine. The restarts also made the node go NotReady twice ( kubelet couldn't resolve the API server's name; `systemd-resolved` logged memory pressure at the same time ), which stalled langfuse and opencode. `ple-io=direct` ( the PLE table read from disk ) was accepted and gave back about 28 GiB on Q2_0, and **with it IQ3_S starts** ( Ready in 2 minutes, no restart, a 249k-token test prompt answered, about 20 GiB of RAM left after use ); what it costs in a real pi session is not measured. Details in SETUP-LLM-BE-STRATA.md.

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

# bookcase-ops LLM backend: strata

Part of the [LLM backends](SETUP-LLM-BACKEND.md).

[Strata](https://github.com/Niko1221/Strata) ( MIT, by Niko1221 ) is a local inference engine built for large mixture-of-experts models on consumer hardware. Its main model is **Qwen3.8-Flash-Next**, 125 billion parameters, which it runs from
GGUF files by keeping the experts in system RAM ( or on the SSD ) and the dense layers and the hottest experts on the GPUs. It serves OpenAI and Anthropic APIs, one model per process here.

* **A different model from the other engines.** The others serve Qwen3.8-27B. Strata's models are the 125B Flash-Next ( and a few others, see its [MODELS.md](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md) ), so its speeds and answers aren't comparable with theirs.
* **Both GPUs, as a layer split.** Each card holds a range of layers and a cache of the hot experts; a token crosses once per verify window, so it needs no NVLink or peer access, which these 3090s don't have.
* **Lots of RAM.** The model needs RAM for its experts plus about 10 GB: 34 GB ( Q2_0 ) to 50 GB ( IQ3_S ) in the docs' figures. `bnenod05` has about 94 GB.
* **It starts scaled to 0.** See [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).
* **Official image.** `ghcr.io/niko1221/strata:0.2.13-cuda13`. The CUDA 13 image needs NVIDIA driver 580 or newer, and `bnenod05` has 580.178.04.
* **Run on the 3090s.** It downloaded the 76 GB IQ3_XXS model in about 30 minutes ( 37 minutes from pod created to serving, 2 minutes on a restart; see [Startup times](#startup-times) ), and served a 63-request pi agent session at a median of about 159 tok/sec, with 98% of prompt tokens reused from its conversation cache ( [below](#what-was-found-running-it-2026-11-15) ). The smaller Q2_0 size downloaded in 13 minutes ( 15 minutes from pod created to serving ) and decoded at a median of about 166 tok/sec in a later pi session. It is a different, larger model from the other engines', so the speeds don't compare directly.

## Install it

1. Put an API key in vault ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) ). Strata won't serve beyond the machine without one, so the role refuses to run without a key of 16 or more characters.

   ```
   echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-strata/strata" api_key=-
   ```

   There is no Hugging Face token: Strata downloads anonymously and doesn't read or send one.

2. Check the node can pull the image: `sudo ctr -n k8s.io images pull ghcr.io/niko1221/strata:0.2.13-cuda13` ( 862 MB compressed in the registry, which the node reports as 0.86 GB; Strata's Docker doc says 2.2 GB on disk, which I didn't measure ).

3. Deploy it. It comes up at 0 replicas; then give it the GPUs with `engine set`:

   ```
   ansible-playbook -vvv -e deployments=bnekub03 -e app=strata k8s_llm_bnekub03.yml
   python bin/llm.py engine set strata
   kubectl -n dev-strata logs -f deploy/strata
   ```

   The first start downloads the model: 76.7 GB for IQ3_XXS, which took 30 minutes here at about 42 MB/s ( against the 16-19 MB/s Strata's docs measured ), and about 37 minutes in all with the pack and draft layer. The log shows `Downloading the model files: 7.7 of 76.7 GB`.
   `engine set` waits 15 minutes by default, so give it `--timeout 1h` on a first start. The timings are in [Startup times](#startup-times).
   The startup probe allows 3 hours. Strata is resumable: an interrupted download carries on when the pod restarts, and everything is kept on the PVC ( `/data/Strata-data`, checked: not the container's own disk ).
   Once downloaded it loads in about 90 seconds, and a restart is Ready in about 2 minutes.

   To see what a choice would do, once the pod is running: `kubectl -n dev-strata exec deploy/strata -- strata launch --plan --family qwen --model IQ3_XXS --context 128k`. The plan for IQ3_XXS at 128k said 76 GB for the model, 1.2 GB left to download ( the DFlash2 drafter ), pictures on, and DFlash2 drafting.

The model and its settings are in the registry, `vars/strata/bnekub03-models.vars.yml`; switch with `python bin/llm.py model load NAME` ( see [Switching models](SETUP-LLM-BACKEND.md#switching-models) ). Each entry has:

* `family` and `model`: Strata's `FAMILY` and `MODEL`. `qwen` with `Q2_0`, `IQ2_XS`, `IQ3_XXS` or `IQ3_S` are ISTA-DASLab's original sizes of Flash-Next, and the registry lists `IQ3_XXS`, `Q2_0` ( the active one at the moment ) and `IQ3_S`.
* `context`: `CONTEXT`, such as `128k`. Left out, Strata takes the largest that fits.
* optionally `parallel`, `kv`, `vision`, `set` ( `STRATA_SET`, one engine setting per item ) and `env` ( any other variable the image reads ). They become environment variables on the container, and are only set when present.
* optionally `drafting`: `auto` ( DFlash2 where Strata has it, the default ), `mtp`, `dflash2` or `off`. **This one isn't a variable the image reads.** `set: ["drafting=mtp"]` is passed to the engine as `--drafting`, which it rejects ( `unknown argument: --drafting` ), and the pod crash-loops.
  So when `drafting` is set the deployment runs `sh -c` with a copy of the image's `entrypoint.sh` that has one line added, `--drafting "$DRAFTING"`, after its `STRATA_SET` line; it stops with a message if the image's script has no such line, and it is plain again when `drafting` is left out.
  Strata saves the choice in its launch preset ( `/data/config/strata/launch-presets/docker.json`, `"drafting": "mtp"` ), which the role now clears at every start ( see "The role resets Strata's saved settings" below ); `strata_drafting` defaults to `mtp`, so an entry that says nothing gets MTP, and `auto` gives Strata's own choice.
  **Don't use `set: ["drafting=mtp"]`.** Strata saves it in the preset as `"extra": {"--drafting": "mtp"}` and in the run config's `user_args`, and passes it to the engine binary, which exits with `unknown argument: --drafting`; the pod then crash-loops, and removing the setting from the registry doesn't fix it, because every start reads the saved preset again.
  What fixed it ( 2026-11-15 ): scale the deployment to 0, back up and edit `/data/config/strata/launch-presets/docker.json` to `"extra": {}` and remove the `--drafting` entries and `user_args` / `launch_set` from `/data/state/strata/launch/docker.json` ( one-off pod with the volume mounted ), and scale back up.
  Also, the playbook can add or change an environment variable on the deployment but not remove one, so a variable taken out of the registry stays on the live deployment until `kubectl -n dev-strata set env deploy/strata NAME-`. DFlash2 needs `strata drafting fetch` once ( [Startup times](#startup-times) ) and runs one request at a time.

| Size | RAM + VRAM for the model | Experts in RAM | Speed | Quality |
|---|---|---|---|---|
| `Q2_0` | 37.6 GB | 34 GB | fastest | good |
| `IQ2_XS` | 39.2 GB | 35.5 GB | fast | better |
| `IQ3_XXS` | 47.0 GB | 43 GB | slower | great |
| `IQ3_S` | 54.8 GB | 50 GB | slowest | best; matches the full model on Strata's published tests |

* **No memory or CPU limit.** Strata's Docker doc says not to put a memory limit on the container: it plans from the whole machine's RAM and loads tens of GB. So the pod has none, and only requests 48 Gi and 12 CPUs. It can crowd out everything else on `bnenod05`, so keep other engines scaled to 0.
* **Memory locking.** The Docker command uses `--ulimit memlock=-1:-1` so the engine can pin the RAM it holds the experts in. Kubernetes has no setting for that, and the node's default limit is 8 MB, which I measured inside another engine's pod. The deployment adds the `IPC_LOCK` capability instead,
  and that was enough: the engine process had 26.8 GiB locked ( `VmLck` in `/proc/<pid>/status` ). `ulimit -l` in a shell in the pod still says 8192, so it isn't a check of this; look at `VmLck`.
* **Context.** The registry's `context` is my choice, not Strata's, and the litellm `num_ctx` must equal it ( `/health` says `max_context`; Strata ignores `num_ctx` ). It is **256k** ( `max_context: 262144`, the model's native maximum ), which started with no `kv` setting. I raised it in steps: 128k ran the 63-request pi session below, 192k then 256k each restarted
  in 2 minutes and answered long test prompts correctly ( [What was found running it](#what-was-found-running-it-2026-11-15) ). Strata's docs say 262K for IQ3_XXS is "not measured". The `Q2_0` entry is at 256k too, and started, served a pi session ( prompts up to 63k tokens ) and answered a 249k-token test prompt correctly; `IQ3_S` is at 256k too and starts only with `ple-io=direct` ( without it the node ran out of RAM, [below](#iq3_s-does-not-fit-at-256k-with-mtp-2026-11-15), which context has little to do with: the pinned KV is 1.55 GiB at 256k and about 0.8 GiB at 128k ); it answered a 249k-token test prompt. If a longer context fails to start or a long prompt restarts the pod, add `kv: "k8v4"` ( keys at 8 bits, values as rotated 4-bit: 23% less KV memory, prompts 2-5% slower ), then go back a step.
* **Thinking.** Flash-Next's template thinks by default at `xhigh`. Strata has a `reasoning_budget_tokens` setting that ends the thinking at N tokens, but it is a key in the engine's own config file, and I haven't found a way to set it server-wide in the Docker image,
  so the registry sends it with every request through litellm ( `extra_body.reasoning_budget_tokens: 8192` ), where a request's own value wins. A direct request with `reasoning_budget_tokens: 40` was cut at 199 characters of thinking and followed by a correct answer.
  I haven't compared it with an uncapped request, or checked that litellm passes it through.
* **No `curl` or `wget` in the image.** It has its own Python at `/opt/strata/terminal/python/bin/python3`, which `urllib` works with, if you need to call the API from inside the pod. With Git Bash on Windows, set `MSYS_NO_PATHCONV=1` or kubectl is given a mangled `/opt/...` path.

## Startup times

Measured on `bnenod05` ( 24 logical CPUs, 94 GB RAM, the NVMe volume ) with `flash-next-iq3-xxs` and `flash-next-q2-0`, from the pod's and the PVC's file timestamps ( UTC ) and the pod log. `( est. )` is arithmetic, not a measurement.
**\*** marks a figure measured on 2026-11-14, before the ethernet cable on `bnellm01` was replaced that night; the network may have been slower then, so downloads and pulls marked \* should be measured again.
To estimate a model of S GB: download ≈ S ÷ the download rate ( counting only the shards the volume doesn't already have, see below ), and the every-start load stays near the figures below ( two sizes have been loaded ).
The Q2_0 download ( 2026-11-15, 02:15 UTC ) came after the cable was replaced, so it has no asterisk; it ran at 47 MB/s against 42 MB/s\* for IQ3_XXS, one sample each, so I can't tell whether the cable made a difference.
The first start's pod creation time is worked out from the pod's age when I looked, so its figures are good to about 30 seconds.

**Sizes**

| What | Size |
|---|---|
| Image `ghcr.io/niko1221/strata:0.2.13-cuda13` | 862 MB compressed in the registry ( 9 layers, the biggest 727 MB ); the node reports 0.86 GB. Strata's Docker doc says 2.2 GB on disk, which I didn't measure |
| Model, IQ3_XXS | 75.8 GB in two files ( 47.0 GB and 28.8 GB ), 71 GiB on disk |
| Model, Q2_0 | 37.6 GB for its own shard ( 37,623,740,192 bytes ). Its second shard ( 28.8 GB ) is the same file as IQ3_XXS's, which Strata keeps once and links ( link count 2 ), so adding Q2_0 used 37.6 GB of the volume. Strata's plan and progress lines count both shards ( 66.4 and 67.3 GB ) |
| Draft layer ( MTP ) and the packs | `/data/Strata-data/mtp` is 6.5 GiB ( the draft file itself is 889 MB ) and is shared by every size; each size has its own pack in `packs` of about 1.5 GiB ( 1.4 GiB for Q2_0 ) |
| DFlash2 drafter ( fetched by hand, below ) | 1.21 GB downloaded ( 1,206,140,008 bytes ), then deleted; 0.84 GB kept in `/data/Strata-data/dflash2` ( a 522 MB pack, a 340 MB pack with a smaller selector, a 0.4 MB vocabulary ) |
| **On the PVC in total** | about 80 GiB with IQ3_XXS alone; `models` is 107 GiB with Q2_0 added, and 507 GB of the 1 TB disk is free |

**One-time, per node** ( repeats when the image tag changes )

| Phase | Size | Time | Rate | Scales with |
|---|---|---|---|---|
| Pull the image | 0.86 GB | not timed; 23-72 s ( est. ) at the 12-38 MB/s\* other pulls ran at | - | image size |

**One-time, per model** ( one row per registry entry; the files stay on the PVC, so switching back costs nothing )

| Entry | Download | Download time | Rate | Pack and draft layer | Pod created to serving |
|---|---|---|---|---|---|
| `flash-next-iq3-xxs` | 75.8 GB | **30 min\*** | **42 MB/s\*** | about 6 min ( the 1.5 GiB pack was written 36 s after the last file, the draft-layer files over the next 4.8 min; the draft layer is shared by every size, so only the first model pays for it, see Q2_0 ) | **about 37 min\*** ( 21% of the 3-hour probe ) |
| `flash-next-q2-0` | 37.6 GB ( the 28.8 GB shard it shares was already there ) | **13 min 14 s** ( 02:15:01 to 02:28:15 UTC ) | **47 MB/s** | **4 s** ( the draft layer was already there; checking the files took 18 s first ) | **15 min 00 s** ( pod created 02:14:57, Ready 02:29:57; about 75 s of it loading the engine ) |
| `flash-next-iq3-s` | 54.8 GB ( 54,817,524,224 bytes ); the 28.8 GB second shard is the same shared file again ( link count 3 ), which is why Strata's plan said 83.6 GB | **19 min 39 s** ( pod created 03:53:18 UTC to the last write of the shard at 04:12:57; the pod's start is in that, so the rate is a little low ) | **46.5 MB/s** | **about 30 s** ( packs written 04:13:25-26 ) | **2 min 00 s** once the files were there, with `ple-io=direct`; without it the node's out-of-memory killer killed the engine 70 s into every load, see [below](#iq3_s-does-not-fit-at-256k-with-mtp-2026-11-15) |

The download time includes the image already being on the node and Strata's own checks, so the rate is a little under the true one. Strata's docs measured only 16-19 MB/s from Hugging Face without a login, so allow double. The pod is `0/1` for the whole of that,
so a first `engine set strata` times out at its default of 15 minutes: the deployment carries on, and `python bin/llm.py engine set strata --timeout 1h` waits for it ( `model load` doesn't wait at all ). The log's `Downloading the model files: N of 67.3 GB` for Q2_0 counted the shared shard in its total, and its first line, 6.7 GB, came at 02:17:06, two minutes in.

**One-time, per drafter** ( DFlash2; the start never downloads it, see the DNS note under [What was found running it](#what-was-found-running-it-2026-11-15) )

| Phase | Size | Time | Rate | Scales with |
|---|---|---|---|---|
| `strata drafting fetch`: download | 1.21 GB | about 1-1.5 min ( the command started at about 00:14:57 UTC and the file's last write was 00:16; I didn't watch it ) | about 15-20 MB/s ( est. from that ) | file size |
| Pack to the engine's q4_0 file | 1.21 GB in, 0.84 GB out | **about 9 min** ( 00:16 to 00:25:09 ) | - | file size; one CPU core at 100% the whole time, plain numpy, nothing written until the end |
| **Fetch in all** | | **about 10 min** | | |

Run on 2026-11-15, on the serving pod: `kubectl -n dev-strata exec deploy/strata -- strata drafting fetch`. It prints `Packing the DFlash2 drafter for the engine… 1.21 of 1.21 GB` and then looks stuck for the 9 minutes; it isn't. Then restart the pod so the engine picks the pack up.

**Every start** ( everything already on the node and the PVC, page cache warm; pod created to Ready )

| Phase | Size processed | Time | Rate | Scales with | Why every start |
|---|---|---|---|---|---|
| Pod created to the engine starting | 76.7 GB of model files, which the log lists as `Getting the model files: 76.7 of 76.7 GB` | 22 s | - | I don't know whether it checks sizes or reads the files | Strata's launcher runs each time |
| Load the engine and the model | about 26.8 GiB pinned in RAM, about 23 GiB on each GPU | 98 s ( 87 s on the first start ) | - | model size, I assume; only one size loaded | the experts and layers are read into RAM and VRAM |
| Load the engine and the model, Q2_0 | the same 26.8 GiB locked ( `VmLck` was identical ); 99% of the experts resident, against 77-86% for IQ3_XXS | about 75 s ( on its first start; a restart wasn't timed ) | - | model size, I assume | the same |
| **Pod created to Ready** | | **2 min** ( 120 s ) with MTP | | | the same at 128k, 192k and 256k context ( 121 s at 256k ). **1 min 30 s** at 256k with DFlash2; the log shows loading in 75 s, I haven't looked at why it is faster. **4 min 00 s** at 256k with MTP after the deployment had been at 0 replicas for a while ( loading took 210 s ): I think the model files had left the page cache, but I haven't checked |

`python bin/llm.py engine list --times` prints each engine's pod age and created-to-Ready time ( while that pod still exists ).

**Limits:** the startup probe allows 3 hours ( 360 × 30 s ), the playbook waits up to 4 hours, and `engine set` waits 15 minutes unless given `--timeout`.

**Normal in the log:** `Startup probe failed … connection refused` for the first minute or so, and `Drafting: MTP ( DFlash2 isn't available in this mode )` at the end. The engine pins 26.8 GiB of RAM as it loads, so the node's free RAM drops then and stays down.

## Configure the model in litellm

`python bin/llm.py model sync` creates the litellm model from the registry entry's `litellm:` block: an OpenAI-compatible model at `http://web.dev-strata.svc.cluster.local:8080/v1`, with the vault `api_key`. Strata serves whatever model name it is sent,
so the name `strata` is only a label; `python bin/llm.py engine list` shows what it reports at `/v1/models`. By hand: Provider **OpenAI-Compatible Endpoints**, Custom Model Name `strata`, Public Model Name `strata-qwen3.8-flash-next:iq3_xxs`, that API base and key,
then `"num_ctx"` in the LiteLLM Params. Run `pi-sync-models` afterwards and restart opencode.

## Metrics

Strata has a `/metrics` endpoint that returns JSON for its own monitor, and Prometheus text with vLLM's metric names when asked ( `?format=prometheus` ), plus its own `strata:` series. The role creates a `ServiceMonitor` that asks for that format and sends the API key as a bearer token.
`/health` answers without a key.

```
python3 ../bin/llm.py metric strata     # speed now, requests running, KV cache use, prefix-cache hits, draft acceptance, GPU and RAM load, tool-call repairs
```

It goes in through a `kubectl port-forward`, with the key from vault ( `. vault-login.sh` first ). Counters are totals since the pod started. `/metrics` has 38 names: `vllm:*` for requests, tokens, latencies, prefix cache and speculation, and `strata:*` for the engine ( state, slots, live speed, GPU busy and memory, host RAM ).
`GET /metrics?format=prometheus` and `Accept: text/plain` return the same thing; `/metrics/prometheus` is a separate, smaller set in llama-server's names ( `llamacpp:*` ) plus `strata:drafts_*` counters.
`metric strata` was checked against a real response.

Per-request speeds come from Strata's engine log, which is a file on the PVC and not the pod's output, and `llm.py` reads it:

```
python3 ../bin/llm.py speed strata --min-out 100     # per request: prompt, share reused from the conversation cache, tokens out, TTFT, read speed, decode tok/s, draft acceptance
```

Each request logs a line like `strata serve: prompt 6404 tokens = 5729 reused + 675 read in 704 ms (958.7 tok/s), 6144 generated in 48275 ms (127.3 tok/s), drafts accepted 3058 of 5891`. The lines have no timestamps, so there is no `--since`: rows are numbered in file order and `--last` takes the newest.
TTFT is estimated as the time to read the uncached part of the prompt. The file is not emptied when the pod restarts: each start appends an `engine started` line, so requests from earlier runs stay in it and `speed strata` counts them all ( after the 192k restart it showed the 63 earlier requests, then the new ones, beginning with a 53-token warm-up request that Strata sends as it starts ). For end-to-end figures use `python3 ../bin/llm.py speed langfuse ... --model strata`.

A Grafana dashboard for these metrics is in [SETUP-GRAFANA.md](SETUP-GRAFANA.md#strata--llm-backend-) ( `ansible/strata-dashboard.json` ), and Prometheus found the ServiceMonitor and was scraping it.

## What was found running it ( 2026-11-15 )

* **Speed.** Over a pi agent session of 63 requests ( prompts from 53 to 88,939 tokens ), decode was a median **158.6 tok/sec** for the 54 with 100+ tokens out ( range 127.3-178.3 ), barely changing with prompt size ( bands of 148-170 ), and 150 tok/sec on a 16,384-token reply.
  Draft acceptance averaged 72% ( 52-84% ), lowest on the longest replies. The full table and comparison are in [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md) section 15. It is a larger, different model from the other engines', so it can't be compared with their figures directly.
* **Conversation cache.** 98.1% of prompt tokens were reused ( weighted by tokens ), the median prompt of 2k+ tokens was 100% reused, and the median first token took 0.5 s ( 1.3 s at the 90th percentile, 7.5 s for the one 24k-token cold prompt ).
* **Expert cache.** 95-99.6% hits ( median 99.1% ), with 96-99.9% of KV block reads from VRAM.
* **Long context ( 2026-11-15 ).** One request at a time, each a repetitive filler text with a passphrase on the first line and the question at the end ( `max_tokens` 300, 74 tokens back ), straight to Strata:

  | Context setting | Prompt tokens | Cached | Time to first token | Read speed | Decode | Result |
  |---|---|---|---|---|---|---|
  | 192k | 156,893 | 0% | 39.6 s | 3,965 tok/s | 144.2 tok/s | passphrase recalled |
  | 192k | 189,083 | 78% | 13.6 s | 3,070 tok/s | 145.1 tok/s | recalled |
  | 256k | 189,083 | 0% | 47.9 s | 3,948 tok/s | 121.3 tok/s | recalled |
  | 256k | 252,093 | 71% | 23.1 s | 3,117 tok/s | 135.7 tok/s | recalled |

  The same prompts at 256k again after switching to DFlash2 ( the four rows above are MTP ):

  | Prompt tokens | Cached | Time to first token | Read speed | Decode | Draft accepted | Result |
  |---|---|---|---|---|---|---|
  | 8,493 | 0% | 4.1 s | 2,061 tok/s | 113.0 tok/s | 59% | recalled |
  | 189,083 | 0% | 41.2 s | 4,585 tok/s | 139.6 tok/s | 63% | recalled |
  | 252,093 | 71% | 21.6 s | 3,326 tok/s | 138.3 tok/s | 61% | recalled |

  Each reply was 42-74 tokens, so the decode figures are rough: against MTP on the same prompts, DFlash2 read the cold 189k prompt a little faster ( 4,585 against 3,948 tok/s ), decoded about the same ( 139.6 against 121.3, 138.3 against 135.7 ) and accepted fewer drafts ( 59-63% against 69-76% ), but I can't say from these which drafter is faster in an agent session.
  The pi footer showed the old 197k until `pi-sync-models` was run after `model sync`; `model sync` alone isn't enough.

  No pod restarts and no out-of-memory, "too long" or truncation lines in the log. The 192k pi session before that ran 20 more requests with prompts of 87k-103k tokens at about 145 tok/sec median decode and 100% reuse.
  **What that doesn't show:** filler text, short replies, one request at a time. Quality at depth, a long reply on top of a 250k prompt, and two requests at once are untested, and there is little to spare: at 256k and idle both GPUs were at 23.6 and 23.7 GiB of 24, and `free` showed 90 of 94 GiB used with 3 GiB available.
* **DFlash2 drafting ( after the first runs, which were MTP ).** Once `strata drafting fetch` had put the drafter on the PVC ( [Startup times](#startup-times) ), a restart at 256k logged `strata dflash2: ... drafter 591 MiB of VRAM (Q4_0 weights)` and `DFlash2 drafter on (/data/Strata-data/dflash2/qwen3.8-flash-next-dflash2.q4_0-selq4.pack ...); windows up to 8 tokens; no MTP layer is loaded`,
  with GPU memory at 23.5 GiB on each card, no higher than with MTP. It also printed `DFlash2 answers one request at a time, so Strata set this model to one request at a time`, so two clients at once queue. A pi agent session with it ( 86 requests, 76 with 100+ tokens out; the warm-up and my test prompts left out ) decoded at a median **149.2 tok/sec** ( range 114-204 ), against 149.8 for all 183 MTP requests before it ( 172 with 100+ out ) and 158.4 for the first MTP session. So it is no faster here: the same median, but slower on long replies ( 141.1 against 158.1 at 1,000+ tokens out, n = 22 and 57 ),
  and its acceptance was lower ( 61% mean against 71%, though DFlash2 drafts windows of up to 8 tokens, so the two rates aren't the same measure ). Prefix reuse was the same ( 98.4% against 98.3% of prompt tokens ) and the median first token took 0.40 s against 0.45 s. The sessions were different tasks, one run each, so a gap of a few tok/sec is noise.
  ( I first measured this with 80 requests, a median of 151.6; the session carried on a little before the next restart, and these are the figures for all of it. )
  I can't say DFlash2 is worth the 10-minute fetch, the one-request-at-a-time limit and the extra 0.6 GB of VRAM, so the registry's `flash-next-iq3-xxs` entry now sets `drafting: "mtp"` ( see [Install it](#install-it) ); the engine log then shows `strata mtp: draft layer loaded, 835 MiB of VRAM` and no DFlash2 lines, and GPU memory is 23.4 and 23.5 GiB. The rest of this page, and section 15 of [SETUP-LLM-MTP.md](SETUP-LLM-MTP.md), is MTP.
* **Pi sessions on the same engine log ( 2026-11-15; IQ3_S added at the end ).** The engine log keeps every start, so I split it at the `engine started` lines ( the figures leave out the 53-token warm-up and my own test prompts; "decode" is the median over requests with 100+ tokens out ):

  | Run | Requests ( 100+ out ) | Largest prompt | Decode median ( p10-p90 ) | 1,000+ out | Draft accepted | Prompt tokens reused | Median first token |
  |---|---|---|---|---|---|---|---|
  | IQ3_XXS, MTP, 128k-256k | 183 ( 172 ) | 102,583 | **149.8** ( 131-170 ) | 158.1, n = 57 | 71% | 98.3% | 0.45 s |
  | IQ3_XXS, DFlash2, 256k | 86 ( 76 ) | 102,092 | **149.2** ( 128-183 ) | 141.1, n = 22 | 61% | 98.4% | 0.40 s |
  | IQ3_XXS, MTP again, 256k | 64 ( 46 ) | 65,950 | **140.7** ( 121-167 ) | 148.2, n = 7 | 70% | **93.6%** | 0.36 s ( p90 3.7 s ) |
  | **Q2_0**, MTP, 256k | 45 ( 38 ) | 63,267 | **166.2** ( 146-195 ) | 165.6, n = 16 | 73% | 96.9% | 0.38 s |
  | **IQ3_S**, MTP, 256k, `ple-io=direct` | 59 ( 56 ) | 122,347 | **134.0** ( 113-152 ) | 134.1, n = 26 | 72% | 97.5% | **0.71 s** ( p90 2.65 s ) |

  Different tasks each, one session each, and the last two were shorter, so differences of a few tok/sec between rows mean little. What I'd say:
  * **Q2_0 is about 11% faster than IQ3_XXS with the same drafter** ( 166.2 against 149.8 and 140.7 ). By prompt size it is 155.7, 163.7 and 167.6 tok/sec for under 15k, 15-45k and 45-65k, against 163.2, 165.3 and 148.5 for the first IQ3_XXS run, so the lead is in the 45-65k band and the others are level or lower ( n = 5, 12 and 21; I have no Q2_0 data over 65k ). Strata puts 99% of its experts in VRAM against 77% for IQ3_XXS with MTP, so a lead is plausible, but I haven't tested why.
    I haven't compared the answers: Strata's docs rank Q2_0 as "good" and IQ3_XXS as "great", and nothing here measures quality.
  * **MTP leaves less room than DFlash2 for experts.** The IQ3_XXS runs had 7,107 expert slots with MTP and 7,824 with DFlash2 ( 77% and 86% of the experts resident ), because the MTP layer takes 835 MiB of VRAM against 591 MiB for the DFlash2 drafter.
  * **Conversation-cache reuse depends on free RAM at the start.** Strata sizes the cache from the free RAM when it starts ( `conversation cache sized from the free RAM: N MiB for up to 4 parked conversations` ): 5,120 MiB in the DFlash2 start, 4,352 MiB for Q2_0 and only 1,024 MiB for the second IQ3_XXS MTP start,
    where six requests logged `skip parking (snapshot 1688-1733 MiB exceeds available budget)` and reuse fell to 93.6%. That start came a few minutes after another, and I think the page cache or locked memory from the previous one had not been given back, but I haven't checked. If reuse is low, look for those lines.
  * **IQ3_S with `ple-io=direct` is the slowest of the three sizes, and I can't say how much of that is the setting.** Over 56 requests with 100+ tokens out ( prompts 2,100-122,347 ) decode had a median of 134.0 tok/sec, 10.5% under IQ3_XXS's 149.8 and 19.4% under Q2_0's 166.2, and it was lower in the bands I can compare: 15-45k 134.8 against 165.3 ( IQ3_XXS ) and 163.7 ( Q2_0 ), over 65k 126.4 against 144.8 ( IQ3_XXS; Q2_0 has no data there ), 45-65k 150.2 against 148.5 and 167.6 ( only 6 requests ).
    The median first token took 0.71 s ( p90 2.65 s ) against 0.45 s and 0.38 s ( p90 1.3-1.5 s ), and the 3 cold prompts over 5k tokens took 8.6 s against 5.9 s and 5.8 s ( 6 and 2 of them ), which is the figure I'd watch for the cost of direct reads. Acceptance ( 72% ) and reuse ( 97.5% ) are the same as the others, and `skip parking` never appeared.
    **Three things differ from the other runs at once:** the model is bigger ( IQ3_S has 6,109 expert slots in VRAM against 7,107 for IQ3_XXS and 12,288 for Q2_0 ), the PLE table is read from disk, and the task is different: the agent carried on from the results of the earlier IQ3_S test, so these prompts aren't the same work as the other sessions. IQ3_S can't start without `ple-io=direct`, so no run separates the first two.
    **To separate them**, run IQ3_XXS ( which works both ways ) with `set: ["ple-io=direct"]` through the same kind of session and compare it with the 149.8 above; I haven't. Memory after the session: 78.9 GiB used, 17.6 GiB available, no restarts in 34 minutes. The disk counters in `/metrics` can't be read back afterwards ( the 60-sample history was all zero once the session was idle ), so they would have to be sampled during one.
  * **Q2_0 leaves more spare:** GPU memory was 23.5 and 23.7 GiB with the model idle ( the engine sizes its expert cache to what is free, so that says little ), and `free` showed 8.8 GiB available ( for IQ3_XXS I saw 3.7, 4.5 and 15.4 GiB available at different times, so that is not a comparison ). The longest prompt in the pi session was 63k tokens; the long-prompt test below took it to 249k.
* **Long-prompt test on Q2_0 ( 2026-11-15, `llm_util.py longprompt` ).** The same filler prompts as for IQ3_XXS, now sent by a script in the repo: `python3 bin/llm_util.py longprompt` ( it finds how many tokens a line of filler is, sends 8k, 190k and 250k-token prompts in turn, each with a passphrase on its first line, checks the reply has it, and compares the pod's restart count before and after; `--tokens N` picks other sizes, and it takes `--engine` for the others ).

  | Prompt tokens | Cached | Seconds | Engine's read speed | Engine's decode | Draft accepted | Result |
  |---|---|---|---|---|---|---|
  | 7,985 | 0% | 3.8 | 2,282 tok/s | 167.0 tok/s | 71% | passphrase recalled |
  | 189,289 | 0% | 43.8 | 4,378 tok/s | 151.7 tok/s | 71% | recalled |
  | 249,031 | 72% | 22.6 | 3,122 tok/s | 137.2 tok/s | 65% | recalled |

  The pod didn't restart and the log has no out-of-memory, "too long" or truncation lines. Compared with IQ3_XXS on the same prompts ( 189,083 and 252,093 tokens ): Q2_0 read the cold 189k prompt in 43.8 s against 47.9 s with MTP and 41.2 s with DFlash2, and decoded at 151.7 and 137.2 tok/sec against 121.3 and 135.7 ( MTP ) or 139.6 and 138.3 ( DFlash2 ). Replies were 52-63 tokens, so the decode figures are rough. These are in [Q2_0's own section of SETUP-LLM-MTP.md](SETUP-LLM-MTP.md#16-strata--qwen38-flash-next-125b-q2_0-two-gpus-).
  GPU memory afterwards was 23.5 and 23.7 GiB, and 8.3 GiB of RAM was available. **What this doesn't show:** filler text and a one-word answer, one request at a time; not quality at depth, not a long reply on top of a 250k prompt, and not two requests together.
* **Drafting fell back to MTP at first.** The plan said DFlash2 drafting, but the log said `Drafting: MTP (DFlash2 isn't available in this mode)`, and above it `The DFlash2 download did not finish ... 0 of 1206140008 bytes ( URLError: [Errno -3] Temporary failure in name resolution )`. So the 1.2 GB drafter file was never fetched ( `/data/Strata-data/dflash2` is empty ),
  and Strata carried on with the MTP. A restart showed the same with `[Errno -5] No address associated with hostname`. From inside the pod every lookup I tried afterwards worked ( including `us.aws.cdn.hf.co`, the host the download is redirected to ), and the same request returned `206` three times, so the failure comes and goes.
  I haven't found the cause: cluster DNS forwards to `192.168.0.24` ( the BIND server on `bnehyp02` ), then `203.12.160.35`, then `8.8.8.8`, and coredns's log has a few `i/o timeout` errors to those on 2026-11-14, none at the time of the start.
* **IQ3_S does not fit at 256k with MTP ( 2026-11-15 ).** The pod restarted six times and every start died about 70 seconds into the load: `lastState` said `OOMKilled` ( exit 137 ), the node logged a `SystemOOM` event naming `strata` for each, and `MemoryPressure` was False afterwards. The pod has no memory limit ( its cgroup says `max` ), so this is the node's own kernel OOM killer, not Kubernetes.
  What the engine's log said just before each death, against the models that did start:

  | Model | Expert arena in RAM | PLE table locked in RAM | Together | Share of the 94.2 GiB the engine saw |
  |---|---|---|---|---|
  | `Q2_0` | 31.6 GiB ( 16,201 huge pages ) | 26.8 GiB | 58.4 GiB | 62% |
  | `IQ3_XXS` | 40.0 GiB ( 20,464 huge pages ) | 26.8 GiB | 66.8 GiB | 71% |
  | `IQ3_S` | **46.8 GiB** ( the log's `expert arena still to fill` ) | 26.8 GiB | **73.6 GiB** | **78%** |

  On top of that come the KV pinned in RAM ( 1.55 GiB at 256k, 0.77 GiB at 128k, so context isn't the cause ), the engine's own buffers, the page cache for reading an 85 GB model, and the node's other processes. I haven't measured what tipped it over, so "78% of the engine's RAM is too much" is my reading of the log, not a finding, and the two models that fit leave only 5 to 9 points between them and the one that doesn't.
  Strata's own docs say the experts need RAM for the experts plus about 10 GB, which for IQ3_S is about 60 GB of 94 GB and looks like it should fit; the PLE table's 26.8 GiB isn't in that figure.

  **Things worth trying, cheapest first ( none tried yet ):**
  1. **`--ple-io direct`** ( through `STRATA_SET: "ple-io=direct"`; the launcher turns a lowercase name into `--ple-io` ). The engine's help says the default `direct` reads the PLE table straight from the SSD without it ever entering RAM, and that `ram` locks it all; the log says `--ple-io ram chosen by the residency policy ( the table is 26.8 GiB, ... 87.5 GiB of RAM is available )`, so Strata chose `ram` itself.
     That would give back 26.8 GiB, at the cost of SSD reads on the token path, which will cost speed; Strata has a `--ple-row-cache` for that ( default 1,048,576 rows ). `STRATA_PLE_AUTO_RAM=0` is the engine's own switch for the same decision ( the engine binary contains that name ).
     **Caution about `set:`.** `launch --plan` accepts any `--set` value, including `drafting=mtp`, which then killed the engine ( `unknown argument` ), so a clean plan proves nothing. The engine's own help does list `--ple-io` and `--ram-keep-gib`, which is the better evidence that it accepts them; I haven't started it with either. Try it on a model that fits first ( Q2_0 ), so a bad flag doesn't crash-loop the node again.
     **Tried on Q2_0 ( 2026-11-15 ): accepted, and it works.** The engine log showed `PLE direct read probe: 52375 rows/s (fine)` in place of `PLE table locked in RAM`, and the start took 1 min 31 s. Same 256k context, same MTP. With the model idle after a start:

     | | PLE table in RAM ( before ) | `ple-io=direct` ( after ) |
     |---|---|---|
     | `free`: used | 88,201 MiB | 60,254 MiB |
     | `free`: available | 8,285 MiB | 36,232 MiB |
     | engine `VmLck` ( locked memory ) | 26.8 GiB | 0 kB |

     That is about 27.9 GiB more available, which matches the 26.8 GiB table. The expert arena was unchanged ( 16,201 huge pages ).
     **Speed, with `llm_util.py longprompt` on the same filler prompts** ( the engine's decode figure ): 8k tokens 150.5 then 179.6 tok/sec ( first and second request ) against 165.6 and 167.0 before; 189k tokens 145.8 against 151.7; 249k tokens 142.6 against 137.2. Reading the cold 189k prompt took 43.7 s against 43.8 s. So no visible cost, and no stall, on these prompts.
     **That test flatters the setting.** The filler is a 20-word loop, so the same n-gram rows are fetched over and over and Strata's row cache ( 1,048,576 rows ) answers nearly all of them from RAM. A real agent session reads varied text, which Strata's warning says can stall a prompt on a slow disk ( not the case here, see below ).
     The log did print that warning for `sdb`, but the node is a virtual machine and `lsblk` shows its disks as `QEMU HARDDISK` with `ROTA 1`, which a virtual disk reports whatever it sits on. The owner confirms every drive on the hypervisor ( `bnellm01` ) is an SSD, so the warning is wrong for this setup, and the probe's 52,375 rows/s fits that. I haven't measured a real session.
     **What would settle it:** a pi session on Q2_0 as it is now, compared with the 166.2 tok/sec median of the earlier session ( 45 requests ), and its first-token times ( earlier median 0.38 s ). **Not done, and the Q2_0 entry no longer sets it** ( the trial ended when IQ3_S became the target ); see the note on Strata remembering it, below.
* **IQ3_S starts with `ple-io=direct` ( 2026-11-15 ).** Same model files, 256k context, MTP; the only change was `set: ["ple-io=direct"]`. It was Ready 2 min 00 s after the pod was created, with no restart and the node staying Ready ( a watcher was running, ready to scale to 0 on the first `OOMKilled`; it wasn't needed ).
  The engine log: `PLE direct read probe: 59611 rows/s (fine)`, an expert arena of 23,983 huge pages ( 46.9 GiB, as the failing starts planned ), an expert cache of 6,109 slots ( IQ3_XXS with MTP got 7,107 ), `VmLck` 0 kB. GPU memory 23,451 and 23,575 MiB ( 22.9 and 23.0 GiB ).
  Host memory ( `free` ): 70,343 MiB used and 26,142 MiB available just after the start, **76,201 used and 20,285 available after the long-prompt test**, against about 60,000 used and 36,000 available for Q2_0 with the same setting. So it fits, with about 20 GiB spare once it has been used rather than the 36 GiB Q2_0 keeps; the 8 GiB conversation cache and the page cache both grow, and I haven't watched it over a long session.
  **Long prompts** ( `llm_util.py longprompt`, same filler as before; engine's own figures ):

  | Prompt tokens | Cached | Seconds | Engine's read speed | Engine's decode | Draft accepted | Result |
  |---|---|---|---|---|---|---|
  | 7,985 | 0% | 4.2 | 2,171 tok/s | 150.5 tok/s ( 115.6 on the first, 8,493-token request ) | 77% | passphrase recalled |
  | 189,289 | 0% | 48.1 | 3,991 tok/s | 135.4 tok/s | 82% | recalled |
  | 249,031 | 72% | 25.1 | 2,819 tok/s | 136.3 tok/s | 78% | recalled |

  No restart and no out-of-memory, "too long" or stall lines. Against the same prompts on the other sizes ( cold 189k read: Q2_0 43.7 s, IQ3_XXS 47.9 s with MTP and 41.2 s with DFlash2, IQ3_S 48.1 s; decode at 189k and 249k: Q2_0 145.8 and 142.6, IQ3_XXS 121.3 and 135.7, IQ3_S 135.4 and 136.3 ) it is in the same range. Replies were 74-77 tokens, so the decode figures are rough, and the filler repeats its n-grams, so this still says nothing about what direct PLE reads cost on varied text.
  **Not measured:** a pi session on IQ3_S ( no baseline for it exists ), quality against IQ3_XXS and Q2_0, and memory over a long session. The log printed the rotational-disk warning again; the drives are SSDs, so it is wrong here.
  **Strata remembers `ple-io` between starts, so taking it out of a registry entry doesn't turn it off.** With IQ3_S running, the saved launch preset ( `/data/config/strata/launch-presets/docker.json` ) holds `"extra": {"--ple-io": "direct"}` and the run config has it in `user_args` and `launch_set`. The same thing kept `--drafting` alive after I removed it earlier, because every start reads the preset again.
  So Q2_0 and IQ3_XXS, which set nothing, would keep reading the PLE table from disk after IQ3_S ( I didn't switch back to check ). **The role now clears this at every start** ( below ), so switching models needs no manual step; until that is applied, the by-hand way is: scale the deployment to 0, set the preset's `extra` to `{}` and remove the `--ple-io` entries from the run config's `args`, `user_args` and `launch_set` ( the one-off pod procedure above ), run `kubectl -n dev-strata set env deploy/strata STRATA_SET-`, and scale up.
* **The role resets Strata's saved settings at every start ( 2026-11-15 ).** An init container ( `reset-saved-settings`, the Strata image with the volume mounted, no GPU ) runs before the engine and moves two files aside: the launch preset `/data/config/strata/launch-presets/docker.json` and the run config `/data/state/strata/launch/docker.json`. It copies them to `/data/state/strata/reset-backup/` first ( overwriting the previous copies ), and leaves the choices file, the engine log and the install files alone.
  Strata writes both files again from the environment on that start, so the registry entry and the container's variables are all that count: a setting an earlier entry used ( `ple-io`, a drafter, `kv`, `parallel` ) is gone when the next entry doesn't name it. Switch off with `strata_reset_saved_settings: false` ( that doesn't remove the init container from an existing Deployment; the patch command is in the role defaults ).
  Two related changes: **`strata_drafting` defaults to `mtp`** ( it used to be unset, and the entries only ran MTP because they inherited it from the saved preset; say `auto` or `dflash2` in an entry for Strata's own choice ), and **the template now always writes `CONTEXT`, `PARALLEL`, `KV`, `VISION`, `DRAFTING` and `STRATA_SET`, empty when an entry leaves them out.** The playbook merges into the live Deployment and a variable missing from the template is not removed from it, which is why `STRATA_SET` outlasted the entry that set it; the image treats an empty value as unset ( it tests each with `[ -n ... ]` ).
  `strata_env` entries are still only written when set, so one removed from an entry stays until `kubectl -n dev-strata set env deploy/strata NAME-`. **Applying this restarts the pod** ( the Deployment's spec changes ), which ends a running session.
  **Tested offline, then on the cluster ( 2026-11-15 ).** Offline: the rendered Deployment with the defaults, with settings, and with the reset off; the init script against a fake volume; and that the image's entrypoint tests those variables with `-n`.
  On the cluster, `model load flash-next-q2-0` from IQ3_S ( which had `ple-io=direct` saved ):
  * the Deployment came out with `MODEL=Q2_0`, `STRATA_SET=''` ( written empty, where before it stayed `ple-io=direct` ), `DRAFTING=mtp` and the new init container;
  * the init container ended `Completed` and logged `removed ... docker.json ( a copy is in /data/state/strata/reset-backup )` for both files; the backup preset still shows `"extra": {"--ple-io": "direct"}` and `"model": "IQ3_S"`, so the copy is of the state that was cleared;
  * afterwards the saved preset had `"extra": {}`, `"model": "Q2_0"`, and the run config had no `user_args` or `launch_set`, one `--ple-io ram` and no `--drafting`;
  * the engine's start line had `--ple-io ram`, `--kv int8` ( Strata's default ) and `--mtp`, the log said `PLE table locked in RAM (--ple-io ram)` and `strata mtp: draft layer loaded`, `VmLck` was back to 26.8 GiB ( 28,125,136 kB ), and `/health` said `max_context: 262144`.
  So nothing from IQ3_S was carried over, and no manual step was needed. **The start took 2 min 30 s to Ready, against about 1 min 30 s for the earlier Q2_0 starts;** the log shows the PLE table taking 36.8 s to lock in RAM, against 2.1 s before. My guess is that the file cache no longer held the table after IQ3_S read it from disk instead; I haven't checked.
  Not tested: a model with `kv` or `parallel` set followed by one without, and DFlash2 ( `strata_drafting: auto` ) after the reset.
* **What Strata exposes about PLE reads ( 2026-11-15 ).** Nothing directly. Its `/metrics` has 38 Prometheus series and 125 keys of JSON, and none of the names mention PLE, n-gram rows, row-cache hits or stalls. The closest are disk counters in the JSON: `hardware.disk_read_mb` ( 4.3 at one instant ) and a 60-sample `history.disk_read_mb` ( min 0.56, max 7.58, mean 2.67 MB per sample; I don't know the sample interval ),
  from a pi session on IQ3_S. Those count every read from the disk, including the model's expert files, so they can't be split into PLE reads, and `llm.py metric strata` doesn't print them. The engine's log has the startup probe ( `PLE direct read probe: 59611 rows/s` ) but no per-request count. A fair test of the cost is therefore still end to end: first-token and decode times in a real session, with and without the setting.
* **The node went NotReady during the IQ3_S crash loop ( 2026-11-15 ).** `bnenod05` reported `Kubelet stopped posting node status` at 04:52:24 UTC and recovered at 04:59:51; the strata pod sat in `Terminating` and langfuse and opencode, on the same node, went with it. An earlier NotReady, about 25 minutes before, cleared itself within a minute. The kubelet service itself stayed up; its log was full of `lookup bnekub03.dev.randomnoun on 127.0.0.53:53: no such host`, so it couldn't reach the API server's name.
  `systemd-resolved` logged `Under memory pressure, flushing caches` at 04:52:28 and 04:54:18, four seconds after the NotReady began; later, `resolvectl query` worked, and BIND answered the name correctly when I asked it directly. I think the memory pressure from the strata loads is what set this off, but I haven't shown how the cache flush became a failed lookup, and the BIND server has been dropping queries independently ( see the DFlash2 note above ), so that may have contributed. What I can say is that every NotReady came during the loop of out-of-memory kills.
  Because the pod has no memory limit, nothing stopped it taking what the node needed for itself. Strata's docs say not to set one; a limit well under the node's 94 GiB ( say 80 ) would make the kernel kill the container instead, and I haven't tried it.
  2. **`--ram-keep-gib 12`** ( or `STRATA_RAM_KEEP_GIB=12` ): the help says the arena is used only if that much stays free after it, otherwise the hottest experts go in a smaller RAM copy and the rest are read from the files. It trades RAM for SSD reads too, but it is the setting built for this.
  3. **`kv: "k8v4"`**: saves about 0.4 GiB of pinned RAM at 256k, and the context isn't the cause, so it won't be enough alone.
  4. **Smaller context** saves only 0.8 GiB pinned RAM at 128k. Not worth it for this.
  5. **No MTP** ( `drafting: "off"` ) saves the 835 MiB draft layer in VRAM and some RAM; DFlash2's drafter is 591 MiB. Small.
  6. **Memory-locked RAM**: the earlier runs locked 26.8 GiB; checking `VmLck` and `free` at the moment of the kill would show whether it's the arena or the page cache, but the container exits too fast to catch. A read-only look at `/proc/meminfo` from a second pod during the load, or `dmesg` on the node ( `bnenod05` needs a shell ), would show the kill's own memory table.
  I'd try 1 first and check IQ3_XXS's speed against it, because a PLE table in RAM is what Strata chose for speed.
* **Memory is nearly full, by design.** With the model idle, each GPU is at about 97% ( 23.4 of 24 GiB ) and RAM is at about 83-89 GiB of 94, of which the engine locks 26.8 GiB. Keep every other engine at 0, and watch for out-of-memory errors on long prompts.
* **`/health`** returns JSON with `status`, `max_context`, `model`, `loaded` and `service`, and works without the key; `/v1/models` needs it. The model name it reports is `qwen3.8-flash-next-iq3_xxs`. Strata serves any name it is sent, and I haven't checked what litellm sends.
* **The probes.** The pod was `1/1` by the morning, so they didn't hold it back, but I didn't watch it go Ready, so I don't know what `/health` returned while it was downloading or loading.
* **Prefix cache.** `vllm:prefix_cache_hits_total` counts the conversation cache: 97.9% of prompt tokens after a pi agent session, against 98.1% from the engine log's own totals.

**Not confirmed:** output quality; tool calls and thinking through litellm and pi; that litellm passes `reasoning_budget_tokens` on; and any session longer than the 63 requests above.

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
  Strata saves the choice in its launch preset ( `/data/config/strata/launch-presets/docker.json`, `"drafting": "mtp"` ), so a registry entry that no longer sets `drafting` keeps the last one: say `auto` to go back.
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
  in 2 minutes and answered long test prompts correctly ( [What was found running it](#what-was-found-running-it-2026-11-15) ). Strata's docs say 262K for IQ3_XXS is "not measured". The `Q2_0` entry is at 256k too, and started, served a pi session ( prompts up to 63k tokens ) and answered a 249k-token test prompt correctly; `IQ3_S` is set to 256k but hasn't been started, and is the one most likely not to fit. If a longer context fails to start or a long prompt restarts the pod, add `kv: "k8v4"` ( keys at 8 bits, values as rotated 4-bit: 23% less KV memory, prompts 2-5% slower ), then go back a step.
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
| `flash-next-iq3-s` | about 54.8 GB ( est.; Strata's plan said 83.6 GB, which counts the shared shard, and I haven't checked that IQ3_S shares it ) | about 19 min ( est. ) | 47 MB/s | about 20 s ( est. ) | about 21 min ( est. ) |

The download time includes the image already being on the node and Strata's own checks, so the rate is a little under the true one. Strata's docs measured only 16-19 MB/s from Hugging Face without a login, so allow double. The pod is `0/1` for the whole of that,
so a first `engine set strata` times out at its default of 15 minutes: the deployment carries on, and `python bin/llm.py engine set strata --timeout 1h` waits for it ( `model load` doesn't wait at all ). I haven't started IQ3_S. The log's `Downloading the model files: N of 67.3 GB` for Q2_0 counted the shared shard in its total, and its first line, 6.7 GB, came at 02:17:06, two minutes in.

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
* **Four pi sessions on the same engine log ( 2026-11-15 ).** The engine log keeps every start, so I split it at the `engine started` lines ( the figures leave out the 53-token warm-up and my own test prompts; "decode" is the median over requests with 100+ tokens out ):

  | Run | Requests ( 100+ out ) | Largest prompt | Decode median ( p10-p90 ) | 1,000+ out | Draft accepted | Prompt tokens reused | Median first token |
  |---|---|---|---|---|---|---|---|
  | IQ3_XXS, MTP, 128k-256k | 183 ( 172 ) | 102,583 | **149.8** ( 131-170 ) | 158.1, n = 57 | 71% | 98.3% | 0.45 s |
  | IQ3_XXS, DFlash2, 256k | 86 ( 76 ) | 102,092 | **149.2** ( 128-183 ) | 141.1, n = 22 | 61% | 98.4% | 0.40 s |
  | IQ3_XXS, MTP again, 256k | 64 ( 46 ) | 65,950 | **140.7** ( 121-167 ) | 148.2, n = 7 | 70% | **93.6%** | 0.36 s ( p90 3.7 s ) |
  | **Q2_0**, MTP, 256k | 45 ( 38 ) | 63,267 | **166.2** ( 146-195 ) | 165.6, n = 16 | 73% | 96.9% | 0.38 s |

  Different tasks each, one session each, and the last two were shorter, so differences of a few tok/sec between rows mean little. What I'd say:
  * **Q2_0 is about 11% faster than IQ3_XXS with the same drafter** ( 166.2 against 149.8 and 140.7 ). By prompt size it is 155.7, 163.7 and 167.6 tok/sec for under 15k, 15-45k and 45-65k, against 163.2, 165.3 and 148.5 for the first IQ3_XXS run, so the lead is in the 45-65k band and the others are level or lower ( n = 5, 12 and 21; I have no Q2_0 data over 65k ). Strata puts 99% of its experts in VRAM against 77% for IQ3_XXS with MTP, so a lead is plausible, but I haven't tested why.
    I haven't compared the answers: Strata's docs rank Q2_0 as "good" and IQ3_XXS as "great", and nothing here measures quality.
  * **MTP leaves less room than DFlash2 for experts.** The IQ3_XXS runs had 7,107 expert slots with MTP and 7,824 with DFlash2 ( 77% and 86% of the experts resident ), because the MTP layer takes 835 MiB of VRAM against 591 MiB for the DFlash2 drafter.
  * **Conversation-cache reuse depends on free RAM at the start.** Strata sizes the cache from the free RAM when it starts ( `conversation cache sized from the free RAM: N MiB for up to 4 parked conversations` ): 5,120 MiB in the DFlash2 start, 4,352 MiB for Q2_0 and only 1,024 MiB for the second IQ3_XXS MTP start,
    where six requests logged `skip parking (snapshot 1688-1733 MiB exceeds available budget)` and reuse fell to 93.6%. That start came a few minutes after another, and I think the page cache or locked memory from the previous one had not been given back, but I haven't checked. If reuse is low, look for those lines.
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
* **Memory is nearly full, by design.** With the model idle, each GPU is at about 97% ( 23.4 of 24 GiB ) and RAM is at about 83-89 GiB of 94, of which the engine locks 26.8 GiB. Keep every other engine at 0, and watch for out-of-memory errors on long prompts.
* **`/health`** returns JSON with `status`, `max_context`, `model`, `loaded` and `service`, and works without the key; `/v1/models` needs it. The model name it reports is `qwen3.8-flash-next-iq3_xxs`. Strata serves any name it is sent, and I haven't checked what litellm sends.
* **The probes.** The pod was `1/1` by the morning, so they didn't hold it back, but I didn't watch it go Ready, so I don't know what `/health` returned while it was downloading or loading.
* **Prefix cache.** `vllm:prefix_cache_hits_total` counts the conversation cache: 97.9% of prompt tokens after a pi agent session, against 98.1% from the engine log's own totals.

**Not confirmed:** output quality; tool calls and thinking through litellm and pi; that litellm passes `reasoning_budget_tokens` on; and any session longer than the 63 requests above.

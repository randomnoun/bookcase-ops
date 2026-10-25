# SETUP-GRAFANA.md

Grafana charts the metrics stored in prometheus.

## Logging in

* Connect to https://grafana.dev.randomnoun/
* The initial username/password is `admin` / `abc123` 

## Setting a datasource

* From the left-hand side menu, select Connections -> Data sources
* Add a prometheus datasource, with the URL of `http://prometheus-prometheus.prometheus.svc.cluster.local:9090`
* Click the 'Explore' button at the bottom to check it connects OK and that metrics are appearing

## Dashboards

You'll find that a lot of dashboards that purport to work with nginx or k8s don't work, or at least, don't work with this particular configuration.

The ones you want are the 'dotdc' dashboards here: https://grafana.com/grafana/dashboards/?plcmt=top-nav&cta=downloads&search=dotdc

### Sonar ( LLM backend )

Gives tokens/sec, latency, queue depth and KV cache usage for the `sonar` backend. 

Prometheus only scrapes it because the `sonar` role creates a `ServiceMonitor` in the `dev-sonar` namespace ( see [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md#sonar) ). Before importing anything, 
check that Prometheus has found it: open `https://prometheus.dev.randomnoun/targets` and look for `serviceMonitor/dev-sonar/sonar`, which should be `UP`. 
If it isn't listed, check that the `dev-sonar` namespace has the `prometheusEnabled=true` label and the ServiceMonitor has the `prometheus: default` label.

Sonar is a fork of vLLM, so vLLM's own dashboard works once the metric names are changed from `vllm:` to `aphrodite:`:

```
curl -sL https://raw.githubusercontent.com/vllm-project/vllm/main/examples/observability/prometheus_grafana/grafana.json \
  | sed -e 's/vllm:/aphrodite:/g' -e 's/"title": "vLLM"/"title": "Sonar"/' > sonar-dashboard.json
```

Then in Grafana go to Dashboards -> New -> Import, upload `sonar-dashboard.json`, and select the prometheus datasource when asked.

The dashboard's 24 metrics all exist on sonar's `/metrics` endpoint, but I haven't checked every panel renders, so edit any that come up empty. 
If a panel is empty, find the metric in Explore ( type `aphrodite:` and let it autocomplete ), which is quicker than guessing.

Useful queries if you'd rather build your own panels:

| What | Query |
|---|---|
| Generation tokens/sec | `sum(rate(aphrodite:generation_tokens_total[1m]))` |
| Prompt tokens/sec | `sum(rate(aphrodite:prompt_tokens_total[1m]))` |
| Prefix cache hit rate | `sum(rate(aphrodite:prompt_tokens_cached_total[5m])) / sum(rate(aphrodite:prompt_tokens_total[5m]))` |
| KV cache usage | `aphrodite:kv_cache_usage_perc` |
| Requests running / queued | `aphrodite:num_requests_running`, `aphrodite:num_requests_waiting` |

Note that this is token throughput across all requests, so it's the sum over concurrent requests. The per-request decode speed is in sonar's log lines 
( `kubectl -n dev-sonar logs -f deploy/sonar | grep "Request completed"` ), which is the number to use when comparing one backend with another.

### GPUs ( dcgm-exporter )

Gives GPU utilisation, VRAM, power draw, temperature and clocks for the two RTX 3090s on `bnenod05`, which is the other half of the picture when you're 
looking at sonar's tokens/sec: it shows whether the cards are actually the bottleneck. 

It's installed by the `dcgm-exporter` system role ( see [ansible/README.md](../ansible/README.md) ), which needs the `gpu-helm-charts` helm repo and the `prometheus` role applied first. 
The exporter doesn't request `nvidia.com/gpu`, so it doesn't compete with ollama / sonar / tabbyapi for the GPUs, and it reads them while one of them is running. 
Check Prometheus has found it at `https://prometheus.dev.randomnoun/targets` ( `serviceMonitor/dcgm-exporter/dcgm-exporter` should be `UP` ), 
then try `DCGM_FI_DEV_GPU_UTIL` in Explore; you should get one series per GPU.

To add a dashboard, go to Dashboards -> New -> Import, enter the ID `12239` ( "NVIDIA DCGM Exporter Dashboard" ) and click Load, then select the prometheus datasource. 
It has 8 panels, and all of the metrics they use are in the exporter's default counters.
That dashboard is from 2021, so some panels may need tweaking. If one's empty, look the metric up in Explore.

Queries for your own panels:

| What | Query |
|---|---|
| GPU utilisation % | `DCGM_FI_DEV_GPU_UTIL` |
| VRAM used ( MiB ) | `DCGM_FI_DEV_FB_USED` |
| VRAM free ( MiB ) | `DCGM_FI_DEV_FB_FREE` |
| Power draw ( W ) | `DCGM_FI_DEV_POWER_USAGE` |
| Temperature ( C ) | `DCGM_FI_DEV_GPU_TEMP` |

Putting `rate(aphrodite:generation_tokens_total[1m])` and `DCGM_FI_DEV_GPU_UTIL` on the same graph is the quickest way to see the relationship between throughput and GPU load.

The exporter's profiling metrics ( `DCGM_FI_PROF_*` ) aren't all supported on GeForce cards, so if one is missing it may be a hardware limitation and not a setup problem. 

### Not scraped yet

Only `sonar` and the GPUs are scraped. `ollama` has no ServiceMonitor, and `litellm` serves `/metrics` behind authentication and needs its prometheus callback enabled first.

### Tabbyapi ( no metrics )

`tabbyapi` can't be scraped: it has no Prometheus support. I checked its source ( the server, router, config, logger and the ExLlamaV3 backend ) for `prometheus`, `/metrics` and OpenTelemetry and found none, 
its config sample has no metrics option, and I couldn't find a community exporter or dashboard for it. So there's no ServiceMonitor for it and nothing to import. 
I haven't checked this against a running tabbyapi, only its source, so it's worth a `curl http://localhost:5000/metrics` after a `kubectl port-forward` the first time you run it.

What you can use instead, when comparing it with sonar:

* **Per-request speed:** tabbyapi logs a line after each generation, with tokens generated, tokens/sec, prompt tokens and how much of the prompt was cached, and queue / prompt / generation times. 
  Compare its generation tokens/sec with sonar's `Decode: ... tokens/s`:

  ```
  kubectl -n dev-tabbyapi logs -f deploy/tabbyapi | grep -i "tokens generated"
  kubectl -n dev-sonar logs -f deploy/sonar | grep "Request completed"
  ```

  The wording of tabbyapi's line is from reading its source, not from seeing it run, so adjust the `grep` if it doesn't match.
* **Over time, per model:** langfuse gets traces for both backends through litellm, so latency and token counts can be compared there by model name.
* **GPU load:** `dcgm-exporter` doesn't care which backend is using the cards, so the GPU dashboard above works for tabbyapi too.

For reference, with the same Qwen3.8-27B 5bpw model and tensor-parallel on both, tabbyapi decodes at about 47-48 tokens/sec and sonar at about 52. 
tabbyapi's log line also shows `(tensor parallel)` or `(autosplit)` when the model loads, which is the first thing to check if its speed looks low.

What you won't get for tabbyapi is the engine-level view that sonar's dashboard has ( queue depth, KV cache usage, inter-token latency ). 
If that turns out to matter, the usual workaround is a small sidecar that tails the log line and exposes the numbers as metrics, which would be a custom thing to maintain.



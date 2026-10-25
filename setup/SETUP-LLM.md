# bookcase-ops LLM setup

This page is the index; details are in these files:

| File | What's in it |
|---|---|
| [SETUP-LLM-ARCHITECTURE.md](SETUP-LLM-ARCHITECTURE.md) | The diagram of how the pieces connect, and why |
| [SETUP-LLM-KUBERNETES.md](SETUP-LLM-KUBERNETES.md) | Creating and configuring the GPU kubernetes node `bnenod05` |
| [SETUP-LLM-MIDDLEWARE.md](SETUP-LLM-MIDDLEWARE.md) | `langfuse`, `litellm` and `searxng` |
| [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md) | The engines that run the models: `ollama`, `sonar` and `tabbyapi` |
| [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md) | `open-webui`, `pi`, `opencode` and `openhands` |

Everything goes through `litellm`, so the frontends never talk to a backend directly.
Only one of `ollama`, `sonar` and `tabbyapi` can use the GPUs at a time, see [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).

## Steps

OK so first you'll need to install **proxmox** on the new `bnellm01` machine that has the GPUs in it - see [SETUP-PROXMOX.md](SETUP-PROXMOX.md).

Then:  

* Create the VM template `tpl-ubuntu-kubernetes-node` via packer. See [../packer-ubuntu-kubernetes-node/README.md](../packer-ubuntu-kubernetes-node/README.md).
* Clone that template to create the `bnenod05` kubernetes node; it'll join the `bnekub03` cluster
* Attach the GPUs to `bnenod05`
* Start installing the containers 

The first three are in [SETUP-LLM-KUBERNETES.md](SETUP-LLM-KUBERNETES.md).

## Kubernetes deployments

Jump over to [ansible/README.md](../ansible/README.md) and get some containers running on the node.

I've created them in this order, you may need to do some configuration in each container to connect them up as you go.

* langfuse ( [middleware](SETUP-LLM-MIDDLEWARE.md#langfuse) )
   * needs postgres, clickhouse and rustfs set up, and secrets in vault; it goes first so litellm has somewhere to send traces
* litellm ( [middleware](SETUP-LLM-MIDDLEWARE.md#litellm) )
   * and create a user in the web UI
* ollama ( [backends](SETUP-LLM-BACKEND.md#ollama) )
   * then install a few models in ollama
   * then configure those models in litellm
* pi ( [frontends](SETUP-LLM-FRONTEND.md#pi) )
   * will connect to litellm (not ollama directly), create a litellm key first

And then once you've got that going, can try these deployments, which will add web search and let you try out some different frontends and backends 

* searxng ( [middleware](SETUP-LLM-MIDDLEWARE.md#searxng) )
   * then configure searxng in litellm
* sonar ( [backends](SETUP-LLM-BACKEND.md#sonar) )
   * needs an image you build yourself, and starts scaled to 0
* tabbyapi ( [backends](SETUP-LLM-BACKEND.md#tabbyapi) )
   * needs two keys in vault, and starts scaled to 0
   * then download an EXL3 model and configure it in litellm
* open-webui ( [frontends](SETUP-LLM-FRONTEND.md#open-webui) )
   * will connect to litellm (not ollama directly), create a litellm key first
* opencode ( [frontends](SETUP-LLM-FRONTEND.md#opencode) )
   * will connect to litellm (not ollama directly), create a litellm key first
* openhands ( [frontends](SETUP-LLM-FRONTEND.md#openhands) )
   * will connect to litellm (not ollama directly), create a litellm key first

Only one of `ollama`, `sonar` and `tabbyapi` can use the GPUs at a time, see [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).

Metrics and dashboards for these ( sonar's tokens/sec, GPU load ) are in [SETUP-GRAFANA.md](SETUP-GRAFANA.md).

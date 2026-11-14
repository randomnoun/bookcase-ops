# bookcase-ops LLM setup

These docs go through the LLM setup for bookcase-ops, it's still in a bit of flux at the moment whilst I work out what works and what doesn't.

| File | What's in it |
|---|---|
| [SETUP-LLM-ARCHITECTURE.md](SETUP-LLM-ARCHITECTURE.md) | The diagram of how the pieces connect |
| [SETUP-LLM-KUBERNETES.md](SETUP-LLM-KUBERNETES.md) | Creating and configuring the GPU kubernetes node `bnenod05` |
| [SETUP-LLM-MIDDLEWARE.md](SETUP-LLM-MIDDLEWARE.md) | `langfuse`, `litellm` and `searxng` |
| [SETUP-LLM-BACKEND.md](SETUP-LLM-BACKEND.md) | The engines that run the models: `ollama`, `sonar`, `tabbyapi`, `ninfer`, `mistralrs` and `sglang`, and how to switch between them and their models |
| [SETUP-LLM-BE-OLLAMA.md](SETUP-LLM-BE-OLLAMA.md) | Setting up `ollama` |
| [SETUP-LLM-BE-SONAR.md](SETUP-LLM-BE-SONAR.md) | Setting up `sonar`, including building it |
| [SETUP-LLM-BE-TABBYAPI.md](SETUP-LLM-BE-TABBYAPI.md) | Setting up `tabbyapi` |
| [SETUP-LLM-BE-NINFER.md](SETUP-LLM-BE-NINFER.md) | Setting up `ninfer` |
| [SETUP-LLM-BE-MISTRALRS.md](SETUP-LLM-BE-MISTRALRS.md) | Setting up `mistralrs` |
| [SETUP-LLM-BE-SGLANG.md](SETUP-LLM-BE-SGLANG.md) | Setting up `sglang` |
| [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md) | `open-webui`, `pi`, `opencode` and `openhands` |

To simplify configuration, all the frontends talk to `litellm`, which then connects to models running on the various backends.

Currently, only one of `ollama`, `sonar`, `tabbyapi`, `ninfer`, `mistralrs` and `sglang` can use the GPUs at a time, see [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).

Once everything's up and running there's a `llm.py` which can be used for switching engines and models, and keeping litellm's models in step with them. 

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

You should probably create the containesr in this order. You may need to do some configuration in each container to connect them up as you go, most of them have a webby frontend you can login to.

* langfuse ( [middleware](SETUP-LLM-MIDDLEWARE.md#langfuse) )
   * needs postgres, clickhouse and rustfs set up, and secrets in vault; it goes first so litellm has somewhere to send traces
* litellm ( [middleware](SETUP-LLM-MIDDLEWARE.md#litellm) )
   * and create a user in the web UI
* ollama ( [backends](SETUP-LLM-BE-OLLAMA.md) )
   * then install a few models in ollama
   * then configure those models in litellm
* pi ( [frontends](SETUP-LLM-FRONTEND.md#pi) )
   * will connect to litellm (not ollama directly), create a litellm key first

And then once you've got that going, can try these deployments, which will add web search and let you try out some different frontends and backends 

* searxng ( [middleware](SETUP-LLM-MIDDLEWARE.md#searxng) )
   * then configure searxng in litellm
* sonar ( [backends](SETUP-LLM-BE-SONAR.md) )
   * needs an image you build yourself, and starts scaled to 0
* tabbyapi ( [backends](SETUP-LLM-BE-TABBYAPI.md) )
   * needs two keys in vault, and starts scaled to 0
   * then download an EXL3 model and configure it in litellm
* ninfer ( [backends](SETUP-LLM-BE-NINFER.md) )
   * needs a key in vault, and starts scaled to 0
   * the model is downloaded by an init container the first time it starts
* mistralrs ( [backends](SETUP-LLM-BE-MISTRALRS.md) )
   * uses the official image, needs a HuggingFace token in vault, and starts scaled to 0
* sglang ( [backends](SETUP-LLM-BE-SGLANG.md) )
   * uses the official image, needs a HuggingFace token in vault, and starts scaled to 0
* open-webui ( [frontends](SETUP-LLM-FRONTEND.md#open-webui) )
   * will connect to litellm (not ollama directly), create a litellm key first
* opencode ( [frontends](SETUP-LLM-FRONTEND.md#opencode) )
   * will connect to litellm (not ollama directly), create a litellm key first
* openhands ( [frontends](SETUP-LLM-FRONTEND.md#openhands) )
   * will connect to litellm (not ollama directly), create a litellm key first

Only one of `ollama`, `sonar`, `tabbyapi`, `ninfer`, `mistralrs` and `sglang` can use the GPUs at a time, see [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).

Metrics and dashboards for these ( sonar's tokens/sec, GPU load ) are in [SETUP-GRAFANA.md](SETUP-GRAFANA.md).

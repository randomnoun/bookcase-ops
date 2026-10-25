# bookcase-ops LLM architecture

Part of the [LLM setup](SETUP-LLM.md).

A maze of twisty little components, all alike:

```mermaid
flowchart TB
    user(["You<br/>browser / terminal"])

    subgraph k8s ["kubernetes cluster bnekub03"]
        ingress["nginx ingress<br/>*.dev.randomnoun (TLS)"]

        subgraph agents ["front ends and agents"]
            openwebui["open-webui<br/>chat UI"]
            opencode["opencode<br/>serve + web UI"]
            openhands["openhands<br/>agent UI + server"]
            pi["pi<br/>terminal agent"]
        end

        litellm["litellm<br/>LLM proxy"]
        langfuse["langfuse<br/>traces + comparison<br/>(+ redis)"]
        searxng["searxng<br/>web search"]

        subgraph bnenod05 ["node bnenod05 (VM on Proxmox host bnellm01)"]
            subgraph backends ["backends (only one runs at a time)"]
                ollama["ollama<br/>:11434<br/>GGUF"]
                sonar["sonar<br/>:2242<br/>starts at 0 replicas"]
                tabbyapi["tabbyapi<br/>:5000<br/>EXL3<br/>starts at 0 replicas"]
            end
            gpus[/"2 x RTX 3090<br/>(PCI passthrough)"/]
            models[("/mnt/models<br/>local-path storage<br/>one PVC per backend")]
        end
    end

    subgraph bnellm01 ["Proxmox host bnellm01"]
        thin[("data<br/>LVM-thin pool")]
    end

    pg[("postgres<br/>bnesql02")]
    ch[("clickhouse<br/>bnesql02")]
    nfs[("NFS storage + rustfs<br/>bnenas05")]

    user -->|HTTPS| ingress
    user -.->|"kubectl exec -it deploy/pi -- pi"| pi

    ingress --> openwebui
    ingress --> opencode
    ingress --> openhands
    ingress --> litellm
    ingress --> langfuse

    agents -->|"OpenAI API<br/>/v1 + virtual key"| litellm
    litellm -->|"ollama_chat"| ollama
    litellm -->|"openai-compatible<br/>/v1"| sonar
    litellm -->|"openai-compatible<br/>/v1 + api key"| tabbyapi
    litellm -->|"search"| searxng
    litellm -->|"OTLP traces"| langfuse
    backends -->|"nvidia-device-plugin<br/>nvidia.com/gpu"| gpus
    backends --- models
    models -->|"1TB disk"| thin

    litellm -->|"db: litellm"| pg
    langfuse -->|"db: langfuse"| pg
    langfuse -->|"traces"| ch
    langfuse -->|"S3: events"| nfs
    openwebui -->|"db: openwebui"| pg
    openhands -->|"db: openhands<br/>(automations only)"| pg

    agents -.->|"PVCs<br/>(sessions, repos)"| nfs
```

So the plan is that whilst I'm still working out how this is supposed to work, I'll have a few different frontends / UIs, and a few different backends.

Everything will get funnelled through litellm, which will act as a proxy for the frontends to talk to the backends.  
This should keep most of the LLM config is centralised somewhere, and should make it easier to connect to remote LLMs later. 
Each frontend has a different litellm virtual API key, so I can see what's going where.

The backends are pinned to `bnenod05` ( they need the GPUs to operate). 
Each backend requests both GPUs, so only one can run at a time: `ollama` runs by default and `sonar` and `tabbyapi` start scaled to 0, see [Switching between engines](SETUP-LLM-BACKEND.md#switching-between-engines).

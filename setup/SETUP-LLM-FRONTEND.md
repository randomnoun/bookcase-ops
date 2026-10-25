# bookcase-ops LLM frontends

Part of the [LLM setup](SETUP-LLM.md).

These all talk to `litellm`, never to a backend directly, each using its own litellm virtual key.

## open-webui

A chat frontend for ollama's models, but it talks to `litellm` rather than `ollama` directly 
( `OPENAI_API_BASE_URL=http://web.dev-litellm.svc.cluster.local:4000/v1`, `ENABLE_OLLAMA_API=false`). 

Before you create, will need a litellm key stored in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md) for details.

## pi

Needs a litellm key in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md).

[pi](https://pi.dev) is an interactive terminal agent rather than a server, so the `pi` role has no Service, Ingress or certificate; 
it's just a long-lived pod that you shell into, so the agent runs in the cluster rather than on your laptop:

```
kubectl -n dev-pi exec -it deploy/pi -- pi
```

There's no published pi image, so the pod starts from `node:24-bookworm-slim` and installs pi ( plus git and ripgrep ) on startup, 
following the [Dockerfile in the pi docs](https://pi.dev/docs/latest/containerization). It takes a minute or so after each pod restart before `pi` is on the path;
`pi_version` is pinned in the vars file so a restart doesn't pick up a new release. 

At startup, if fetches the list of models from litellm. To pick up a model you've added to litellm since, run this, then open `/model` in pi ( which reloads the file ):

```
kubectl -n dev-pi exec deploy/pi -- pi-sync-models
```

Projects live in `/workspace` and sessions in `/root/.pi/agent`, both on the same 100Gi PVC. pi has no database option, so no postgres.

`pi-sync-models` writes `/root/.pi/agent/models.json`, and each model in it gets what litellm knows about it:

* **Context window:** the model's `num_ctx` from litellm's model settings, so pi knows when to compact. Ollama models without one get `ollama_context_length`.
* **Output limit:** pi allows 16384 output tokens per request unless told otherwise. Models behind an `api_base` listed in `pi_max_tokens_rules` ( in the pi vars file ) get a different `maxTokens`; at the moment 
  that's 32768 for tabbyapi, which doesn't stream a tool call's arguments, so writing a large file produces one long silent response that can use up the whole 16384.
* **Session id:** every request carries pi's session id as an `x-session-id` header, which litellm passes to langfuse so each pi session appears under Sessions there. 
  See [SETUP-LLM-MIDDLEWARE.md](SETUP-LLM-MIDDLEWARE.md#sessions).

The sync prints these as it writes them, e.g. `local-tabbyapi-qwen3.8:27b-5bpw (ctx 262144, max out 32768)`.

### One-time setup

These are changes to files on the PVC, not to the role, so they survive pod restarts but would need repeating on a new PVC. 

**Request timeout.** pi gives up on a request if nothing arrives for 5 minutes ( `httpIdleTimeoutMs`, default 300000 ). tabbyapi doesn't stream a tool call's arguments, it sends the
whole call at the end, so while the model writes a big file pi sees nothing for minutes and the request is cut ( pi reports `terminated` ). Raise the timeout to 15 minutes. 
This is a global setting and can't be set per model. It adds the key to `settings.json` and leaves the other settings alone:

```
kubectl -n dev-pi exec deploy/pi -- node -e '
const fs=require("fs"), f="/root/.pi/agent/settings.json";
const s=JSON.parse(fs.readFileSync(f,"utf8"));
s.httpIdleTimeoutMs=900000;
fs.writeFileSync(f+".tmp",JSON.stringify(s,null,2)+"\n");
fs.renameSync(f+".tmp",f);'

kubectl -n dev-pi exec deploy/pi -- cat /root/.pi/agent/settings.json
```

Then start a new pi session so it's read. `0` or `"disabled"` turns the timeout off. The role only copies `settings.json` in when it's missing, which is why this isn't in the role.

**Plan mode.** pi ships a plan mode extension, a read-only exploration mode where the edit and write tools are off and only read-only bash commands are allowed ( see the [pi changelog](https://pi.dev/changelog) ). 
It's included with pi as an example, so copy it into the agent's extensions folder:

```
kubectl -n dev-pi exec deploy/pi -- sh -c 'mkdir -p /root/.pi/agent/extensions && cp -r /usr/local/lib/node_modules/@earendil-works/pi-coding-agent/examples/extensions/plan-mode /root/.pi/agent/extensions/'
```

Then `/plan` ( or `Ctrl+Alt+P` ) toggles it, and `/todos` shows progress through the plan. The copy comes from the installed pi package, so after changing `pi_version` it may be worth repeating it to pick up a newer version.

## opencode

Needs the litellm key and server password in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md).

Accessible at `https://opencode.dev.randomnoun`. 

Is backed by a PVC instead of a database. The API is protected by HTTP basic auth ( user `opencode`, password from vault ).

opencode can't discover models, so the pod builds its model list from litellm's `/models` endpoint when it starts. 
To pick up a model you've added to litellm since:

```
kubectl -n dev-opencode rollout restart deploy/opencode
```

The same PVC holds the repositories it works on as well as its sessions and auth. The pod's home directory is `/data/workspace`, which is where
the web UI's "Add project" picker starts, so clone repositories straight into it:

```
kubectl -n dev-opencode exec deploy/opencode -- git clone https://github.com/<you>/<repo>.git /data/workspace/<repo>
```

The storage class allows volume expansion, so if that fills up, edit `opencode_volume_size_gb` and re-run the playbook.

The image is `ghcr.io/anomalyco/opencode` and is bare alpine, so the pod installs git, jq and curl on startup.

## openhands

See [SETUP-DATABASE.md](SETUP-DATABASE.md) for the database and vault steps.

OpenHands is now a single all-in-one image ( `ghcr.io/openhands/agent-canvas` ) containing the web UI, the agent server and an automation backend. 
This role is based on the [helm chart](https://github.com/OpenHands/OpenHands/tree/main/helm/agent-canvas) in the OpenHands repo, which upstream describes as experimental. A few things to know:

* The UI is at `https://openhands.dev.randomnoun/canvas` ( the `/canvas` prefix is baked into the image ). The first visit walks you through a short onboarding:
   * **Add a backend:** 
      * Host: `https://openhands.dev.randomnoun` ( no `/canvas` ),
      * Type: `Local`
      * Session key: the session API key from vault: `vault kv get -mount=secret -field=session_api_key k8s/bnekub03/secret/dev-openhands/openhands`
   * **Choose your agent:** 
      * pick **OpenHands**. The choice is per backend, and can be changed under Settings → Agent later
   * **Set up your LLM:** 
      * Choose **Advanced**
      * Custom Model: `openai/local-qwen3-coder:30b` 
      * Base URL: `http://web.dev-litellm.svc.cluster.local:4000/v1`
      * API Key: the litellm virtual key for openhands ( see [SETUP-DATABASE.md](SETUP-DATABASE.md) )
      
* It turns out you need openhands 'Enterprise' to get the isolation features I was installing this for ( might be able to install docker-in-docker later ) 
* You need to install the tables manually. For postgres it starts OK, finds no tables, and carries on, so chat and coding sessions work but the automation features won't. 
* Create the tables once, and again after bumping `openhands_image`, via:

```
kubectl -n dev-openhands exec -it deploy/openhands -- sh -c '
export AUTOMATION_DB_URL=$(echo "$AUTOMATION_DB_URL" | sed "s#postgresql+asyncpg#postgresql+pg8000#")
python -c "
from alembic import command
from alembic.config import Config
cfg = Config()
cfg.set_main_option(\"script_location\", \"/usr/local/lib/python3.13/site-packages/openhands/automation/migrations\")
command.upgrade(cfg, \"head\")
"'
```

Check it works from bnesql02 via:

```
sudo -u postgres psql -d openhands -c '\dt'
```

Should return 13 tables.

* The PVC holds `~/.openhands` and `~/workspace`, where the agent's repos live.

To check it all works, start a new conversation, and ask it "create a file hello.txt containing 'hi', then list the directory" 
It should write the file and run `ls -la` for real.

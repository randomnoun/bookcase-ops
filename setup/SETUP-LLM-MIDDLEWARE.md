# bookcase-ops LLM middleware

Part of the [LLM setup](SETUP-LLM.md).

The pieces that sit between the frontends and the backends: `litellm` is the proxy every frontend talks to, `searxng` gives the models web search, and `langfuse` records what goes through litellm.

## litellm

So to get this running the first time ( and then again any time it upgrades ), you'll need to do a 'prisma' schema update, via:

```
C:\Users\knoxg>kubectl -n dev-litellm exec -it litellm-c5778658b-7pv4b -- bash
bash-5.3# cd /app
bash-5.3# prisma db push --schema schema.prisma --accept-data-loss
prisma:warn Prisma doesn't know which engines to download for the Linux distro "wolfi". Falling back to Prisma engines built "debian".
Please report your experience by creating an issue at https://github.com/prisma/prisma/issues so we can add your distro to the list of known supported distros.
prisma:warn Prisma doesn't know which engines to download for the Linux distro "wolfi". Falling back to Prisma engines built "debian".
Please report your experience by creating an issue at https://github.com/prisma/prisma/issues so we can add your distro to the list of known supported distros.
Prisma schema loaded from schema.prisma
Datasource "client": PostgreSQL database "litellm", schema "public" at "bnesql02.dev.randomnoun:5432"

🚀  Your database is now in sync with your Prisma schema. Done in 456ms

Running generate... (Use --skip-generate to skip the generators)
command terminated with exit code 137
```

Once it's running:

* Create a team ( 'randomnoun' )
* Create a user

If you're me, then setup some byzantine structure to be able to use the LLMs that are hidden behind your work VPN, 
but that's not the sort of thing you're going to describe in a public-facing repository. 

## searxng

A search proxy that gives the LLMs web search. Accessible at `https://searxng.dev.randomnoun`.

Needs a secret key ( for CSRF/session signing ) in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md). Once both are running, configure it in litellm ( the litellm side isn't written up here yet ).

## langfuse

Observability for the whole stack: every request that goes through litellm gets recorded as a trace, with the model, prompt, response, token counts, latency and cost,
so I can compare frontends ( by their virtual key ) and backends ( by model ) side by side. Accessible at `https://langfuse.dev.randomnoun`.

Langfuse doesn't talk to the frontends or backends at all; it's a sink for litellm. Because every request already goes through litellm, there's nothing to configure in 
open-webui, pi, opencode or openhands.

Needs a postgres database, a clickhouse database, a rustfs bucket and a bunch of secrets in vault, see [SETUP-DATABASE.md](SETUP-DATABASE.md). 
Postgres and clickhouse run natively on `bnesql02`, and the S3 storage is the rustfs on `bnenas05`. Only `redis` ( a queue and cache ) is deployed by the role, 
into the `dev-langfuse` namespace as a single replica backed by a PVC.

The first startup creates the organisation, project, user and project API keys from the `langfuse_init_*` vars, so there's no signup to do. 
Log in as `langfuse_init_user_email` with the `init_user_password` from vault. Signups are disabled.

**Order matters here:** deploy `langfuse` before `litellm`. The litellm role is configured to send traces to langfuse ( via the `langfuse_otel` callback, using the project keys from vault ), 
so it'll log export errors until langfuse is up. To turn tracing off again, set `litellm_langfuse_host: ""` in the litellm vars file and re-run the litellm role.

To check it's working, make any request through litellm ( e.g. the `curl` test under [ollama](SETUP-LLM-BACKEND.md#ollama) ) and look under Tracing in the langfuse UI. 
To split the results by frontend, filter on the user or key alias; the pi / opencode / open-webui / openhands virtual keys each show up separately.

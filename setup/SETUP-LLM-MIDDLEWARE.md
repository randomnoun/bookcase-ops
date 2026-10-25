# bookcase-ops LLM middleware

Part of the [LLM setup](SETUP-LLM.md).

The pieces that sit between the frontends and the backends: `langfuse` records what goes through litellm, so it comes first; `litellm` is the proxy every frontend talks to; and `searxng` gives the models web search.

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

### Sessions

Requests are grouped into sessions, which you can browse on the Sessions page. A session id has to be sent by whatever makes the request, since langfuse can't work one out; litellm reads it from a request
header ( `x-litellm-session-id`, or a plain `x-session-id` ) or from `metadata.session_id`, and passes it on. pi sends its session id as `x-session-id`, so each pi session is one langfuse session. 
See [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#pi). Requests from a client that doesn't send an id are recorded as individual traces, outside any session.

### Prompts and responses

Every request and response is recorded as well as the model, token counts, cost and timing, so a trace or session shows the whole conversation, including tool calls and their results. 
This is controlled by `litellm_langfuse_capture_message_content` in the litellm vars file ( it sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` on the litellm pod ). 
litellm's own default is to leave the content out; this setup records it.

### Storage

Because the content is recorded, langfuse stores every prompt and response. pi resends its whole conversation on each turn, with prompts of 20,000 to 30,000 tokens, so this grows quickly even though it compresses well.

* **clickhouse** ( on `bnesql02` ) holds the traces and observations that the UI reads, so it's the one to watch. 
* **rustfs** ( the S3 bucket ) also holds langfuse's raw copy of every event, so the content is stored there as well. 

I don't know how large a day of pi use is; measure it. On `bnesql02`, with the clickhouse password in the environment:

```
df -h /var/lib/clickhouse
clickhouse-client --query "SELECT table, formatReadableSize(sum(bytes_on_disk)) AS on_disk, formatReadableSize(sum(data_uncompressed_bytes)) AS raw, sum(rows) AS rows FROM system.parts WHERE active AND database='langfuse' GROUP BY table ORDER BY sum(bytes_on_disk) DESC"
clickhouse-client --query "SELECT table, formatReadableSize(sum(bytes_on_disk)) FROM system.parts WHERE active AND database='system' GROUP BY table ORDER BY sum(bytes_on_disk) DESC LIMIT 5"
```

You have to use the `default` user for this: the `langfuse` user can't read the `system` tables. The last query matters because clickhouse's own `system` log tables have no expiry by default and use the same disk. 
Langfuse's documentation says to alert at 80% disk usage, and describes how to cap those log tables in its scaling guide.

To limit growth, langfuse has a Data Retention setting that deletes traces, observations, scores and media older than a number of days, nightly, from both clickhouse and the bucket ( its documentation says you don't need to set ClickHouse TTLs yourself ). 
I haven't checked whether it's available on a self-hosted install without a licence key. Check under the project settings in the langfuse UI.

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

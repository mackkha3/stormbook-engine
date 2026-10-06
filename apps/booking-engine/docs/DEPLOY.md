# Deploying the booking engine to Railway

Instructions only. Nothing in this repo deploys the engine; you do it when
you decide to. Finish [GHL_SETUP.md](GHL_SETUP.md) steps 1–3 and 5–6 first.
Step 4 (the webhook URL) is the last thing you do here.

## The one rule: a single instance

**Run exactly one instance, with one worker.** A homeowner's burst of texts is
collected in the server's memory for 8 seconds before the engine answers
once. With two instances, or uvicorn `--workers 2`, the texts of one burst
land in different processes and the homeowner gets two replies.

- Railway → service → Settings → **Replicas: 1**. No horizontal scaling.
- Never add `--workers` to the start command.
- Turn **Serverless / app sleeping off**. A sleeping service loses the
  waiting texts and answers the first webhook slowly.

Scaling past one instance needs the debounce moved out of memory (a database
or Redis queue). That is a code change, not a setting.

## Before the first deploy

Three small changes to commit. They are listed here, not made for you.

1. **Real ids in the client config.** Fill `config/clients/<slug>.yaml` from
   GHL_SETUP.md step 6. Ids are not secrets and belong in the repo. The token
   does not: only its env var *name* goes in `ghl_token_env`.
2. **`apps/booking-engine/railpack.json`**, same shape as `apps/demo-relay`:

```json
{
  "$schema": "https://schema.railpack.com",
  "provider": "python",
  "packages": {
    "python": "3.13",
    "uv": "0.11.30"
  },
  "deploy": {
    "startCommand": "uv run --frozen --no-dev uvicorn booking_engine.main:app --host 0.0.0.0 --port $PORT --no-access-log"
  }
}
```

   `--no-access-log` matters: with the plain GHL Webhook action the secret is
   in the URL, and access logs record URLs. The engine also redacts
   `secret=...` from its own log lines as a second guard.
3. **A Postgres driver.** The engine ships with SQLite only:

```bash
cd apps/booking-engine
uv add "psycopg[binary]"
uv run pytest
```

   Commit `pyproject.toml` and `uv.lock`.

## Create the service

1. Railway → New Project → Deploy from GitHub repo → this repo.
2. Settings → **Root Directory**: `apps/booking-engine`.
3. Settings → **Replicas**: 1. Serverless off.
4. Settings → Networking → Generate Domain, or add a custom domain such as
   `engine.stormbook.ai` with a CNAME to the Railway target.

## Postgres

SQLite on Railway is wiped on every deploy, which would forget who is booked,
handed off or opted out. Use Postgres.

1. In the project: **New → Database → PostgreSQL**.
2. On the engine service, set `DATABASE_URL` yourself (see the table below).
   Do **not** reference Railway's own `DATABASE_URL` directly: it starts with
   `postgresql://`, which makes SQLAlchemy look for a driver that is not
   installed. The engine needs the `postgresql+psycopg://` prefix.
3. Deploy. Tables are created on first start. There is no data to migrate
   from SQLite unless you ran real conversations locally.

## Environment variables

Service → Variables. `.env.example` is the template; `.env` is never deployed.

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key. Required |
| `GHL_WEBHOOK_SECRET` | `openssl rand -hex 32`. Required. The same value goes in the GHL webhook URL |
| `GHL_TOKEN_<CLIENT>` | One per client, named by that client's `ghl_token_env` (e.g. `GHL_TOKEN_ROOF_DEMO`). The Private Integration token |
| `DATABASE_URL` | `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |
| `DEBOUNCE_SECONDS` | Leave unset (8) |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `30`. Lets texts still inside their 8-second window be answered before the old deployment stops. Railway's default is 0 |
| `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` | `0`. Keeps old and new deployments from running side by side |
| `PORT` | Injected by Railway. Do not set |

`Postgres` in the reference variables is the database service's name; change
it if you named yours differently.

The server refuses to start, with a message naming the problem, if
`ANTHROPIC_API_KEY` or `GHL_WEBHOOK_SECRET` is missing, a client config still
has a `...` placeholder, or a client's token variable is empty.

## Health check

Settings → Deploy → **Healthcheck Path**: `/health`.

```bash
curl https://<engine-host>/health
# {"status":"ok","version":"...","uptime_seconds":12,"clients":1,"timestamp":"..."}
```

Railway only switches traffic to a new deployment after `/health` returns 200,
so a bad config fails the deploy and the previous version keeps serving.
`clients` is the number of client configs loaded.

## Point the GHL webhook at it

In each client's "Customer replied → Webhook" workflow (GHL_SETUP.md step 4),
set the URL to:

```
https://<engine-host>/webhooks/ghl/inbound?secret=<GHL_WEBHOOK_SECRET>
```

Then check, in this order:

```bash
# 1. No secret: must be 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<engine-host>/webhooks/ghl/inbound -d '{}'

# 2. Right secret, made-up location: must be 200 {"status":"ignored","reason":"unknown_location"}
curl -s -X POST "https://<engine-host>/webhooks/ghl/inbound?secret=$GHL_WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' -d '{"location_id":"none","contact_id":"none"}'
```

3. Publish the workflow, tag a test contact (your own phone) `engine-active`,
   and text the roofer's number. Work through "Check on the first live
   conversation" in GHL_SETUP.md step 8 before any real lead is tagged.

## Day to day

- **Logs:** structured JSON in Railway's log viewer. Useful events: `turn`
  (one per homeowner turn, with intent, template and reply), `inbound_ignored`,
  `ghl_call_rejected`, `webhook_rejected`, `turn_failed`.
- **Deploys:** a text that arrives during the switch between deployments can
  be answered late or, rarely, twice. Deploy when leads are quiet.
- **Adding a client:** commit their YAML, add their `GHL_TOKEN_<CLIENT>`
  variable, redeploy, then set up their GHL workflows.
- **Rotating the webhook secret:** change `GHL_WEBHOOK_SECRET`, redeploy, then
  update the URL in every client's workflow. Replies are rejected in between.
- **Rotating a GHL token:** GHL keeps the old token working for 7 days after a
  rotation. Update the variable and redeploy inside that window.

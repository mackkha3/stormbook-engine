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

Fill `config/clients/<slug>.yaml` with real ids (GHL_SETUP.md step 6) and
commit it. Ids are not secrets and belong in the repo. The token does not:
only its env var *name* goes in `ghl_token_env`.

Already in the repo:

- `railpack.json`: Python 3.13, uv, and the start command. It passes
  `--no-access-log` because with the plain GHL Webhook action the secret is in
  the URL, and access logs record URLs. The engine also redacts `secret=...`
  from its own log lines as a second guard.
- The Postgres driver (`psycopg`).

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
2. On the engine service, add the variable `DATABASE_URL` with the value
   `${{Postgres.DATABASE_URL}}`. `Postgres` is the database service's name;
   change it if you named yours differently. Railway's URL starts with
   `postgresql://` (or `postgres://`); the engine switches it to its own
   driver automatically, so use it as it is.
3. Deploy. Tables are created on first start. There is no data to migrate
   from SQLite unless you ran real conversations locally.

## Environment variables

Service → Variables. `.env.example` is the template; `.env` is never deployed.

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key. Required |
| `GHL_WEBHOOK_SECRET` | `openssl rand -hex 32`. Required. The same value goes in the GHL webhook URL |
| `GHL_TOKEN_<CLIENT>` | One per client, named by that client's `ghl_token_env` (e.g. `GHL_TOKEN_ROOF_DEMO`). The Private Integration token |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `ADMIN_SECRET` | `openssl rand -hex 32`. Optional. Unlocks `/admin/reset-contact`; while unset that route rejects everything. Use a different value from `GHL_WEBHOOK_SECRET` |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |
| `DEBOUNCE_SECONDS` | Leave unset (8) |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `30`. Lets texts still inside their 8-second window be answered before the old deployment stops. Railway's default is 0 |
| `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` | `0`. Keeps old and new deployments from running side by side |
| `PORT` | Injected by Railway. Do not set |

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
- **Resetting a test contact:** makes the engine forget a contact, so their next
  text starts a new conversation. It deletes the engine's conversation and turns
  for that contact only. Nothing in GHL changes: cancel a test appointment and
  remove the `booked` / `engine-handoff` tags there yourself, and make sure
  `engine-active` is on. An opted-out contact is refused (409) and stays opted out.

  ```bash
  curl -s -X POST "https://<engine-host>/admin/reset-contact?secret=$ADMIN_SECRET&location_id=<location>&contact_id=<contact>"
  # {"status":"reset","previous_status":"booked","appointment_id":"...","booked_time":"...","turns_deleted":3}
  # {"status":"nothing_to_reset"}   or   409 {"status":"refused","reason":"opted_out"}
  ```
- **Rotating the webhook secret:** change `GHL_WEBHOOK_SECRET`, redeploy, then
  update the URL in every client's workflow. Replies are rejected in between.
- **Rotating a GHL token:** GHL keeps the old token working for 7 days after a
  rotation. Update the variable and redeploy inside that window.

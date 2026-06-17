# intake-api

Stormbook's lead receiver. Normalizes inbound leads from the intake form,
Meta Lead Ads, and Angi (stub), then forwards them to GoHighLevel.

- **Stack:** Python 3.13, FastAPI, Pydantic v2, httpx, uv, uvicorn.
- **Deployment:** Railway via Railpack (`railpack.json`).
- **Storage:** none — per-client config is a JSON file + env vars.

## Local development

Prereqs: [uv](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cd apps/intake-api

# Install/sync dependencies (creates .venv/ in this directory)
uv sync

# Copy env template and fill in keys
cp .env.example .env

# (Dev only) Either copy the example client config or create your own
cp data/clients.example.json data/clients.json

# Start the dev server with auto-reload
uv run uvicorn intake_api.main:app --reload --port 3000
```

Smoke check:

```bash
curl localhost:3000/health
```

## Scripts

| Command                                                       | What it does                              |
|---------------------------------------------------------------|-------------------------------------------|
| `uv sync`                                                     | Install/sync deps into `.venv/`           |
| `uv run uvicorn intake_api.main:app --reload --port 3000`     | Local dev server                          |
| `uv run pytest`                                               | Run the test suite                        |
| `uv run ruff check src/ tests/`                               | Lint                                      |
| `uv run ruff format src/ tests/`                              | Format                                    |

There is no separate type-check step — Pydantic + Ruff cover v1.

## Endpoints

| Method | Path             | Purpose                                                 |
|--------|------------------|---------------------------------------------------------|
| POST   | `/intake`        | Receive a lead from the intake form (or any direct caller). |
| GET    | `/webhook/meta`  | Meta verification challenge.                            |
| POST   | `/webhook/meta`  | Meta Lead Ads webhook (HMAC-verified).                  |
| POST   | `/webhook/angi`  | Angi webhook (stub for v1).                             |
| GET    | `/health`        | Liveness + version + uptime. Railway hits this.         |

Every response carries an `X-Request-ID` header and a `request_id` in the
body. When a lead goes missing, that's the debug anchor across logs.

## Adding a new client

1. **GHL side (operator, no code):** Inside the client's GHL sub-account,
   note their `locationId` and the custom-field IDs that match
   `spec.md` §4.2 (Damage Type, Stormbook Lead ID, Homeowner Status,
   Insurance Status, Inspection Date, Property Address).

2. **Config:** Pick the path that matches your environment.

   **Local dev** — edit `apps/intake-api/data/clients.json` directly
   (gitignored). Iterate fast; the loader re-reads on every lookup.

   **Production (Railway)** — set the `CLIENTS_JSON` env var to the
   JSON-encoded blob in Railway → Project → Variables. The env var
   takes precedence over `data/clients.json`; if it's set, the file is
   ignored entirely. Rotate or add a client by updating the variable
   and restarting the service — no redeploy required.

   To turn an existing local file into the env-var value:

   ```bash
   cd apps/intake-api
   jq -c . data/clients.json
   # copy that output into Railway → Variables → CLIENTS_JSON
   ```

   Shape (same for both paths):

   ```json
   {
     "henderson-roofing": {
       "ghl_location_id": "loc_abc123",
       "custom_field_ids": {
         "damage_type":       "cf_...",
         "stormbook_lead_id": "cf_...",
         "homeowner_status":  "cf_...",
         "insurance_status":  "cf_...",
         "inspection_date":   "cf_...",
         "property_address":  "cf_..."
       },
       "service_area_zips": ["75701", "75702"]
     }
   }
   ```

3. **Secret (Railway env var):** Set
   `GHL_API_KEY_HENDERSON_ROOFING=<their API key>` in Railway → Project
   → Variables. Naming rule: `GHL_API_KEY_` + `client_id` uppercased,
   `-` → `_`.

4. **Form link:** Hand the client an intake URL with their `client_id`,
   e.g. `https://intake.stormbook.ai/?client_id=henderson-roofing&company=Henderson+Roofing`.

5. **Verify:** POST a test payload to `/intake` with their `client_id`
   and confirm the contact lands in their GHL sub-account.

## Deploying to Railway

This service ships via Railway's GitHub integration + Railpack
auto-detection. No Dockerfile.

1. **Connect repo:** Railway dashboard → New Project → Deploy from GitHub
   → pick this repo. In the service's Settings → Source → set the
   **Root Directory** to `apps/intake-api/`.

2. **Build:** Railpack detects Python from `pyproject.toml` and follows
   the steps in `railpack.json` — install uv, then
   `uv sync --frozen --no-dev`.

3. **Start command:** Defined in `railpack.json` —
   `uv run uvicorn intake_api.main:app --host 0.0.0.0 --port $PORT`.

4. **Env vars:** Set in Railway → Project → Variables, per
   `.env.example`. At minimum:
   - `ENVIRONMENT=production`
   - `GOOGLE_MAPS_API_KEY=…`
   - `META_APP_SECRET=…`, `META_VERIFY_TOKEN=…`, `META_PAGE_ACCESS_TOKEN=…`
   - One `GHL_API_KEY_<CLIENT_ID>=…` per pilot.
   - `CORS_ORIGINS=https://intake.stormbook.ai` — comma-separated list
     of origins allowed to POST to the API. Dev defaults to
     `http://localhost:4321` (the intake-form's dev port); production
     must set this explicitly to the intake form's domain. If a client
     embeds the form on their own site (e.g.
     `henderson-roofing.com`), append that origin to the list.

5. **Public domain:** Railway → Service → Settings → Networking →
   Generate Domain (gets you `*.up.railway.app`). Then add a CNAME from
   `api.stormbook.ai` to that hostname and add the custom domain in
   Railway.

6. **Client data:** `data/clients.json` is gitignored, so it does **not**
   ship with the deploy. Set the `CLIENTS_JSON` env var in Railway to
   the JSON-encoded blob — that's the canonical production source and
   takes precedence over any file on disk. See "Adding a new client"
   above for the `jq -c .` one-liner that turns a local file into the
   env-var value. Rotate config by editing the variable and restarting
   the service; no redeploy needed.

## Wiring Meta Lead Ads to this endpoint

1. In Meta Business Suite → your app → Webhooks, subscribe to the
   `leadgen` field on your Page.
2. **Callback URL:** `https://api.stormbook.ai/webhook/meta`
3. **Verify token:** the value you set for `META_VERIFY_TOKEN` in
   Railway. Meta hits `GET /webhook/meta` once with that token; we
   echo `hub.challenge` back if it matches.
4. **App secret:** the value of `META_APP_SECRET`. Meta signs each POST
   with `X-Hub-Signature-256` — we reject unsigned/invalid POSTs with
   `401 META_SIGNATURE_INVALID`.
5. **Page access token:** the value of `META_PAGE_ACCESS_TOKEN`. Used
   to fetch lead details from the Graph API once we receive a
   `leadgen_id` ping.
6. In your Meta Lead Form, add a custom question or hidden field named
   `client_id` and set it to the matching slug from `data/clients.json`
   so we can route the lead. If absent, we fall back to the `page_id`.

## Debugging a missing lead — where to look

Railway captures stdout/stderr. Filter the logs by the request_id the
client saw in their response (or in our error response):

```text
{"event": "intake_received", "request_id": "..."}
{"event": "client_not_found", "request_id": "..."}            # 404 path
{"event": "validation_failed", "request_id": "..."}           # 422 path
{"event": "ghl_post_attempt", "request_id": "...", "attempt": 1}
{"event": "ghl_post_success", "request_id": "..."}
{"event": "ghl_unrecoverable", "request_id": "..."}           # 502 path; logs full payload
{"event": "meta_signature_invalid", "request_id": "..."}      # 401 from Meta path
{"event": "meta_lead_fetched", "request_id": "..."}
```

Each line is a single JSON event. If `ghl_unrecoverable` fired, the
full normalized payload is in the log line — that's enough to replay
the lead by hand into GHL while you fix the underlying issue.

## Testing

```bash
uv run pytest
```

External calls (GHL, Meta Graph, Google Maps) are all mocked via
`pytest-httpx`. No real outbound traffic during the test run.

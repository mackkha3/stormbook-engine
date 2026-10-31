# Deployment

## Production environments

- Netlify — `intake-form` and `demo-dashboard` (static sites)
- Railway — `intake-api` (Python 3.13 / FastAPI service, deployed via Railpack)

## Domains and DNS

- `intake.stormbook.ai` — intake form (Netlify)
- `dash.stormbook.ai` — demo dashboard (Netlify)
- `api.stormbook.ai` — intake API (CNAME → Railway-provided domain)

## Environment variables required per app

### intake-form

No build-time env vars. Per-deploy runtime config lives in
`apps/intake-form/config.json` (gitignored). Set:

- `apiBaseUrl` — `https://api.stormbook.ai`
- `mock` — `false` in production

### demo-dashboard

None. Static, mock data only.

### intake-api

Set in Railway dashboard → Project → Variables. `.env.example` is the
canonical template.

| Var                          | Required | Purpose                                                          |
|------------------------------|----------|------------------------------------------------------------------|
| `PORT`                       | auto     | Injected by Railway. Locally defaults to 3000.                  |
| `ENVIRONMENT`                | yes      | `production` in Railway, `development` locally.                  |
| `LOG_LEVEL`                  | no       | `INFO` default, `DEBUG` for verbose runs.                        |
| `GOOGLE_MAPS_API_KEY`        | yes      | Server-side Geocoding API key.                                   |
| `META_VERIFY_TOKEN`          | yes      | Random ≥32-char string; same value goes in Meta's webhook UI.    |
| `META_APP_SECRET`            | yes      | Meta App Secret — verifies `X-Hub-Signature-256` on POSTs.       |
| `META_PAGE_ACCESS_TOKEN`     | yes      | Page access token for fetching lead details from Graph API.      |
| `GHL_API_KEY_<CLIENT_ID>`    | per-client | One per pilot. Naming: client_id uppercased, `-` → `_`.        |
| `CORS_ORIGINS`               | yes (prod) | Comma-separated allowed origins for browser POSTs. Defaults to `http://localhost:4321` in dev; must be set explicitly in production (e.g. `https://intake.stormbook.ai`). |

Per-client GHL location IDs + custom field IDs live in
`apps/intake-api/data/clients.json` (gitignored). Use
`data/clients.example.json` as the template.

## Per-client deployment checklist

Each client follows a 14-day deploy: kickoff, GHL sub-account setup, lead
source integration, SMS template customization, internal testing, client UAT,
refinements, soft launch, then full launch and handoff.

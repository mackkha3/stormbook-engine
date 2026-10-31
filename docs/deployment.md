# Deployment

Seven apps, two deploy targets. Each Python service is its own Railway service, deployed via Railpack from its `railpack.json`. Each static app is its own Netlify site with no build step.

## Railway — Python services

Set each service's Root Directory to its app folder and its variables in Railway → Project → Variables. Each service's `.env.example` is the canonical template.

**`apps/intake-api`** receives web form and Meta Lead Ads leads and forwards them to GoHighLevel. It serves `api.stormbook.ai` (CNAME → Railway-provided domain). It needs `META_VERIFY_TOKEN`, `META_APP_SECRET` and `META_PAGE_ACCESS_TOKEN` for the Meta webhook, `GOOGLE_MAPS_API_KEY` for geocoding, one `GHL_API_KEY_<CLIENT_ID>` per client (client_id uppercased, `-` → `_`), and `CORS_ORIGINS` in production. Per-client GHL location and custom field IDs live in `data/clients.json` (gitignored); use `data/clients.example.json` as the template.

**`apps/booking-engine`** reads homeowner replies and books inspections inside the text conversation. Run exactly one instance with one worker and serverless off, because follow-up texts are collected in memory before the engine answers. It uses Railway Postgres through `DATABASE_URL`, since SQLite is wiped on every deploy. Full steps, including the GHL webhook, are in [the deploy guide](../apps/booking-engine/docs/DEPLOY.md).

**`apps/demo-relay`** is the Twilio SMS bridge for live demos. It needs the Twilio credentials and sending number, `CORS_ORIGINS`, and `DEMO_ACCESS_TOKEN` to protect `POST /fire` in production. Leave `TWILIO_SIGNATURE_VERIFICATION` on anywhere real Twilio traffic can reach. Full steps, including the Twilio webhook and A2P 10DLC registration, are in [its README](../apps/demo-relay/README.md).

## Netlify — static apps

**`apps/intake-form`** is the homeowner lead capture form at `intake.stormbook.ai`. Per-deploy config lives in `config.json` (gitignored): set `apiBaseUrl` to `https://api.stormbook.ai` and `mock` to `false` in production.

**`apps/demo-form`** is the demo request form at `demo.stormbook.ai`, used to fire texts through demo-relay. Set `relayUrl` in `config.json` (gitignored) to the relay's Railway domain.

**`apps/demo-dashboard`** is the metrics dashboard demo at `dash.stormbook.ai`. It runs on mock data and needs no configuration.

**`stormbook-website-deploy`** is the marketing site and legal pages at `stormbook.ai`. It lives at the repo root rather than in `apps/` and needs no configuration.

## Per-client deployment checklist

Each client follows a 14-day deploy: kickoff, GHL sub-account setup, lead
source integration, SMS template customization, internal testing, client UAT,
refinements, soft launch, then full launch and handoff.

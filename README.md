# Stormbook Engine

[![CI](https://github.com/mackkha3/stormbook-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/mackkha3/stormbook-engine/actions/workflows/ci.yml)

Stormbook is an AI-powered SMS lead-to-inspection booking engine for
storm-restoration roofers. It captures inbound leads from Meta Lead Ads and web
forms, targets a first SMS reply within 60 seconds, qualifies the homeowner over
a short text conversation, and books the inspection directly on the client's
calendar. Stormbook is operated by OTHONAS GROUP LLC.

## Architecture

A monorepo of seven apps: three Python services and four static front ends.

### Python/FastAPI services (Railway)

| App | Purpose |
|---|---|
| [`apps/intake-api`](apps/intake-api) | Receives web form and Meta Lead Ads leads and forwards them to GoHighLevel, where a workflow sends the first SMS |
| [`apps/booking-engine`](apps/booking-engine) | Reads homeowner replies and books inspections inside the text conversation. The AI reads, the templates write: Claude classifies and extracts, and every outbound word comes from a template |
| [`apps/demo-relay`](apps/demo-relay) | Twilio SMS bridge for live product demos |

### Static HTML/JS apps (Netlify)

| App | Purpose |
|---|---|
| [`apps/intake-form`](apps/intake-form) | Homeowner lead capture form |
| [`apps/demo-form`](apps/demo-form) | Demo request form |
| [`apps/demo-dashboard`](apps/demo-dashboard) | Metrics dashboard demo, using mock data |
| [`stormbook-website-deploy`](stormbook-website-deploy) | Marketing site and legal pages |

## Stack

- **Services:** Python 3.13, FastAPI, Pydantic v2, uv, httpx, SQLAlchemy
- **Quality:** pytest, pytest-cov, ruff
- **Front ends:** vanilla HTML/CSS/JS, Chart.js
- **Integrations:** Twilio (with an A2P 10DLC consent check), Meta Graph API,
  GoHighLevel, Anthropic Claude API

## Testing

- 792 tests across the three Python services, with 94.0% statement coverage:

  | Service | Tests | Coverage |
  |---|---:|---:|
  | booking-engine | 594 | 94.4% |
  | demo-relay | 101 | 95.6% |
  | intake-api | 97 | 91.0% |

- GoHighLevel, Twilio, Meta and Anthropic are mocked at the boundaries.
- Live Anthropic tests are gated behind the `live` marker and excluded from CI.
  To run them, set `ANTHROPIC_API_KEY` and run `uv run pytest -m live` in
  `apps/booking-engine`.
- CI runs the full suite and ruff in about 36 seconds, on Ubuntu, through a
  per-service GitHub Actions matrix.

## Security

Webhook signatures are verified with hand-rolled code and no vendor SDK:

- **Twilio (demo-relay):** HMAC-SHA1 over the URL and sorted form parameters.
  Invalid requests get a 403 and are never processed. See
  [`twilio_sig.py`](apps/demo-relay/src/demo_relay/lib/twilio_sig.py).
- **Meta (intake-api):** HMAC-SHA256 over the raw request body, checked against
  `X-Hub-Signature-256`. Requests get a 401 if the signature is invalid or the
  app secret is unset. See
  [`meta.py`](apps/intake-api/src/intake_api/handlers/meta.py).
- **Timing-safe comparison:** signatures are compared with `hmac.compare_digest`
  on bytes, because it raises `TypeError` on non-ASCII `str` input.
- **Railway proxy:** demo-relay rebuilds the URL Twilio signed from
  `X-Forwarded-Proto` and `X-Forwarded-Host`, since the app sees internal HTTP
  behind the proxy.
- **Local development:** `TWILIO_SIGNATURE_VERIFICATION=off` skips the Twilio
  check and logs a warning on each request. It is intended for local use only;
  verification is on unless explicitly disabled.

Secrets are never committed. Each service's `.env.example` is its configuration
contract, and `.env` files are gitignored.

## Project isolation

The Node toolchain is pinned per project, with no global installs. Each Python
service has its own uv-managed environment. See
[docs/isolation.md](docs/isolation.md).

## Deployment

The Python services deploy to Railway using Railpack, and the static apps deploy
to Netlify. See [docs/deployment.md](docs/deployment.md), plus
[the booking-engine deploy guide](apps/booking-engine/docs/DEPLOY.md) and
[the demo-relay README](apps/demo-relay/README.md).

## Development quickstart

```bash
cd apps/<app>     # booking-engine, demo-relay or intake-api
uv sync
uv run pytest
```

## License

[MIT](LICENSE). The Stormbook name and logos are not covered by this license.

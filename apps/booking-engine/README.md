# booking-engine

Stormbook **Build #5**: the booking engine. It reads a homeowner's reply to a
roofer's text, and books the roof inspection **inside the text conversation**.
No booking links, no "call us back."

> **The AI reads. The templates write.**
> The model only classifies a message and extracts details. Every word a
> homeowner sees is rendered from `config/templates.yaml`, and a code-level
> safety check re-verifies that before each send.

**Status: Phases 1–3 complete** (core engine, GHL integration, deploy guide). The server takes GHL's inbound webhook,
reads free slots from the roofer's GHL calendar, books there, and sends the
reply, tags, notes and stage moves through the GHL API. Nothing is deployed.
GHL-side setup: [docs/GHL_SETUP.md](docs/GHL_SETUP.md). Deploying to Railway:
[docs/DEPLOY.md](docs/DEPLOY.md).

## Try it: the terminal simulator

```bash
cd apps/booking-engine
cp .env.example .env        # then add your ANTHROPIC_API_KEY
uv sync
uv run python -m booking_engine.simulate --client roof-demo
```

You are the homeowner. The simulator shows the opening text GHL would have
sent and seeds the conversation with it, exactly like production, so start
with your reply (`yes`, `how much is this`, `can u do sat morning`, `STOP`).
After each text you see the intent the model read, the template used, the
reply, any calendar booking, CRM actions (tags, notes, stage) and handoffs.

| Command | What it does |
|---|---|
| `/state` | Show the saved conversation state |
| `/slots` | Show the mock calendar's next open times |
| `/take N` | Someone else grabs offered slot N, then pick it to see the re-check |
| `/new` | Start over as a new homeowner |
| `/quit` | Exit |

Flags: `--lead-source web_form` replies to the web-form opening text instead
of the missed-call one (default `missed_call`); `--debounce 8` waits 8 seconds for follow-up texts like production does
(default 0, so replies are instant); `--verbose` also prints the JSON turn logs.

Never install packages globally: `uv` owns the environment.

## Tests

```bash
uv run pytest               # 433 tests, no network, no API key
uv run pytest -m live       # 87 more: real model vs. every fixture message (reads ANTHROPIC_API_KEY from .env, costs a few cents)
uv run ruff check .
```

| File | Covers |
|---|---|
| `tests/fixtures/homeowner_messages.yaml` | 87 realistic homeowner texts across all 15 intents |
| `test_understanding.py` | JSON validation, retry-once-then-handoff, opt-out keywords, live model eval |
| `test_conversation.py` | Every fixture → expected action; unclear/handoff/opt-out/cap rules |
| `test_safety.py` | Output safety check: template-only, no dollar amounts, banned phrases, calendar-only times |
| `test_slots_and_booking.py` | Real slots only, spreading, no repeats, re-check before booking, reschedule, cancel |
| `test_debounce_idempotency.py` | Burst of texts = one turn; duplicate message ids dropped |
| `test_photo.py` | Picture-only texts: photo template, contact note, no slots once booked, model never asked |
| `test_ghl_ids.py` | The ids helper (read-only, token never printed); Railway Postgres URL rewrite |
| `test_ghl_client.py` | Every GHL path, `Version` header and body; retry rules; token never logged; GHL calendar |
| `test_webhook.py` | The webhook end to end against a fake GHL (`tests/ghl_fake.py`): secret, payload shapes, `engine-active` gating, booking, retries, handoff and resume |

## Editing what homeowners see

All wording is in `config/templates.yaml`. Edit it and restart; no code
changes. Templates may only use `{company_name}`, `{slot_1}`, `{slot_2}`,
`{booked_time}`, `{service_area}`, `{company_phone}`. A template containing a
dollar amount, a banned phrase (`deductible`, `guarantee`, `insurance will`,
`covered by insurance`, `free roof`, `waive`) or a hardcoded day/time is
rejected at startup.

### Opening messages

The first text of every conversation is sent by a GHL workflow, not by the
engine. `templates.yaml` holds a copy per lead source (`opening_missed_call`,
`opening_web_form`) so the model knows what a homeowner's first reply is
answering. A new conversation's history is seeded with that text. **Keep
these in sync with the wording in GHL.** `opening_web_form` is still a
placeholder (marked TODO in the file).

## Running the server

```bash
uv run uvicorn booking_engine.main:app --port 8000
```

It refuses to start until `ANTHROPIC_API_KEY` and `GHL_WEBHOOK_SECRET` are
set, every client config is free of placeholders, and each client's token env
var is set. Routes: `GET /health` and `POST /webhooks/ghl/inbound`.

What the webhook does with a homeowner's reply:

1. Rejects it without the shared secret (`?secret=...` in the URL, or an
   `X-Webhook-Secret` header).
2. Finds the client by GHL location id and reads the contact from GHL.
   No `engine-active` tag: ignored. Tag back on a handed-off contact: the
   engine resumes.
3. On a first reply, reads the opening text GHL actually sent and moves the
   opportunity to `replied`.
4. Queues the message; the reply goes out after the debounce window.

A picture with no words is answered from the `photo_received` template (or
`photo_received_booked` once an inspection is booked) without asking the
model, and a note is added to the contact.

Debounced turns wait in memory, so run **one** server process. Deploying:
[docs/DEPLOY.md](docs/DEPLOY.md).

## Adding a roofer

Copy `config/clients/roof-demo.yaml` to `config/clients/<slug>.yaml` and fill
it in. `uv run python -m booking_engine.ghl_ids --location <id>` prints the
calendar, pipeline and stage ids to copy. The simulator accepts the `...` placeholders; the server
(`booking_engine.main`) refuses to start until every ID is real and the token
env var named by `ghl_token_env` is set.

## How a turn works

1. `engine.receive` drops duplicate message ids, then debounces (~8s) so a
   burst of texts is one turn.
2. `understanding.py` asks `claude-haiku-4-5-20251001` (temperature 0, JSON
   schema output) for the intent, with the last 6 messages and current state.
   A failed or invalid answer is retried once, then the turn is handed off.
3. Code rules apply: an opt-out always wins, non-English hands off,
   confidence under 0.7 becomes `unclear`, and a `none_work` or `affirm` that
   names a day or time is handled as `propose_time`. A message that is exactly a carrier
   keyword (STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, OPTOUT, REVOKE) is an
   opt-out in every state, decided in code without asking the model.
4. `conversation.py` picks the action: a template, slots from the calendar,
   a booking / move / cancel, or a handoff.
5. `templates.py` renders the template and runs the output safety check. If
   it fails, the homeowner gets the handoff message instead.
6. The reply, tags, note and stage change go out through the CRM gateway; the
   turn is saved to the database and logged as one JSON line.

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app + routes (`/health`, `/webhooks/ghl/inbound`) |
| `settings.py` | Env var loading |
| `config_loader.py` | Client config loading + validation |
| `db.py`, `models.py`, `store.py` | Database setup, tables, state read/write |
| `understanding.py` | Anthropic call → validated intent JSON |
| `conversation.py` | Intent → action (the state machine) |
| `templates.py` | Template rendering + output safety check |
| `slots.py` | Slot filtering, spreading, formatting |
| `calendar/` | Calendar interface, mock (simulator + tests), GHL (server) |
| `ghl_client.py` | Every GHL API call: timeouts, one retry, logging |
| `ghl_ids.py` | Prints a sub-account's calendar, pipeline and stage ids for the client config |
| `inbound_payload.py` | Webhook secret check, GHL payload → internal shape |
| `webhook.py` | Inbound flow: tag gating, resume after handoff, opening text, dedupe id |
| `engine.py` | The turn pipeline that wires the above together |
| `turn.py` | Value types for one turn (inbound message, decision, result) |
| `debounce.py` | Collects a burst of texts into one turn |
| `gateway.py` | CRM actions interface (SMS, tags, notes, stage): in-memory recorder + GHL |
| `logger.py` | Structured JSON logs (same as demo-relay) |
| `simulate.py` | Terminal simulator |

## CRM tags the engine sets

| Tag | When |
|---|---|
| `booked` | Inspection booked (opportunity also moves to the `booked` stage) |
| `engine-handoff` | Handed to a human; `engine-active` is removed and a note is added |
| `engine-emergency` | Added alongside `engine-handoff` for emergencies |
| `engine-cancelled` | Homeowner cancelled a booked inspection; a note is added. Removed again if they text back to rebook |
| `engine-opted-out` | Any opt-out, keyword or model-detected. Nothing is sent |
| `engine-optout-booked` | Opted out while an inspection is still booked. The appointment is kept and a note tells the roofer they can no longer text this person |

## Environment variables

See `.env.example`. The simulator needs only `ANTHROPIC_API_KEY`.

| Var | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Message understanding |
| `DATABASE_URL` | no | Defaults to `sqlite:///./booking_engine.db` in this folder. A Railway `postgres://` URL works as it is |
| `DEBOUNCE_SECONDS` | no | Default 8 |
| `ENVIRONMENT`, `LOG_LEVEL` | no | `development`, `INFO` |
| `GHL_WEBHOOK_SECRET` | server | Shared secret GHL sends as `?secret=...` or in the `X-Webhook-Secret` header |
| `GHL_TOKEN_ROOF_DEMO` | server | Private Integration token; one env var per client, named by `ghl_token_env` |

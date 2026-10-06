# booking-engine

Stormbook **Build #5**: the booking engine. It reads a homeowner's reply to a
roofer's text, and books the roof inspection **inside the text conversation**.
No booking links, no "call us back."

> **The AI reads. The templates write.**
> The model only classifies a message and extracts details. Every word a
> homeowner sees is rendered from `config/templates.yaml`, and a code-level
> safety check re-verifies that before each send.

**Status: Phase 1** (core engine, no GHL). The engine runs end to end against
a mock calendar and an in-memory CRM. The GHL webhook, real calendar and real
SMS arrive in Phase 2; deployment docs in Phase 3.

## Try it: the terminal simulator

```bash
cd apps/booking-engine
cp .env.example .env        # then add your ANTHROPIC_API_KEY
uv sync
uv run python -m booking_engine.simulate --client roof-demo
```

You are the homeowner. GHL's first text is assumed already sent, so start
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

Flags: `--debounce 8` waits 8 seconds for follow-up texts like production does
(default 0, so replies are instant); `--verbose` also prints the JSON turn logs.

Never install packages globally: `uv` owns the environment.

## Tests

```bash
uv run pytest               # 354 tests, no network, no API key
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

## Editing what homeowners see

All wording is in `config/templates.yaml`. Edit it and restart; no code
changes. Templates may only use `{company_name}`, `{slot_1}`, `{slot_2}`,
`{booked_time}`, `{service_area}`, `{company_phone}`. A template containing a
dollar amount, a banned phrase (`deductible`, `guarantee`, `insurance will`,
`covered by insurance`, `free roof`, `waive`) or a hardcoded day/time is
rejected at startup.

## Adding a roofer

Copy `config/clients/roof-demo.yaml` to `config/clients/<slug>.yaml` and fill
it in. The simulator accepts the `...` placeholders; the server
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
| `main.py` | FastAPI app + routes (Phase 1: `/health`) |
| `settings.py` | Env var loading |
| `config_loader.py` | Client config loading + validation |
| `db.py`, `models.py`, `store.py` | Database setup, tables, state read/write |
| `understanding.py` | Anthropic call → validated intent JSON |
| `conversation.py` | Intent → action (the state machine) |
| `templates.py` | Template rendering + output safety check |
| `slots.py` | Slot filtering, spreading, formatting |
| `calendar/` | Calendar interface, mock (Phase 1), GHL (Phase 2) |
| `engine.py` | The turn pipeline that wires the above together |
| `turn.py` | Value types for one turn (inbound message, decision, result) |
| `debounce.py` | Collects a burst of texts into one turn |
| `gateway.py` | CRM actions interface (SMS, tags, notes, stage) + in-memory recorder |
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

See `.env.example`. Phase 1 needs only `ANTHROPIC_API_KEY`.

| Var | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Message understanding |
| `DATABASE_URL` | no | Defaults to `sqlite:///./booking_engine.db` in this folder |
| `DEBOUNCE_SECONDS` | no | Default 8 |
| `ENVIRONMENT`, `LOG_LEVEL` | no | `development`, `INFO` |
| `GHL_WEBHOOK_SECRET`, `GHL_TOKEN_ROOF_DEMO` | Phase 2 | Unused in Phase 1 |

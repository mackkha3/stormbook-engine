# demo-relay

Twilio SMS bridge for the **phone-in-my-hand demo**. Mack fills out
`apps/demo-form`, hits fire, and a real SMS lands on the phone he's holding
while the prospect watches on Zoom.

This is **not** the production intake system — that's `apps/intake-api`
(Build #3), which is trigger-gated to pilot #1's signature. This service is
disposable sales infrastructure. When pilot #3 signs and Voice AI (Vapi.ai)
ships, delete `apps/demo-form` and `apps/demo-relay` together.

## What it does

| Message | Trigger | Copy |
|---|---|---|
| 1 | `POST /fire` | Opener: two slots offered, "Which day works better?" |
| 2 | 10 min of silence | Nudge: "reply with a better time" |
| 3A | Reply names a slot day | "You're booked for {day} at {time}" |
| 3D | Reply reads as wrong number | "No problem — we'll close this out" |

Anything else classifies as `UNMATCHED` and the relay stays silent. That's
deliberate: Mack narrates those live — *"and here Jane could say a different
time, and Mike takes over."* STOP / HELP / UNSUBSCRIBE are handled by Twilio
at the platform level and are not touched by this code.

State lives in an in-memory dict keyed by homeowner phone, expiring after
24h. No database, no disk. A restart loses in-flight conversations, which
costs one re-fire of the form; Twilio's message log is the real audit trail.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/fire` | JSON body, see `models.FireRequest`. Returns the message body it sent. `403` and no SMS unless `sms_consent` is `true`. |
| `POST` | `/twilio/inbound` | Twilio's form-urlencoded webhook. Always 200 + empty TwiML. |
| `GET`  | `/health` | Railway liveness. Includes `active_conversations_count`. |

`/fire` has **no auth**. It sits behind a screen share for the length of
three pilot sales. If the URL ever leaks publicly, add a `DEMO_ACCESS_TOKEN`
env var and a Bearer check in `handlers/fire.py` — don't build more than that.

## Local development

```bash
cd apps/demo-relay
cp .env.example .env        # then fill in the Twilio values
uv sync
uv run pytest -v            # 75 tests, no network
uv run uvicorn demo_relay.main:app --reload --port 3001
```

Never install packages globally — `uv` owns the environment.

Fire a message by hand:

```bash
curl -s localhost:3001/fire -H 'Content-Type: application/json' -d '{
  "contractor_first_name": "Mike",
  "contractor_company": "Henderson Roofing",
  "homeowner_first_name": "Jane",
  "homeowner_phone": "+19035550199",
  "homeowner_address": "123 Elm St",
  "damage_type": "Hail",
  "slot_a_day": "Tuesday",
  "slot_a_time": "10:00 AM",
  "slot_b_day": "Thursday",
  "slot_b_time": "2:00 PM",
  "sms_consent": true,
  "sms_consent_at": "2026-10-11T15:00:00Z"
}'
```

To test inbound replies against a local server, expose it with a tunnel
(`npx --yes localtunnel --port 3001`, or ngrok if you already have it) and
point the Twilio webhook at `<tunnel>/twilio/inbound`. Otherwise deploy to
Railway and test there — it's faster.

### Environment variables

| Var | Required | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | yes | Console → Account Info |
| `TWILIO_AUTH_TOKEN` | yes | Live credential. Rotate if it ever hits a screen share. |
| `TWILIO_FROM_NUMBER` | yes | E.164, the provisioned number |
| `TWILIO_SIGNATURE_VERIFICATION` | no | Default `on`. Set to `off` only for local dev with hand-POSTed inbound messages; every bypassed request logs a warning. |
| `CORS_ORIGINS` | no | Comma-separated. Default `http://localhost:4323,http://127.0.0.1:4323` |
| `ENVIRONMENT` | no | `development` \| `production` |
| `LOG_LEVEL` | no | Default `INFO` |
| `MESSAGE_2_DELAY_SECONDS` | no | Default `600`. Set to `30` for a rehearsal. |

## Twilio setup

1. Buy an SMS-capable US number (Console → Phone Numbers → Buy a number).
2. Console → Phone Numbers → **your number** → Messaging →
   *A message comes in*: **Webhook**, `HTTP POST`,
   `https://<your-railway-domain>/twilio/inbound`.
3. Save. Twilio now POSTs every inbound SMS to the relay.
4. **A2P 10DLC**: US carriers require a registered campaign for
   application-to-person SMS. Register in Console → Messaging → Regulatory
   Compliance. Unregistered traffic gets filtered — including, eventually,
   your demo. This is a console task, not something this code does. Do it
   before the first prospect call, not the morning of.

If a demo SMS never arrives, check Console → Monitor → Logs → Messaging.
The relay logs the Twilio SID on every send, so the two line up.

## Railway deployment

Railpack config lives in `railpack.json` (Python 3.13, `uv sync --frozen
--no-dev`, uvicorn on `$PORT`).

1. Railway → New Project → Deploy from GitHub repo → this repo.
2. Settings → **Root Directory**: `apps/demo-relay`.
3. Variables: set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_FROM_NUMBER`, `CORS_ORIGINS=https://demo.stormbook.ai`,
   `ENVIRONMENT=production`, `LOG_LEVEL=INFO`.
4. Settings → Networking → Generate Domain (or add
   `demo-relay.stormbook.ai` and point a CNAME at the Railway target).
5. Deploy, then `curl https://<domain>/health` — expect
   `{"status":"ok", ...}`.
6. Paste that domain into the Twilio webhook (above) and into
   `apps/demo-form/config.json`.

Logs are structured JSON on stdout/stderr; Railway's log viewer is the sink.
Grep for `fire_sent`, `inbound_classified`, `message_2_sent`.

## Running a live demo

Before the call:

- [ ] `curl https://<relay-domain>/health` returns ok. Railway free-tier
      containers sleep — this also wakes it.
- [ ] Open the demo form, fire one test SMS to your own phone. Confirm it
      lands. Reply "Tuesday", confirm Message 3A lands.
- [ ] Delete that test thread from the phone so the demo starts clean.
- [ ] Phone on silent-with-vibrate, screen brightness up, notification
      preview **on** (otherwise the prospect sees nothing).

On the call:

1. Share the demo form tab. The yellow banner tells the prospect this is
   live, not a mock — that's the credibility beat.
2. Ask the prospect for details: *"What's your company called? Pick a day."*
   Type their answers in. It's their lead now, not a canned script.
3. Type your own phone number in the homeowner field. Say what you're doing.
4. Hit **Fire SMS**. Stop talking. Hold the phone up to the camera.
5. When it buzzes, read the message out loud. The confirmation screen shows
   the same text — point at both.
6. Reply "Tuesday" on the phone. Message 3A lands in ~2 seconds. That's the
   close: *"That's a booked appointment, and nobody touched a keyboard."*
7. Narrate what you're not showing: *"If Jane had asked for a different
   time, or asked a question, it routes to Mike. We're not faking an AI
   that handles everything — we're taking the 80% that's mechanical."*

If the SMS doesn't arrive within ~15 seconds: don't wait in silence. Say
*"carrier's being slow, happens"*, keep talking, and check Twilio's Messaging
log after the call. A pre-call test makes this nearly impossible.

## Tests

```bash
uv run pytest -v
```

75 tests, all external calls mocked with `pytest-httpx`. No test touches the
real Twilio API.

## What this deliberately doesn't do

- No database, no disk persistence
- No `twilio` SDK (one endpoint, called with httpx)
- No Branches 2/4/5/6 (counter-offer, call-me, questions, nonsense)
- No shared code with `apps/intake-api` — patterns are copied, not imported,
  because the two services have different lifecycles

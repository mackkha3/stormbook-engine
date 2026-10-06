# GHL setup for the booking engine

What to create in a roofer's GHL sub-account so the booking engine can answer
homeowner replies and book inspections. Do this once per client, then fill in
`config/clients/<client_slug>.yaml`.

Nothing here deploys anything. Step 4 needs the engine's public URL, which
exists only after Phase 3.

## 1. Private Integration token

Sub-account → **Settings → Private Integrations → Create new integration**.
Name it `Stormbook booking engine` and tick exactly these scopes:

| Scope | Used for |
|---|---|
| `contacts.readonly` | Read the contact's tags, name and phone |
| `contacts.write` | Add/remove tags, add notes |
| `conversations.readonly` | Find the contact's conversation |
| `conversations/message.readonly` | Read the opening text and the inbound message |
| `conversations/message.write` | Send the reply SMS |
| `calendars.readonly` | Read free slots |
| `calendars/events.write` | Create, move and cancel appointments |
| `opportunities.readonly` | Find the contact's opportunity |
| `opportunities.write` | Move it between stages |

GHL shows the token **once**. Put it in the engine's `.env` under the name in
the client config's `ghl_token_env` (for the demo client: `GHL_TOKEN_ROOF_DEMO`).
Never paste it into a YAML file or commit it. GHL recommends rotating it
every 90 days; the old token keeps working for 7 days after a rotation.

## 2. Tags

Create these under **Settings → Tags** so workflows can use them:

| Tag | Who sets it | Meaning |
|---|---|---|
| `engine-active` | **Your first-text workflows** | The engine may answer this contact |
| `engine-handoff` | Engine | A human must take over. `engine-active` is removed |
| `engine-emergency` | Engine | Added with `engine-handoff` for active leaks and similar |
| `booked` | Engine | Inspection booked |
| `engine-cancelled` | Engine | Homeowner cancelled by text |
| `engine-opted-out` | Engine | Homeowner opted out. Nothing more is sent |
| `engine-optout-booked` | Engine | Opted out while an inspection is still booked |

**Add `engine-active` in the workflows that send the first text** (missed call
and web form). The engine ignores every contact without it. Those workflows
should also create the contact's opportunity in the pipeline at the `texted`
stage: the engine moves an existing opportunity, it never creates one.

To hand a contact back to the engine after a handoff, add `engine-active`
again. The engine resumes on their next text and clears `engine-handoff`.

## 3. Calendar

Use one calendar for inspections, with at least one team member assigned.

- Slot duration **60 minutes**, matching `inspection_duration_minutes`.
- Availability, buffers and minimum notice are yours to set: the engine only
  offers what GHL reports as free, and GHL re-validates the slot when booking.
- **Turn off GHL's own appointment confirmation to the contact.** In the
  calendar's notification settings, disable the "appointment booked /
  confirmed" notification sent to the contact, and check that no workflow
  triggered by "Customer booked appointment" or "Appointment status" texts the
  homeowner a confirmation. The engine already sends one, and a second text
  with different wording looks broken. Reminders and notifications to your
  own team can stay on.

## 4. Workflow: "Customer replied → Webhook"

1. **Trigger:** Customer Replied. Filter **Reply channel = SMS**.
2. Optional filter: contact has tag `engine-active`. This only saves webhook
   executions; the engine checks the tag itself either way.
3. **Action:** Custom Webhook (a premium action, billed per execution).
   - Method `POST`
   - URL `https://<engine-host>/webhooks/ghl/inbound`
   - Authorization: none
   - Header `X-Webhook-Secret`: the value of `GHL_WEBHOOK_SECRET` in the
     engine's `.env`. Generate it with `openssl rand -hex 32`.
   - Header `Content-Type`: `application/json`
   - Body (raw JSON):

```json
{
  "location_id": "{{location.id}}",
  "contact_id": "{{contact.id}}",
  "contact_phone": "{{contact.phone}}",
  "message_body": "{{message.body}}"
}
```

Keep `location_id` and `contact_id` first. If a homeowner's text contains a
quote or a line break the JSON arrives broken; the engine then reads those two
ids and fetches the text from GHL itself.

`message_id` and `timestamp` are optional. Without a message id the engine
uses the id of the matching message in the GHL conversation to drop retries.

The plain **Webhook** action is not enough: per GHL's help article it has no
custom headers, so it cannot carry the secret.

Responses: `200 {"status": "accepted" | "duplicate" | "ignored"}`, `401` wrong
or missing secret, `422` unreadable payload, `502` GHL API unreachable.

## 5. Workflow: `engine-handoff` alert

1. **Trigger:** Contact Tag → tag added → `engine-handoff`.
2. **Action:** internal notification (email and/or SMS) to the roofer with the
   contact's name, phone and a link to the conversation. The reason and the
   last messages are in the newest note on the contact.
3. Optional branch: if the contact also has `engine-emergency`, notify by SMS
   or call as well. That tag means water is coming in right now.

## 6. IDs for the client config

| Config field | Where to find it |
|---|---|
| `ghl_location_id` | Settings → Business Profile → Location ID. Also in the URL: `/v2/location/<id>/...` |
| `calendar_id` | First command below |
| `pipeline_id`, `stage_ids.*` | Second command below. Stage ids are not shown in the GHL UI |
| `ghl_token_env` | Name of the env var from step 1, not the token itself |

```bash
export GHL_TOKEN='<the private integration token>'
export LOC='<location id>'

# Calendars: id + name
curl -s "https://services.leadconnectorhq.com/calendars/?locationId=$LOC" \
  -H "Authorization: Bearer $GHL_TOKEN" -H "Version: 2021-04-15"

# Pipelines: id + name, each with its stages (id + name)
curl -s "https://services.leadconnectorhq.com/opportunities/pipelines?locationId=$LOC" \
  -H "Authorization: Bearer $GHL_TOKEN" -H "Version: 2021-07-28"
```

Both also prove the token and its scopes work. Map your four stages to
`texted`, `replied`, `booked` and `no_response`. The server refuses to start
while any id is still a `...` placeholder or the token env var is empty.

## 7. Endpoints the engine calls

Base URL `https://services.leadconnectorhq.com`. Every call sends
`Authorization: Bearer <token>` and a `Version` header. Checked against GHL's
published OpenAPI specs
([GoHighLevel/highlevel-api-docs](https://github.com/GoHighLevel/highlevel-api-docs),
`apps/*.json`) on 2026-10-06.

| Purpose | Method + path | Version | Request |
|---|---|---|---|
| Send SMS | `POST /conversations/messages` | 2021-04-15 | `{type: "SMS", contactId, message}` |
| Find conversation | `GET /conversations/search` | 2021-04-15 | `?locationId&contactId&limit=1` |
| Read messages | `GET /conversations/{conversationId}/messages` | 2021-04-15 | `?limit=20` |
| Free slots | `GET /calendars/{calendarId}/free-slots` | 2021-04-15 | `?startDate&endDate` (epoch ms, max 31 days) `&timezone` |
| Create appointment | `POST /calendars/events/appointments` | 2021-04-15 | `{calendarId, locationId, contactId, startTime, endTime, title, appointmentStatus: "confirmed"}` |
| Move appointment | `PUT /calendars/events/appointments/{eventId}` | 2021-04-15 | `{calendarId, startTime, endTime}` |
| Cancel appointment | `PUT /calendars/events/appointments/{eventId}` | 2021-04-15 | `{appointmentStatus: "cancelled"}` |
| Read contact | `GET /contacts/{contactId}` | 2021-07-28 | |
| Add tags | `POST /contacts/{contactId}/tags` | 2021-07-28 | `{tags: [...]}` |
| Remove tags | `DELETE /contacts/{contactId}/tags` | 2021-07-28 | `{tags: [...]}` |
| Add note | `POST /contacts/{contactId}/notes` | 2021-07-28 | `{body}` |
| Find opportunity | `GET /opportunities/search` | 2021-07-28 | `?location_id&contact_id&pipeline_id&limit=1` |
| Move stage | `PUT /opportunities/{id}` | 2021-07-28 | `{pipelineId, pipelineStageId}` |

GHL documents rate limits of 100 requests per 10 seconds and 200,000 per day
per sub-account. A booked conversation costs roughly 15 calls.

## 8. Check on the first live conversation

The engine has only been tested against a fake GHL. Three things the specs
leave open, to confirm with one real test contact before a real lead:

1. **The SMS sends.** GHL's spec lists `subType` and `status` as required on
   `POST /conversations/messages`, but they describe email and stored
   messages, so the engine sends only `type`, `contactId` and `message`. If
   the send returns 4xx, the log line `ghl_call_rejected` shows GHL's reason.
2. **A taken slot is recognised.** When GHL refuses a booking, the engine
   treats a 400/409/422 whose message mentions "slot" as "taken" and offers
   other times. Any other refusal hands off.
3. **The merge fields fill in.** In the workflow's execution log, confirm the
   webhook body shows real values for `{{location.id}}`, `{{contact.id}}` and
   `{{message.body}}`.

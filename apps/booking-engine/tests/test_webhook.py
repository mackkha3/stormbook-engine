"""The GHL inbound webhook, end to end against a fake GHL: auth, mapping, gating, booking."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from booking_engine import main
from booking_engine.calendar.ghl import GhlCalendar
from booking_engine.db import init_db, make_engine
from booking_engine.engine import PHOTO_NOTE, Engine
from booking_engine.gateway import GhlGateway
from booking_engine.inbound_payload import BadPayload, parse_inbound, secret_ok
from booking_engine.logger import _JsonFormatter
from booking_engine.settings import Settings
from booking_engine.store import Store
from booking_engine.templates import Templates
from booking_engine.webhook import ClientRuntime, build_runtimes
from tests.conftest import NOW, ScriptedUnderstander, U
from tests.ghl_fake import LOCATION, OPENING, TOKEN, FakeGhl, live_client

SECRET = "shared-secret"


class Site:
    """The FastAPI app wired to a fake GHL, a scripted model and a frozen clock."""

    def __init__(self) -> None:
        self.ghl = FakeGhl()
        self.client = live_client()
        self.store = Store(init_db(make_engine("sqlite:///:memory:")))
        self.understander = ScriptedUnderstander()
        gateway = GhlGateway(self.ghl.client, self.client)
        self.engine = Engine(
            client=self.client, templates=Templates.load(), store=self.store,
            understander=self.understander, calendar=GhlCalendar(self.ghl.client, self.client),
            gateway=gateway, debounce_seconds=0.0, clock=lambda: NOW,
        )
        state = main.app.state
        state.settings = Settings(
            anthropic_api_key="k", database_url="sqlite:///:memory:", debounce_seconds=0.0,
            environment="test", ghl_webhook_secret=SECRET,
        )
        state.runtimes = {LOCATION: ClientRuntime(self.client, self.engine, self.ghl.client, gateway)}
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://engine.test"
        )
        self._ids = 0

    async def post(self, payload: dict, secret: str | None = SECRET) -> httpx.Response:
        headers = {"X-Webhook-Secret": secret} if secret is not None else {}
        response = await self.http.post("/webhooks/ghl/inbound", json=payload, headers=headers)
        await self.engine.debouncer.drain()
        return response

    async def text(self, body: str, understanding=None, **extra) -> httpx.Response:
        """One homeowner SMS arriving the way the GHL workflow delivers it."""
        if understanding is not None:
            self.understander.queue.append(understanding)
        self._ids += 1
        self.ghl.add_inbound(f"m-{self._ids}", body)
        payload = {"location_id": LOCATION, "contact_id": "c1", "contact_phone": "+19035550199",
                   "message_body": body, **extra}
        return await self.post(payload)

    @property
    def conv(self):
        return self.store.get_conversation(self.client.client_slug, "c1")


@pytest.fixture
def site() -> Site:
    return Site()


# --- authentication + payload mapping --------------------------------------------

def test_secret_check_fails_closed():
    assert secret_ok(SECRET, SECRET)
    assert not secret_ok("wrong", SECRET)
    assert not secret_ok(None, SECRET)
    assert not secret_ok("", "") and not secret_ok("anything", "")


@pytest.mark.parametrize("secret", [None, "wrong"])
async def test_requests_without_the_shared_secret_are_rejected(site: Site, secret):
    response = await site.post({"location_id": LOCATION, "contact_id": "c1",
                                "message_body": "yes"}, secret=secret)
    assert response.status_code == 401
    assert site.ghl.requests == [] and site.conv is None


async def test_a_payload_without_location_or_contact_is_a_422(site: Site):
    response = await site.post({"message_body": "yes"})
    assert response.status_code == 422 and "location_id" in response.json()["detail"]
    assert (await site.http.post("/webhooks/ghl/inbound", content=b"not json",
                                 headers={"X-Webhook-Secret": SECRET})).status_code == 422


def test_ghl_payload_shapes_all_map_to_the_internal_shape():
    flat = parse_inbound({
        "location_id": "L", "contact_id": "C", "contact_phone": "+1903", "message_id": "M",
        "message_body": " yes ", "timestamp": "2026-10-05T14:00:00Z", "lead_source": "web_form",
    })
    assert (flat.location_id, flat.contact_id, flat.body, flat.message_id) == ("L", "C", "yes", "M")
    assert flat.timestamp.isoformat() == "2026-10-05T14:00:00+00:00" and flat.lead_source == "web_form"

    standard = parse_inbound({       # GHL's standard workflow webhook + Custom Data rows
        "contact_id": "C", "full_name": "Jane Doe", "phone": "+1903",
        "location": {"id": "L", "name": "Roof Demo"},
        "message": {"type": 2, "body": "tmrw after 3"},
        "customData": {"lead_source": "missed_call"},
    })
    assert (standard.location_id, standard.contact_id, standard.body) == ("L", "C", "tmrw after 3")
    assert standard.message_id is None and standard.name == "Jane Doe"

    marketplace = parse_inbound({"locationId": "L", "contactId": "C", "body": "yes",
                                 "messageId": "M", "dateAdded": "2026-10-05T14:00:00.000Z"})
    assert (marketplace.location_id, marketplace.message_id) == ("L", "M")
    with pytest.raises(BadPayload):
        parse_inbound(["not", "an", "object"])


async def test_a_text_with_quotes_that_breaks_the_json_is_read_back_from_ghl(site: Site):
    site.ghl.add_inbound("m-quote", 'he said "tuesday"\nafter 3')
    site.understander.queue.append(U("propose_time", preferred_time_window="afternoon"))
    broken = ('{"location_id": "loc-1", "contact_id": "c1", '
              '"message_body": "he said "tuesday"\nafter 3"}')
    response = await site.http.post("/webhooks/ghl/inbound", content=broken.encode(),
                                    headers={"X-Webhook-Secret": SECRET})
    await site.engine.debouncer.drain()
    assert response.json() == {"status": "accepted"}
    turn = site.store.turns(site.conv.id)[-1]
    assert turn.inbound_text == 'he said "tuesday"\nafter 3'
    assert turn.inbound_message_ids == ["m-quote"] and len(site.ghl.sent) == 1


async def test_a_payload_with_no_text_and_nothing_inbound_in_ghl_is_ignored(site: Site):
    response = await site.post({"location_id": LOCATION, "contact_id": "c1", "message_body": ""})
    assert response.json() == {"status": "ignored", "reason": "empty_message"}
    assert site.ghl.sent == []


def test_app_boots_with_a_live_config_and_guards_the_webhook(monkeypatch):
    for name, value in {"ANTHROPIC_API_KEY": "k", "GHL_WEBHOOK_SECRET": SECRET,
                        "GHL_TOKEN_ROOF_DEMO": TOKEN, "DATABASE_URL": "sqlite:///:memory:"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main, "load_all_clients", lambda **_: {LOCATION: live_client()})
    with TestClient(main.app) as client:
        assert client.get("/health").json()["clients"] == 1
        assert client.post("/webhooks/ghl/inbound", json={}).status_code == 401


# --- the secret as a query parameter (GHL's plain Webhook action) -----------------

async def test_the_secret_is_also_accepted_as_a_query_parameter(site: Site):
    site.ghl.add_inbound("m-q", "yes")
    payload = {"location_id": LOCATION, "contact_id": "c1", "message_body": "yes"}
    wrong = await site.http.post("/webhooks/ghl/inbound?secret=wrong", json=payload)
    assert wrong.status_code == 401 and site.ghl.requests == []
    right = await site.http.post(f"/webhooks/ghl/inbound?secret={SECRET}", json=payload)
    await site.engine.debouncer.drain()
    assert right.json() == {"status": "accepted"} and len(site.ghl.sent) == 1


def test_a_secret_in_a_request_line_is_redacted_from_the_logs():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1", "POST", f"/webhooks/ghl/inbound?secret={SECRET}&x=1", "1.1", 200), None,
    )
    line = _JsonFormatter().format(record)
    assert SECRET not in line and "secret=[redacted]&x=1" in line


# --- picture-only texts -------------------------------------------------------------

PHOTO = ["https://storage.example/roof.jpg"]


async def test_a_picture_only_text_is_thanked_and_noted_on_the_contact(site: Site):
    site.ghl.add_inbound("m-pic", "", attachments=PHOTO)
    response = await site.post({"location_id": LOCATION, "contact_id": "c1", "message_body": ""})
    assert response.json() == {"status": "accepted"}
    assert site.ghl.sent == [(
        "Thanks for the photo. Your inspector will take a look on-site. "
        "Does Mon 10/5 at 11:00 AM or Tue 10/6 at 3:00 PM work?"
    )]
    assert site.ghl.bodies("POST", "/notes") == [{"body": PHOTO_NOTE}]
    assert site.understander.contexts == []
    assert site.store.turns(site.conv.id)[-1].inbound_message_ids == ["m-pic"]


async def test_a_picture_after_booking_is_thanked_without_slots(site: Site):
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))
    site.ghl.add_inbound("m-pic", "", attachments=PHOTO)
    await site.post({"location_id": LOCATION, "contact_id": "c1", "message_body": ""})
    assert site.ghl.sent[-1] == "Thanks for the photo. Your inspector will take a look on-site."
    assert site.conv.status == "booked" and len(site.ghl.appointments) == 1


async def test_a_picture_sent_with_words_is_noted_and_the_words_are_understood(site: Site):
    site.ghl.add_inbound("m-both", "is this bad", attachments=PHOTO)
    site.understander.queue.append(U("deferrable_question"))
    await site.post({"location_id": LOCATION, "contact_id": "c1", "message_body": "is this bad"})
    assert site.ghl.sent[-1].startswith("Good question.")
    assert site.ghl.bodies("POST", "/notes") == [{"body": PHOTO_NOTE}]


async def test_a_picture_from_a_contact_without_engine_active_is_ignored(site: Site):
    site.ghl.tags = set()
    site.ghl.add_inbound("m-pic", "", attachments=PHOTO)
    response = await site.post({"location_id": LOCATION, "contact_id": "c1", "message_body": ""})
    assert response.json()["reason"] == "not_engine_active" and site.ghl.sent == []


# --- gating ------------------------------------------------------------------------

async def test_unknown_location_is_ignored(site: Site):
    response = await site.post({"location_id": "someone-else", "contact_id": "c1",
                                "message_body": "yes"})
    assert response.json() == {"status": "ignored", "reason": "unknown_location"}
    assert site.ghl.requests == []


async def test_contact_without_the_engine_active_tag_is_ignored(site: Site):
    site.ghl.tags = {"some-other-tag"}
    response = await site.text("yes")
    assert response.json() == {"status": "ignored", "reason": "not_engine_active"}
    assert site.ghl.sent == [] and site.conv is None


async def test_contact_from_another_sub_account_is_ignored(site: Site):
    site.ghl.contact_location = "other-location"
    assert (await site.text("yes")).json()["reason"] == "contact_not_in_location"
    assert site.ghl.sent == []


async def test_ghl_being_down_returns_502_and_sends_nothing(site: Site):
    site.ghl.fail[("GET", "/contacts/c1")] += [httpx.Response(500)] * 2
    assert (await site.text("yes")).status_code == 502
    assert site.ghl.sent == [] and site.conv is None


# --- the happy path, through the real GHL client -----------------------------------

async def test_first_reply_offers_real_slots_and_seeds_the_ghl_opening_text(site: Site):
    response = await site.text("yes", U("affirm"))
    assert response.json() == {"status": "accepted"}
    assert site.ghl.sent == [(
        "Great! I can get an inspector out Mon 10/5 at 11:00 AM or Tue 10/6 at 3:00 PM. "
        "Which works better? Just reply 1 or 2."
    )]
    # The model saw the opening message GHL actually sent, not the configured fallback.
    assert site.understander.contexts[0].history == [{"role": "engine", "text": OPENING}]
    # First reply: the opportunity moves to the `replied` stage.
    assert site.ghl.bodies("PUT", "/opportunities/opp-1")[0]["pipelineStageId"] == "st-replied"


async def test_picking_a_slot_books_it_in_ghl_and_updates_the_crm(site: Site):
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))

    assert site.ghl.appointments["appt-1"] == {
        "calendarId": "cal-1", "locationId": LOCATION, "contactId": "c1",
        "startTime": "2026-10-05T11:00:00-05:00", "endTime": "2026-10-05T12:00:00-05:00",
        "title": "Roof inspection: Jane Doe", "appointmentStatus": "confirmed",
    }
    assert site.ghl.sent[-1].startswith("You're all set for Mon 10/5 at 11:00 AM.")
    assert site.ghl.bodies("PUT", "/opportunities/opp-1")[-1]["pipelineStageId"] == "st-booked"
    assert "booked" in site.ghl.tags
    assert site.conv.status == "booked" and site.conv.appointment_id == "appt-1"


async def test_slot_taken_between_offer_and_pick_is_not_booked(site: Site):
    await site.text("yes", U("affirm"))
    site.ghl.slots.remove(site.ghl.slots[0])              # someone else books Mon 11:00
    await site.text("1", U("pick_slot", slot_choice=1))
    assert site.ghl.appointments == {} and site.conv.status == "slots_offered"
    assert site.ghl.sent[-1].startswith("That time's taken, but I have")


async def test_reschedule_and_cancel_act_on_the_same_ghl_appointment(site: Site):
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))
    await site.text("can we move it", U("reschedule"))
    await site.text("2", U("pick_slot", slot_choice=2))
    moved = site.ghl.appointments["appt-1"]["startTime"]
    assert moved != "2026-10-05T11:00:00-05:00" and len(site.ghl.appointments) == 1
    await site.text("please cancel my inspection", U("cancel"))
    assert site.ghl.appointments["appt-1"]["appointmentStatus"] == "cancelled"
    assert "engine-cancelled" in site.ghl.tags


# --- idempotency -------------------------------------------------------------------

async def test_a_retried_webhook_never_produces_a_second_reply(site: Site):
    payload = {"location_id": LOCATION, "contact_id": "c1", "message_id": "ghl-msg-1",
               "message_body": "yes"}
    assert (await site.post(payload)).json() == {"status": "accepted"}
    assert (await site.post(payload)).json() == {"status": "duplicate"}
    assert len(site.ghl.sent) == 1


async def test_without_a_message_id_the_ghl_message_id_is_used_for_dedupe(site: Site):
    site.ghl.add_inbound("ghl-msg-7", "yes")
    payload = {"location_id": LOCATION, "contact_id": "c1", "message_body": "yes"}
    assert (await site.post(payload)).json() == {"status": "accepted"}
    assert (await site.post(payload)).json() == {"status": "duplicate"}
    assert len(site.ghl.sent) == 1
    assert site.store.turns(site.conv.id)[-1].inbound_message_ids == ["ghl-msg-7"]


async def test_a_retry_is_still_dropped_when_ghl_messages_cannot_be_read(site: Site):
    site.ghl.fail[("GET", "/conversations/search")] += [httpx.Response(500)] * 4
    payload = {"location_id": LOCATION, "contact_id": "c1", "message_body": "yes",
               "timestamp": "2026-10-05T14:00:00Z"}
    assert (await site.post(payload)).json() == {"status": "accepted"}
    assert (await site.post(payload)).json() == {"status": "duplicate"}
    assert len(site.ghl.sent) == 1


# --- handoff, and coming back from it ------------------------------------------------

async def test_handoff_tags_the_contact_adds_a_note_and_then_stays_silent(site: Site):
    await site.text("can someone call me", U("human_request"))
    assert {"engine-handoff"} <= site.ghl.tags and "engine-active" not in site.ghl.tags
    note = site.ghl.bodies("POST", "/notes")[0]["body"]
    assert "Reason: human_request" in note and "homeowner: can someone call me" in note
    sent = len(site.ghl.sent)

    response = await site.text("hello?")
    assert response.json() == {"status": "ignored", "reason": "not_engine_active"}
    assert len(site.ghl.sent) == sent


async def test_adding_engine_active_back_resumes_the_conversation(site: Site):
    await site.text("can someone call me", U("human_request"))
    site.ghl.tags.add("engine-active")                    # a human hands it back in GHL

    assert (await site.text("ok lets book it", U("affirm"))).json() == {"status": "accepted"}
    assert site.ghl.sent[-1].startswith("Great! I can get an inspector out")
    assert site.conv.status == "slots_offered" and site.conv.handoff_reason is None
    assert "engine-handoff" not in site.ghl.tags          # so the next handoff alerts again


async def test_stop_after_a_handoff_is_still_recorded_as_an_opt_out(site: Site):
    await site.text("can someone call me", U("human_request"))
    sent = len(site.ghl.sent)
    assert (await site.text("STOP")).json() == {"status": "accepted"}
    assert site.conv.status == "opted_out" and "engine-opted-out" in site.ghl.tags
    assert len(site.ghl.sent) == sent

    site.ghl.tags.add("engine-active")                    # even re-tagged, opted out is final
    await site.text("yes")
    assert site.conv.status == "opted_out" and len(site.ghl.sent) == sent


async def test_a_turn_that_fails_on_a_ghl_outage_still_alerts_a_human(site: Site):
    site.ghl.fail[("POST", "/conversations/messages")] += [httpx.Response(500)] * 2
    await site.text("yes", U("affirm"))
    assert site.conv.status == "handoff" and site.conv.handoff_reason == "internal_error: GhlError"
    assert "engine-handoff" in site.ghl.tags and "engine-active" not in site.ghl.tags
    assert "internal_error" in site.ghl.bodies("POST", "/notes")[0]["body"]


async def test_no_free_slots_in_ghl_hands_off(site: Site):
    site.ghl.slots = []
    await site.text("yes", U("affirm"))
    assert site.conv.status == "handoff" and "engine-handoff" in site.ghl.tags
    assert site.ghl.sent == [
        "Thanks! Someone from Roof Demo will reach out shortly to find a time that works."
    ]


# --- wiring ----------------------------------------------------------------------------

async def test_build_runtimes_gives_each_client_real_ghl_backends(site: Site, monkeypatch):
    monkeypatch.setenv("GHL_TOKEN_ROOF_DEMO", TOKEN)
    runtimes = build_runtimes(
        settings=main.app.state.settings, clients={LOCATION: site.client},
        templates=Templates.load(), store=site.store, understander=site.understander,
        http=site.ghl.http,
    )
    runtime = runtimes[LOCATION]
    assert isinstance(runtime.engine.calendar, GhlCalendar)
    assert isinstance(runtime.engine.gateway, GhlGateway)
    await runtime.ghl.get_contact("c1")
    assert site.ghl.requests[-1].headers["Authorization"] == f"Bearer {TOKEN}"


def test_server_refuses_to_start_without_its_secrets():
    blank = Settings(anthropic_api_key="", database_url="x", debounce_seconds=8,
                     environment="test", ghl_webhook_secret="")
    assert blank.missing_for_server() == ["ANTHROPIC_API_KEY", "GHL_WEBHOOK_SECRET"]

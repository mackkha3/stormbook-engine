"""ghl_client + the GHL calendar: exact paths, Version headers and bodies, retries, errors."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import httpx
import pytest

from booking_engine.calendar.base import CalendarError, SlotUnavailable
from booking_engine.calendar.ghl import GhlCalendar
from booking_engine.gateway import GhlGateway
from booking_engine.ghl_client import GhlError
from booking_engine.templates import Templates
from tests.conftest import NOW
from tests.ghl_fake import CALENDAR, LOCATION, PIPELINE, TOKEN, FakeGhl, live_client, slot


@pytest.fixture
def ghl() -> FakeGhl:
    return FakeGhl()


# --- endpoints, headers, bodies ------------------------------------------------

async def test_every_call_carries_the_bearer_token_and_the_right_version_header(ghl: FakeGhl):
    c = ghl.client
    await c.send_sms("c1", "hello")
    await c.recent_messages("c1")
    await c.free_slots(CALENDAR, start_ms=1, end_ms=2, timezone="America/Chicago")
    await c.create_appointment(
        calendar_id=CALENDAR, contact_id="c1", title="t",
        start=slot(5, 11).isoformat(), end=slot(5, 12).isoformat(),
    )
    await c.update_appointment("appt-1", appointmentStatus="cancelled")
    await c.get_contact("c1")
    await c.add_tags("c1", ["booked"])
    await c.remove_tags("c1", ["engine-active"])
    await c.add_note("c1", "note")
    opportunity = await c.find_opportunity("c1", PIPELINE)
    await c.update_opportunity_stage(opportunity, pipeline_id=PIPELINE, stage_id="st-booked")

    seen = [(r.method, r.url.path, r.headers["Version"]) for r in ghl.requests]
    assert seen == [
        ("POST", "/conversations/messages", "2021-04-15"),
        ("GET", "/conversations/search", "2021-04-15"),
        ("GET", "/conversations/conv-1/messages", "2021-04-15"),
        ("GET", f"/calendars/{CALENDAR}/free-slots", "2021-04-15"),
        ("POST", "/calendars/events/appointments", "2021-04-15"),
        ("PUT", "/calendars/events/appointments/appt-1", "2021-04-15"),
        ("GET", "/contacts/c1", "2021-07-28"),
        ("POST", "/contacts/c1/tags", "2021-07-28"),
        ("DELETE", "/contacts/c1/tags", "2021-07-28"),
        ("POST", "/contacts/c1/notes", "2021-07-28"),
        ("GET", "/opportunities/search", "2021-07-28"),
        ("PUT", "/opportunities/opp-1", "2021-07-28"),
    ]
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in ghl.requests)
    assert all(str(r.url).startswith("https://services.leadconnectorhq.com/") for r in ghl.requests)


async def test_request_bodies_and_query_strings_match_the_documented_shapes(ghl: FakeGhl):
    c = ghl.client
    await c.send_sms("c1", "hello")
    await c.free_slots(CALENDAR, start_ms=1000, end_ms=2000, timezone="America/Chicago")
    await c.create_appointment(
        calendar_id=CALENDAR, contact_id="c1", title="Roof inspection: Jane Doe",
        start="2026-10-05T11:00:00-05:00", end="2026-10-05T12:00:00-05:00",
    )
    await c.remove_tags("c1", ["engine-active"])
    await c.add_note("c1", "why")
    await c.find_opportunity("c1", PIPELINE)
    await c.update_opportunity_stage("opp-1", pipeline_id=PIPELINE, stage_id="st-booked")

    assert ghl.bodies("POST", "/conversations/messages") == [
        {"type": "SMS", "contactId": "c1", "message": "hello"}
    ]
    assert dict(ghl.calls("GET", "free-slots")[0].url.params) == {
        "startDate": "1000", "endDate": "2000", "timezone": "America/Chicago",
    }
    assert ghl.bodies("POST", "/calendars/events/appointments") == [{
        "calendarId": CALENDAR, "locationId": LOCATION, "contactId": "c1",
        "startTime": "2026-10-05T11:00:00-05:00", "endTime": "2026-10-05T12:00:00-05:00",
        "title": "Roof inspection: Jane Doe", "appointmentStatus": "confirmed",
    }]
    assert ghl.bodies("DELETE", "/tags") == [{"tags": ["engine-active"]}]
    assert ghl.bodies("POST", "/notes") == [{"body": "why"}]
    assert dict(ghl.calls("GET", "/opportunities/search")[0].url.params) == {
        "location_id": LOCATION, "contact_id": "c1", "pipeline_id": PIPELINE, "limit": "1",
    }
    assert ghl.bodies("PUT", "/opportunities/") == [
        {"pipelineId": PIPELINE, "pipelineStageId": "st-booked"}
    ]


async def test_recent_messages_come_back_oldest_first_whatever_the_nesting(ghl: FakeGhl):
    ghl.add_inbound("m-1", "yes")
    assert [m["id"] for m in await ghl.client.recent_messages("c1")] == ["m-open", "m-1"]
    ghl.fail[("GET", "/conversations/conv-1/messages")].append(
        httpx.Response(200, json={"messages": list(reversed(ghl.messages)), "nextPage": False})
    )
    assert [m["id"] for m in await ghl.client.recent_messages("c1")] == ["m-open", "m-1"]


# --- retries, errors, logging --------------------------------------------------

async def test_a_5xx_is_retried_once_then_succeeds(ghl: FakeGhl):
    ghl.fail[("POST", "/contacts/c1/tags")].append(httpx.Response(503))
    await ghl.client.add_tags("c1", ["booked"])
    assert len(ghl.calls("POST", "/tags")) == 2 and "booked" in ghl.tags


async def test_two_5xx_in_a_row_raise_and_there_is_no_third_attempt(ghl: FakeGhl):
    ghl.fail[("POST", "/contacts/c1/tags")] += [httpx.Response(500), httpx.Response(502)]
    with pytest.raises(GhlError) as error:
        await ghl.client.add_tags("c1", ["booked"])
    assert error.value.status == 502 and len(ghl.calls("POST", "/tags")) == 2


async def test_a_4xx_is_never_retried(ghl: FakeGhl):
    ghl.fail[("GET", "/contacts/c1")].append(httpx.Response(401, json={"message": "Invalid token"}))
    with pytest.raises(GhlError) as error:
        await ghl.client.get_contact("c1")
    assert error.value.status == 401 and len(ghl.calls("GET", "/contacts/c1")) == 1


async def test_a_timeout_on_a_read_is_retried_once(ghl: FakeGhl):
    ghl.fail[("GET", "/contacts/c1")].append(httpx.ReadTimeout("slow"))
    assert (await ghl.client.get_contact("c1"))["name"] == "Jane Doe"
    assert len(ghl.calls("GET", "/contacts/c1")) == 2


async def test_a_response_timeout_on_send_is_not_repeated_so_no_text_goes_out_twice(ghl: FakeGhl):
    ghl.fail[("POST", "/conversations/messages")].append(httpx.ReadTimeout("slow"))
    with pytest.raises(GhlError):
        await ghl.client.send_sms("c1", "hello")
    assert len(ghl.calls("POST", "/conversations/messages")) == 1


async def test_a_failed_connection_on_send_is_retried_because_nothing_was_sent(ghl: FakeGhl):
    ghl.fail[("POST", "/conversations/messages")].append(httpx.ConnectError("refused"))
    assert await ghl.client.send_sms("c1", "hello") == "m-out"


async def test_the_token_never_reaches_the_logs(ghl: FakeGhl, caplog):
    caplog.set_level(logging.DEBUG)
    ghl.fail[("GET", "/contacts/c1")] += [httpx.Response(500), httpx.Response(403, text="nope")]
    await ghl.client.add_tags("c1", ["booked"])
    with pytest.raises(GhlError) as error:
        await ghl.client.get_contact("c1")
    logged = json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert "ghl_call" in logged and TOKEN not in logged and TOKEN not in str(error.value)


# --- the GHL calendar ----------------------------------------------------------

@pytest.fixture
def calendar(ghl: FakeGhl) -> GhlCalendar:
    return GhlCalendar(ghl.client, live_client())


async def test_free_slots_are_exactly_what_ghl_returned_inside_the_window(ghl, calendar):
    free = await calendar.free_slots(NOW + timedelta(hours=2), NOW + timedelta(days=1, hours=2))
    assert free == [slot(5, 11), slot(5, 14), slot(6, 10)]       # traceId ignored, rest outside
    params = dict(ghl.calls("GET", "free-slots")[0].url.params)
    assert params["timezone"] == "America/Chicago"
    assert params["startDate"] == str(int((NOW + timedelta(hours=2)).timestamp() * 1000))


async def test_a_window_longer_than_ghl_allows_is_split_into_several_calls(ghl, calendar):
    await calendar.free_slots(NOW, NOW + timedelta(days=45))
    spans = [                                  # in days; the range crosses a clock change
        round((int(r.url.params["endDate"]) - int(r.url.params["startDate"])) / 86_400_000)
        for r in ghl.calls("GET", "free-slots")
    ]
    assert spans == [30, 15]


async def test_is_free_asks_ghl_again_and_sees_a_slot_someone_else_took(ghl, calendar):
    assert await calendar.is_free(slot(5, 14))
    ghl.slots.remove(slot(5, 14))
    assert not await calendar.is_free(slot(5, 14))


async def test_booking_creates_a_one_hour_appointment_in_the_client_timezone(ghl, calendar):
    appointment_id = await calendar.book(
        slot(6, 10), contact_id="c1", title="Roof inspection: Jane Doe", duration_minutes=60
    )
    assert appointment_id == "appt-1"
    assert ghl.appointments["appt-1"]["startTime"] == "2026-10-06T10:00:00-05:00"
    assert ghl.appointments["appt-1"]["endTime"] == "2026-10-06T11:00:00-05:00"


async def test_a_slot_ghl_refuses_is_reported_as_taken_and_other_errors_are_not(ghl, calendar):
    with pytest.raises(SlotUnavailable):
        await calendar.book(slot(8, 10), contact_id="c1", title="t", duration_minutes=60)
    ghl.fail[("POST", "/calendars/events/appointments")].append(
        httpx.Response(400, json={"message": "calendarId must be a string"})
    )
    with pytest.raises(CalendarError) as error:
        await calendar.book(slot(6, 10), contact_id="c1", title="t", duration_minutes=60)
    assert not isinstance(error.value, SlotUnavailable)


async def test_reschedule_moves_and_cancel_cancels_the_same_appointment(ghl, calendar):
    appointment_id = await calendar.book(slot(6, 10), contact_id="c1", title="t", duration_minutes=60)
    await calendar.reschedule(appointment_id, slot(7, 13), duration_minutes=60)
    assert ghl.appointments[appointment_id]["startTime"] == "2026-10-07T13:00:00-05:00"
    assert ghl.appointments[appointment_id]["endTime"] == "2026-10-07T14:00:00-05:00"
    await calendar.cancel(appointment_id)
    assert ghl.appointments[appointment_id]["appointmentStatus"] == "cancelled"
    assert len(ghl.appointments) == 1


async def test_a_ghl_outage_surfaces_as_a_calendar_error(ghl, calendar):
    ghl.fail[("GET", f"/calendars/{CALENDAR}/free-slots")] += [httpx.Response(500)] * 2
    with pytest.raises(CalendarError):
        await calendar.free_slots(NOW, NOW + timedelta(days=1))


# --- the GHL gateway -----------------------------------------------------------

async def test_gateway_only_sends_template_output_and_moves_the_stage_by_name(ghl: FakeGhl):
    gateway = GhlGateway(ghl.client, live_client())
    with pytest.raises(TypeError):
        await gateway.send_sms("c1", "free-typed text")
    await gateway.send_sms("c1", Templates.load().render("handoff", {"company_name": "Roof Demo"}))
    await gateway.set_stage("c1", "booked")
    assert len(ghl.sent) == 1 and "Roof Demo" in ghl.sent[0]
    assert ghl.bodies("PUT", "/opportunities/opp-1") == [
        {"pipelineId": PIPELINE, "pipelineStageId": "st-booked"}
    ]


async def test_a_missing_opportunity_or_failed_stage_move_never_raises(ghl: FakeGhl):
    gateway = GhlGateway(ghl.client, live_client())
    ghl.opportunities = []
    await gateway.set_stage("c1", "booked")
    assert ghl.calls("PUT", "/opportunities/") == []
    ghl.fail[("GET", "/opportunities/search")] += [httpx.Response(500)] * 2
    await gateway.set_stage("c1", "booked")

"""Address and name after booking: ask, check, write to GHL, and the 30-minute alert."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from booking_engine import main
from booking_engine.db import ADDED_COLUMNS, init_db, make_engine, migrate
from booking_engine.details import clean_address, clean_name, split_name
from booking_engine.engine import ADDRESS_WAIT
from booking_engine.models import Base
from tests.conftest import Harness, U
from tests.test_webhook import Site

ASK = "You're booked for today at 11 AM. What's the address, and who should the inspector ask for?"


async def book(h: Harness) -> None:
    await h.reach("offered")
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.reply == ASK
    h.gateway.calls.clear()


def calls(h: Harness, name: str) -> list[dict]:
    return [c for c in h.gateway.calls if c["call"] == name]


def details(text: str, address: str | None = None, name: str | None = None, intent="give_details"):
    return U(intent, address=address, contact_name=name)


# --- the checks ---------------------------------------------------------------

@pytest.mark.parametrize("address", [
    "FM 2493 Tyler", "12345 CR 4120", "Hwy 69 N", "Loop 323",          # the Texas rural forms
    "Farm to Market Road 2493", "88 County Road 12 Lindale", "4410 Highway 31 E", "RR 2 Box 14",
    "Spur 248", "PR 7041", "US 69 N", "123 Oak St, Tyler, TX 75703", "1500 S Broadway Ave #4",
])
def test_addresses_that_are_texted_back(address):
    assert clean_address(address) == address


@pytest.mark.parametrize("address", [
    None, "", "2 kids", "the white house", "123", "Oak St",                  # no number / street
    "see https://maps.app/x 12 st", "12 Oak St / back gate", "me@x.com 4 st",
    "123 Oak St 3 PM", "4 Oak St tomorrow",                                  # reads as a time
    "100 Free Roof Rd",                                                      # banned phrase
    "1" * 98 + " St",                                                        # too long
])
def test_addresses_that_are_never_texted_back(address):
    assert clean_address(address) is None


def test_addresses_are_tidied():
    assert clean_address("  123  Oak St,  Tyler , ") == "123 Oak St, Tyler"


@pytest.mark.parametrize("name", [
    "Jane", "Mary-Jo", "O'Brien", "O’Brien", "José", "Chris and Amy", "Chris & Amy",
    "Mary-Jo O'Brien", "Zoë Núñez", "Dr. Smith",
])
def test_names_that_are_texted_back(name):
    assert clean_name(name) == name


@pytest.mark.parametrize("name", [
    None, "", "R2D2", "Jane 2", "and", "Chris and", "& Amy", "jane@x.com", "www.jane.com",
    "x" * 41, "Jane; drop", "Monday",
])
def test_names_that_are_never_texted_back(name):
    assert clean_name(name) is None


def test_names_split_for_the_contact():
    assert split_name("Mary-Jo O'Brien") == ("Mary-Jo", "O'Brien")
    assert split_name("José") == ("José", "")
    assert split_name("Chris and Amy") == ("Chris and Amy", "")


# --- the conversation -----------------------------------------------------------

async def test_booking_happens_first_then_the_address_question():
    h = Harness()
    await h.reach("offered")
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.reply == ASK and h.conv.status == "booked"
    assert len(h.calendar.appointments) == 1
    assert calls(h, "set_stage") and not calls(h, "add_tags")       # `booked` tag held back
    assert h.conv.address_requested_at is not None


async def test_address_and_name_are_confirmed_and_written_to_ghl():
    h = Harness()
    await book(h)
    result = await h.say("123 Oak St, Tyler. Ask for Jane",
                         details("", "123 Oak St, Tyler", "Jane"))
    assert result.reply == (
        "You're all set for today at 11 AM at 123 Oak St, Tyler. Our inspector will ask for "
        "Jane. Just text here if anything changes."
    )
    assert calls(h, "update_contact")[0]["fields"] == {
        "address1": "123 Oak St, Tyler", "firstName": "Jane", "lastName": ""}
    (appointment,) = h.calendar.appointments.values()
    assert appointment.title == "Roof inspection: Jane, 123 Oak St, Tyler"
    (note,) = [c["body"] for c in calls(h, "add_note")]
    assert "Address: 123 Oak St, Tyler" in note and "Ask for: Jane" in note
    assert "Inspection: Mon 10/5 at 11:00 AM" in note
    assert calls(h, "add_tags") == [{"call": "add_tags", "contact_id": "contact-1", "tags": ["booked"]}]
    assert h.conv.address_requested_at is None


async def test_address_only_keeps_the_ghl_name_out_of_every_text():
    h = Harness()                                    # GHL's caller-ID name is "Jane Doe"
    await book(h)
    result = await h.say("FM 2493 Tyler", details("", "FM 2493 Tyler"))
    assert result.reply == (
        "You're all set for today at 11 AM at FM 2493 Tyler. Our inspector will meet you "
        "there. Just text here if anything changes."
    )
    assert calls(h, "update_contact")[0]["fields"] == {"address1": "FM 2493 Tyler"}
    assert not [t for t in h.sent if "Jane" in t or "Doe" in t]


async def test_a_name_alone_gets_asked_for_the_address_then_the_confirmation():
    h = Harness()
    await book(h)
    first = await h.say("Chris and Amy", details("", name="Chris and Amy"))
    assert first.reply == "Thanks, Chris and Amy. What's the address for the inspection?"
    assert calls(h, "update_contact")[0]["fields"] == {"firstName": "Chris and Amy", "lastName": ""}
    assert not calls(h, "add_tags") and h.conv.address_requested_at is not None

    second = await h.say("12345 CR 4120", details("", "12345 CR 4120"))
    assert second.reply.startswith("You're all set for today at 11 AM at 12345 CR 4120. "
                                   "Our inspector will ask for Chris and Amy.")
    assert ["booked"] in [c["tags"] for c in calls(h, "add_tags")]


async def test_an_address_that_fails_the_checks_is_noted_not_texted():
    h = Harness()
    await book(h)
    result = await h.say("the white house by the big oak, 3 PM gate code",
                         details("", "white house by the big oak, 3 PM gate code"))
    assert result.template_key == "booked"           # the plain confirmation
    assert "oak" not in result.reply.lower() and not calls(h, "update_contact")
    (note,) = [c["body"] for c in calls(h, "add_note")]
    assert "Address as texted (not checked, not saved to the contact): white house" in note
    # The roofer's alert goes out now, with the note, rather than "address missing" later.
    assert ["booked"] in [c["tags"] for c in calls(h, "add_tags")]
    assert h.conv.address_requested_at is None


async def test_details_given_with_a_reschedule_are_kept_and_the_move_confirms_with_them():
    h = Harness()
    await book(h)
    moved = await h.say("123 Oak St. can we do tomorrow instead",
                        U("reschedule", address="123 Oak St"))
    assert moved.template_key == "reschedule"
    assert calls(h, "update_contact")[0]["fields"] == {"address1": "123 Oak St"}
    done = await h.say("1", U("pick_slot", slot_choice=1))
    assert done.template_key == "booked_at_address"
    assert " at 123 Oak St. Our inspector will meet you there." in done.reply


async def test_a_web_form_lead_with_an_address_is_never_asked():
    h = Harness()
    h.contact = replace(h.contact, address="500 Elm St, Tyler")
    await h.reach("offered")
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.reply.startswith("You're all set for today at 11 AM at 500 Elm St, Tyler.")
    assert ["booked"] in [c["tags"] for c in h.gateway.calls if c["call"] == "add_tags"]
    assert h.conv.address_requested_at is None
    (appointment,) = h.calendar.appointments.values()
    assert appointment.title == "Roof inspection: Jane Doe, 500 Elm St, Tyler"


async def test_a_ghl_address_that_fails_the_checks_is_asked_for():
    h = Harness()
    h.contact = replace(h.contact, address="Apt 5")
    await h.reach("offered")
    assert (await h.say("1", U("pick_slot", slot_choice=1))).reply == ASK


async def test_ok_after_the_question_stays_quiet_and_keeps_waiting():
    h = Harness()
    await book(h)
    result = await h.say("ok", U("affirm", acknowledgment_only=True))
    assert result.reply is None and h.conv.address_requested_at is not None


# --- the 30-minute alert ----------------------------------------------------------

async def test_no_address_after_30_minutes_tells_the_roofer_once():
    h = Harness()
    await book(h)
    h.now += ADDRESS_WAIT - timedelta(minutes=1)
    assert await h.engine.flag_missing_addresses() == 0 and not h.gateway.calls

    h.now += timedelta(minutes=2)
    assert await h.engine.flag_missing_addresses() == 1
    (note,) = [c["body"] for c in calls(h, "add_note")]
    assert note.startswith("Booking engine: Address missing, call them.")
    assert "Mon 10/5 at 11:00 AM" in note
    assert calls(h, "add_tags")[0]["tags"] == ["booked", "engine-address-missing"]
    assert h.conv.address_missing and h.conv.address_requested_at is None
    assert not calls(h, "send_sms")                  # the homeowner is not texted

    h.gateway.calls.clear()
    h.now += timedelta(minutes=5)
    assert await h.engine.flag_missing_addresses() == 0 and not h.gateway.calls


async def test_an_address_after_the_alert_clears_it_and_is_confirmed():
    h = Harness()
    await book(h)
    h.now += ADDRESS_WAIT + timedelta(minutes=1)
    await h.engine.flag_missing_addresses()
    h.gateway.calls.clear()

    result = await h.say("Loop 323", details("", "Loop 323"))
    assert result.template_key == "booked_at_address"
    assert calls(h, "remove_tags") == [
        {"call": "remove_tags", "contact_id": "contact-1", "tags": ["engine-address-missing"]}]
    assert not calls(h, "add_tags")                  # `booked` already went on with the alert
    assert not h.conv.address_missing


async def test_a_cancelled_booking_is_not_chased():
    h = Harness()
    await book(h)
    await h.say("please cancel my inspection", U("cancel"))
    h.now += ADDRESS_WAIT * 2
    assert await h.engine.flag_missing_addresses() == 0
    assert not [c for c in h.gateway.calls if c.get("tags") == ["booked", "engine-address-missing"]]


async def test_an_opt_out_still_alerts_because_the_inspection_stands():
    h = Harness()
    await book(h)
    await h.say("STOP")
    h.now += ADDRESS_WAIT * 2
    assert await h.engine.flag_missing_addresses() == 1


async def test_a_ghl_failure_leaves_the_alert_for_the_next_sweep():
    h = Harness()
    await book(h)
    h.now += ADDRESS_WAIT * 2

    async def down(*args, **kwargs):
        raise RuntimeError("GHL down")

    real, h.gateway.add_note = h.gateway.add_note, down
    assert await h.engine.flag_missing_addresses() == 0
    assert h.conv.address_requested_at is not None
    h.gateway.add_note = real
    assert await h.engine.flag_missing_addresses() == 1


async def test_the_app_sweeps_every_client_and_survives_a_failure():
    class Engine:
        def __init__(self, fail: bool) -> None:
            self.fail, self.runs = fail, 0

        async def flag_missing_addresses(self) -> int:
            self.runs += 1
            if self.fail:
                raise RuntimeError("boom")
            return 0

    class Runtime:
        def __init__(self, slug: str, fail: bool) -> None:
            self.engine, self.client = Engine(fail), type("C", (), {"client_slug": slug})()

    runtimes = {"a": Runtime("a", fail=True), "b": Runtime("b", fail=False)}
    task = asyncio.create_task(main.sweep_missing_addresses(runtimes, every=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    assert runtimes["a"].engine.runs >= 2 and runtimes["b"].engine.runs >= 2


# --- through the webhook and the GHL client ---------------------------------------

async def test_webhook_flow_writes_the_address_to_the_ghl_contact_and_appointment():
    site = Site()
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))
    assert site.ghl.sent[-1] == ASK and "booked" not in site.ghl.tags

    await site.text("FM 2493 Tyler, ask for Mary-Jo O'Brien",
                    details("", "FM 2493 Tyler", "Mary-Jo O'Brien"))
    assert site.ghl.sent[-1].startswith(
        "You're all set for today at 11 AM at FM 2493 Tyler. Our inspector will ask for Mary-Jo O'Brien.")
    assert site.ghl.contact_fields == {
        "address1": "FM 2493 Tyler", "firstName": "Mary-Jo", "lastName": "O'Brien"}
    assert site.ghl.appointments["appt-1"]["title"] == (
        "Roof inspection: Mary-Jo O'Brien, FM 2493 Tyler")
    assert "booked" in site.ghl.tags


async def test_webhook_reads_the_address_already_on_the_ghl_contact():
    site = Site()
    site.ghl.contact_fields = {"address1": "4410 Old Jacksonville Hwy", "city": "Tyler"}
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))
    assert site.ghl.sent[-1].startswith(
        "You're all set for today at 11 AM at 4410 Old Jacksonville Hwy, Tyler.")
    assert "booked" in site.ghl.tags


# --- the migration ----------------------------------------------------------------

def _old_schema_engine():
    """A database as production has it: conversations without the added columns."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for name in ADDED_COLUMNS["conversations"]:
            conn.execute(sa.text(f"ALTER TABLE conversations DROP COLUMN {name}"))
        conn.execute(sa.text(
            "INSERT INTO conversations (client_slug, contact_id, contact_phone, status, "
            "current_offer, offered_slots, awaiting_preference, none_work_streak, unclear_streak, "
            "engine_message_count, created_at, updated_at) VALUES ('roof-demo', 'c-old', '+1', "
            "'booked', '[]', '[]', 0, 0, 0, 0, '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
        ))
    return engine


def test_migration_adds_the_columns_and_keeps_existing_rows():
    engine = _old_schema_engine()
    statements: list[str] = []
    sa.event.listen(engine, "before_cursor_execute",
                    lambda conn, cursor, statement, *a: statements.append(statement))
    init_db(engine)

    names = {c["name"] for c in sa.inspect(engine).get_columns("conversations")}
    assert set(ADDED_COLUMNS["conversations"]) <= names
    assert names == set(Base.metadata.tables["conversations"].c.keys())
    with engine.begin() as conn:
        row = conn.execute(sa.text(
            "SELECT status, address, address_requested_at, address_missing FROM conversations"
        )).one()
    assert tuple(row) == ("booked", None, None, 0)
    ddl = [s for s in statements if s.lstrip().upper().startswith(("ALTER", "DROP", "CREATE"))]
    assert ddl and all(s.startswith("ALTER TABLE conversations ADD COLUMN ") for s in ddl)


def test_migration_is_safe_to_run_twice():
    engine = _old_schema_engine()
    init_db(engine)
    assert migrate(engine) == [] and init_db(engine) is not None


def test_every_model_column_is_either_original_or_listed_as_added():
    """Adding a column to a live table without listing it would break production."""
    original = {
        "id", "client_slug", "contact_id", "contact_phone", "contact_name", "status",
        "current_offer", "offered_slots", "offer_template", "awaiting_preference",
        "none_work_streak", "unclear_streak", "engine_message_count", "appointment_id",
        "booked_time", "handoff_reason", "created_at", "updated_at",
    }
    model = set(Base.metadata.tables["conversations"].c.keys())
    assert model == original | set(ADDED_COLUMNS["conversations"])


def test_postgres_gets_if_not_exists_and_a_default_for_the_flag():
    from sqlalchemy.dialects import postgresql

    executed: list[str] = []

    class FakePostgres:
        dialect = postgresql.dialect()

        def begin(self):
            class Conn:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def execute(self, statement):
                    executed.append(str(statement))

            return Conn()

    from booking_engine import db

    original_inspect = db.inspect
    db.inspect = lambda engine: type("I", (), {"get_columns": lambda self, t: [{"name": "id"}]})()
    try:
        migrate(FakePostgres())
    finally:
        db.inspect = original_inspect
    prefix = "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
    assert executed == [
        prefix + "address VARCHAR(200)",
        prefix + "given_name VARCHAR(60)",
        prefix + "address_requested_at TIMESTAMP WITH TIME ZONE",
        prefix + "address_missing BOOLEAN NOT NULL DEFAULT false",
    ]

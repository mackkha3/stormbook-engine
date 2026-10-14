"""The output safety check: nothing reaches a homeowner unless it is a clean template render."""

from __future__ import annotations

import pytest

from booking_engine.config_loader import ConfigError
from booking_engine.conversation import Decision
from booking_engine.gateway import RecordingGateway
from booking_engine.templates import (
    BANNED_PHRASES,
    RenderedMessage,
    TemplateError,
    Templates,
    UnsafeOutput,
)
from tests.conftest import SLOT_1, SLOT_2, Harness, U

TIMES = ["Mon 10/5 at 11:00 AM", "Tue 10/6 at 12:00 PM"]
BASE = {"offer": "I can do {slot_1} or {slot_2}.", "plain": "Thanks from {company_name}!"}


def _reason(templates: Templates, message, allowed=TIMES) -> str:
    with pytest.raises(UnsafeOutput) as info:
        templates.check_output(message, allowed)
    return info.value.reason


def test_clean_template_render_passes():
    templates = Templates(BASE)
    message = templates.render("offer", {"slot_1": TIMES[0], "slot_2": TIMES[1]})
    templates.check_output(message, TIMES)


def test_rejects_text_that_did_not_come_from_a_template_key():
    templates = Templates(BASE)
    assert _reason(templates, RenderedMessage("made_up_key", "Hello there")) == "not_from_template"
    assert _reason(templates, "a bare string the AI wrote") == "not_from_template"


def test_rejects_text_that_differs_from_what_the_template_renders():
    templates = Templates(BASE)
    tampered = RenderedMessage("plain", "Thanks from Roof Demo! Also your roof is totaled.",
                               {"company_name": "Roof Demo"})
    assert _reason(templates, tampered) == "not_from_template"


@pytest.mark.parametrize("amount", ["$500", "$ 1,200", "about 300 dollars", "50 bucks"])
def test_rejects_dollar_amounts(amount):
    templates = Templates({"plain": "Thanks from {company_name}!"})
    message = templates.render("plain", {"company_name": f"Roofs for {amount}"})
    assert _reason(templates, message) == "dollar_amount"


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_rejects_each_banned_phrase(phrase):
    templates = Templates({"plain": "Thanks from {company_name}!"})
    message = templates.render("plain", {"company_name": f"We {phrase.upper()} it"})
    assert _reason(templates, message) == f"banned_phrase:{phrase}"


def test_rejects_a_time_the_calendar_did_not_return():
    templates = Templates(BASE)
    message = templates.render("offer", {"slot_1": TIMES[0], "slot_2": "Wed 10/7 at 3:00 PM"})
    assert _reason(templates, message) == "time_not_from_calendar"
    assert _reason(templates, templates.render("offer", {"slot_1": TIMES[0], "slot_2": TIMES[1]}),
                   allowed=[]) == "time_not_from_calendar"


@pytest.mark.parametrize(
    "text",
    ["It costs $99.", "We guarantee it.", "Your insurance will pay.", "Come by at 3:00 PM.",
     "See you Tuesday!", "Hi {homeowner_name}"],
)
def test_templates_file_is_rejected_at_load_if_it_breaks_a_rule(text):
    with pytest.raises(TemplateError):
        Templates({"bad": text})


def test_shipped_templates_pass_and_render_strictly():
    templates = Templates.load()
    with pytest.raises(TemplateError):
        templates.render("offer_slots", {"slot_1": TIMES[0]})        # {slot_2} missing
    with pytest.raises(TemplateError):
        templates.render("no_such_template", {})


async def test_gateway_refuses_anything_that_is_not_a_rendered_template():
    with pytest.raises(TypeError):
        await RecordingGateway().send_sms("contact-1", "free text")


def test_client_config_that_would_trip_the_filter_is_refused_at_startup():
    with pytest.raises(ConfigError, match="company_name"):
        Harness(company_name="Lifetime Guarantee Roofing")


async def test_engine_hands_off_instead_of_sending_an_unverified_time(h: Harness, monkeypatch):
    async def rogue_decision(turn):
        # Slots that the calendar never returned this turn (`verified` is empty).
        return Decision(template_key="offer_slots", slots=[SLOT_1, SLOT_2])

    monkeypatch.setattr("booking_engine.engine.decide", rogue_decision)
    result = await h.say("yes", U("affirm"))
    assert result.template_key == "handoff"
    assert result.handoff_reason == "output_rejected:time_not_from_calendar"
    assert h.sent == ["Thanks. Someone from Roof Demo will be in touch with you shortly."]
    assert h.conv.status == "handoff"


async def test_no_message_in_a_long_conversation_ever_fails_the_check(h: Harness):
    script = [
        U("basic_question"), U("deferrable_question"), U("is_bot"), U("none_work"),
        U("propose_time", preferred_time_window="after 15:00"), U("pick_slot", slot_choice=1),
        U("reschedule"), U("pick_slot", slot_choice=2),
    ]
    for understanding in script:
        result = await h.say("...", understanding)
        assert result.handoff_reason is None and result.reply
    assert len(h.calendar.appointments) == 1

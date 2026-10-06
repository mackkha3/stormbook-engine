"""Picture-only texts: thanked from a template, noted for the roofer, never sent to the model."""

from __future__ import annotations

from booking_engine.engine import PHOTO_NOTE, InboundMessage
from booking_engine.slots import format_slot
from tests.conftest import SLOT_1, SLOT_2, TZ, Harness, U


async def send_photo(h: Harness, body: str = ""):
    message = InboundMessage(message_id=f"photo-{len(h.gateway.calls)}-{body}", body=body, photo=True)
    return await h.engine.handle_turn(h.contact, [message])


async def test_a_photo_with_no_words_gets_the_photo_template_with_two_real_slots(h: Harness):
    result = await send_photo(h)
    assert result.template_key == "photo_received" and result.intent == "photo"
    assert h.sent == [(
        "Thanks for the photo. Your inspector will take a look on-site. "
        f"Does {format_slot(SLOT_1, TZ)} or {format_slot(SLOT_2, TZ)} work?"
    )]
    assert h.understander.contexts == []                  # the model was never asked
    assert h.gateway.notes() == [PHOTO_NOTE]
    assert h.conv.status == "slots_offered"
    assert h.store.turns(h.conv.id)[-1].inbound_text == "[photo]"


async def test_the_slots_offered_with_a_photo_reply_can_be_booked(h: Harness):
    await send_photo(h)
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.booking["action"] == "booked" and h.conv.status == "booked"
    # The model saw the photo turn in the history.
    assert {"role": "homeowner", "text": "[photo]"} in h.understander.contexts[0].history


async def test_a_photo_keeps_the_times_already_on_the_table(h: Harness):
    await h.reach("offered")
    before = list(h.conv.current_offer)
    await send_photo(h)
    assert h.conv.current_offer == before


async def test_a_photo_after_booking_is_thanked_without_offering_slots(h: Harness):
    await h.reach("booked")
    appointment = h.conv.appointment_id
    result = await send_photo(h)
    assert result.template_key == "photo_received_booked"
    assert h.sent == ["Thanks for the photo. Your inspector will take a look on-site."]
    assert h.gateway.notes() == [PHOTO_NOTE]
    assert h.conv.status == "booked" and h.conv.appointment_id == appointment
    assert h.conv.current_offer == []


async def test_a_photo_with_words_is_understood_normally_and_still_noted(h: Harness):
    h.understander.queue.append(U("deferrable_question"))
    result = await send_photo(h, "how bad is this")
    assert result.template_key == "deflect" and len(h.understander.contexts) == 1
    assert h.gateway.notes() == [PHOTO_NOTE]


async def test_a_photo_after_a_handoff_gets_no_reply(h: Harness):
    await h.say("can someone call me", U("human_request"))
    h.gateway.calls.clear()
    result = await send_photo(h)
    assert result.ignored and h.gateway.calls == []


async def test_a_photo_when_the_calendar_is_full_hands_off(h: Harness):
    for slot in await h.calendar.free_slots(h.now, h.now.replace(year=2027)):
        h.calendar.take(slot)
    result = await send_photo(h)
    assert result.template_key == "no_slots_available" and h.conv.status == "handoff"
    assert PHOTO_NOTE in h.gateway.notes()


async def test_a_photo_reply_counts_toward_the_message_cap(h: Harness):
    for _ in range(8):
        await send_photo(h)
    result = await send_photo(h)
    assert result.handoff_reason == "max_messages_without_booking"

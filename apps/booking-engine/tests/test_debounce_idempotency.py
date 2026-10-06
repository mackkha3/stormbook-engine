"""Debounce (a burst of texts is one turn) and idempotency (webhook retries are dropped)."""

from __future__ import annotations

import asyncio

from booking_engine.debounce import Debouncer
from booking_engine.engine import Contact
from tests.conftest import Harness, U


async def test_debouncer_groups_a_burst_and_restarts_the_timer_on_each_message():
    batches: list[list[str]] = []

    async def handler(key, items):
        batches.append(items)

    debouncer = Debouncer(0.08, handler)
    debouncer.submit("a", "yes")
    await asyncio.sleep(0.05)
    debouncer.submit("a", "tuesday works")           # arrives inside the window: timer restarts
    await asyncio.sleep(0.05)
    assert batches == []                             # 0.10s since the first text, still waiting
    await debouncer.drain()
    assert batches == [["yes", "tuesday works"]]


async def test_debouncer_never_runs_two_turns_for_one_contact_at_once():
    running = 0
    overlaps = 0
    batches: list[list[str]] = []

    async def handler(key, items):
        nonlocal running, overlaps
        running += 1
        overlaps += running > 1
        await asyncio.sleep(0.06)
        batches.append(items)
        running -= 1

    debouncer = Debouncer(0.01, handler)
    debouncer.submit("a", "one")
    await asyncio.sleep(0.03)                        # first turn is now in flight
    debouncer.submit("a", "two")
    await debouncer.drain()
    assert batches == [["one"], ["two"]] and overlaps == 0


async def test_burst_of_texts_gets_exactly_one_reply():
    h = Harness(debounce_seconds=0.05)
    await h.reach("offered")
    h.understander.queue.append(U("pick_slot", slot_choice=2))
    for body in ("yes", "tuesday works", "thx"):
        assert h.engine.receive(h.contact, h.message(body))
        await asyncio.sleep(0.01)
    await h.engine.debouncer.drain()

    assert len(h.results) == 1 and len(h.gateway.sent) == 1
    assert h.results[0].inbound_text == "yes\ntuesday works\nthx"
    assert h.understander.contexts[-1].message == "yes\ntuesday works\nthx"
    assert h.results[0].template_key == "booked" and len(h.calendar.appointments) == 1


async def test_duplicate_message_id_is_dropped():
    h = Harness(debounce_seconds=0.02)
    assert h.engine.receive(h.contact, h.message("yes", message_id="ghl-123"))
    assert not h.engine.receive(h.contact, h.message("yes", message_id="ghl-123"))
    await h.engine.debouncer.drain()
    assert len(h.results) == 1 and len(h.gateway.sent) == 1
    assert h.results[0].inbound_text == "yes"


async def test_webhook_retry_after_the_reply_does_not_send_a_second_reply():
    h = Harness(debounce_seconds=0.01)
    h.engine.receive(h.contact, h.message("yes", message_id="ghl-123"))
    await h.engine.debouncer.drain()
    assert len(h.gateway.sent) == 1

    assert not h.engine.receive(h.contact, h.message("yes", message_id="ghl-123"))
    await h.engine.debouncer.drain()
    homeowner_turns = [t for t in h.store.turns(h.conv.id) if t.inbound_text]
    assert len(h.gateway.sent) == 1 and len(homeowner_turns) == 1
    assert homeowner_turns[0].inbound_message_ids == ["ghl-123"]


async def test_dedupe_survives_a_restart():
    h = Harness(debounce_seconds=0.01)
    h.engine.receive(h.contact, h.message("yes", message_id="ghl-123"))
    await h.engine.debouncer.drain()
    # A second engine on the same database (a redeploy) still knows the id.
    assert not h.store.register_inbound(h.client.client_slug, "ghl-123")
    assert h.store.register_inbound("another-client", "ghl-123")


async def test_text_sent_while_a_turn_is_processing_becomes_the_next_turn():
    h = Harness(debounce_seconds=0.01)
    h.understander.delay = 0.08                      # a slow model call
    h.understander.queue += [U("affirm"), U("pick_slot", slot_choice=1)]
    h.engine.receive(h.contact, h.message("yes"))
    await asyncio.sleep(0.04)                        # first turn is mid-flight
    h.engine.receive(h.contact, h.message("1"))
    await h.engine.debouncer.drain()
    assert [r.template_key for r in h.results] == ["offer_slots", "booked"]
    assert len(h.gateway.sent) == 2                  # one reply per turn, in order


async def test_different_contacts_are_debounced_independently():
    h = Harness(debounce_seconds=0.02)
    other = Contact(contact_id="contact-2", phone="+19035550111")
    h.engine.receive(h.contact, h.message("yes"))
    h.engine.receive(other, h.message("yes"))
    await h.engine.debouncer.drain()
    assert len(h.results) == 2
    assert sorted(c["contact_id"] for c in h.gateway.calls if c["call"] == "send_sms") == [
        "contact-1", "contact-2"]

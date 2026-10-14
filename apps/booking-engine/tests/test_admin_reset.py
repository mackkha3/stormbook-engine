"""POST /admin/reset-contact: forget one contact's conversation, never an opt-out."""

from __future__ import annotations

from dataclasses import replace

import pytest

from booking_engine import main
from booking_engine.store import OptOutProtected
from tests.conftest import Harness, U
from tests.ghl_fake import LOCATION
from tests.test_webhook import SECRET, Site

ADMIN = "admin-secret"


@pytest.fixture
def site() -> Site:
    site = Site()
    main.app.state.settings = replace(main.app.state.settings, admin_secret=ADMIN)
    return site


async def reset(site: Site, secret: str | None = ADMIN, **params):
    params = {"location_id": LOCATION, "contact_id": "c1", **params}
    if secret is not None:
        params["secret"] = secret
    return await site.http.post("/admin/reset-contact", params=params)


async def book(site: Site) -> None:
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))
    assert site.conv.status == "booked"


@pytest.mark.parametrize("secret", [None, "wrong", SECRET])
async def test_reset_needs_the_admin_secret_not_the_webhook_secret(site: Site, secret):
    await book(site)
    response = await reset(site, secret)
    assert response.status_code == 401
    assert site.conv.status == "booked" and len(site.store.turns(site.conv.id)) == 3


async def test_reset_is_off_while_no_admin_secret_is_configured(site: Site):
    main.app.state.settings = replace(main.app.state.settings, admin_secret="")
    await book(site)
    assert (await reset(site, "")).status_code == 401
    assert (await reset(site, None)).status_code == 401
    assert site.conv is not None


async def test_reset_deletes_the_conversation_and_its_turns(site: Site):
    await book(site)
    conversation_id = site.conv.id
    ghl_calls = len(site.ghl.requests)

    response = await reset(site)
    assert response.status_code == 200
    assert response.json() == {
        "status": "reset", "previous_status": "booked", "appointment_id": "appt-1",
        "booked_time": "2026-10-05T16:00:00+00:00", "turns_deleted": 3,
    }
    assert site.conv is None and site.store.turns(conversation_id) == []
    assert len(site.ghl.requests) == ghl_calls            # nothing in GHL was touched

    assert (await reset(site)).json() == {"status": "nothing_to_reset"}


async def test_after_a_reset_the_next_text_starts_a_fresh_conversation(site: Site):
    await book(site)
    await reset(site)
    await site.text("Yes", U("affirm"))
    assert site.conv.status == "slots_offered" and site.conv.appointment_id is None
    assert [t.template_key for t in site.store.turns(site.conv.id)] == [
        "opening_from_ghl", "offer_slots"
    ]


async def test_reset_takes_the_ids_from_a_json_body_or_a_header_secret(site: Site):
    await book(site)
    response = await site.http.post(
        "/admin/reset-contact", headers={"X-Admin-Secret": ADMIN},
        json={"location_id": LOCATION, "contact_id": "c1"},
    )
    assert response.json()["status"] == "reset" and site.conv is None


async def test_reset_never_deletes_an_opted_out_contact(site: Site):
    await site.text("yes", U("affirm"))
    await site.text("STOP")
    assert site.conv.status == "opted_out"
    turns = len(site.store.turns(site.conv.id))

    response = await reset(site)
    assert response.status_code == 409
    assert response.json() == {"status": "refused", "reason": "opted_out"}
    assert site.conv.status == "opted_out" and len(site.store.turns(site.conv.id)) == turns
    # Still opted out: a later text gets no reply.
    sent = len(site.ghl.sent)
    await site.text("yes", U("affirm"))
    assert len(site.ghl.sent) == sent and site.conv.status == "opted_out"


async def test_reset_only_touches_the_named_contact_and_keeps_seen_message_ids(h: Harness):
    await h.say("yes", U("affirm"))
    other = h.store.get_or_create(h.client.client_slug, "contact-2", "+19035550111", None)[0]
    h.store.add_turn(other.id, inbound_text="hi", inbound_message_ids=["x-1"])
    assert h.store.register_inbound(h.client.client_slug, "msg-1")

    removed = h.store.reset_contact(h.client.client_slug, h.contact.contact_id)
    assert removed["turns_deleted"] == 2 and h.conv is None
    assert h.store.get_conversation(h.client.client_slug, "contact-2") is not None
    assert len(h.store.turns(other.id)) == 1
    assert h.store.reset_contact("another-client", "contact-2") is None
    # A webhook retry of a text from before the reset is still a duplicate.
    assert not h.store.register_inbound(h.client.client_slug, "msg-1")


async def test_store_refuses_to_delete_an_opt_out_whoever_asks(h: Harness):
    await h.say("STOP")
    with pytest.raises(OptOutProtected):
        h.store.reset_contact(h.client.client_slug, h.contact.contact_id)
    assert h.conv.status == "opted_out"


@pytest.mark.parametrize(
    "params, status",
    [({"location_id": "nope"}, 404), ({"contact_id": ""}, 422), ({"location_id": ""}, 422)],
)
async def test_reset_rejects_an_unknown_location_or_missing_ids(site: Site, params, status):
    await book(site)
    assert (await reset(site, **params)).status_code == status
    assert site.conv.status == "booked"

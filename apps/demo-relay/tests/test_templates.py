from __future__ import annotations

import pytest

from demo_relay import templates
from demo_relay.templates import MissingTemplateField

CONTEXT = {
    "contractor_first_name": "Mike",
    "contractor_company": "Henderson Roofing",
    "homeowner_first_name": "Jane",
    "homeowner_address": "123 Elm St",
    "damage_type": "Hail",
    "slot_a_day": "Tuesday",
    "slot_a_time": "10:00 AM",
    "slot_b_day": "Thursday",
    "slot_b_time": "2:00 PM",
}


def test_message_1_renders_full_body():
    body = templates.render_message_1(CONTEXT)
    assert body.startswith("Hi Jane, this is Mike with Henderson Roofing.")
    assert "123 Elm St" in body
    assert "Tuesday at 10:00 AM" in body
    assert "Thursday at 2:00 PM" in body
    assert body.endswith("Which day works better?")


def test_message_1_lowercases_damage_type():
    body = templates.render_message_1(CONTEXT)
    assert "about the hail damage" in body
    assert "Hail damage" not in body


def test_message_1_handles_multi_word_damage_type():
    body = templates.render_message_1({**CONTEXT, "damage_type": "Roof leak"})
    assert "about the roof leak damage" in body


def test_message_2_renders():
    body = templates.render_message_2(CONTEXT)
    assert body.startswith("Jane — Mike here again.")
    assert "Tuesday at 10:00 AM" in body
    assert "Thursday at 2:00 PM" in body


def test_message_3a_uses_chosen_slot():
    body = templates.render_message_3a(CONTEXT, chosen_day="Thursday", chosen_time="2:00 PM")
    assert "booked for Thursday at 2:00 PM, 123 Elm St" in body
    assert body.endswith("— Mike, Henderson Roofing")
    # The unchosen slot must not leak into the confirmation.
    assert "Tuesday" not in body


def test_message_3d_closes_out():
    body = templates.render_message_3d(CONTEXT)
    assert "sorry for the confusion" in body
    assert body.endswith("— Mike, Henderson Roofing")


def test_special_characters_pass_through_unescaped():
    ctx = {
        **CONTEXT,
        "contractor_company": "O'Brien & Sons Roofing",
        "homeowner_first_name": "Renée",
        "homeowner_address": "12 O'Hara St #4",
    }
    body = templates.render_message_1(ctx)
    assert "O'Brien & Sons Roofing" in body
    assert "Renée" in body
    assert "12 O'Hara St #4" in body
    assert "&amp;" not in body


def test_missing_field_raises_with_clear_message():
    ctx = {k: v for k, v in CONTEXT.items() if k != "slot_b_time"}
    with pytest.raises(MissingTemplateField) as exc:
        templates.render_message_1(ctx)
    assert exc.value.field == "slot_b_time"
    assert "slot_b_time" in str(exc.value)
    assert "message_1" in str(exc.value)


def test_empty_field_is_treated_as_missing():
    # An empty string on a phone reads as a bug; fail loudly instead.
    with pytest.raises(MissingTemplateField):
        templates.render_message_3d({**CONTEXT, "contractor_company": ""})


def test_extra_context_keys_are_ignored():
    body = templates.render_message_3d({**CONTEXT, "state": "replied", "conversation_id": "abc"})
    assert "replied" not in body

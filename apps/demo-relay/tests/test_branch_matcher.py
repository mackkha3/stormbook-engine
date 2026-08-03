from __future__ import annotations

import pytest

from demo_relay.lib.branch_matcher import classify, is_ambiguous

SLOTS = {"slot_a_day": "Tuesday", "slot_b_day": "Thursday"}


# --- Branch 3: wrong number -------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "wrong number",
        "Sorry, wrong number buddy",
        "who is this?",
        "this is not me",
        "That's not mine",
        "I didn't request an inspection",
        "I did not request this",
    ],
)
def test_wrong_number_positive(body):
    assert classify(body, **SLOTS).branch == "WRONG_NUMBER"


@pytest.mark.parametrize(
    "body",
    [
        "Tuesday works great",
        "Sounds good, see you then",
    ],
)
def test_wrong_number_negative(body):
    assert classify(body, **SLOTS).branch != "WRONG_NUMBER"


def test_wrong_number_beats_a_day_mention():
    # "wrong number, not Tuesday" is a close-out, not a booking.
    match = classify("wrong number, I'm not free Tuesday either", **SLOTS)
    assert match.branch == "WRONG_NUMBER"
    assert match.matched_keyword == "wrong number"


# --- Branch 1: slot A -------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "keyword"),
    [
        ("Tuesday", "tuesday"),
        ("tues works", "tues"),
        ("Tue is better for me", "tue"),
        ("Let's do TUESDAY at 10", "tuesday"),
    ],
)
def test_slot_a_positive(body, keyword):
    match = classify(body, **SLOTS)
    assert match.branch == "YES_SLOT_A"
    assert match.matched_keyword == keyword


@pytest.mark.parametrize("body", ["Thursday", "no thanks"])
def test_slot_a_negative(body):
    assert classify(body, **SLOTS).branch != "YES_SLOT_A"


# --- Branch 1: slot B -------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "keyword"),
    [
        ("Thursday please", "thursday"),
        ("thurs", "thurs"),
        ("thu works", "thu"),
        ("thur is fine", "thur"),
    ],
)
def test_slot_b_positive(body, keyword):
    match = classify(body, **SLOTS)
    assert match.branch == "YES_SLOT_B"
    assert match.matched_keyword == keyword


@pytest.mark.parametrize("body", ["Tuesday", "call me instead"])
def test_slot_b_negative(body):
    assert classify(body, **SLOTS).branch != "YES_SLOT_B"


# --- UNMATCHED --------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "Can you do Saturday morning instead?",
        "How much does it cost?",
        "call me",
        "",
        "k",
    ],
)
def test_unmatched(body):
    match = classify(body, **SLOTS)
    assert match.branch == "UNMATCHED"
    assert match.matched_keyword is None


@pytest.mark.parametrize("body", ["either works", "Tuesday or Thursday both fine"])
def test_ambiguous_replies_are_unmatched(body):
    assert classify(body, **SLOTS).branch == "UNMATCHED"


def test_is_ambiguous_flags_both_days():
    assert is_ambiguous("Tuesday or Thursday", **SLOTS) is True
    assert is_ambiguous("Tuesday", **SLOTS) is False


@pytest.mark.parametrize(
    ("body", "slots"),
    [
        # Substring matching would fire on these; word boundaries must not.
        ("I have no money for this", {"slot_a_day": "Monday", "slot_b_day": "Friday"}),
        ("it's a sunny day", {"slot_a_day": "Sunday", "slot_b_day": "Monday"}),
        ("we have a wedding that week", {"slot_a_day": "Wednesday", "slot_b_day": "Friday"}),
    ],
)
def test_day_aliases_respect_word_boundaries(body, slots):
    assert classify(body, **slots).branch == "UNMATCHED"


def test_other_day_pairs_still_match():
    slots = {"slot_a_day": "Monday", "slot_b_day": "Saturday"}
    assert classify("mon works", **slots).branch == "YES_SLOT_A"
    assert classify("sat is better", **slots).branch == "YES_SLOT_B"

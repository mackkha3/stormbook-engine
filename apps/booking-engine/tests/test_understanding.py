"""Message understanding: fixture coverage, JSON validation, retry-once, and a live eval."""

from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from booking_engine.understanding import (
    INTENTS,
    MODEL,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    AnthropicUnderstander,
    TurnContext,
    UnderstandingError,
    build_user_content,
    keyword_opt_out,
    parse_understanding,
)
from tests.conftest import FIXTURES, NOW, SLOT_1, SLOT_2, Harness, U

VALID = {
    "intent": "pick_slot", "confidence": 0.93, "slot_choice": 2, "preferred_date": None,
    "preferred_time_window": None, "question_topic": None, "acknowledgment_only": False,
    "language": "en", "notes": "picked the second slot",
}
MESSY = [
    "ya", "tmrw aft 3??", "how much is this gonna cost me", "who dis", "STOP",
    "is this a robot", "can u come sat morning", "neither", "nvm",
]


def _response(payload, stop_reason="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


class FakeClient:
    """Stands in for AsyncAnthropic: returns/raises queued outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ctx(**overrides) -> TurnContext:
    return TurnContext(**{"message": "2", "now": NOW, "status": "slots_offered", **overrides})


def test_fixture_file_covers_every_intent_with_at_least_60_messages():
    assert len(FIXTURES) >= 60
    assert {f["intent"] for f in FIXTURES} == set(INTENTS)
    texts = {f["text"] for f in FIXTURES}
    assert not [m for m in MESSY if m not in texts]
    assert "my insurance guy said" in " ".join(texts)


def test_valid_json_is_parsed():
    u = parse_understanding(json.dumps({**VALID, "preferred_date": "2026-10-10"}))
    assert (u.intent, u.slot_choice, u.preferred_date) == ("pick_slot", 2, dt.date(2026, 10, 10))


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({**VALID, "intent": "book_it_now"}),
        json.dumps({**VALID, "confidence": 1.7}),
        json.dumps({**VALID, "preferred_time_window": "whenever"}),
        json.dumps({**VALID, "preferred_time_window": "after 27:00"}),
        json.dumps({**VALID, "preferred_date": "next tuesday"}),
        json.dumps({**VALID, "question_topic": "pricing"}),
        json.dumps({**VALID, "acknowledgment_only": "sort of"}),
        json.dumps({**VALID, "reply_to_homeowner": "Sure, it costs $200!"}),
        json.dumps({k: v for k, v in VALID.items() if k != "confidence"}),
    ],
)
def test_off_spec_json_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_understanding(bad)


@pytest.mark.parametrize("window", ["morning", "afternoon", "evening", "after 15:00", "before 9:30"])
def test_known_time_windows_are_accepted(window):
    assert parse_understanding(json.dumps({**VALID, "preferred_time_window": window}))


async def test_request_uses_haiku_temperature_zero_and_a_strict_schema():
    client = FakeClient(_response(VALID))
    await AnthropicUnderstander("k", client=client).understand(_ctx())
    call = client.calls[0]
    assert call["model"] == MODEL == "claude-haiku-4-5-20251001"
    assert call["temperature"] == 0
    schema = call["output_config"]["format"]
    assert schema["type"] == "json_schema" and schema["schema"]["additionalProperties"] is False


async def test_invalid_json_is_retried_once_then_succeeds():
    client = FakeClient(_response("{oops"), _response(VALID))
    u = await AnthropicUnderstander("k", client=client).understand(_ctx())
    assert u.intent == "pick_slot" and len(client.calls) == 2


async def test_invalid_json_twice_raises_and_never_guesses():
    client = FakeClient(_response("{oops"), _response({**VALID, "intent": "maybe"}), _response(VALID))
    with pytest.raises(UnderstandingError):
        await AnthropicUnderstander("k", client=client).understand(_ctx())
    assert len(client.calls) == 2      # one retry, no more


async def test_api_failure_is_retried_once_then_raises():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.test"))
    client = FakeClient(error, error)
    with pytest.raises(UnderstandingError):
        await AnthropicUnderstander("k", client=client).understand(_ctx())
    assert len(client.calls) == 2


async def test_truncated_or_refused_response_counts_as_a_failure():
    client = FakeClient(_response(VALID, stop_reason="max_tokens"), _response(VALID, "refusal"))
    with pytest.raises(UnderstandingError):
        await AnthropicUnderstander("k", client=client).understand(_ctx())


def test_prompt_shows_state_and_only_the_last_six_messages():
    history = [{"role": "homeowner", "text": f"message {i}"} for i in range(1, 10)]
    content = build_user_content(_ctx(offered=[SLOT_1, SLOT_2], history=history))
    assert "1. Mon 10/5 at 11:00 AM" in content and "2. Tue 10/6 at 12:00 PM" in content
    assert "Monday 2026-10-05 09:00" in content
    assert "message 3\n" not in content and "message 4" in content and "message 9" in content


async def test_engine_sends_the_model_at_most_six_prior_messages(h: Harness):
    for _ in range(5):
        await h.say("how much", U("deferrable_question"))
    assert len(h.understander.contexts[-1].history) == 6


@pytest.mark.parametrize(
    "text",
    ["STOP", "stop", "Stop.", "unsubscribe", "END", "quit", "remove me from your list",
     "please stop texting me", "yes tuesday works but stop texting me after this",
     "opt out", "dont text me again", "actually STOP", "ok stop", "please stop", "Stop!!"],
)
def test_opt_out_keywords_are_caught_in_code(text):
    assert keyword_opt_out([text])


@pytest.mark.parametrize(
    "keyword", ["STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"]
)
def test_each_carrier_keyword_alone_is_an_opt_out(keyword):
    for variant in (keyword, keyword.lower(), keyword.title(), f"  {keyword.lower()} \n"):
        assert keyword_opt_out([variant])
    assert keyword_opt_out(["yes that works", keyword.lower()])    # its own text in a burst


@pytest.mark.parametrize(
    "text",
    ["can you stop by tomorrow?", "could someone stop by saturday", "water won't stop coming in",
     "the leak stopped but there's damage",
     # ...and the same sort of thing shouted, shortened, or misspelled
     "CAN YOU STOP BY TOMORROW?", "WATER WONT STOP COMING IN", "it wont stop", "leak wont stop",
     "rain finally stopped", "it won't STOP leaking", "cant stop it", "stop by anytime",
     "how do i stop the leak until you get here", "take me off tuesday and put me on wednesday",
     "can you stop by tuesday", "yes", "the bus stop is near my house", "nvm",
     "can we cancel tuesday", "i need to cancel my appointment", "cancel it", "please cancel",
     "the end of the week works", "quit calling my husband he is at work, text me"],
)
def test_ordinary_messages_are_not_opt_outs(text):
    assert not keyword_opt_out([text])


def test_no_fixture_trips_the_opt_out_rule_unless_it_is_an_opt_out():
    for fixture in FIXTURES:
        caught = keyword_opt_out([fixture["text"]])
        if fixture["intent"] != "opt_out":
            assert not caught, fixture["text"]
        if fixture.get("keyword"):
            assert caught, fixture["text"]


def test_schema_and_prompt_carry_acknowledgment_only():
    assert OUTPUT_SCHEMA["properties"]["acknowledgment_only"] == {"type": "boolean"}
    assert "acknowledgment_only" in OUTPUT_SCHEMA["required"]
    assert "acknowledgment_only" in SYSTEM_PROMPT
    assert parse_understanding(json.dumps({**VALID, "acknowledgment_only": True})).acknowledgment_only


def test_schema_and_prompt_carry_question_topic():
    topic = OUTPUT_SCHEMA["properties"]["question_topic"]
    assert topic["anyOf"][0]["enum"] == ["who", "area", "other"]
    assert "question_topic" in OUTPUT_SCHEMA["required"] and "question_topic" in SYSTEM_PROMPT
    u = parse_understanding(json.dumps({**VALID, "intent": "basic_question", "question_topic": "area"}))
    assert u.question_topic == "area"


# --- live eval: real model, real prompt (pytest -m live) -----------------------


@pytest.mark.live
@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["text"][:40] for f in FIXTURES])
async def test_live_model_classifies_fixture(fixture):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    if fixture.get("keyword"):
        pytest.skip("caught by the code opt-out rule; the model is never asked")
    h = Harness()
    await h.reach(fixture["state"])
    conv, tz = h.conv, h.client.tz
    ctx = TurnContext(
        message=fixture["text"], now=NOW, status=conv.status if conv else "active",
        offered=[dt.datetime.fromisoformat(k).astimezone(tz) for k in (conv.current_offer if conv else [])],
        booked_time=(
            dt.datetime.fromisoformat(conv.booked_time).astimezone(tz)
            if conv and conv.booked_time else None
        ),
        awaiting_preference=bool(conv and conv.awaiting_preference),
        history=h.store.recent_messages(conv.id) if conv else [],
    )
    u = await AnthropicUnderstander(api_key).understand(ctx)
    if fixture.get("live_not_opt_out"):
        assert u.intent != "opt_out", u.notes
        return
    assert u.intent == fixture["intent"], u.notes
    assert u.language == "en"
    if u.intent != "opt_out":
        assert u.confidence >= 0.7
    for key in ("slot_choice", "preferred_time_window", "question_topic", "acknowledgment_only"):
        if key in fixture:
            assert getattr(u, key) == fixture[key]
    if u.intent != "basic_question":
        assert u.question_topic is None
    if "preferred_date" in fixture:
        assert u.preferred_date == dt.date.fromisoformat(fixture["preferred_date"])

"""Message understanding: fixture coverage, JSON validation, retry-once, and a live eval."""

from __future__ import annotations

import datetime as dt
import inspect
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.types import JSONOutputFormatParam

from booking_engine.settings import APP_ROOT, Settings
from booking_engine.understanding import (
    INTENTS,
    MIN_CONFIDENCE,
    MODEL,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    AnthropicUnderstander,
    TurnContext,
    UnderstandingError,
    apply_confidence_rule,
    apply_preference_rule,
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
    assert call["extra_body"] == {"temperature": 0}
    schema = call["output_config"]["format"]
    assert schema["type"] == "json_schema" and schema["schema"]["additionalProperties"] is False


async def test_request_matches_the_installed_sdk_signature():
    """Every keyword we pass must exist on the real AsyncAnthropic.messages.create.

    The fake client accepts anything, so without this a parameter the SDK has
    dropped (as 1.x did with `temperature`) only fails on a live call.
    """
    client = FakeClient(_response(VALID))
    await AnthropicUnderstander("k", client=client).understand(_ctx())
    real = anthropic.AsyncAnthropic(api_key="not-used").messages.create
    signature = inspect.signature(real)
    signature.bind(**client.calls[0])                     # TypeError on an unknown keyword
    assert not {"temperature", "top_p", "top_k"} & set(client.calls[0])
    # The structured-output parameter is typed as output_config.format = {type, schema}.
    assert "output_config" in signature.parameters
    assert set(JSONOutputFormatParam.__annotations__) == {"type", "schema"}
    assert set(client.calls[0]["output_config"]["format"]) == {"type", "schema"}


async def test_real_sdk_client_builds_the_request_without_a_type_error():
    """Run our call through the real SDK up to the network, which is stubbed out."""
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": json.dumps(VALID)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    real = anthropic.AsyncAnthropic(
        api_key="not-used", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    u = await AnthropicUnderstander("k", client=real).understand(_ctx())
    await real.close()
    assert u.intent == "pick_slot"
    (body,) = sent
    assert body["model"] == MODEL and body["temperature"] == 0
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False


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
    api_key = Settings.from_env().anthropic_api_key      # reads apps/booking-engine/.env
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set (put it in apps/booking-engine/.env)")
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
    # Opened and closed inside this test's own event loop.
    async with AnthropicUnderstander(api_key) as understander:
        raw = await understander.understand(ctx)
    # What the engine acts on: low confidence → unclear; a named day/time → propose_time.
    u = apply_preference_rule(apply_confidence_rule(raw))
    if fixture.get("live_not_opt_out"):
        assert u.intent != "opt_out", u.notes
        return
    assert u.intent == fixture["intent"], f"model said {raw.intent} ({raw.confidence}): {raw.notes}"
    assert u.language == "en"
    if fixture["intent"] == "unclear":
        return          # nothing else to extract, and low confidence is a fine way to get here
    if u.intent != "opt_out":
        assert u.confidence >= MIN_CONFIDENCE
    for key in ("slot_choice", "preferred_time_window", "question_topic", "acknowledgment_only"):
        if key in fixture:
            assert getattr(u, key) == fixture[key]
    if u.intent != "basic_question":
        assert u.question_topic is None
    if "preferred_date" in fixture:
        assert u.preferred_date == dt.date.fromisoformat(fixture["preferred_date"])


def test_settings_load_the_api_key_from_the_app_env_file(tmp_path, monkeypatch):
    """Live tests and the simulator get ANTHROPIC_API_KEY from .env with no manual export."""
    assert APP_ROOT.name == "booking-engine" and (APP_ROOT / ".env.example").is_file()
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr("booking_engine.settings.APP_ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings.from_env().anthropic_api_key == "sk-from-dotenv"


# --- confidence rule (shared by the engine and the live eval) ------------------


def test_confidence_rule_routes_low_confidence_to_unclear_except_opt_out():
    low = parse_understanding(json.dumps({**VALID, "confidence": 0.69}))
    assert apply_confidence_rule(low).intent == "unclear"
    assert apply_confidence_rule(low).slot_choice == 2            # details are kept
    edge = parse_understanding(json.dumps({**VALID, "confidence": 0.7}))
    assert apply_confidence_rule(edge).intent == "pick_slot"
    opt_out = parse_understanding(json.dumps({**VALID, "intent": "opt_out", "confidence": 0.2}))
    assert apply_confidence_rule(opt_out).intent == "opt_out"
    already = parse_understanding(json.dumps({**VALID, "intent": "unclear", "confidence": 0.3}))
    assert apply_confidence_rule(already).intent == "unclear"


# --- prompt wording the live run depends on -------------------------------------


def test_prompt_defines_each_question_topic():
    for line in (
        '"who" = who is texting, what company this is, why are you texting me.',
        '"area" = the service area or location',
        '"other" = any other factual question about the business',
        '"are you guys licensed" is "other"',
    ):
        assert line in SYSTEM_PROMPT


def test_prompt_says_a_different_time_after_booking_is_reschedule():
    assert "ANY request for a different day or time is reschedule" in SYSTEM_PROMPT
    assert "can we do wednesday instead" in SYSTEM_PROMPT


# --- one client, closed properly ---------------------------------------------------


async def test_understander_reuses_one_client_and_closes_it_in_the_running_loop():
    understander = AnthropicUnderstander("not-used")
    client = understander._client
    assert isinstance(client, anthropic.AsyncAnthropic) and not client.is_closed()
    assert understander._client is client                 # same client for every turn
    await understander.aclose()
    assert client.is_closed()
    await understander.aclose()                           # closing twice is harmless


async def test_understander_as_context_manager_closes_on_exit_even_on_error():
    with pytest.raises(RuntimeError):
        async with AnthropicUnderstander("not-used") as understander:
            client = understander._client
            raise RuntimeError("turn blew up")
    assert client.is_closed()


async def test_understander_does_not_close_a_client_it_was_given():
    shared = anthropic.AsyncAnthropic(api_key="not-used")
    async with AnthropicUnderstander("k", client=shared):
        pass
    assert not shared.is_closed()
    await shared.close()


async def test_simulator_closes_its_client_before_the_loop_ends(monkeypatch):
    import argparse

    from booking_engine import simulate

    created: list[AnthropicUnderstander] = []

    class Tracking(AnthropicUnderstander):
        def __init__(self, api_key):
            super().__init__(api_key)
            created.append(self)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(simulate, "AnthropicUnderstander", Tracking)
    monkeypatch.setattr("builtins.input", lambda _prompt: "/quit")
    args = argparse.Namespace(client="roof-demo", debounce=0.0, verbose=False)
    assert await simulate.run(args) == 0
    assert len(created) == 1 and created[0]._client.is_closed()


# --- a named day or time is never ignored ------------------------------------------


@pytest.mark.parametrize("intent", ["none_work", "affirm"])
@pytest.mark.parametrize(
    "named", [{"preferred_date": "2026-10-11"}, {"preferred_time_window": "after 17:00"},
              {"preferred_date": "2026-10-11", "preferred_time_window": "after 17:00"}],
)
def test_none_work_or_affirm_with_a_named_day_or_time_becomes_propose_time(intent, named):
    u = parse_understanding(json.dumps({**VALID, "slot_choice": None, "intent": intent, **named}))
    routed = apply_preference_rule(u)
    assert routed.intent == "propose_time"
    assert (str(routed.preferred_date) if routed.preferred_date else None) == named.get("preferred_date")
    assert routed.preferred_time_window == named.get("preferred_time_window")


@pytest.mark.parametrize(
    "intent", ["none_work", "affirm", "pick_slot", "reschedule", "cancel", "unclear", "opt_out"]
)
def test_preference_rule_leaves_everything_else_alone(intent):
    plain = parse_understanding(json.dumps({**VALID, "slot_choice": None, "intent": intent}))
    assert apply_preference_rule(plain).intent == intent            # nothing named
    if intent not in ("none_work", "affirm"):
        dated = plain.model_copy(update={"preferred_date": dt.date(2026, 10, 11)})
        assert apply_preference_rule(dated).intent == intent


def test_prompt_covers_rejecting_while_naming_a_time_and_work_hours():
    assert (
        "Rejecting the offered times while naming another day or time is propose_time"
        in SYSTEM_PROMPT
    )
    assert "I work till 5, anything on sunday?" in SYSTEM_PROMPT
    assert '"I work till 5" or "I get off at 5" is "after 17:00"' in SYSTEM_PROMPT
    assert '"next week" or "later" is not a specific day' in SYSTEM_PROMPT

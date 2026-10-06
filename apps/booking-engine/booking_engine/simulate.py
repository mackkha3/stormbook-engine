"""Terminal simulator: text the engine like a homeowner. No GHL, no phone number.

    python -m booking_engine.simulate --client roof-demo

Uses the real Anthropic understanding, the real templates and the real
conversation logic, with a mock calendar and an in-memory CRM. Every turn is
also written to the database, like production.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime

from booking_engine.calendar.mock import MockCalendar
from booking_engine.config_loader import ClientConfig, ConfigError, load_client
from booking_engine.db import init_db, make_engine
from booking_engine.engine import Contact, Engine, InboundMessage, TurnResult
from booking_engine.gateway import RecordingGateway
from booking_engine.logger import configure as configure_logging
from booking_engine.settings import Settings
from booking_engine.slots import format_slot, from_key
from booking_engine.store import Store
from booking_engine.templates import TemplateError, Templates
from booking_engine.understanding import AnthropicUnderstander

HELP = """Commands:
  /state     show this conversation's saved state
  /slots     show the mock calendar's next open times
  /take N    someone else grabs offered slot N (tests the re-check before booking)
  /new       start over as a brand-new homeowner
  /quit      exit
Anything else is sent as a text from the homeowner."""


def _print_turn(result: TurnResult) -> None:
    u = result.understanding or {}
    if result.ignored:
        print(f"  [ignored: conversation is {result.status}; the engine stays silent]")
        return
    details = ", ".join(
        f"{k}={u[k]}" for k in ("slot_choice", "preferred_date", "preferred_time_window") if u.get(k)
    )
    print(
        f"  [intent: {result.intent or 'n/a'}"
        + (f" ({u['confidence']:.2f})" if u else "")
        + (f" | {details}" if details else "")
        + (f" | {u['notes']}" if u.get("notes") else "")
        + "]"
    )
    if result.reply:
        print(f"  [template: {result.template_key}]")
        print(f"\nEngine: {result.reply}\n")
    else:
        print("\n  (no reply sent)\n")
    if result.booking:
        print(f"  [calendar: {result.booking}]")
    if result.handoff_reason:
        print(f"  [HANDOFF to a human. Reason: {result.handoff_reason}]")
    for call in result.crm_calls:
        if call["call"] != "send_sms":
            print(f"  [CRM {call['call']}: { {k: v for k, v in call.items() if k != 'call'} }]")
    print(f"  [status: {result.status}]")


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    try:
        client = load_client(args.client)
        templates = Templates.load()
    except (ConfigError, TemplateError) as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 1
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        return 1

    # One Anthropic client for the whole session, closed before the event loop ends.
    async with AnthropicUnderstander(settings.anthropic_api_key) as understander:
        await _session(args, settings, client, templates, understander)
    return 0


async def _session(
    args: argparse.Namespace,
    settings: Settings,
    client: ClientConfig,
    templates: Templates,
    understander: AnthropicUnderstander,
) -> None:
    store = Store(init_db(make_engine(settings.database_url)))
    calendar = MockCalendar(client.tz)
    engine = Engine(
        client=client, templates=templates, store=store, calendar=calendar,
        understander=understander,
        gateway=RecordingGateway(), debounce_seconds=args.debounce, on_turn=_print_turn,
    )

    def new_contact() -> Contact:
        return Contact(contact_id=f"sim-{uuid.uuid4().hex[:8]}", phone="+19035550199")

    contact = new_contact()
    print(f"Stormbook booking engine simulator. Client: {client.company_name} ({client.timezone})")
    if client.placeholder_fields():
        print("Note: this client config still has GHL placeholders. Fine for the simulator.")
    print("You are the homeowner. GHL has already sent the first text; type your reply.")
    print(HELP + "\n")

    while True:
        try:
            line = (await asyncio.to_thread(input, "Homeowner: ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        conv = store.get_conversation(client.client_slug, contact.contact_id)
        if line in ("/quit", "/exit"):
            break
        if line == "/help":
            print(HELP)
        elif line == "/new":
            await engine.debouncer.drain()
            contact = new_contact()
            print("  [new homeowner, fresh conversation]")
        elif line == "/state":
            if conv is None:
                print("  [no conversation yet]")
            else:
                offer = [format_slot(from_key(k, client.tz), client.tz) for k in conv.current_offer]
                booked = conv.booked_time and format_slot(from_key(conv.booked_time, client.tz), client.tz)
                print(
                    f"  [status={conv.status} offer={offer} booked={booked} "
                    f"appointment={conv.appointment_id} engine_messages={conv.engine_message_count} "
                    f"times_ever_offered={len(conv.offered_slots)} handoff_reason={conv.handoff_reason}]"
                )
        elif line == "/slots":
            now = datetime.now(UTC)
            free = await calendar.free_slots(now, now.replace(year=now.year + 1))
            print("  " + "\n  ".join(format_slot(s, client.tz) for s in free[:20]))
        elif line.startswith("/take"):
            number = line[5:].strip()
            if conv is None or not number.isdigit() or not 1 <= int(number) <= len(conv.current_offer):
                print("  [usage: /take N, where N is one of the slots currently offered]")
            else:
                slot = from_key(conv.current_offer[int(number) - 1], client.tz)
                calendar.take(slot)
                print(f"  [{format_slot(slot, client.tz)} is now taken by someone else]")
        elif line.startswith("/"):
            print(HELP)
        else:
            engine.receive(contact, InboundMessage(message_id=uuid.uuid4().hex, body=line))
            if args.debounce == 0:
                await engine.debouncer.drain()

    await engine.debouncer.drain()


def main() -> None:
    parser = argparse.ArgumentParser(description="Text the booking engine like a homeowner.")
    parser.add_argument("--client", default="roof-demo", help="client slug in config/clients/")
    parser.add_argument(
        "--debounce", type=float, default=0.0,
        help="seconds to wait for more texts before replying (production uses 8; default 0)",
    )
    parser.add_argument("--verbose", action="store_true", help="also print the JSON turn logs")
    args = parser.parse_args()
    configure_logging()
    if not args.verbose:
        logging.getLogger().setLevel(logging.ERROR)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

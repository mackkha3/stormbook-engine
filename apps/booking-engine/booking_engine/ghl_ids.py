"""Print the GHL ids a client config needs. Read-only: two GET calls, nothing is changed.

    uv run python -m booking_engine.ghl_ids --location <location id>

Lists the sub-account's calendars (name + id) and its pipelines with their
stages (name + id), ready to copy into config/clients/<slug>.yaml. The token
is read from .env, under the env var named by the client config's
`ghl_token_env`, and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from booking_engine.config_loader import ConfigError, load_client
from booking_engine.ghl_client import GhlClient, GhlError
from booking_engine.logger import configure as configure_logging
from booking_engine.settings import Settings, secret_from_env

_HINTS = {
    401: "GHL rejected the token. Check it was copied whole and belongs to this sub-account.",
    403: "The token lacks a scope. It needs calendars.readonly and opportunities.readonly.",
    404: "GHL does not know this location id.",
    422: "GHL does not accept this location id.",
}


def _row(item: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        return "(unreadable)", ""
    return str(item.get("name") or "(no name)"), str(item.get("id") or "")


def render(location_id: str, calendars: list, pipelines: list) -> str:
    lines = [f"Location {location_id}", "", "Calendars  (calendar_id)"]
    lines += [f"  {name:<40} {id_}" for name, id_ in map(_row, calendars)] or ["  (none)"]
    lines += ["", "Pipelines  (pipeline_id, then stage_ids)"]
    for pipeline in pipelines:
        name, id_ = _row(pipeline)
        lines.append(f"  {name:<40} {id_}")
        stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
        lines += [f"      {s_name:<36} {s_id}" for s_name, s_id in map(_row, stages or [])]
    if not pipelines:
        lines.append("  (none)")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    Settings.from_env()                      # loads .env
    try:
        client = load_client(args.client)
    except ConfigError as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 1
    token = secret_from_env(client.ghl_token_env)
    if not token:
        print(f"{client.ghl_token_env} is not set. Add the Private Integration token to .env.",
              file=sys.stderr)
        return 1
    async with httpx.AsyncClient() as http:
        ghl = GhlClient(http, token=token, location_id=args.location)
        try:
            calendars = await ghl.list_calendars()
            pipelines = await ghl.list_pipelines()
        except GhlError as exc:
            hint = _HINTS.get(exc.status or 0, "GHL could not be reached. Try again.")
            print(f"GHL call failed ({exc}). {hint}", file=sys.stderr)
            return 1
    print(render(args.location, calendars, pipelines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print the GHL ids a client config needs.")
    parser.add_argument("--location", required=True, help="the GHL sub-account's location id")
    parser.add_argument(
        "--client", default="roof-demo",
        help="client slug whose ghl_token_env names the token to use (default roof-demo)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    configure_logging()
    logging.getLogger().setLevel(logging.CRITICAL)     # ids only, no JSON log lines
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

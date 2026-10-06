"""The inbound webhook's payload: the shared-secret check and GHL's body → internal shape.

Flat keys, GHL's standard workflow-webhook keys and `customData` are all
accepted. A homeowner's text can contain quotes or line breaks, which break
the JSON a GHL merge field is pasted into; `parse_body` then salvages the ids
and leaves the text empty, and webhook.py reads the text back from GHL.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from booking_engine.logger import get

log = get(__name__)

SECRET_HEADER = "X-Webhook-Secret"
SECRET_PARAM = "secret"      # ?secret=... for GHL's plain Webhook action, which has no headers


class BadPayload(ValueError):
    """The webhook body is missing something the engine cannot work without."""


@dataclass(frozen=True)
class Inbound:
    location_id: str
    contact_id: str
    body: str
    message_id: str | None = None
    phone: str = ""
    name: str | None = None
    timestamp: datetime | None = None
    lead_source: str | None = None


def secret_ok(provided: str | None, expected: str) -> bool:
    """Constant-time check of the shared secret. An unset secret matches nothing."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def _dig(raw: dict[str, Any], path: str) -> Any:
    node: Any = raw
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _first(raw: dict[str, Any], *paths: str) -> str | None:
    """First non-empty value among `paths`, looked up at the top level and in customData."""
    for path in paths:
        for source in (path, f"customData.{path}"):
            value = _dig(raw, source)
            if isinstance(value, str | int) and str(value).strip():
                return str(value).strip()
    return None


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_inbound(raw: Any) -> Inbound:
    """Map GHL's webhook body to the internal shape. Raises BadPayload."""
    if not isinstance(raw, dict):
        raise BadPayload("expected a JSON object")
    location_id = _first(raw, "location_id", "locationId", "location.id")
    contact_id = _first(raw, "contact_id", "contactId", "contact.id")
    missing = [n for n, v in (("location_id", location_id), ("contact_id", contact_id)) if not v]
    if missing:
        raise BadPayload(f"missing required field(s): {', '.join(missing)}")
    return Inbound(
        location_id=location_id,
        contact_id=contact_id,
        body=_first(raw, "message_body", "message.body", "body") or "",
        message_id=_first(raw, "message_id", "messageId", "message.id"),
        phone=_first(raw, "contact_phone", "phone") or "",
        name=_first(raw, "contact_name", "full_name"),
        timestamp=_timestamp(_first(raw, "timestamp", "dateAdded")),
        lead_source=_first(raw, "lead_source"),
    )


_SALVAGE = re.compile(r'"(location_id|contact_id)"\s*:\s*"([^"\\]+)"')


def parse_body(text: str) -> Inbound:
    """Parse the raw webhook body. Broken JSON still yields the ids if they are readable."""
    try:
        return parse_inbound(json.loads(text))
    except json.JSONDecodeError as exc:
        ids = dict(_SALVAGE.findall(text))
        if "location_id" not in ids or "contact_id" not in ids:
            raise BadPayload("body is not valid JSON") from exc
        log.warning("webhook_json_salvaged", extra={"contact_id": ids["contact_id"]})
        return Inbound(location_id=ids["location_id"], contact_id=ids["contact_id"], body="")

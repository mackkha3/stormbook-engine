"""All GHL API v2 calls. Nothing else in the engine talks HTTP to GHL.

Base URL: https://services.leadconnectorhq.com
Auth:     `Authorization: Bearer <location-level Private Integration token>`
Version:  required on every call, and it differs by API family:
          calendars + conversations use 2021-04-15, contacts + opportunities
          use 2021-07-28.

Paths, headers and bodies were checked against GHL's published OpenAPI specs
(github.com/GoHighLevel/highlevel-api-docs, apps/*.json) on 2026-10-06; see
docs/GHL_SETUP.md for the full table.

Every call has a timeout, is retried once on a 5xx or a network failure, and
is logged with method, path and status. The token is never logged.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from booking_engine.logger import get

log = get(__name__)

BASE_URL = "https://services.leadconnectorhq.com"
VERSION_CALENDARS = "2021-04-15"
VERSION_CONVERSATIONS = "2021-04-15"
VERSION_CONTACTS = "2021-07-28"
VERSION_OPPORTUNITIES = "2021-07-28"
TIMEOUT_SECONDS = 10.0
_ATTEMPTS = 2          # the call, plus one retry


class GhlError(Exception):
    """A GHL call failed for good (after the retry, or with a 4xx)."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _id(value: str) -> str:
    return quote(value, safe="")


class GhlClient:
    """One client's GHL sub-account. Shares the process-wide httpx client."""

    def __init__(self, http: httpx.AsyncClient, *, token: str, location_id: str) -> None:
        self._http = http
        self._token = token
        self.location_id = location_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        version: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        replay_safe: bool = True,
    ) -> dict[str, Any]:
        """One GHL call. `replay_safe=False` marks calls that must not be repeated after
        a response timeout (the SMS may have gone out, the appointment may exist)."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Version": version,
            "Accept": "application/json",
        }
        for attempt in range(1, _ATTEMPTS + 1):
            last = attempt == _ATTEMPTS
            try:
                response = await self._http.request(
                    method, BASE_URL + path, params=params, json=json, headers=headers,
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.TransportError as exc:      # includes every timeout
                never_sent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
                log.warning(
                    "ghl_call_failed",
                    extra={"method": method, "path": path, "attempt": attempt,
                           "error": type(exc).__name__},
                )
                if last or not (replay_safe or never_sent):
                    raise GhlError(f"{method} {path}: {type(exc).__name__}") from exc
                continue
            log.info(
                "ghl_call",
                extra={"method": method, "path": path, "status": response.status_code,
                       "attempt": attempt},
            )
            if response.status_code >= 500 and not last:
                continue
            if response.status_code >= 400:
                body = response.text[:500]
                log.error(
                    "ghl_call_rejected",
                    extra={"method": method, "path": path, "status": response.status_code,
                           "body": body},
                )
                raise GhlError(
                    f"{method} {path}: HTTP {response.status_code}",
                    status=response.status_code, body=body,
                )
            try:
                data = response.json()
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
        raise AssertionError("unreachable")

    # --- conversations (Version 2021-04-15) -----------------------------------

    async def send_sms(self, contact_id: str, text: str) -> str:
        """POST /conversations/messages. Returns the new message id."""
        data = await self._request(
            "POST", "/conversations/messages", version=VERSION_CONVERSATIONS,
            json={"type": "SMS", "contactId": contact_id, "message": text},
            replay_safe=False,
        )
        return str(data.get("messageId") or "")

    async def recent_messages(self, contact_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """The contact's latest messages, oldest first. Empty if they have no conversation."""
        found = await self._request(
            "GET", "/conversations/search", version=VERSION_CONVERSATIONS,
            params={"locationId": self.location_id, "contactId": contact_id, "limit": 1},
        )
        conversations = found.get("conversations") or []
        if not conversations:
            return []
        data = await self._request(
            "GET", f"/conversations/{_id(conversations[0]['id'])}/messages",
            version=VERSION_CONVERSATIONS, params={"limit": limit},
        )
        # Documented as {messages: [...]}; the live API has also nested it one level deeper.
        messages = data.get("messages")
        if isinstance(messages, dict):
            messages = messages.get("messages")
        messages = [m for m in messages or [] if isinstance(m, dict)]
        return sorted(messages, key=lambda m: str(m.get("dateAdded") or ""))

    # --- contacts (Version 2021-07-28) ----------------------------------------

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        """GET /contacts/{contactId}. Returns the contact object (tags, name, phone...)."""
        data = await self._request(
            "GET", f"/contacts/{_id(contact_id)}", version=VERSION_CONTACTS
        )
        contact = data.get("contact")
        return contact if isinstance(contact, dict) else {}

    async def add_tags(self, contact_id: str, tags: list[str]) -> None:
        await self._request(
            "POST", f"/contacts/{_id(contact_id)}/tags", version=VERSION_CONTACTS,
            json={"tags": tags},
        )

    async def remove_tags(self, contact_id: str, tags: list[str]) -> None:
        await self._request(
            "DELETE", f"/contacts/{_id(contact_id)}/tags", version=VERSION_CONTACTS,
            json={"tags": tags},
        )

    async def add_note(self, contact_id: str, body: str) -> None:
        await self._request(
            "POST", f"/contacts/{_id(contact_id)}/notes", version=VERSION_CONTACTS,
            json={"body": body},
        )

    # --- calendars (Version 2021-04-15) ---------------------------------------

    async def free_slots(
        self, calendar_id: str, *, start_ms: int, end_ms: int, timezone: str
    ) -> list[str]:
        """GET /calendars/{calendarId}/free-slots. ISO start times; max 31 days per call.

        The response is a map keyed by date: {"2026-10-07": {"slots": ["...-05:00"]}}.
        """
        data = await self._request(
            "GET", f"/calendars/{_id(calendar_id)}/free-slots", version=VERSION_CALENDARS,
            params={"startDate": start_ms, "endDate": end_ms, "timezone": timezone},
        )
        slots: list[str] = []
        for day in data.values():
            if isinstance(day, dict) and isinstance(day.get("slots"), list):
                slots.extend(s for s in day["slots"] if isinstance(s, str))
        return slots

    async def create_appointment(
        self, *, calendar_id: str, contact_id: str, start: str, end: str, title: str
    ) -> str:
        """POST /calendars/events/appointments. GHL re-validates the slot itself.

        Returns the appointment id. A taken slot comes back as a 4xx GhlError.
        """
        data = await self._request(
            "POST", "/calendars/events/appointments", version=VERSION_CALENDARS,
            json={
                "calendarId": calendar_id,
                "locationId": self.location_id,
                "contactId": contact_id,
                "startTime": start,
                "endTime": end,
                "title": title,
                "appointmentStatus": "confirmed",
            },
            replay_safe=False,
        )
        appointment_id = str(data.get("id") or "")
        if not appointment_id:
            raise GhlError("POST /calendars/events/appointments: no appointment id returned")
        return appointment_id

    async def update_appointment(self, appointment_id: str, **fields: Any) -> None:
        """PUT /calendars/events/appointments/{eventId} (startTime, endTime, appointmentStatus)."""
        await self._request(
            "PUT", f"/calendars/events/appointments/{_id(appointment_id)}",
            version=VERSION_CALENDARS, json=fields,
        )

    async def list_calendars(self) -> list[dict[str, Any]]:
        """GET /calendars/. The sub-account's calendars (for finding calendar_id)."""
        data = await self._request(
            "GET", "/calendars/", version=VERSION_CALENDARS,
            params={"locationId": self.location_id},
        )
        return [c for c in data.get("calendars") or [] if isinstance(c, dict)]

    # --- opportunities (Version 2021-07-28) -----------------------------------

    async def list_pipelines(self) -> list[dict[str, Any]]:
        """GET /opportunities/pipelines. Pipelines with their stages (for finding ids)."""
        data = await self._request(
            "GET", "/opportunities/pipelines", version=VERSION_OPPORTUNITIES,
            params={"locationId": self.location_id},
        )
        return [p for p in data.get("pipelines") or [] if isinstance(p, dict)]

    async def find_opportunity(self, contact_id: str, pipeline_id: str) -> str | None:
        """GET /opportunities/search. The contact's opportunity id in this pipeline, if any."""
        data = await self._request(
            "GET", "/opportunities/search", version=VERSION_OPPORTUNITIES,
            params={
                "location_id": self.location_id, "contact_id": contact_id,
                "pipeline_id": pipeline_id, "limit": 1,
            },
        )
        opportunities = data.get("opportunities") or []
        return str(opportunities[0]["id"]) if opportunities else None

    async def update_opportunity_stage(
        self, opportunity_id: str, *, pipeline_id: str, stage_id: str
    ) -> None:
        """PUT /opportunities/{id}."""
        await self._request(
            "PUT", f"/opportunities/{_id(opportunity_id)}", version=VERSION_OPPORTUNITIES,
            json={"pipelineId": pipeline_id, "pipelineStageId": stage_id},
        )

"""A fake GHL API for tests: an httpx transport that answers like GHL and records calls."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import httpx

from booking_engine.config_loader import ClientConfig, StageIds, load_client
from booking_engine.ghl_client import GhlClient
from tests.conftest import TZ

LOCATION, CALENDAR, PIPELINE, TOKEN = "loc-1", "cal-1", "pipe-1", "pit-secret-token"
STAGES = StageIds(texted="st-texted", replied="st-replied", booked="st-booked", no_response="st-none")
OPENING = "Hi, this is Roof Demo. Sorry we missed your call! Want a free roof inspection?"


def live_client(**overrides) -> ClientConfig:
    return load_client("roof-demo").model_copy(
        update={"ghl_location_id": LOCATION, "calendar_id": CALENDAR, "pipeline_id": PIPELINE,
                "stage_ids": STAGES, "company_phone": "+19035550100", **overrides}
    )


def slot(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=TZ)


class FakeGhl:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tags: set[str] = {"engine-active"}
        self.contact_location = LOCATION
        self.slots: list[datetime] = [
            slot(5, 11), slot(5, 14), slot(6, 10), slot(6, 15), slot(7, 9), slot(7, 13),
        ]
        self.messages: list[dict[str, Any]] = [
            {"id": "m-open", "direction": "outbound", "messageType": "TYPE_SMS",
             "body": OPENING, "dateAdded": "2026-10-05T13:00:00.000Z"},
        ]
        self.opportunities: list[dict[str, Any]] = [{"id": "opp-1"}]
        self.appointments: dict[str, dict[str, Any]] = {}
        self.fail: dict[tuple[str, str], list[httpx.Response | Exception]] = defaultdict(list)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        self.client = GhlClient(self.http, token=TOKEN, location_id=LOCATION)

    # --- what the tests look at --------------------------------------------

    def calls(self, method: str | None = None, path_part: str = "") -> list[httpx.Request]:
        return [
            r for r in self.requests
            if (method is None or r.method == method) and path_part in r.url.path
        ]

    def bodies(self, method: str, path_part: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.calls(method, path_part)]

    @property
    def sent(self) -> list[str]:
        return [b["message"] for b in self.bodies("POST", "/conversations/messages")]

    def add_inbound(self, message_id: str, body: str, attachments: list[str] | None = None) -> None:
        self.messages.append(
            {"id": message_id, "direction": "inbound", "messageType": "TYPE_SMS", "body": body,
             "attachments": attachments or [],
             "dateAdded": f"2026-10-05T14:{len(self.messages):02d}:00.000Z"}
        )

    # --- the API -------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        queued = self.fail[(method, path)]
        if queued:
            outcome = queued.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        body = json.loads(request.content) if request.content else {}

        if method == "GET" and path.startswith("/contacts/"):
            return httpx.Response(200, json={"contact": {
                "id": path.split("/")[2], "locationId": self.contact_location,
                "name": "Jane Doe", "phone": "+19035550199", "tags": sorted(self.tags),
            }})
        if path.endswith("/tags"):
            self.tags = (self.tags | set(body["tags"])) if method == "POST" else (
                self.tags - set(body["tags"])
            )
            return httpx.Response(201 if method == "POST" else 200, json={"tags": sorted(self.tags)})
        if method == "POST" and path.endswith("/notes"):
            return httpx.Response(201, json={"note": {"id": "note-1", "body": body["body"]}})
        if path == "/conversations/search":
            return httpx.Response(200, json={"conversations": [{"id": "conv-1"}], "total": 1})
        if method == "GET" and path == "/conversations/conv-1/messages":
            newest_first = list(reversed(self.messages))
            return httpx.Response(200, json={"messages": {
                "lastMessageId": newest_first[-1]["id"], "nextPage": False,
                "messages": newest_first,
            }})
        if method == "POST" and path == "/conversations/messages":
            return httpx.Response(200, json={"conversationId": "conv-1", "messageId": "m-out"})
        if path == f"/calendars/{CALENDAR}/free-slots":
            days: dict[str, Any] = {"traceId": "trace-123"}
            for s in self.slots:
                days.setdefault(s.date().isoformat(), {"slots": []})["slots"].append(s.isoformat())
            return httpx.Response(200, json=days)
        if method == "POST" and path == "/calendars/events/appointments":
            start = datetime.fromisoformat(body["startTime"])
            if start not in self.slots:
                return httpx.Response(400, json={
                    "statusCode": 400, "message": "The slot you have selected is no longer available.",
                })
            self.slots.remove(start)
            appointment_id = f"appt-{len(self.appointments) + 1}"
            self.appointments[appointment_id] = body
            return httpx.Response(200, json={"id": appointment_id, **body})
        if method == "PUT" and path.startswith("/calendars/events/appointments/"):
            self.appointments[path.rsplit("/", 1)[1]].update(body)
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1]})
        if method == "GET" and path == "/calendars/":
            return httpx.Response(200, json={"calendars": [
                {"id": CALENDAR, "name": "Roof inspections", "locationId": LOCATION},
            ]})
        if method == "GET" and path == "/opportunities/pipelines":
            return httpx.Response(200, json={"pipelines": [{
                "id": PIPELINE, "name": "Storm leads", "stages": [
                    {"id": STAGES.texted, "name": "Texted"}, {"id": STAGES.replied, "name": "Replied"},
                    {"id": STAGES.booked, "name": "Booked"},
                    {"id": STAGES.no_response, "name": "No response"},
                ],
            }]})
        if path == "/opportunities/search":
            return httpx.Response(200, json={"opportunities": self.opportunities, "meta": {}})
        if method == "PUT" and path.startswith("/opportunities/"):
            return httpx.Response(200, json={"opportunity": {"id": path.rsplit("/", 1)[1]}})
        return httpx.Response(404, json={"message": f"fake GHL has no {method} {path}"})

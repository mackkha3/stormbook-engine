"""Google Maps geocoding — non-blocking.

If the geocode call fails for any reason, we log a warning and proceed
with the raw address. A lost lat/lng is better than a lost lead.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel

from intake_api.lib.logger import get

log = get(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GEOCODE_TIMEOUT_SECONDS = 4.0


class GeocodeResult(BaseModel):
    formatted_address: str | None = None
    lat: float | None = None
    lng: float | None = None
    state: str | None = None
    zip_code: str | None = None


async def geocode(address: str, *, client: httpx.AsyncClient | None = None) -> GeocodeResult | None:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        log.warning("geocode_skipped_no_api_key")
        return None
    if not address:
        return None

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=GEOCODE_TIMEOUT_SECONDS)

    try:
        resp = await client.get(
            GEOCODE_URL,
            params={"address": address, "key": api_key},
            timeout=GEOCODE_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            log.warning(
                "geocode_failed",
                extra={"status": resp.status_code, "address": address},
            )
            return None
        body: dict[str, Any] = resp.json()
        if body.get("status") != "OK":
            log.warning(
                "geocode_failed",
                extra={"status": body.get("status"), "address": address},
            )
            return None
        results = body.get("results") or []
        if not results:
            return None
        top = results[0]
        loc = top.get("geometry", {}).get("location", {})
        state = None
        zip_code = None
        for component in top.get("address_components") or []:
            types = component.get("types") or []
            if "administrative_area_level_1" in types:
                state = component.get("short_name")
            if "postal_code" in types:
                zip_code = component.get("long_name")
        return GeocodeResult(
            formatted_address=top.get("formatted_address"),
            lat=loc.get("lat"),
            lng=loc.get("lng"),
            state=state,
            zip_code=zip_code,
        )
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("geocode_failed", extra={"error": str(exc), "address": address})
        return None
    finally:
        if owns_client:
            await client.aclose()

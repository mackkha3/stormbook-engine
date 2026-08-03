"""Twilio Messages API over raw httpx.

We call exactly one endpoint, so the `twilio` SDK (~30 transitive packages)
is not worth carrying in a workspace with a finite lifespan.

POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json
Auth: HTTP Basic (account SID / auth token)
Body: form-urlencoded — To, From, Body
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx

from demo_relay.lib.logger import get

log = get(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
TIMEOUT_SECONDS = 10.0
RETRY_DELAYS_MS = (200, 800, 3200)


class TwilioConfigError(RuntimeError):
    """Required Twilio env vars are missing."""


class TwilioSendError(RuntimeError):
    """Twilio rejected the send, or was unreachable after retries."""


@dataclass(frozen=True)
class TwilioConfig:
    account_sid: str
    auth_token: str
    from_number: str

    @classmethod
    def from_env(cls) -> TwilioConfig:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", account_sid),
                ("TWILIO_AUTH_TOKEN", auth_token),
                ("TWILIO_FROM_NUMBER", from_number),
            )
            if not value
        ]
        if missing:
            raise TwilioConfigError(f"missing env vars: {', '.join(missing)}")
        return cls(account_sid=account_sid, auth_token=auth_token, from_number=from_number)


async def send_sms(
    to: str,
    body: str,
    *,
    from_number: str,
    account_sid: str,
    auth_token: str,
    http: httpx.AsyncClient,
) -> str:
    """Send one SMS. Returns the Twilio message SID.

    Retries 5xx and transport errors at 200ms → 800ms → 3200ms. A 4xx is our
    mistake (bad number, unverified sender) and fails immediately.
    """
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
    form = {"To": to, "From": from_number, "Body": body}
    auth = (account_sid, auth_token)

    attempts = len(RETRY_DELAYS_MS) + 1
    last_error: str = "unknown"

    for attempt in range(attempts):
        try:
            response = await http.post(
                url, data=form, auth=auth, timeout=TIMEOUT_SECONDS
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "twilio_send_transport_error",
                extra={"attempt": attempt + 1, "to": to, "error": last_error},
            )
        else:
            if response.status_code < 400:
                sid = _extract_sid(response)
                log.info(
                    "twilio_send_success",
                    extra={"to": to, "twilio_message_sid": sid, "attempt": attempt + 1},
                )
                return sid
            last_error = f"HTTP {response.status_code}: {_safe_text(response)}"
            if response.status_code < 500:
                log.error("twilio_send_rejected", extra={"to": to, "error": last_error})
                raise TwilioSendError(last_error)
            log.warning(
                "twilio_send_server_error",
                extra={"attempt": attempt + 1, "to": to, "error": last_error},
            )

        if attempt < attempts - 1:
            await asyncio.sleep(RETRY_DELAYS_MS[attempt] / 1000)

    log.error("twilio_send_exhausted", extra={"to": to, "error": last_error})
    raise TwilioSendError(last_error)


def _extract_sid(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    return str(payload.get("sid", "")) if isinstance(payload, dict) else ""


def _safe_text(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except Exception:  # noqa: BLE001
        return "<unreadable>"

"""Hand-rolled Twilio request signature verifier.

Twilio ships a `twilio` SDK that includes `RequestValidator`, but we
already made the "no SDK for one API call" decision when writing the
outbound send path in `lib/twilio_client.py`. The signature algorithm
is small and stable — hand-rolling keeps our dependency footprint
exactly matching the surface area we actually use.

Spec: https://www.twilio.com/docs/usage/webhooks/webhooks-security
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def build_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute the Twilio-style HMAC-SHA1 signature for a webhook POST.

    `url` must include scheme + host + path + query string exactly as
    Twilio called it. `params` must be the form-urlencoded POST body
    as a flat dict of strings.
    """
    sorted_items = sorted(params.items())
    canonical = url + "".join(f"{k}{v}" for k, v in sorted_items)
    digest = hmac.new(
        auth_token.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(
    *,
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature_header: str | None,
) -> bool:
    """Timing-safe check of a presented X-Twilio-Signature header."""
    if not signature_header or not auth_token:
        return False
    expected = build_signature(auth_token, url, params)
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str.
    return hmac.compare_digest(expected.encode("utf-8"), signature_header.encode("utf-8"))

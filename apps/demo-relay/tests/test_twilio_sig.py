from __future__ import annotations

import base64
import hashlib
import hmac

from demo_relay.lib.twilio_sig import build_signature, verify_signature

TOKEN = "test_auth_token"
URL = "https://relay.example.com/twilio/inbound"
PARAMS = {"From": "+19035550199", "Body": "Tuesday"}


def _hmac_b64(canonical: str) -> str:
    digest = hmac.new(TOKEN.encode(), canonical.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _verify(signature_header: str | None, *, auth_token: str = TOKEN) -> bool:
    return verify_signature(
        auth_token=auth_token, url=URL, params=PARAMS, signature_header=signature_header
    )


def test_valid_signature_passes():
    assert _verify(build_signature(TOKEN, URL, PARAMS)) is True


def test_invalid_signature_rejected():
    assert _verify(build_signature("some_other_token", URL, PARAMS)) is False


def test_missing_header_rejected():
    assert _verify(None) is False


def test_missing_auth_token_rejected():
    # Signed with an empty key, so only the empty-token guard can reject it.
    assert _verify(build_signature("", URL, PARAMS), auth_token="") is False


def test_params_sorted_alphabetically():
    expected = _hmac_b64(f"{URL}Bodyhello" + "From+1" + "NumMedia0")

    assert build_signature(TOKEN, URL, {"NumMedia": "0", "From": "+1", "Body": "hello"}) == expected


def test_url_includes_query_string():
    url = f"{URL}?client=henderson"

    assert build_signature(TOKEN, url, {}) == _hmac_b64(url)
    assert build_signature(TOKEN, url, {}) != build_signature(TOKEN, URL, {})


def test_non_ascii_header_rejected():
    assert _verify("sïgnature") is False

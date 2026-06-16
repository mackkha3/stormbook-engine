from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest


@pytest.mark.asyncio
async def test_meta_verify_success(client):
    resp = await client.get(
        "/webhook/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": os.environ["META_VERIFY_TOKEN"],
            "hub.challenge": "challenge-1234",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "challenge-1234"


@pytest.mark.asyncio
async def test_meta_verify_wrong_token(client):
    resp = await client.get(
        "/webhook/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-1234",
        },
    )
    assert resp.status_code == 403


def _signed(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_meta_post_invalid_signature(client):
    body = json.dumps({"entry": []}).encode()
    resp = await client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "META_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_meta_post_valid_signature_empty_entries(client):
    body = json.dumps({"entry": []}).encode()
    secret = os.environ["META_APP_SECRET"]
    resp = await client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _signed(body, secret),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0

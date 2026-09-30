"""Webhook mode: payload decoding, the mandatory checks, and the routes."""

import base64
import hashlib
import json
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from Crypto.Cipher import AES
from lark_oapi.core.utils import AESCipher

from card_actions import card_action_response
from webhook_server import (
    CARD_PATH,
    EVENT_PATH,
    HEALTH_PATH,
    WebhookAuthError,
    WebhookPayloadError,
    build_app,
    decode_payload,
    make_dispatcher,
)

VERIFICATION_TOKEN = "vt_synthetic"
ENCRYPT_KEY = "ek_synthetic_secret"


def encrypt_body(plaintext: str, key: str, iv: bytes = b"0123456789abcdef") -> str:
    """Produce a Feishu-style envelope: base64(iv || AES-CBC(payload)).

    Matches ``lark_oapi.core.utils.AESCipher``: SHA-256 key, IV prefix, PKCS#7.
    """
    digest = hashlib.sha256(key.encode()).digest()
    data = plaintext.encode()
    pad_len = AES.block_size - (len(data) % AES.block_size)
    padded = data + bytes([pad_len]) * pad_len
    return base64.b64encode(
        iv + AES.new(digest, AES.MODE_CBC, iv).encrypt(padded)
    ).decode()


def test_decode_plain_payload_with_matching_token():
    raw = json.dumps({"header": {"token": VERIFICATION_TOKEN}, "event": {}}).encode()
    payload = decode_payload(raw, verification_token=VERIFICATION_TOKEN)
    assert payload["event"] == {}


def test_decode_accepts_the_v1_top_level_token():
    raw = json.dumps({"token": VERIFICATION_TOKEN, "challenge": "c"}).encode()
    assert decode_payload(raw, verification_token=VERIFICATION_TOKEN)["challenge"] == "c"


def test_decode_without_a_configured_token_skips_the_check():
    raw = json.dumps({"event": {"x": 1}}).encode()
    assert decode_payload(raw)["event"] == {"x": 1}


def test_decode_rejects_a_wrong_token():
    raw = json.dumps({"header": {"token": "nope"}, "event": {}}).encode()
    with pytest.raises(WebhookAuthError):
        decode_payload(raw, verification_token=VERIFICATION_TOKEN)


def test_decode_rejects_a_missing_token_when_one_is_configured():
    raw = json.dumps({"event": {}}).encode()
    with pytest.raises(WebhookAuthError):
        decode_payload(raw, verification_token=VERIFICATION_TOKEN)


def test_decode_rejects_non_json_and_non_object_bodies():
    with pytest.raises(WebhookPayloadError):
        decode_payload(b"not json")
    with pytest.raises(WebhookPayloadError):
        decode_payload(b"[1,2,3]")


def test_decode_decrypts_an_encrypted_envelope():
    inner = json.dumps(
        {"header": {"token": VERIFICATION_TOKEN}, "event": {"hello": "world"}}
    )
    raw = json.dumps({"encrypt": encrypt_body(inner, ENCRYPT_KEY)}).encode()
    payload = decode_payload(
        raw, verification_token=VERIFICATION_TOKEN, encrypt_key=ENCRYPT_KEY
    )
    assert payload["event"] == {"hello": "world"}


def test_our_encryptor_matches_the_sdk_decryptor():
    """Pins the envelope format this module decrypts."""
    raw = json.dumps({"encrypt": encrypt_body('{"a": 1}', ENCRYPT_KEY)}).encode()
    assert decode_payload(raw, encrypt_key=ENCRYPT_KEY) == {"a": 1}
    assert AESCipher(ENCRYPT_KEY).decrypt_str(
        json.loads(raw.decode())["encrypt"]
    ) == '{"a": 1}'


def test_decode_rejects_encrypted_payload_without_a_key():
    raw = json.dumps({"encrypt": encrypt_body('{"a": 1}', ENCRYPT_KEY)}).encode()
    with pytest.raises(WebhookAuthError):
        decode_payload(raw)


def test_decode_rejects_a_tampered_or_wrongly_keyed_envelope():
    raw = json.dumps({"encrypt": encrypt_body('{"a": 1}', "another-key")}).encode()
    with pytest.raises(WebhookPayloadError):
        decode_payload(raw, encrypt_key=ENCRYPT_KEY)


def test_decode_checks_the_token_inside_an_encrypted_envelope():
    inner = json.dumps({"header": {"token": "wrong"}, "event": {}})
    raw = json.dumps({"encrypt": encrypt_body(inner, ENCRYPT_KEY)}).encode()
    with pytest.raises(WebhookAuthError):
        decode_payload(
            raw, verification_token=VERIFICATION_TOKEN, encrypt_key=ENCRYPT_KEY
        )


def test_make_dispatcher_routes_through_the_sdk_handler():
    handler = MagicMock()
    handler._do_without_validation.return_value = {"ok": True}
    dispatch = make_dispatcher(handler)
    assert dispatch({"event": {}}) == {"ok": True}
    raw = handler._do_without_validation.call_args.args[0]
    assert json.loads(raw.decode()) == {"event": {}}


class _Recorder:
    """Stand-in for the SDK dispatcher: records payloads, answers card calls."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, payload: dict):
        self.calls.append(payload)
        if (payload.get("event") or {}).get("action"):
            return card_action_response(ok=True)
        return None


@pytest.fixture
async def webhook():
    """An aiohttp test client over the webhook app."""
    recorder = _Recorder()
    client = TestClient(
        TestServer(
            build_app(
                dispatcher=recorder,
                verification_token=VERIFICATION_TOKEN,
                encrypt_key=ENCRYPT_KEY,
            )
        )
    )
    await client.start_server()
    try:
        yield client, recorder
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_webhook_health(webhook):
    client, _ = webhook
    resp = await client.get(HEALTH_PATH)
    assert resp.status == 200
    assert await resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_webhook_event_echoes_the_challenge(webhook):
    client, _ = webhook
    resp = await client.post(
        EVENT_PATH,
        json={"challenge": "abc123", "token": VERIFICATION_TOKEN, "type": "url_verification"},
    )
    assert resp.status == 200
    assert await resp.json() == {"challenge": "abc123"}


@pytest.mark.asyncio
async def test_webhook_event_dispatches_a_verified_payload(webhook):
    client, recorder = webhook
    payload = {"schema": "2.0", "header": {"token": VERIFICATION_TOKEN, "event_type": "x"}}
    resp = await client.post(EVENT_PATH, json=payload)
    assert resp.status == 200
    assert await resp.json() == {"code": 0}
    assert recorder.calls == [payload]


@pytest.mark.asyncio
async def test_webhook_event_rejects_a_bad_token(webhook):
    client, recorder = webhook
    resp = await client.post(EVENT_PATH, json={"header": {"token": "nope"}})
    assert resp.status == 401
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_webhook_event_rejects_a_bad_body(webhook):
    client, _ = webhook
    resp = await client.post(EVENT_PATH, data=b"not json")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_webhook_event_accepts_an_encrypted_payload(webhook):
    client, recorder = webhook
    inner = json.dumps(
        {"schema": "2.0", "header": {"token": VERIFICATION_TOKEN, "event_type": "x"}}
    )
    resp = await client.post(
        EVENT_PATH, json={"encrypt": encrypt_body(inner, ENCRYPT_KEY)}
    )
    assert resp.status == 200
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_webhook_card_returns_the_toast_response(webhook):
    client, _ = webhook
    payload = {
        "schema": "2.0",
        "header": {"token": VERIFICATION_TOKEN, "event_type": "card.action.trigger"},
        "event": {"action": {"value": {"answer_thread_id": "t"}}},
    }
    resp = await client.post(CARD_PATH, json=payload)
    assert resp.status == 200
    body = await resp.json()
    assert body["toast"]["content"] == "Answer submitted"
    assert body["toast"]["type"] == "success"


@pytest.mark.asyncio
async def test_webhook_card_with_no_result_acknowledges(webhook):
    client, _ = webhook
    resp = await client.post(
        CARD_PATH, json={"header": {"token": VERIFICATION_TOKEN}}
    )
    assert resp.status == 200
    assert await resp.json() == {"code": 0}

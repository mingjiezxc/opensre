"""SDK-seam tests for ``lark_client``.

The bot is in no live group and no real App Secret is available to the test
suite, so the request builders — the only place this service touches the
generated SDK — are asserted directly. A wrong builder method name or a wrong
path/verb fails here instead of at runtime.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lark_client import (
    MSG_TYPE_INTERACTIVE,
    MSG_TYPE_TEXT,
    RECEIVE_ID_TYPE_CHAT,
    USER_ID_TYPE_OPEN_ID,
    BotInfo,
    LarkClient,
    create_message_request,
    encode_content,
    get_user_request,
    patch_message_request,
    reply_message_request,
)


def _response(*, ok=True, data=None):
    response = MagicMock()
    response.success.return_value = ok
    response.code = 0 if ok else 99991672
    response.msg = "ok" if ok else "no permission"
    response.get_log_id.return_value = "log-1"
    response.data = data
    return response


def make_client() -> LarkClient:
    """A real LarkClient (construction is offline) with a stubbed SDK client."""
    client = LarkClient(app_id="cli_synthetic", app_secret="secret_synthetic")
    client._client = MagicMock()
    return client


def test_create_message_request_builds_the_documented_call():
    request = create_message_request(
        receive_id="oc_1",
        receive_id_type=RECEIVE_ID_TYPE_CHAT,
        msg_type=MSG_TYPE_INTERACTIVE,
        content="{}",
    )
    assert request.uri == "/open-apis/im/v1/messages"
    assert request.http_method.name == "POST"
    assert request.receive_id_type == "chat_id"
    body = request.body
    assert body.receive_id == "oc_1"
    assert body.msg_type == "interactive"
    assert body.content == "{}"


def test_patch_message_request_uses_the_message_path_and_patch_verb():
    request = patch_message_request(message_id="om_1", content='{"a":1}')
    assert request.uri == "/open-apis/im/v1/messages/:message_id"
    assert request.http_method.name == "PATCH"
    assert request.paths["message_id"] == "om_1"
    assert request.body.content == '{"a":1}'


def test_reply_message_request_carries_message_id_and_body():
    request = reply_message_request(
        message_id="om_2", msg_type=MSG_TYPE_TEXT, content='{"text":"hi"}'
    )
    assert request.uri == "/open-apis/im/v1/messages/:message_id/reply"
    assert request.http_method.name == "POST"
    assert request.paths["message_id"] == "om_2"
    assert request.body.msg_type == "text"
    assert request.body.content == '{"text":"hi"}'


def test_get_user_request_uses_open_id_lookup():
    request = get_user_request(open_id="ou_synthetic_bot")
    assert request.uri == "/open-apis/contact/v3/users/:user_id"
    assert request.user_id_type == USER_ID_TYPE_OPEN_ID == "open_id"
    assert request.paths["user_id"] == "ou_synthetic_bot"


def test_tenant_access_token_is_read_from_the_http_body(monkeypatch):
    """The tenant token must come from a direct HTTP POST.

    The generated SDK call builds the right request but never populates
    ``response.data`` (the body stays inside an opaque ``RawResponse``), so the
    token is not reachable through the SDK model. This pins the HTTP path that
    replaced it, including the endpoint, verb and credential payload.
    """
    captured: dict = {}

    class _Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self):
            return {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "t-synthetic-token",
                "expire": 7200,
            }

    class _Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            return _Resp()

    monkeypatch.setattr("lark_client.aiohttp.ClientSession", _Session)
    client = make_client()

    token = asyncio.run(client._tenant_access_token())

    assert token == "t-synthetic-token"
    assert captured["url"].endswith("/open-apis/auth/v3/tenant_access_token/internal")
    assert captured["json"] == {
        "app_id": "cli_synthetic",
        "app_secret": "secret_synthetic",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 99991672, "msg": "no permission"},
        {"code": 0, "msg": "ok"},  # success status but no token field
        {"code": 0, "msg": "ok", "tenant_access_token": ""},
        "not-a-dict",
    ],
)
def test_tenant_access_token_returns_none_on_bad_bodies(monkeypatch, payload):
    class _Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self):
            return payload

    class _Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, **kw):
            return _Resp()

    monkeypatch.setattr("lark_client.aiohttp.ClientSession", _Session)
    assert asyncio.run(make_client()._tenant_access_token()) is None


def test_encode_content_is_compact_and_keeps_unicode():
    assert encode_content({"text": "检查"}) == '{"text":"检查"}'
    assert json.loads(encode_content({"text": "a b"})) == {"text": "a b"}


@pytest.mark.asyncio
async def test_send_card_returns_the_created_message_id():
    client = make_client()
    client._client.im.v1.message.acreate = AsyncMock(
        return_value=_response(data=MagicMock(message_id="om_new"))
    )
    assert await client.send_card(receive_id="oc_1", card={"elements": []}) == "om_new"
    request = client._client.im.v1.message.acreate.await_args.args[0]
    assert request.body.msg_type == MSG_TYPE_INTERACTIVE
    assert json.loads(request.body.content) == {"elements": []}


@pytest.mark.asyncio
async def test_send_text_is_plain_text():
    client = make_client()
    client._client.im.v1.message.acreate = AsyncMock(
        return_value=_response(data=MagicMock(message_id="om_t"))
    )
    await client.send_text(receive_id="oc_1", text="hello")
    request = client._client.im.v1.message.acreate.await_args.args[0]
    assert request.body.msg_type == MSG_TYPE_TEXT
    assert json.loads(request.body.content) == {"text": "hello"}


@pytest.mark.asyncio
async def test_send_failures_are_swallowed_and_reported_as_none():
    client = make_client()
    client._client.im.v1.message.acreate = AsyncMock(return_value=_response(ok=False))
    assert await client.send_card(receive_id="oc_1", card={}) is None

    client._client.im.v1.message.acreate = AsyncMock(side_effect=RuntimeError("boom"))
    assert await client.send_card(receive_id="oc_1", card={}) is None


@pytest.mark.asyncio
async def test_patch_card_reports_acceptance():
    client = make_client()
    client._client.im.v1.message.apatch = AsyncMock(return_value=_response())
    assert await client.patch_card("om_1", {"elements": []}) is True

    client._client.im.v1.message.apatch = AsyncMock(return_value=_response(ok=False))
    assert await client.patch_card("om_1", {"elements": []}) is False

    client._client.im.v1.message.apatch = AsyncMock(side_effect=RuntimeError("gone"))
    assert await client.patch_card("om_1", {"elements": []}) is False


@pytest.mark.asyncio
async def test_patch_card_without_a_message_id_short_circuits():
    client = make_client()
    client._client.im.v1.message.apatch = AsyncMock()
    assert await client.patch_card("", {"elements": []}) is False
    client._client.im.v1.message.apatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_sends_a_card_or_text():
    client = make_client()
    client._client.im.v1.message.areply = AsyncMock(
        return_value=_response(data=MagicMock(message_id="om_reply"))
    )
    assert await client.reply(message_id="om_1", card={"elements": []}) == "om_reply"
    card_request = client._client.im.v1.message.areply.await_args.args[0]
    assert card_request.body.msg_type == MSG_TYPE_INTERACTIVE

    await client.reply(message_id="om_1", text="plain")
    text_request = client._client.im.v1.message.areply.await_args.args[0]
    assert text_request.body.msg_type == MSG_TYPE_TEXT
    assert json.loads(text_request.body.content) == {"text": "plain"}


@pytest.mark.asyncio
async def test_display_name_returns_the_name():
    client = make_client()
    client._client.contact.v3.user.aget = AsyncMock(
        return_value=_response(data=MagicMock(user=SimpleNamespace(name="  Jane Doe  ")))
    )
    assert await client.get_user_display_name("ou_user") == "Jane Doe"


@pytest.mark.asyncio
async def test_display_name_never_falls_back_to_the_open_id():
    client = make_client()
    for outcome in (
        _response(ok=False, data=MagicMock(user=SimpleNamespace(name="Jane"))),
        _response(data=MagicMock(user=SimpleNamespace(name="   "))),
        _response(data=MagicMock(user=None)),
        _response(data=None),
    ):
        client._client.contact.v3.user.aget = AsyncMock(return_value=outcome)
        assert await client.get_user_display_name("ou_user_12345") is None

    client._client.contact.v3.user.aget = AsyncMock(side_effect=RuntimeError("no scope"))
    assert await client.get_user_display_name("ou_user_12345") is None
    assert await client.get_user_display_name("") is None


@pytest.mark.asyncio
async def test_display_name_is_capped():
    client = make_client()
    client._client.contact.v3.user.aget = AsyncMock(
        return_value=_response(data=MagicMock(user=SimpleNamespace(name="x" * 300)))
    )
    name = await client.get_user_display_name("ou_user")
    assert name is not None and len(name) == 128


def test_parse_bot_info_shape():
    assert LarkClient.parse_bot_info(
        {"code": 0, "bot": {"open_id": "ou_bot", "app_name": "Bot", "activate_status": 2}}
    ) == BotInfo(open_id="ou_bot", name="Bot", activate_status=2)


def test_parse_bot_info_rejects_unusable_payloads():
    for payload in (
        None,
        {},
        "nope",
        {"code": 99991672, "msg": "denied"},
        {"code": 0, "bot": {}},
        {"code": 0, "bot": {"open_id": ""}},
        {"code": 0, "bot": {"open_id": 42}},
    ):
        assert LarkClient.parse_bot_info(payload) is None

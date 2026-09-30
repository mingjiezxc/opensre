"""Pure-function tests for ``message_parser`` (no SDK, no I/O)."""

import json
from types import SimpleNamespace

from helpers import SYNTHETIC_CHAT_ID, SYNTHETIC_USER_OPEN_ID, make_mention, make_receive_event

from message_parser import (
    chat_id_of,
    chat_type_of,
    content_of,
    extract_text,
    is_bot_mentioned,
    is_p2p,
    mentions_of,
    message_id_of,
    sender_open_id,
    should_handle_message,
    strip_bot_name,
    strip_mentions,
)

BOT_OPEN_ID = "ou_synthetic_bot"


def test_extract_text_from_json_string():
    assert extract_text('{"text":"Investigate oc-1234"}') == "Investigate oc-1234"


def test_extract_text_from_dict_and_unicode():
    assert extract_text({"text": "@_user_1 检查一下"}) == "@_user_1 检查一下"


def test_extract_text_non_text_message_is_empty():
    # Image / file / sticker messages carry a different key.
    assert extract_text(json.dumps({"image_key": "img_v2_x"})) == ""


def test_extract_text_garbage_inputs():
    assert extract_text("not json") == ""
    assert extract_text(None) == ""
    assert extract_text('{"text": 42}') == ""


def test_is_bot_mentioned_true_when_open_id_matches():
    event = make_receive_event(
        text="@_user_1 investigate", mentions=[make_mention(BOT_OPEN_ID)]
    )
    assert is_bot_mentioned(event, BOT_OPEN_ID) is True


def test_is_bot_mentioned_false_for_another_user():
    event = make_receive_event(mentions=[make_mention("ou_someone_else")])
    assert is_bot_mentioned(event, BOT_OPEN_ID) is False


def test_is_bot_mentioned_false_without_mentions():
    assert is_bot_mentioned(make_receive_event(mentions=[]), BOT_OPEN_ID) is False


def test_is_bot_mentioned_false_when_bot_id_unresolved():
    """An unresolved open_id must never be treated as a match."""
    event = make_receive_event(mentions=[make_mention(BOT_OPEN_ID)])
    assert is_bot_mentioned(event, "") is False
    assert is_bot_mentioned(event, None) is False


def test_is_bot_mentioned_accepts_sdk_objects():
    mention = SimpleNamespace(key="@_user_1", id=SimpleNamespace(open_id=BOT_OPEN_ID))
    message = SimpleNamespace(mentions=[mention], content='{"text":"hi"}', chat_id="oc_1")
    event = SimpleNamespace(event=SimpleNamespace(message=message, sender=None))
    assert is_bot_mentioned(event, BOT_OPEN_ID) is True
    assert mentions_of(event) == [mention]


def test_strip_mentions_removes_placeholder_and_collapses_gap():
    event = make_receive_event(
        text="Hello @_user_1 check pods", mentions=[make_mention(BOT_OPEN_ID)]
    )
    stripped = strip_mentions(
        extract_text(content_of(event)), mentions_of(event)
    )
    assert stripped == "Hello check pods"


def test_strip_mentions_leading_mention():
    assert strip_mentions("@_user_1 investigate oc-9", []) == "investigate oc-9"


def test_strip_mentions_removes_known_key_and_leftovers():
    text = "@_user_1 ping @_user_2"
    mentions = [make_mention("ou_a", key="@_user_1"), make_mention("ou_b", key="@_user_2")]
    assert strip_mentions(text, mentions) == "ping"


def test_strip_mentions_noop_without_mentions():
    assert strip_mentions("check redis", []) == "check redis"


def test_strip_bot_name_removes_literal_name():
    assert strip_bot_name("OpenSRE help me", "OpenSRE") == "help me"
    assert strip_bot_name("help me", "") == "help me"


def test_is_p2p():
    assert is_p2p("p2p") is True
    assert is_p2p("P2P") is True
    assert is_p2p("group") is False
    assert is_p2p(None) is False
    assert is_p2p("") is False


def test_event_accessors():
    event = make_receive_event(chat_type="group", chat_id=SYNTHETIC_CHAT_ID)
    assert chat_id_of(event) == SYNTHETIC_CHAT_ID
    assert chat_type_of(event) == "group"
    assert sender_open_id(event) == SYNTHETIC_USER_OPEN_ID
    assert message_id_of(event) == "om_synthetic_message"


def test_event_accessors_accept_sdk_objects():
    message = SimpleNamespace(
        chat_id="oc_obj", chat_type="p2p", message_id="om_obj", content="{}", mentions=[]
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_obj"))
    event = SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))
    assert chat_id_of(event) == "oc_obj"
    assert chat_type_of(event) == "p2p"
    assert sender_open_id(event) == "ou_obj"
    assert message_id_of(event) == "om_obj"


def test_group_requires_mention_but_p2p_does_not():
    assert should_handle_message(text="hi", mentioned=False, chat_type="group") is False
    assert should_handle_message(text="hi", mentioned=True, chat_type="group") is True
    assert should_handle_message(text="hi", mentioned=False, chat_type="p2p") is True


def test_empty_text_is_never_handled():
    assert should_handle_message(text="   ", mentioned=True, chat_type="p2p") is False
    assert should_handle_message(text="", mentioned=True, chat_type="group") is False

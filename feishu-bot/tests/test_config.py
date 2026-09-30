"""Configuration resolution, defaults and the credential-missing exit path."""

import pytest

from config import (
    DEFAULT_BOT_NAME,
    DEFAULT_DOMAIN,
    DEFAULT_PORT,
    MODE_WEBHOOK,
    MODE_WS,
    FeishuConfig,
)


def test_defaults_with_an_empty_environment():
    cfg = FeishuConfig(env={})
    assert cfg.PORT == DEFAULT_PORT == 3979
    assert cfg.FEISHU_MODE == MODE_WS
    assert cfg.FEISHU_BOT_NAME == DEFAULT_BOT_NAME == "OpenSRE"
    assert cfg.FEISHU_DOMAIN == DEFAULT_DOMAIN
    assert cfg.FEISHU_APP_ID == ""
    assert cfg.FEISHU_APP_SECRET == ""
    assert cfg.FEISHU_VERIFICATION_TOKEN == ""
    assert cfg.FEISHU_ENCRYPT_KEY == ""
    assert cfg.FEISHU_BOT_OPEN_ID == ""


def test_is_configured_requires_app_id_and_secret():
    assert FeishuConfig(env={}).is_configured() is False
    assert FeishuConfig(env={"FEISHU_APP_ID": "cli_x"}).is_configured() is False
    assert FeishuConfig(env={"FEISHU_APP_SECRET": "s"}).is_configured() is False
    assert (
        FeishuConfig(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s"}).is_configured()
        is True
    )


def test_blank_values_fall_back_to_defaults():
    """A key present but empty (``PORT=``) must not disable the default."""
    cfg = FeishuConfig(env={"PORT": "", "FEISHU_MODE": "", "FEISHU_DOMAIN": ""})
    assert cfg.PORT == DEFAULT_PORT
    assert cfg.FEISHU_MODE == MODE_WS
    assert cfg.FEISHU_DOMAIN == DEFAULT_DOMAIN


def test_port_garbage_falls_back():
    assert FeishuConfig(env={"PORT": "not-a-number"}).PORT == DEFAULT_PORT
    assert FeishuConfig(env={"PORT": "8080"}).PORT == 8080


def test_mode_is_normalised_and_switchable():
    assert FeishuConfig(env={"FEISHU_MODE": " WebHook "}).FEISHU_MODE == MODE_WEBHOOK
    assert FeishuConfig(env={"FEISHU_MODE": "webhook"}).is_webhook_mode() is True
    assert FeishuConfig(env={"FEISHU_MODE": "ws"}).is_webhook_mode() is False


def test_lark_international_domain_can_be_selected():
    cfg = FeishuConfig(env={"FEISHU_DOMAIN": "https://open.larksuite.com"})
    assert cfg.FEISHU_DOMAIN == "https://open.larksuite.com"


def test_overrides_win_over_the_environment():
    cfg = FeishuConfig(env={"FEISHU_APP_ID": "cli_env"}, FEISHU_APP_ID="cli_override")
    assert cfg.FEISHU_APP_ID == "cli_override"


def test_agent_client_uses_config_and_includes_optional_team_token():
    cfg = FeishuConfig(
        env={
            "SRE_AGENT_URL": "http://agent:8000",
            "INVESTIGATE_AUTH_TOKEN": "investigate-token",
            "OPENSRE_TEAM_TOKEN": "team-token",
        }
    )
    client = cfg.agent_client()
    assert client.base_url == "http://agent:8000"
    headers = client.headers()
    assert headers["Authorization"] == "Bearer investigate-token"
    assert headers["X-OpenSRE-Team-Token"] == "team-token"


def test_agent_client_omits_absent_tokens():
    client = FeishuConfig(env={}).agent_client()
    assert client.headers() == {}


def test_main_exits_cleanly_when_credentials_are_missing(monkeypatch):
    """Mirrors teams-bot/app.py: no credentials must not be a crash loop."""
    import app

    monkeypatch.setattr(app, "FeishuConfig", lambda: FeishuConfig(env={}))
    with pytest.raises(SystemExit) as excinfo:
        app.main()
    assert excinfo.value.code == 0

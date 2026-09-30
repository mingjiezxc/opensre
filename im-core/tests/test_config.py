"""Tests for the shared IM config base."""

import importlib
import os

import opensre_im_core.config as config_mod


def _reload(monkeypatch, **env):
    """Reload the module with a controlled environment.

    load_dotenv uses override=False, so values already in os.environ win; the
    parent repo .env cannot repopulate keys we blank out here.
    """
    for key in (
        "SRE_AGENT_URL",
        "INVESTIGATE_AUTH_TOKEN",
        "WEB_UI_PUBLIC_BASE_URL",
        "OPENSRE_TEAM_TOKEN",
    ):
        monkeypatch.setenv(key, env.get(key, ""))
    return importlib.reload(config_mod)


def test_defaults_when_env_is_blank(monkeypatch):
    mod = _reload(monkeypatch)
    cfg = mod.BaseIMConfig()
    assert cfg.SRE_AGENT_URL == "http://localhost:8000"
    assert cfg.INVESTIGATE_AUTH_TOKEN == ""
    assert cfg.WEB_UI_PUBLIC_BASE_URL == ""
    assert cfg.OPENSRE_TEAM_TOKEN == ""


def test_reads_configured_values(monkeypatch):
    mod = _reload(
        monkeypatch,
        SRE_AGENT_URL="http://sre-agent:8000",
        INVESTIGATE_AUTH_TOKEN="tok",
        WEB_UI_PUBLIC_BASE_URL="https://opensre.example.com",
        OPENSRE_TEAM_TOKEN="team-tok",
    )
    cfg = mod.BaseIMConfig()
    assert cfg.SRE_AGENT_URL == "http://sre-agent:8000"
    assert cfg.WEB_UI_PUBLIC_BASE_URL == "https://opensre.example.com"
    assert cfg.agent_headers() == {
        "Authorization": "Bearer tok",
        "X-OpenSRE-Team-Token": "team-tok",
    }
    assert cfg.agent_headers(accept_sse=True)["Accept"] == "text/event-stream"


def test_agent_headers_empty_without_tokens(monkeypatch):
    mod = _reload(monkeypatch)
    cfg = mod.BaseIMConfig()
    assert cfg.agent_headers() == {}
    assert cfg.agent_headers(accept_sse=True) == {"Accept": "text/event-stream"}


def test_is_configured_requires_all_values():
    assert config_mod.is_configured("a", "b") is True
    assert config_mod.is_configured("a", "") is False
    assert config_mod.is_configured("") is False
    assert config_mod.is_configured() is True  # vacuously true, no credentials needed


def teardown_module(module):
    # Restore the module to whatever the real environment provides.
    importlib.reload(config_mod)
    assert os is not None  # keep the import meaningful for linters

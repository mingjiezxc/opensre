# feishu-bot/config.py
"""Environment-backed configuration for the Feishu / Lark bot.

Only the platform-specific settings live here; everything shared with the other
OpenSRE chat bots (sre-agent URL, tokens, console base URL) is inherited from
:class:`opensre_im_core.config.BaseIMConfig`.

Unlike the frozen base class, values are resolved in ``__init__`` from an
injectable mapping instead of being snapshotted at import time. Two reasons:

* tests get a deterministic config (``FeishuConfig(env={...})``) without mutating
  the process environment or reloading the module;
* ``load_dotenv()`` walks up from the working directory, so an import-time
  snapshot could silently pick up a developer's repo-root ``.env``.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

from dotenv import load_dotenv
from opensre_im_core.config import BaseIMConfig, is_configured

load_dotenv()

DEFAULT_PORT = 3979
DEFAULT_BOT_NAME = "OpenSRE"
# Both domains are first-class; the international app uses larksuite.com.
DEFAULT_DOMAIN = "https://open.feishu.cn"
LARK_DOMAIN = "https://open.larksuite.com"

MODE_WS = "ws"
MODE_WEBHOOK = "webhook"

# Mirrors BaseIMConfig's defaults, applied to the injected mapping so a caller
# that passes ``env=`` gets that mapping honoured end to end.
DEFAULT_SRE_AGENT_URL = "http://localhost:8000"


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    """``PORT`` as an int, falling back to ``default`` on blank/garbage input."""
    raw = str(env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class FeishuConfig(BaseIMConfig):
    """Feishu app credentials plus the shared OpenSRE settings."""

    def __init__(
        self, env: Optional[Mapping[str, str]] = None, **overrides
    ) -> None:
        src: Mapping[str, str] = os.environ if env is None else env

        self.PORT = _int_env(src, "PORT", DEFAULT_PORT)

        # Credentials are taken verbatim — never stripped, so a secret that
        # happens to end in whitespace still works.
        self.FEISHU_APP_ID = src.get("FEISHU_APP_ID") or ""
        self.FEISHU_APP_SECRET = src.get("FEISHU_APP_SECRET") or ""
        self.FEISHU_VERIFICATION_TOKEN = src.get("FEISHU_VERIFICATION_TOKEN") or ""
        self.FEISHU_ENCRYPT_KEY = src.get("FEISHU_ENCRYPT_KEY") or ""
        self.FEISHU_MODE = (src.get("FEISHU_MODE") or MODE_WS).strip().lower()
        self.FEISHU_BOT_NAME = src.get("FEISHU_BOT_NAME") or DEFAULT_BOT_NAME
        self.FEISHU_DOMAIN = (src.get("FEISHU_DOMAIN") or "").strip() or DEFAULT_DOMAIN
        # Optional override for the startup GET /open-apis/bot/v3/info lookup:
        # tests pin it so @mention detection needs no network, and deployments
        # may pin it to skip the round trip.
        self.FEISHU_BOT_OPEN_ID = src.get("FEISHU_BOT_OPEN_ID") or ""

        self.SRE_AGENT_URL = src.get("SRE_AGENT_URL") or DEFAULT_SRE_AGENT_URL
        self.INVESTIGATE_AUTH_TOKEN = src.get("INVESTIGATE_AUTH_TOKEN") or ""
        self.WEB_UI_PUBLIC_BASE_URL = src.get("WEB_UI_PUBLIC_BASE_URL") or ""
        self.OPENSRE_TEAM_TOKEN = src.get("OPENSRE_TEAM_TOKEN") or ""

        for key, value in overrides.items():
            setattr(self, key, value)

    def is_configured(self) -> bool:
        """A usable bot needs an app id and secret; nothing else is required."""
        return is_configured(self.FEISHU_APP_ID, self.FEISHU_APP_SECRET)

    def is_webhook_mode(self) -> bool:
        return self.FEISHU_MODE == MODE_WEBHOOK

    def agent_client(self):
        """An :class:`AgentClient` wired to sre-agent from this config."""
        from opensre_im_core.agent_client import AgentClient

        return AgentClient(
            self.SRE_AGENT_URL,
            auth_token=self.INVESTIGATE_AUTH_TOKEN,
            team_token=self.OPENSRE_TEAM_TOKEN,
        )

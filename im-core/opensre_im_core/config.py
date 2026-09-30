"""Environment-backed configuration shared by all OpenSRE IM bots.

The platform-specific bits (Teams credentials, Feishu app id/secret) stay in
each bot's own ``config.py``; only the sre-agent/console settings live here.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class BaseIMConfig:
    """Common settings every IM bot needs to reach OpenSRE.

    Values use ``os.environ.get(...) or default`` rather than a ``get`` default
    argument: a key that is present but blank (``SRE_AGENT_URL=`` in a .env file)
    would otherwise yield ``""`` and silently disable the default.
    """

    SRE_AGENT_URL = os.environ.get("SRE_AGENT_URL") or "http://localhost:8000"
    INVESTIGATE_AUTH_TOKEN = os.environ.get("INVESTIGATE_AUTH_TOKEN") or ""
    WEB_UI_PUBLIC_BASE_URL = os.environ.get("WEB_UI_PUBLIC_BASE_URL") or ""
    # Optional per-team token. When set, it is sent as X-OpenSRE-Team-Token so
    # sre-agent can resolve real org/team identity instead of DEFAULT_TENANT_ID
    # / DEFAULT_TEAM_ID — without it every bot-triggered run is attributed to
    # "local"/"default".
    OPENSRE_TEAM_TOKEN = os.environ.get("OPENSRE_TEAM_TOKEN") or ""

    def agent_headers(self, *, accept_sse: bool = False) -> dict[str, str]:
        """Auth headers for sre-agent. Empty when no token is configured."""
        headers: dict[str, str] = {}
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        if self.INVESTIGATE_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {self.INVESTIGATE_AUTH_TOKEN}"
        if self.OPENSRE_TEAM_TOKEN:
            headers["X-OpenSRE-Team-Token"] = self.OPENSRE_TEAM_TOKEN
        return headers


def is_configured(*values: str) -> bool:
    """True when every supplied credential is non-empty (all are required)."""
    return all(bool(v) for v in values)

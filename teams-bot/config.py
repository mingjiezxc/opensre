# teams-bot/config.py
"""Environment-backed configuration for the Teams bot.

The sre-agent/console settings (``SRE_AGENT_URL``, ``INVESTIGATE_AUTH_TOKEN``,
``WEB_UI_PUBLIC_BASE_URL``, ``OPENSRE_TEAM_TOKEN``) live in the shared
:class:`opensre_im_core.config.BaseIMConfig`; this module only adds the Teams
credentials and maps them onto the Teams SDK's ``CLIENT_*`` env names.
"""

import os

from dotenv import load_dotenv
from opensre_im_core.config import BaseIMConfig

load_dotenv()


def _export_sdk_env(app_id: str, password: str, tenant_id: str) -> None:
    """Map OpenSRE TEAMS_* names to Teams SDK CLIENT_* env vars."""
    if app_id:
        os.environ.setdefault("CLIENT_ID", app_id)
    if password:
        os.environ.setdefault("CLIENT_SECRET", password)
    if tenant_id:
        os.environ.setdefault("TENANT_ID", tenant_id)


class Config(BaseIMConfig):
    PORT = int(os.environ.get("PORT", "3978"))

    TEAMS_APP_ID = os.environ.get("TEAMS_APP_ID", "")
    TEAMS_APP_PASSWORD = os.environ.get("TEAMS_APP_PASSWORD", "")
    TEAMS_TENANT_ID = os.environ.get("TEAMS_TENANT_ID", "")

    def __init__(self) -> None:
        _export_sdk_env(
            self.TEAMS_APP_ID, self.TEAMS_APP_PASSWORD, self.TEAMS_TENANT_ID
        )

    def is_configured(self) -> bool:
        return bool(
            self.TEAMS_APP_ID and self.TEAMS_APP_PASSWORD and self.TEAMS_TENANT_ID
        )

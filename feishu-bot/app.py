# feishu-bot/app.py
"""Entrypoint for the OpenSRE Feishu / Lark bot.

Two delivery modes, selected by ``FEISHU_MODE``:

* ``ws`` (default) — the official long-connection client. No public URL, no
  tunnel; ideal for local development and self-hosted installs. Note that
  ``lark.ws.Client.start()`` runs its own event loop and blocks, so this mode is
  driven from the main thread rather than from ``asyncio.run``.
* ``webhook`` — an aiohttp server exposing ``/webhook/event`` and
  ``/webhook/card`` for Feishu's request-URL delivery.

With no credentials the process logs a warning and exits 0, mirroring
``teams-bot/app.py`` so a missing .env never turns into a crash loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiohttp import web
from lark_oapi.core.enum import LogLevel
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws import Client as WsClient

from bot_handlers import FeishuRuntime, register_handlers
from config import FeishuConfig
from lark_client import LarkClient
from webhook_server import EVENT_PATH, build_app, make_dispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def build_runtime(cfg: FeishuConfig) -> FeishuRuntime:
    """Create the long-lived collaborators for one bot process."""
    return FeishuRuntime(
        lark=LarkClient(
            app_id=cfg.FEISHU_APP_ID,
            app_secret=cfg.FEISHU_APP_SECRET,
            domain=cfg.FEISHU_DOMAIN,
            log_level=LogLevel.INFO,
        ),
        agent_client=cfg.agent_client(),
        bot_open_id=cfg.FEISHU_BOT_OPEN_ID,
        bot_name=cfg.FEISHU_BOT_NAME,
        web_ui_base_url=cfg.WEB_UI_PUBLIC_BASE_URL,
    )


def resolve_bot_open_id(lark: LarkClient) -> str:
    """Resolve the bot's open_id at startup; "" when it cannot be fetched.

    @mention detection compares against this id, and it is deliberately not
    hardcoded — the same source runs against several tenants.
    """
    try:
        info = asyncio.run(lark.get_bot_info())
    except Exception:
        logger.exception("feishu bot identity lookup failed")
        return ""
    if info is None:
        logger.warning(
            "could not resolve the bot open_id — group @mentions will be ignored"
        )
        return ""
    logger.info("feishu bot identity: %s (%s)", info.name or "?", info.open_id)
    return info.open_id


def build_dispatcher(cfg: FeishuConfig, runtime: FeishuRuntime) -> EventDispatcherHandler:
    builder = EventDispatcherHandler.builder(
        cfg.FEISHU_ENCRYPT_KEY, cfg.FEISHU_VERIFICATION_TOKEN
    )
    register_handlers(builder, runtime=runtime)
    return builder.build()


def run_ws(cfg: FeishuConfig, runtime: FeishuRuntime) -> None:
    """Blocking long-connection mode."""
    dispatcher = build_dispatcher(cfg, runtime)
    client = WsClient(
        cfg.FEISHU_APP_ID,
        cfg.FEISHU_APP_SECRET,
        event_handler=dispatcher,
        log_level=LogLevel.INFO,
        domain=cfg.FEISHU_DOMAIN,
    )
    logger.info("Starting OpenSRE feishu-bot (long connection)")
    client.start()


async def run_webhook(cfg: FeishuConfig, runtime: FeishuRuntime) -> None:
    """Request-URL mode: serve until cancelled."""
    dispatcher = build_dispatcher(cfg, runtime)
    app = build_app(
        dispatcher=make_dispatcher(dispatcher),
        verification_token=cfg.FEISHU_VERIFICATION_TOKEN,
        encrypt_key=cfg.FEISHU_ENCRYPT_KEY,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.PORT)  # noqa: S104 - container bind
    await site.start()
    logger.info(
        "OpenSRE feishu-bot listening on http://0.0.0.0:%s%s", cfg.PORT, EVENT_PATH
    )
    await asyncio.Event().wait()


def run(cfg: FeishuConfig) -> None:
    runtime = build_runtime(cfg)
    if not runtime.bot_open_id:
        runtime.bot_open_id = resolve_bot_open_id(runtime.lark)
    if cfg.is_webhook_mode():
        asyncio.run(run_webhook(cfg, runtime))
    else:
        run_ws(cfg, runtime)


def main() -> None:
    cfg = FeishuConfig()
    if not cfg.is_configured():
        logger.warning(
            "FEISHU_APP_ID / FEISHU_APP_SECRET not fully set — feishu-bot exiting cleanly"
        )
        sys.exit(0)
    run(cfg)


if __name__ == "__main__":
    main()

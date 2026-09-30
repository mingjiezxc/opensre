"""Platform-agnostic IM core for OpenSRE chat integrations.

Extracted from ``teams-bot`` (Microsoft Teams) so that additional platforms —
currently Feishu/Lark — can reuse one implementation of the SSE → progress →
question-card pipeline instead of copying it a third time.

Nothing here imports a platform SDK. Each platform supplies a
:class:`~opensre_im_core.investigation_runner.PlatformHooks` implementation
that renders cards, sends text, and owns its own message identity.
"""

__all__ = [
    "agent_client",
    "config",
    "investigation_runner",
    "markdown_parser",
    "models",
    "progress_text",
    "run_links",
    "stream_handler",
    "tool_display",
]

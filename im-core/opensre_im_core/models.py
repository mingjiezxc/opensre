"""Investigation state for IM integrations.

Migrated verbatim from ``teams-bot/state.py`` (whose docstring traced it back to
Slack's ``MessageState``) — this is the third consumer of the same shape in the
Python tree, which is why it now lives in the shared core.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    name: str
    tool_use_id: Optional[str] = None
    input: dict = field(default_factory=dict)
    running: bool = True
    success: Optional[bool] = None
    summary: Optional[str] = None


@dataclass
class ThoughtSection:
    text: str
    completed: bool = False
    tools: list = field(default_factory=list)  # list[ToolCall]


@dataclass
class InvestigationState:
    thread_id: str
    run_id: Optional[str] = None
    thoughts: list = field(default_factory=list)
    current_tool: Optional[ToolCall] = None
    final_result: Optional[str] = None
    error: Optional[str] = None
    background_waiting_label: Optional[str] = None
    pending_background_count: int = 0
    background_notification: Optional[str] = None
    # Server-side count of messages queued mid-run (SSE "message_queued").
    pending_queued_messages: int = 0

    def current_thought_text(self) -> str:
        return self.thoughts[-1].text if self.thoughts else ""


def new_pending_questions() -> dict[str, list[dict[str, Any]]]:
    """thread_id -> question list from the SSE "question" event."""
    return {}


def new_question_card_ids() -> dict[str, str]:
    """thread_id -> platform message/card id (for in-place timeout updates)."""
    return {}


def new_active_investigations() -> set[str]:
    """thread_ids with an in-flight run (SSE stream still open)."""
    return set()


# Module-level defaults, mirroring teams-bot/state.py. Platforms that need
# isolation (notably tests) should build their own via the factories above.
pending_questions: dict[str, list[dict[str, Any]]] = {}
question_card_ids: dict[str, str] = {}
active_investigations: set[str] = set()

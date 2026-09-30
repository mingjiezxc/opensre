"""Teams re-export of the shared tool-display helpers (``opensre_im_core``).

Humanizing tool calls and nesting Bash under the latest Skill is shared display
logic (it mirrors ``web_ui/src/lib/toolDisplay.ts``), so it lives in the core.
Kept as a named re-export so ``from tool_display import ...`` keeps resolving
inside the Teams package.
"""

from opensre_im_core.tool_display import (
    PLAN_TOOLS,
    SKILL_SCRIPT_RE,
    DisplayTool,
    humanize_tool_summary,
    is_plan_tool,
    nest_bash_under_skills,
)

__all__ = [
    "PLAN_TOOLS",
    "SKILL_SCRIPT_RE",
    "DisplayTool",
    "humanize_tool_summary",
    "is_plan_tool",
    "nest_bash_under_skills",
]

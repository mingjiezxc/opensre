"""Teams view of the shared investigation state (``opensre_im_core.models``).

The state machine moved into the platform-agnostic core; this module is a thin
re-export so every Teams import path (``from state import ...``) keeps working.

``question_activity_ids`` is the Teams name for the core's ``question_card_ids``
(a Teams question card is addressed by its Bot Framework *activity* id). It is
the **same mapping object**, not a copy, so the shared runner's timeout lookup
and the Teams card-action handler always see one another's writes.
"""

from opensre_im_core.models import (
    InvestigationState,
    ThoughtSection,
    ToolCall,
    active_investigations,
    pending_questions,
    question_card_ids,
)

question_activity_ids = question_card_ids

__all__ = [
    "InvestigationState",
    "ThoughtSection",
    "ToolCall",
    "active_investigations",
    "pending_questions",
    "question_activity_ids",
]

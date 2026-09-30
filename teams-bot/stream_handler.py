"""Teams re-export of the shared SSE-event handler (``opensre_im_core``).

The event→state reducer no longer lives in the Teams bot; it is platform-neutral
and shared with every other IM integration. Kept as a named re-export so
``from stream_handler import ...`` keeps resolving inside the Teams package.
"""

from opensre_im_core.stream_handler import (
    StreamStepResult,
    handle_stream_event,
    parse_sse_event,
)

__all__ = [
    "StreamStepResult",
    "handle_stream_event",
    "parse_sse_event",
]

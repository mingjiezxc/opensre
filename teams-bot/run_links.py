"""Teams re-export of the shared OpenSRE run links (``opensre_im_core``).

The ``View in OpenSRE`` label and the ``/team/agent-runs/<id>`` URL shape are
decided strings shared by every IM integration. Kept as a named re-export so
``from run_links import ...`` keeps resolving inside the Teams package.
"""

from opensre_im_core.run_links import (
    VIEW_LINK_LABEL,
    build_agent_run_url,
    format_view_link_markdown,
)

__all__ = [
    "VIEW_LINK_LABEL",
    "build_agent_run_url",
    "format_view_link_markdown",
]

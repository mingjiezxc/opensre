# teams-bot/progress_text.py
"""Teams re-export of the shared progress renderer (``opensre_im_core``).

The text itself is platform-neutral ("OpenSRE — Investigating…" + humanized
tool rows); the Teams bot only feeds it to ``ctx.stream``/a channel PUT. Kept as
a named re-export so ``from progress_text import build_progress_text`` keeps
resolving inside the Teams package.
"""

from opensre_im_core.progress_text import build_progress_text

__all__ = ["build_progress_text"]

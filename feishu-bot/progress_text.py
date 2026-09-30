# feishu-bot/progress_text.py
"""Live progress rendering — re-exported from ``opensre-im-core``.

Deliberately a re-export and not a copy: thought nesting, tool humanization, the
plan line and the "View in OpenSRE" link are platform-neutral, and keeping a
second implementation here is exactly the drift the shared core exists to
prevent. This module exists so ``feishu-bot`` reads like its siblings.
"""

from opensre_im_core.progress_text import build_progress_text

__all__ = ["build_progress_text"]

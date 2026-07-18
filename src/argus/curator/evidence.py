"""Guardrail on the curator itself: a quote offered as evidence for a drop
has to actually appear somewhere in the context. Without this check, the
curator can kill a real finding just as easily on an unverifiable claim as
a true one — the same failure mode that made the panel's judge pass lower
recall in testing.
"""

from __future__ import annotations

import re

from argus.context.gather import Context


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_appears_in_context(quote: str | None, context: Context) -> bool:
    if not quote or not quote.strip():
        return False

    needle = _normalize(quote)
    if len(needle) < 6:
        # Too short to be meaningful evidence either way.
        return False

    haystacks = [context.diff] + [f.content or "" for f in context.changed_files]
    return any(needle in _normalize(h) for h in haystacks)

"""Deliberate limits on what a lens gets to see.

The project this tool is modelled on found that dumping full files or
"everywhere this symbol is used" context lowered recall: models read bulk
context as reassurance ("this must be handled somewhere") rather than
evidence. So the default here is narrow, and widening it is an explicit
opt-in in config, not a fallback when something looks incomplete.
"""

from __future__ import annotations

import fnmatch
from dataclasses import replace

from argus.config import ContextConfig


def is_ignored(path: str, ignore_globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in ignore_globs)


def truncate_diff_parts(parts: list[str], max_bytes: int) -> tuple[list[str], bool]:
    """Keeps whole per-file diff chunks, in order, up to max_bytes total --
    drops whatever doesn't fit rather than cutting a hunk mid-line, since a
    half-hunk is worse than no hunk (a lens can't tell truncated context from
    a real change).

    Exists as a hard ceiling independent of _max_input_tokens's per-model
    budget check in models/client.py, which silently no-ops for any model
    litellm doesn't have pricing/context-window metadata for (a custom or
    very new provider model string) -- see #78: that combination, on a
    266-file/14,644-line merge-commit PR, produced a completely uncapped
    prompt and hung a review for 20+ minutes until someone gave up and
    cancelled it by hand. This runs unconditionally at gather time, before
    any model is even chosen, so it protects the pathological case
    regardless of which model ends up reviewing the PR."""
    kept: list[str] = []
    total = 0
    for part in parts:
        size = len(part.encode("utf-8"))
        if kept and total + size > max_bytes:
            return kept, True
        kept.append(part)
        total += size
    return kept, False


def apply_budget(files: list, config: ContextConfig) -> list:
    """Filters generated/lock files, truncates oversized content, and caps
    the number of files a lens will ever see."""
    kept = []
    for f in files:
        if is_ignored(f.path, config.ignore_globs):
            continue
        content = f.content
        truncated = False
        if (
            content is not None
            and len(content.encode("utf-8", "ignore")) > config.max_bytes_per_file
        ):
            content = content.encode("utf-8", "ignore")[: config.max_bytes_per_file].decode(
                "utf-8", "ignore"
            )
            truncated = True
        kept.append(replace(f, content=content, truncated=truncated))

    return kept[: config.max_files]

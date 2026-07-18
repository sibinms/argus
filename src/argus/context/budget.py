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

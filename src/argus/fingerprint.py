"""Stable identity for a finding across runs — shared between posting
(dedup, thread resolution) and the curator (matching a reply back to the
finding it was left on)."""

from __future__ import annotations

import hashlib
import re

from argus.lenses.base import Finding


def normalize_summary(summary: str) -> str:
    """Collapse a summary to a stable key: lowercase, drop punctuation and
    digits, squeeze whitespace. Keeps the fingerprint steady across small
    wording changes in the model's output."""
    text = re.sub(r"[^a-z ]+", " ", summary.lower())
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(f: Finding) -> str:
    """Deliberately excludes the line number (it drifts as the PR gains
    commits) and normalizes the summary (the model rewords it run to run),
    so the same underlying issue keeps the same fingerprint and is posted
    at most once."""
    key = f"{f.file}|{normalize_summary(f.summary)}"
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

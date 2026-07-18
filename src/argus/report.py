"""Turns curated findings into the two shapes everything else needs: a
human-readable markdown report (shadow mode, PR summary, job summary) and
the subset of findings that qualify for inline posting.
"""

from __future__ import annotations

from argus.config import PostingConfig
from argus.lenses.base import Finding

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def postable_findings(findings: list[Finding], posting: PostingConfig) -> list[Finding]:
    threshold = _CONFIDENCE_RANK.get(posting.min_confidence, 1)
    return [
        f
        for f in findings
        if f.status in ("kept", "downgraded") and _CONFIDENCE_RANK.get(f.confidence, 0) >= threshold
    ]


def verdict(findings: list[Finding], posting: PostingConfig) -> str:
    postable = postable_findings(findings, posting)
    if not postable:
        return "approve"
    if any(f.confidence == "high" for f in postable):
        return "request_changes"
    return "comment"


def render_markdown(findings: list[Finding], posting: PostingConfig) -> str:
    postable = postable_findings(findings, posting)
    dropped = [f for f in findings if f.status == "dropped"]

    lines = ["# Argus review", ""]

    if not postable:
        lines.append("No findings above the configured confidence threshold. Looks good.")
    else:
        lines.append(f"{len(postable)} finding(s):\n")
        for f in postable:
            location = f"`{f.file}:{f.line}`" if f.file else "(general)"
            lines.append(f"### {location} — {f.summary}")
            lines.append(f"*lens: {f.lens} · confidence: {f.confidence}*")
            lines.append("")
            lines.append(f.detail or "")
            lines.append("")

    if posting.show_dropped_reasoning and dropped:
        lines.append("<details>")
        lines.append(f"<summary>{len(dropped)} suspicion(s) the curator dropped, and why</summary>")
        lines.append("")
        for f in dropped:
            location = f"`{f.file}:{f.line}`" if f.file else "(general)"
            lines.append(f"- {location} — {f.summary} ({f.lens}): {f.drop_reason}")
        lines.append("")
        lines.append("</details>")

    return "\n".join(lines)

"""Orchestrates curation: dedupe near-identical findings from different
lenses, ask the curator model to decide keep/drop_noise/drop/downgrade for
each, then enforce the drop rules.

Two kinds of drop, deliberately treated differently:

- "drop_noise" — the finding never asserted a real problem (pure narration of
  a change) or its impact claim is mis-scoped. This is a judgement about the
  finding's own text, so no code quote is required.
- "drop" — the finding asserts a real problem the curator claims is factually
  wrong. This disputes the code, so it must cite a quote that actually appears
  in the diff/files; if it can't, we refuse the drop and keep the finding
  (downgraded) rather than trusting the model's say-so.
"""

from __future__ import annotations

from argus.context.gather import Context
from argus.curator.evidence import quote_appears_in_context
from argus.lenses.base import Finding
from argus.models.client import curate_with_model

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _is_duplicate(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if a.line is not None and b.line is not None and abs(a.line - b.line) > 3:
        return False
    a_words = set(_normalize_words(a.summary))
    b_words = set(_normalize_words(b.summary))
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap > 0.6


def _normalize_words(text: str) -> list[str]:
    return [w.lower() for w in text.split() if len(w) > 3]


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Merges findings from different lenses that describe the same problem,
    keeping the highest-confidence version and recording every lens that
    raised it."""
    merged: list[Finding] = []
    for f in findings:
        match = next((m for m in merged if _is_duplicate(m, f)), None)
        if match is None:
            merged.append(f)
            continue
        if _CONFIDENCE_RANK.get(f.confidence, 0) > _CONFIDENCE_RANK.get(match.confidence, 0):
            match.confidence = f.confidence
            match.detail = f.detail
        if f.lens not in match.lens:
            match.lens = f"{match.lens}+{f.lens}"
    return merged


def curate(findings: list[Finding], context: Context, model: str) -> list[Finding]:
    deduped = dedupe(findings)
    decisions = curate_with_model(deduped, context, model)

    curated: list[Finding] = []
    for finding, decision in zip(deduped, decisions):
        action = decision.get("action", "keep")
        reason = decision.get("reason", "")
        evidence_quote = decision.get("evidence_quote")

        if action == "drop_noise":
            # Not a claim about the code (narration / mis-scoped impact), so no
            # quote is required — the finding's own wording is the grounds.
            finding.status = "dropped"
            finding.drop_reason = reason
        elif action == "drop":
            if quote_appears_in_context(evidence_quote, context):
                finding.status = "dropped"
                finding.drop_reason = reason
            else:
                # Curator couldn't back its own claim with real text — refuse
                # the drop and keep the finding, downgraded, with a note.
                finding.status = "downgraded"
                finding.confidence = "low"
                finding.drop_reason = (
                    f"curator tried to drop this but its cited evidence wasn't "
                    f"found in the diff/files, so it was kept at low confidence "
                    f"(original curator reason: {reason})"
                )
        elif action == "downgrade":
            finding.status = "downgraded"
            finding.confidence = decision.get("confidence", "low")
            finding.drop_reason = reason
        else:
            finding.status = "kept"
            finding.confidence = decision.get("confidence", finding.confidence)

        curated.append(finding)

    return curated

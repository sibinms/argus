"""Thin wrapper around whichever LLM provider is configured, for the two
model roles: running a lens (propose findings) and running the curator
(verify or drop them).

Uses litellm so any provider it supports works just by changing the model
string in .argus/config.yml — "claude-haiku-4-5", "gpt-4o-mini",
"gemini/gemini-1.5-flash", and so on. Argus has no provider-specific code;
whichever provider a model string points at, set that provider's own API
key as an environment variable (ANTHROPIC_API_KEY, OPENAI_API_KEY,
GEMINI_API_KEY, ...) — see https://docs.litellm.ai/docs/providers for the
full list. Lens and curator can each point at a different provider.

Split deliberately: lenses are meant to run on a cheap model asked to
over-report, the curator on a stronger model asked to only kill a finding
when it can point at contradicting evidence. Which model plays which role
is set in .argus/config.yml, not hardcoded here.
"""

from __future__ import annotations

import json

from litellm import completion

from argus.context.gather import Context
from argus.lenses.base import Finding, Lens


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Models sometimes wrap the array in a sentence ("Here are the
        # findings: [...]"). Salvage the outermost JSON array rather than
        # dropping the whole lens's output.
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


PLANNER_SYSTEM_PROMPT = """\
You are writing a one-page technical brief for a panel of five independent code \
reviewers. Each reviewer specialises in a single narrow angle (security, tests, \
error handling, contracts, correctness) and shares no notes with the others, so \
this brief is the only shared context they have.

Read the pull request below and produce a brief with exactly these three sections:

## Intent
One sentence: what is this PR trying to accomplish?

## Key invariants
Bullet list (3–5 items): what must stay true after this change? Name specific \
fields, functions, data flows, or external contracts. Be concrete — \
"sequence counter must equal the maximum assigned ID, not the count of unnumbered \
rows" rather than "IDs must be correct". Include business-logic invariants, not \
just code-level ones.

## What to verify
Bullet list (3–5 items): specific, named behaviours a reviewer should check, \
framed as yes/no questions answerable from the diff and files. Example: \
"Does the backfill seed the counter from max(existing reference_id)+1, or only \
from the count of newly numbered rows?" Prefer questions that catch the most \
common mistake for this type of change.

Additionally: if the diff adds any string literals used as dictionary keys in \
`.get()`, `__getitem__`, or similar lookups, list each one verbatim and \
ask: is this key spelled correctly? A single transposed or duplicated \
character (e.g. "memebers" instead of "members") causes `.get()` to \
silently return 0 or None on every call — the bug never raises, so it \
can survive in production undetected for months.

Keep the whole brief under 300 words. The reviewers will do their own reading; \
your job is to point their attention at what matters most.\
"""


def generate_pr_summary(context: Context, model: str) -> str:
    """Runs the planner once before lenses fire. Returns a brief that is
    injected into every lens's context so each reviewer knows what the PR
    is trying to do and what invariants to verify."""
    parts = []
    if context.pr_title:
        parts.append(f"# PR title\n{context.pr_title}")
    if context.pr_body:
        parts.append(f"# PR description\n{context.pr_body}")
    parts.append(f"# Diff\n```diff\n{context.diff}\n```")
    user_prompt = "\n\n".join(parts)
    try:
        # Gemini 2.5 Flash uses thinking tokens that count against max_tokens.
        # A brief with ~300 words needs ~400 real tokens; thinking easily uses
        # 3000+, so we need a generous budget here or the response is truncated.
        return _complete(PLANNER_SYSTEM_PROMPT, user_prompt, model, max_tokens=8192)
    except Exception:
        return ""


def _context_prompt(context: Context) -> str:
    parts = []
    if context.pr_title:
        parts.append(f"# PR title\n{context.pr_title}")
    if context.pr_body:
        parts.append(f"# PR description\n{context.pr_body}")
    if context.pr_summary:
        parts.append(f"# Review brief\n{context.pr_summary}")

    parts.append(f"# Diff\n```diff\n{context.diff}\n```")

    for f in context.changed_files:
        if f.content is None:
            continue
        note = " (truncated)" if f.truncated else ""
        parts.append(f"# File: {f.path}{note}\n```\n{f.content}\n```")

    return "\n\n".join(parts)


def _complete(system_prompt: str, user_prompt: str, model: str, max_tokens: int) -> str:
    response = completion(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=120,  # never let a stalled provider hang the whole review
    )
    return response.choices[0].message.content or ""


def run_lens(lens: Lens, context: Context, model: str, max_tokens: int = 4096) -> list[Finding]:
    text = _complete(lens.system_prompt(), _context_prompt(context), model, max_tokens)

    try:
        raw_findings = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        return []

    findings = []
    for item in raw_findings:
        if not isinstance(item, dict) or "summary" not in item:
            continue
        findings.append(
            Finding(
                lens=lens.name,
                file=item.get("file"),
                line=item.get("line"),
                summary=item["summary"],
                detail=item.get("detail", ""),
                confidence=item.get("confidence", "low"),
                quote=item.get("quote"),
            )
        )
    return findings


CURATOR_SYSTEM_PROMPT = """You are the curator for a panel of code review lenses. \
Each lens proposed findings independently and was told to over-report — expect \
noise, near-duplicates, wrong guesses, and findings that merely describe a \
change without naming a real problem.

Your job: let through only findings a busy engineer would be glad to get on \
their PR, and remove the rest with a defensible reason.

For each finding choose exactly one action:
- "keep": a real, correctly-scoped problem. Set confidence (low|medium|high) \
to how sure you are it is genuine and worth acting on.
- "drop_noise": the finding does not actually assert a problem — it only \
describes or restates what the change does ("added a timeout", "logic \
changed", "renamed X"), OR its stated impact is plainly wrong (e.g. it claims \
external consumers break, but the changed file is internal to this repo and \
ships to no one). No quote is required; justify it in one sentence by naming \
what the finding fails to assert, or why its impact claim doesn't hold.
- "drop": the finding asserts a real problem, but it is factually wrong and \
you can prove it with a specific quote from the diff or files (e.g. it worries \
about a missing check that a quoted line actually performs). You MUST put that \
quote in evidence_quote.
- "downgrade": you are not sure it is wrong, but not confident it is right. \
Keep it at low confidence rather than deleting it.

Rules:
- Never use "drop" (factual) without a real quote. "I doubt it" is not grounds; \
use "downgrade".
- Prefer "drop_noise" for pure narration and mis-scoped impact; reserve "drop" \
for a finding that makes a real but disprovable claim.
- Judge blast radius honestly. A change to a repo's own config, CI, workflow, \
tests, or private helpers does not break external consumers. The public \
surface is exported code, API/response shapes, CLI flags, shipped config \
defaults, action inputs, and migrations — nothing else.
- Merge near-duplicates: keep the clearest one, drop_noise the rest.
- Pre-existing issues are out of scope. If a finding describes a problem in \
code that was NOT added or changed by this PR (i.e. the quoted line does not \
appear as a `+` line in the diff), drop_noise it — the PR author cannot fix \
what they didn't touch, and the finding has nothing to do with this change.

Respond with JSON only: a list of objects, one per input finding in the same \
order, with keys: action (keep|drop_noise|drop|downgrade), confidence \
(low|medium|high, your revised confidence if kept/downgraded), reason (one \
sentence), and evidence_quote (a real quote justifying a "drop", or null)."""


def curate_with_model(
    findings: list[Finding], context: Context, model: str, max_tokens: int = 4096
) -> list[dict]:
    if not findings:
        return []

    findings_payload = [
        {
            "index": i,
            "lens": f.lens,
            "file": f.file,
            "line": f.line,
            "summary": f.summary,
            "detail": f.detail,
            "confidence": f.confidence,
            "quote": f.quote,
        }
        for i, f in enumerate(findings)
    ]

    user_prompt = (
        _context_prompt(context)
        + "\n\n# Findings to curate\n```json\n"
        + json.dumps(findings_payload, indent=2)
        + "\n```"
    )

    text = _complete(CURATOR_SYSTEM_PROMPT, user_prompt, model, max_tokens)

    try:
        decisions = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        # If the curator's own output is unparseable, keep everything rather
        # than silently dropping findings we can't account for.
        return [
            {
                "action": "keep",
                "confidence": f.confidence,
                "reason": "curator output unparseable",
                "evidence_quote": None,
            }
            for f in findings
        ]

    if len(decisions) != len(findings):
        return [
            {
                "action": "keep",
                "confidence": f.confidence,
                "reason": "curator returned mismatched count",
                "evidence_quote": None,
            }
            for f in findings
        ]

    return decisions

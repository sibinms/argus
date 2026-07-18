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
    return json.loads(text.strip())


def _context_prompt(context: Context) -> str:
    parts = []
    if context.pr_title:
        parts.append(f"# PR title\n{context.pr_title}")
    if context.pr_body:
        parts.append(f"# PR description\n{context.pr_body}")

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
noise, near-duplicates, and some wrong guesses.

For each finding, decide one of:
- "keep": the finding holds up. Keep its confidence, or raise/lower it if warranted.
- "drop": you have a specific, quotable reason the finding is wrong (e.g. the \
thing it worries about doesn't exist in the diff, or a quoted line elsewhere \
in the context already contradicts it). You must supply that quote.
- "downgrade": you're not sure it's wrong, but you're not confident it's right \
either. Keep it as "low" confidence rather than deleting it.

You may only choose "drop" if you can quote real text from the diff or files \
that contradicts the finding. "I doubt it" or "seems unlikely" is not grounds \
for dropping — use "downgrade" instead.

Respond with JSON only: a list of objects, one per input finding in the same \
order, with keys: action (keep|drop|downgrade), confidence (low|medium|high, \
your revised confidence if kept/downgraded), reason (one sentence), and \
evidence_quote (a real quote justifying a drop, or null)."""


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

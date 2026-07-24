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
import logging

from litellm import completion, get_model_info, token_counter

from argus.context.gather import Context
from argus.lenses.base import Finding, Lens

logger = logging.getLogger(__name__)

# litellm's max_input_tokens is reported by the provider, but token_counter
# falls back to an approximate tokenizer for models it has no exact one for
# (e.g. most non-OpenAI providers), so leave headroom rather than trimming
# right up to the reported limit.
_INPUT_TOKEN_SAFETY_MARGIN = 0.9


def _max_input_tokens(model: str) -> int | None:
    try:
        limit = get_model_info(model).get("max_input_tokens")
    except Exception:
        # Model not in litellm's database (custom/unlisted provider model) —
        # no known limit to trim against, so leave the prompt as built.
        return None
    return int(limit * _INPUT_TOKEN_SAFETY_MARGIN) if limit else None


def _count_tokens(model: str, system_prompt: str, user_prompt: str) -> int | None:
    try:
        return token_counter(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception:
        # Can't verify the size against this model's tokenizer — don't trim
        # blind, since we'd have no way to know when to stop.
        return None


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
You are writing a one-page technical brief for a panel of eight independent code \
reviewers. Each reviewer specialises in a single narrow angle (security, tests, \
error handling, contracts, correctness, deleted-code behaviour, reuse of existing \
helpers, efficiency) and shares no notes with the others, so \
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
        return _complete(PLANNER_SYSTEM_PROMPT, user_prompt, model)
    except Exception:
        # Lenses run fine without a brief, just with less shared context, so
        # this shouldn't fail the whole review — but log it so a planner
        # outage is visible instead of silently degrading review quality.
        logger.warning("planner failed to generate a PR summary", exc_info=True)
        return ""


def _context_prompt(context: Context, model: str, system_prompt: str) -> str:
    # Fixed parts (title/description/brief/diff) always stay — they're the
    # smallest and highest-value part of the prompt. Per-file dumps are the
    # part that can balloon on a large PR, so they're what gets dropped, from
    # the end, if the assembled prompt would exceed this model's real input
    # budget (see PR #2399: a 30-file context overflowed qwen-max's
    # 30720-token limit and crashed the whole review, not just that lens).
    fixed_parts = []
    if context.pr_title:
        fixed_parts.append(f"# PR title\n{context.pr_title}")
    if context.pr_body:
        fixed_parts.append(f"# PR description\n{context.pr_body}")
    if context.pr_summary:
        fixed_parts.append(f"# Review brief\n{context.pr_summary}")
    fixed_parts.append(f"# Diff\n```diff\n{context.diff}\n```")

    file_parts = []
    for f in context.changed_files:
        if f.content is None:
            continue
        note = " (truncated)" if f.truncated else ""
        file_parts.append(f"# File: {f.path}{note}\n```\n{f.content}\n```")

    budget = _max_input_tokens(model)
    if budget is None:
        return "\n\n".join(fixed_parts + file_parts)

    dropped = 0
    while file_parts:
        prompt = "\n\n".join(fixed_parts + file_parts)
        tokens = _count_tokens(model, system_prompt, prompt)
        if tokens is None or tokens <= budget:
            break
        file_parts.pop()
        dropped += 1

    if dropped:
        logger.warning("dropped %d file(s) from context to fit %s's input budget", dropped, model)

    return "\n\n".join(fixed_parts + file_parts)


def _complete(system_prompt: str, user_prompt: str, model: str) -> str:
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=120,  # never let a stalled provider hang the whole review
    )
    return response.choices[0].message.content or ""


def _coerce_line(value: object) -> int | None:
    # Models sometimes return the line number as a numeric string
    # (e.g. "42") instead of an int; normalize here so downstream code
    # can rely on the Finding.line: int | None contract.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def run_lens(lens: Lens, context: Context, model: str) -> list[Finding]:
    system_prompt = lens.system_prompt()
    text = _complete(system_prompt, _context_prompt(context, model, system_prompt), model)

    try:
        raw_findings = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        # A lens returning unparseable output looks identical to "found
        # nothing" downstream, so at least log it — one lens failing
        # shouldn't fail the whole review, but it shouldn't be invisible either.
        logger.warning("lens %r returned unparseable output, skipping", lens.name)
        return []

    findings = []
    for item in raw_findings:
        if not isinstance(item, dict) or "summary" not in item:
            continue
        findings.append(
            Finding(
                lens=lens.name,
                file=item.get("file"),
                line=_coerce_line(item.get("line")),
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
- **PLAUSIBLE by default.** Do not drop a finding for being "speculative" or \
"depends on runtime state" when the scenario is realistic: concurrency races, \
None on a rare-but-reachable path (error handler, missing optional field), \
falsy-zero treated as missing, off-by-one on a boundary the code does not \
exclude, retry storms or partial failures. These are PLAUSIBLE — downgrade \
rather than drop. Only use "drop" when you can quote the actual code that \
disproves the claim.
- Never use "drop" (factual) without a real quote. "I doubt it" is not grounds; \
use "downgrade".
- Prefer "drop_noise" for pure narration and mis-scoped impact; reserve "drop" \
for a finding that makes a real but disprovable claim.
- Judge blast radius honestly. A change to a repo's own config, CI, workflow, \
tests, or private helpers does not break external consumers. The public \
surface is exported code, API/response shapes, CLI flags, shipped config \
defaults, action inputs, and migrations — nothing else.
- Merge near-duplicates: keep the clearest one, drop_noise the rest.
- **Positive observations are not findings.** If a finding states that a \
control is present, a check is correct, or an implementation is good — that \
is not a problem. drop_noise it immediately, regardless of confidence.
- **Pre-existing issues are out of scope.** If a finding's quoted line does \
not appear as a `+` line in the diff, it is a pre-existing issue the PR author \
cannot fix — drop_noise it.
- **A finding's detail may include a "Reply on this finding's thread" section** \
— that's a real person's response from a previous review round, not something \
you can verify against the code. If it credibly explains why the finding \
doesn't apply (missing context the lens didn't have, an intentional tradeoff \
with a stated reason), that supports "drop_noise". If it disputes the finding \
without addressing the substance ("nah", "not a real issue" with no reason), \
keep your original judgement. Either way, a reply is still someone's word, not \
evidence — it can justify "drop_noise" or "downgrade", but never "drop": that \
still requires an actual quote from the diff or files, exactly as if there \
were no reply at all.

Respond with JSON only: a list of objects, one per input finding in the same \
order, with keys: action (keep|drop_noise|drop|downgrade), confidence \
(low|medium|high, your revised confidence if kept/downgraded), reason (one \
sentence), and evidence_quote (a real quote justifying a "drop", or null)."""


def curate_with_model(findings: list[Finding], context: Context, model: str) -> list[dict]:
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
        _context_prompt(context, model, CURATOR_SYSTEM_PROMPT)
        + "\n\n# Findings to curate\n```json\n"
        + json.dumps(findings_payload, indent=2)
        + "\n```"
    )

    text = _complete(CURATOR_SYSTEM_PROMPT, user_prompt, model)

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

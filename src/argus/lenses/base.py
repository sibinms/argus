"""A lens is one narrow reviewer with one job: propose suspicions along a
single angle (security, missing tests, whatever its instructions say). It is
deliberately not asked to be right — that is the curator's job. A lens that
never raises a low-confidence finding is a lens that will also never raise
the real one."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    lens: str
    file: str | None
    line: int | None
    summary: str
    detail: str
    confidence: str  # low | medium | high
    quote: str | None = None
    status: str = "proposed"  # proposed | kept | dropped | downgraded
    drop_reason: str | None = None


@dataclass
class Lens:
    name: str
    instructions: str

    def system_prompt(self) -> str:
        return (
            "You are one lens in a panel of code reviewers, each covering a "
            "single narrow angle. Your angle for this review:\n\n"
            f"{self.instructions}\n\n"
            "What counts as a finding:\n"
            "- A finding must assert a concrete problem: something that could "
            "break, produce a wrong result, or is missing and should exist. "
            "Say what goes wrong, and for whom.\n"
            "- Do NOT report a change just because it changed. 'Added a "
            "timeout', 'refactored X', 'logic changed', 'renamed Y' are "
            "descriptions, not findings. If a change is reasonable and you "
            "cannot name a concrete problem with it, say nothing about it.\n\n"
            "How to report:\n"
            "- Over-report genuine suspicions. A missed real bug is far worse "
            "than a wrong guess — a separate curator verifies every finding, "
            "so you do not need to be certain. But a suspicion still means you "
            "can name the problem you suspect; it is not licence to flag "
            "neutral changes.\n"
            "- You may flag omissions (something that should exist but doesn't) "
            "and unchanged code that the diff makes directly relevant — meaning "
            "the changed lines alter how that specific unchanged code now "
            "executes (e.g. a caller that now passes a different type, a guard "
            "that the new code bypasses). Do NOT flag pre-existing issues in "
            "the same file that are unrelated to the changed lines; those are "
            "out of scope for a PR review.\n"
            "- Every finding needs a `quote`: a short exact string copied from "
            "the diff or file content that anchors it. For an omission, "
            "describe it in `detail` and leave `quote` null.\n"
            "- Stay strictly inside your angle. Do not comment on anything "
            "outside it.\n\n"
            "Respond with JSON only: a list of objects with keys file, line, "
            "summary, detail, confidence (low|medium|high), quote. The summary "
            "must name the problem, not the change. Use an empty list if you "
            "see no real problem on this angle."
        )

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
            "Rules:\n"
            "- Over-report. A missed real bug is far worse than a wrong guess here — "
            "another pass will verify your findings, you do not need to be certain.\n"
            "- You may flag omissions (something that should exist but doesn't), "
            "not only lines that changed.\n"
            "- You may flag unchanged code if the diff makes it newly relevant "
            "(e.g. a caller that now hits a changed function differently).\n"
            "- Every finding needs a `quote`: a short exact string copied from the "
            "diff or file content that anchors your claim. If you cannot quote "
            "anything, describe the omission in `detail` and leave `quote` null.\n"
            "- Stay inside your angle. Do not comment on things outside it.\n\n"
            "Respond with JSON only: a list of objects with keys "
            "file, line, summary, detail, confidence (low|medium|high), quote. "
            "Use an empty list if you truly see nothing on this angle."
        )

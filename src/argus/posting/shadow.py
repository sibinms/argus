"""Shadow mode: never touches the PR, just writes the report to disk (and
to the GitHub Actions job summary, if running in Actions) so the tool can
be validated against real traffic before it's trusted to post."""

from __future__ import annotations

import os
from pathlib import Path

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.report import render_markdown


def write_shadow_report(
    findings: list[Finding], posting: PostingConfig, out_path: str = "argus-report.md"
) -> str:
    markdown = render_markdown(findings, posting)
    Path(out_path).write_text(markdown)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(markdown + "\n")

    return markdown

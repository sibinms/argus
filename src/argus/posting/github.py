"""Active mode: posts inline comments on the changed lines and a summary
verdict on the pull request itself.

GitHub's review API only accepts an inline comment when its line is part of
the pull request's diff, and it rejects the *entire* review (422 "Line could
not be resolved") if any single comment points elsewhere. Lenses, though, are
free to flag unchanged lines and omissions. So we only attach inline comments
to lines that actually appear in the diff; every finding still shows up in
the summary body (which `render_markdown` builds in full), so nothing is
lost. As a final guard, if a review with comments is still rejected, we retry
with a body-only review rather than failing the run.
"""

from __future__ import annotations

import re

from github import Github
from github.GithubException import GithubException
from github.PullRequest import ReviewComment

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.report import postable_findings, render_markdown, verdict

_EVENT_MAP = {
    "approve": "APPROVE",
    "comment": "COMMENT",
    "request_changes": "REQUEST_CHANGES",
}

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def _comment_body(f: Finding) -> str:
    return f"**{f.summary}** *(lens: {f.lens}, confidence: {f.confidence})*\n\n{f.detail}"


def _patch_new_lines(patch: str | None) -> set[int]:
    """The new-file line numbers a patch touches (added + context lines) —
    exactly the lines GitHub will accept an inline comment on."""
    lines: set[int] = set()
    if not patch:
        return lines
    new_ln = 0
    in_hunk = False
    for raw in patch.splitlines():
        header = _HUNK_HEADER.match(raw)
        if header:
            new_ln = int(header.group(1))
            in_hunk = True
            continue
        if not in_hunk or raw.startswith(("+++", "---")):
            continue
        if raw.startswith("-") or raw.startswith("\\"):
            continue  # removed line / "no newline" marker: no new-file line
        # added ("+") or context (" ") line: advances the new-file counter
        lines.add(new_ln)
        new_ln += 1
    return lines


def commentable_lines(pr) -> dict[str, set[int]]:
    """Maps each changed file to the set of lines an inline comment can
    anchor to, read from the PR's own diff."""
    result: dict[str, set[int]] = {}
    for pr_file in pr.get_files():
        result[pr_file.filename] = _patch_new_lines(pr_file.patch)
    return result


def post_to_github(
    repo_full_name: str,
    pr_number: int,
    token: str,
    findings: list[Finding],
    posting: PostingConfig,
) -> None:
    gh = Github(token)
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    anchorable = commentable_lines(pr)
    postable = postable_findings(findings, posting)

    comments: list[ReviewComment] = [
        {"path": f.file, "line": f.line, "side": "RIGHT", "body": _comment_body(f)}
        for f in postable
        if f.file is not None and f.line is not None and f.line in anchorable.get(f.file, set())
    ]

    summary = render_markdown(findings, posting)
    event = _EVENT_MAP[verdict(findings, posting)]

    try:
        pr.create_review(body=summary, event=event, comments=comments)
    except GithubException:
        # A comment line GitHub couldn't resolve would sink the whole review.
        # The summary already contains every finding, so fall back to posting
        # it without inline comments rather than failing the run.
        if comments:
            pr.create_review(body=summary, event=event)
        else:
            raise

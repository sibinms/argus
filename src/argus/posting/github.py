"""Active mode: posts inline comments on the changed lines and a summary
verdict on the pull request itself.

Findings without a file/line (pure omissions, like a missing test) can't be
anchored to a diff line, so they go in the summary body instead of as inline
comments — GitHub's review API has no concept of a comment that isn't
attached to a line.
"""

from __future__ import annotations

from github import Github
from github.PullRequest import ReviewComment

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.report import postable_findings, render_markdown, verdict

_EVENT_MAP = {
    "approve": "APPROVE",
    "comment": "COMMENT",
    "request_changes": "REQUEST_CHANGES",
}


def _comment_body(f: Finding) -> str:
    return f"**{f.summary}** *(lens: {f.lens}, confidence: {f.confidence})*\n\n{f.detail}"


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

    postable = postable_findings(findings, posting)
    line_findings = [f for f in postable if f.file and f.line]

    comments: list[ReviewComment] = [
        {"path": f.file, "line": f.line, "side": "RIGHT", "body": _comment_body(f)}
        for f in line_findings
        if f.file is not None and f.line is not None
    ]

    summary = render_markdown(findings, posting)
    event = _EVENT_MAP[verdict(findings, posting)]

    pr.create_review(body=summary, event=event, comments=comments)

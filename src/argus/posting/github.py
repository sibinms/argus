"""Active mode: posts Argus's review on a pull request, idempotently.

Argus runs on every push, so naive posting stacks a fresh review and a fresh
copy of every comment each time, and never resolves anything. This module
acts as a moderator instead:

- One rolling **summary** comment, edited in place across runs (never stacked).
- Each finding is fingerprinted and posted as an inline comment **once**;
  a finding already posted is not posted again.
- When a finding is no longer raised (it was addressed), its inline thread is
  resolved rather than left open.
- A new formal review is only submitted when there is something new to say
  (a new inline comment, or a changed verdict) — otherwise Argus stays quiet.

GitHub only accepts an inline comment on a line that is part of the diff, and
rejects the whole review (422) otherwise, so inline comments are limited to
diff lines; every finding still appears in the summary. Nothing is ever
deleted — comments are resolved (collapsed) or edited, never removed.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request

from github import Github
from github.GithubException import GithubException
from github.PullRequest import ReviewComment

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.report import postable_findings, render_markdown, verdict

# "approve" is handled separately (see post_to_github): it becomes a real
# APPROVE only when posting.approve_reviews is on, otherwise a positive
# COMMENT — because a bot APPROVE 422s in any repo without the "Allow GitHub
# Actions to approve pull requests" setting. These two always map directly.
_EVENT_MAP = {
    "comment": "COMMENT",
    "request_changes": "REQUEST_CHANGES",
}

_VERDICT_HEADER = {
    "approve": "✅ **Argus** — looks good",
    "comment": "💬 **Argus** — a few notes",
    "request_changes": "❌ **Argus** — changes requested",
}

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
_SUMMARY_MARKER = "<!-- argus:summary -->"
_FP_MARKER = re.compile(r"<!-- argus:fp:([0-9a-f]{12}) -->")


def _fingerprint(f: Finding) -> str:
    """Stable identity for a finding across runs: same file, line, and summary
    means the same finding, so it is posted at most once."""
    key = f"{f.file}|{f.line}|{f.summary.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _comment_body(f: Finding) -> str:
    marker = f"<!-- argus:fp:{_fingerprint(f)} -->"
    return f"{marker}\n**{f.summary}** *(lens: {f.lens}, confidence: {f.confidence})*\n\n{f.detail}"


def partition_findings(current_fps: set[str], posted_fps: set[str]) -> tuple[set[str], set[str]]:
    """Split fingerprints into (new, addressed): new findings not yet posted,
    and previously-posted findings that are no longer raised."""
    new = current_fps - posted_fps
    addressed = posted_fps - current_fps
    return new, addressed


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


def _posted_fingerprints(pr) -> set[str]:
    """Fingerprints Argus has already posted as inline comments on this PR."""
    posted: set[str] = set()
    for comment in pr.get_review_comments():
        match = _FP_MARKER.search(comment.body or "")
        if match:
            posted.add(match.group(1))
    return posted


def _upsert_summary(pr, body: str) -> None:
    """Edit Argus's single summary comment in place, or create it if absent."""
    full = f"{_SUMMARY_MARKER}\n{body}"
    for comment in pr.get_issue_comments():
        if _SUMMARY_MARKER in (comment.body or ""):
            comment.edit(full)
            return
    pr.create_issue_comment(full)


# GitHub reports a review's *state* (APPROVED / CHANGES_REQUESTED / COMMENTED)
# in a different vocabulary from the *event* used to create one (APPROVE /
# REQUEST_CHANGES / COMMENT), so map between them to compare like with like.
_STATE_TO_EVENT = {
    "APPROVED": "APPROVE",
    "CHANGES_REQUESTED": "REQUEST_CHANGES",
    "COMMENTED": "COMMENT",
}


def _last_bot_event(pr) -> str | None:
    """The event equivalent of Argus's most recent formal review, so we can
    avoid re-submitting an unchanged verdict."""
    last: str | None = None
    for review in pr.get_reviews():
        body = review.body or ""
        if _SUMMARY_MARKER in body or "**Argus**" in body:
            mapped = _STATE_TO_EVENT.get(review.state)
            if mapped:
                last = mapped
    return last


def _resolve_addressed_threads(
    repo_full_name: str, pr_number: int, token: str, addressed_fps: set[str]
) -> None:
    """Best-effort: collapse (resolve) the review threads whose finding is no
    longer raised. Uses the GraphQL API; any failure here is non-fatal —
    resolution is housekeeping, not part of posting the review."""
    if not addressed_fps:
        return
    try:
        threads = _graphql_review_threads(repo_full_name, pr_number, token)
        for thread in threads:
            if thread["isResolved"]:
                continue
            body = thread["firstBody"]
            match = _FP_MARKER.search(body or "")
            if match and match.group(1) in addressed_fps:
                _graphql_resolve_thread(thread["id"], token)
    except Exception:
        # Never let housekeeping break the run.
        return


def _graphql(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(  # nosec B310 - fixed https GitHub API URL
        "https://api.github.com/graphql",
        data=payload,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _graphql_review_threads(repo_full_name: str, pr_number: int, token: str) -> list[dict]:
    owner, name = repo_full_name.split("/", 1)
    query = """
    query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){
          reviewThreads(first:100){
            nodes{ id isResolved comments(first:1){ nodes{ body } } }
          }
        }
      }
    }"""
    data = _graphql(query, {"owner": owner, "name": name, "number": pr_number}, token)
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    threads = []
    for node in nodes:
        comments = node["comments"]["nodes"]
        threads.append(
            {
                "id": node["id"],
                "isResolved": node["isResolved"],
                "firstBody": comments[0]["body"] if comments else "",
            }
        )
    return threads


def _graphql_resolve_thread(thread_id: str, token: str) -> None:
    mutation = """
    mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id } } }"""
    _graphql(mutation, {"id": thread_id}, token)


def post_to_github(
    repo_full_name: str,
    pr_number: int,
    token: str,
    findings: list[Finding],
    posting: PostingConfig,
) -> None:
    gh = Github(token, timeout=30)
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    anchorable = commentable_lines(pr)
    postable = postable_findings(findings, posting)

    inline_findings = [
        f
        for f in postable
        if f.file is not None and f.line is not None and f.line in anchorable.get(f.file, set())
    ]
    current_fps = {_fingerprint(f) for f in inline_findings}
    posted_fps = _posted_fingerprints(pr)
    new_fps, addressed_fps = partition_findings(current_fps, posted_fps)

    # Only post inline comments for findings not already on the PR.
    new_comments: list[ReviewComment] = [
        {"path": f.file, "line": f.line, "side": "RIGHT", "body": _comment_body(f)}
        for f in inline_findings
        if f.file is not None and f.line is not None and _fingerprint(f) in new_fps
    ]

    v = verdict(findings, posting)
    if v == "approve":
        event = "APPROVE" if posting.approve_reviews else "COMMENT"
    else:
        event = _EVENT_MAP[v]

    # The rolling summary is the always-current conclusion; refresh it every run.
    header = _VERDICT_HEADER[v]
    _upsert_summary(pr, f"{header}\n\n{render_markdown(findings, posting)}")

    # Resolve threads for findings that have since been addressed.
    _resolve_addressed_threads(repo_full_name, pr_number, token, addressed_fps)

    # Submit a formal review only when there is something new to say: a new
    # inline comment, or a verdict that differs from Argus's last review.
    # Otherwise stay quiet instead of stacking an identical review.
    if not new_comments and event == _last_bot_event(pr):
        return

    review_body = f"{header} — details in the Argus summary comment."
    try:
        pr.create_review(body=review_body, event=event, comments=new_comments)
    except GithubException as e:
        status = getattr(e, "status", None)
        message = str(e).lower()
        if status == 422 and "not permitted to approve" in message:
            pr.create_review(body=review_body, event="COMMENT", comments=new_comments)
        elif new_comments and status == 422:
            pr.create_review(body=review_body, event=event)
        else:
            raise

"""Active mode: posts Argus's review on a pull request, idempotently.

Argus runs on every push, so naive posting stacks a fresh review and a fresh
copy of every comment each time, and never resolves anything. This module
acts as a moderator instead:

- Findings attach as **inline comments** on their diff line — no rolling
  summary comment. Each finding is fingerprinted and posted **once**; a
  finding already posted is not posted again, so a run that finds nothing
  new stays visibly silent rather than editing something old in place.
- A finding still flagged but with a reply on its thread from someone other
  than Argus is sent back through the curator with that reply as context,
  so a human explaining why it doesn't apply can change the verdict.
- When a finding is no longer raised (it was addressed), its inline thread is
  resolved rather than left open.
- Findings that can't anchor to a diff line (GitHub only accepts inline
  comments on lines the diff touches) go in a small **overflow** comment
  instead — same one-time-only posting rule, just not tied to a line.
- A new formal review is only submitted when there is something new to say
  (a new inline/overflow comment, or a changed verdict) — otherwise Argus
  stays quiet. Nothing is ever deleted — comments are resolved (collapsed),
  never removed.
"""

from __future__ import annotations

import json
import re
import urllib.request

from github import Github
from github.GithubException import GithubException
from github.PullRequest import ReviewComment

from argus.config import PostingConfig
from argus.context.gather import Context
from argus.curator.curate import recurate_with_replies
from argus.fingerprint import fingerprint as _fingerprint
from argus.lenses.base import Finding
from argus.report import postable_findings, verdict

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
_OVERFLOW_MARKER = "<!-- argus:overflow -->"
_FP_MARKER = re.compile(r"<!-- argus:fp:([0-9a-f]{12}) -->")


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


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


def _posted_overflow_fingerprints(pr) -> set[str]:
    """Fingerprints Argus has already surfaced in an overflow comment (a
    finding that couldn't attach to a diff line)."""
    posted: set[str] = set()
    for comment in pr.get_issue_comments():
        body = comment.body or ""
        if _OVERFLOW_MARKER not in body:
            continue
        posted.update(match.group(1) for match in _FP_MARKER.finditer(body))
    return posted


def _overflow_comment_body(findings: list[Finding]) -> str:
    lines = [
        _OVERFLOW_MARKER,
        "**Argus** — finding(s) that don't attach to a changed line:",
        "",
    ]
    for f in findings:
        marker = f"<!-- argus:fp:{_fingerprint(f)} -->"
        location = f"`{f.file}`" if f.file else "(general)"
        lines.append(f"{marker}\n### {location} — {f.summary}")
        lines.append(f"*lens: {f.lens} · confidence: {f.confidence}*")
        lines.append("")
        lines.append(f.detail or "")
        lines.append("")
    return "\n".join(lines)


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
        if "**Argus**" in body:
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
            nodes{
              id
              isResolved
              comments(first:20){ nodes{ body author{ login } } }
            }
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
                "comments": comments,
            }
        )
    return threads


def _graphql_resolve_thread(thread_id: str, token: str) -> None:
    mutation = """
    mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id } } }"""
    _graphql(mutation, {"id": thread_id}, token)


def thread_replies_by_fingerprint(threads: list[dict]) -> dict[str, list[str]]:
    """Maps a finding's fingerprint to any reply left on its thread by
    someone other than whoever posted the first comment (i.e. Argus itself,
    regardless of which identity — PAT, bot account, or GitHub App — posted
    it). Threads with no reply, or with an unrecognized first comment,
    aren't included."""
    replies: dict[str, list[str]] = {}
    for thread in threads:
        comments = thread["comments"]
        if not comments:
            continue
        first = comments[0]
        match = _FP_MARKER.search(first.get("body") or "")
        if not match:
            continue
        fp = match.group(1)
        first_author = (first.get("author") or {}).get("login")
        for comment in comments[1:]:
            author = (comment.get("author") or {}).get("login")
            body = comment.get("body")
            if body and author != first_author:
                replies.setdefault(fp, []).append(body)
    return replies


def fetch_thread_replies(repo_full_name: str, pr_number: int, token: str) -> dict[str, list[str]]:
    """Best-effort: replies keyed by finding fingerprint. Any failure returns
    no replies rather than breaking the run — reply-awareness is an
    enhancement, not required for posting to work."""
    try:
        threads = _graphql_review_threads(repo_full_name, pr_number, token)
        return thread_replies_by_fingerprint(threads)
    except Exception:
        return {}


def post_to_github(
    repo_full_name: str,
    pr_number: int,
    token: str,
    findings: list[Finding],
    posting: PostingConfig,
    context: Context,
    curator_model: str,
) -> None:
    gh = Github(token, timeout=30)
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    # A finding still flagged but with a reply on its thread (from someone
    # other than Argus) gets re-judged with that reply as context, before
    # anything else decides what's new/addressed this run.
    replies = fetch_thread_replies(repo_full_name, pr_number, token)
    findings = recurate_with_replies(findings, replies, context, curator_model)

    anchorable = commentable_lines(pr)
    postable = postable_findings(findings, posting)

    inline_findings: list[Finding] = []
    overflow_findings: list[Finding] = []
    for f in postable:
        if f.file is not None and f.line is not None and f.line in anchorable.get(f.file, set()):
            inline_findings.append(f)
        else:
            overflow_findings.append(f)

    current_fps = {_fingerprint(f) for f in inline_findings}
    posted_fps = _posted_fingerprints(pr)
    new_fps, addressed_fps = partition_findings(current_fps, posted_fps)

    # New findings not already on the PR, highest confidence first...
    new_inline = [f for f in inline_findings if _fingerprint(f) in new_fps]
    new_inline.sort(key=lambda f: _CONFIDENCE_RANK.get(f.confidence, 0), reverse=True)

    # ...limited by the hard lifetime cap. Once the PR already carries
    # max_inline_comments Argus comments, no more are posted inline. This is
    # what makes "endless comments" impossible regardless of dedup. A finding
    # that's bumped by the cap isn't lost — it falls into the overflow
    # comment below, same as one that never anchors to a line at all.
    budget = max(0, posting.max_inline_comments - len(posted_fps))
    inline_to_post = new_inline[:budget]
    bumped_by_cap = new_inline[budget:]
    new_comments: list[ReviewComment] = [
        {"path": f.file, "line": f.line, "side": "RIGHT", "body": _comment_body(f)}
        for f in inline_to_post
        if f.file is not None and f.line is not None
    ]

    # Findings that can't attach to a diff line — or that could, but didn't
    # fit under the inline cap — get the same new-vs-already-posted
    # treatment, just via a small standalone comment instead of an inline
    # thread, posted only when there's something new to say there.
    overflow_candidates = overflow_findings + bumped_by_cap
    overflow_current_fps = {_fingerprint(f) for f in overflow_candidates}
    overflow_posted_fps = _posted_overflow_fingerprints(pr)
    overflow_new_fps, _ = partition_findings(overflow_current_fps, overflow_posted_fps)
    new_overflow = [f for f in overflow_candidates if _fingerprint(f) in overflow_new_fps]
    new_overflow.sort(key=lambda f: _CONFIDENCE_RANK.get(f.confidence, 0), reverse=True)
    if new_overflow:
        pr.create_issue_comment(_overflow_comment_body(new_overflow))

    v = verdict(findings, posting)
    if v == "approve":
        event = "APPROVE" if posting.approve_reviews else "COMMENT"
    else:
        event = _EVENT_MAP[v]
    header = _VERDICT_HEADER[v]

    # Resolve threads for findings that have since been addressed (fixed in
    # the diff, or dismissed by a reply the curator accepted above).
    _resolve_addressed_threads(repo_full_name, pr_number, token, addressed_fps)

    # Submit a formal review only when there is something new to say: a new
    # inline or overflow comment, or a verdict that differs from Argus's last
    # review. Otherwise stay quiet instead of stacking an identical review.
    if not new_comments and not new_overflow and event == _last_bot_event(pr):
        return

    review_body = header
    try:
        pr.create_review(body=review_body, event=event, comments=new_comments)
    except GithubException as e:
        status = getattr(e, "status", None)
        message = str(e).lower()
        if status == 422 and (
            "not permitted to approve" in message
            or "request changes on your own pull request" in message
        ):
            pr.create_review(body=review_body, event="COMMENT", comments=new_comments)
        elif new_comments and status == 422:
            # The inline comment landed on a line outside the diff; retry body-only.
            # Use COMMENT rather than the original event so REQUEST_CHANGES doesn't
            # cause a second 422 when the token owner is the PR author.
            pr.create_review(body=review_body, event="COMMENT")
        else:
            raise

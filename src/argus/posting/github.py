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
  so a human explaining why it doesn't apply can change the verdict. Under
  incremental review this run's own findings may not include it at all (its
  file might be outside this run's diff scope) — when that happens it's
  rebuilt from its own posted comment instead of being silently ignored.
- When a finding is no longer raised (it was addressed), its inline thread is
  resolved rather than left open.
- Findings that can't anchor to a diff line (GitHub only accepts inline
  comments on lines the diff touches) go in a small **overflow** comment
  instead — same one-time-only posting rule, just not tied to a line.
- A new formal review is only submitted when there is something new to say
  (a new inline/overflow comment, or a changed verdict) — otherwise Argus
  stays quiet. Nothing is ever deleted — comments are resolved (collapsed),
  never removed.
- Every posted review is stamped with a hidden marker recording the commit
  SHA it was run against (last_reviewed_sha reads it back), so the next run
  can diff only what's changed since then instead of the whole PR again.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request

from github import Github
from github.GithubException import GithubException
from github.PullRequest import ReviewComment
from github.PullRequestComment import PullRequestComment

from argus.config import PostingConfig
from argus.context.gather import Context
from argus.curator.curate import recurate_with_replies
from argus.fingerprint import fingerprint as _fingerprint
from argus.lenses.base import Finding
from argus.report import postable_findings, verdict

logger = logging.getLogger(__name__)

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
_REVIEWED_SHA_MARKER = re.compile(r"<!-- argus:reviewed-sha:([0-9a-f]{40}) -->")


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _comment_body(f: Finding) -> str:
    marker = f"<!-- argus:fp:{_fingerprint(f)} -->"
    return f"{marker}\n**{f.summary}** *(lens: {f.lens}, confidence: {f.confidence})*\n\n{f.detail}"


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


def _posted_inline_comments(pr) -> dict[str, PullRequestComment]:
    """Maps each fingerprint Argus has already posted as an inline comment on
    this PR to that comment itself — the file (for post_to_github's
    addressed_fps) and the rendered body (for reconstructing a stale reply
    target, see _reconstruct_finding) both come from here."""
    posted: dict[str, PullRequestComment] = {}
    for comment in pr.get_review_comments():
        match = _FP_MARKER.search(comment.body or "")
        if match:
            posted[match.group(1)] = comment
    return posted


_CONFIDENCE_LEVELS = "low|medium|high"
# Greedy summary + anchored at end-of-line: a summary containing "**" or ")"
# still parses correctly, since regex backtracking finds the last position
# where the fixed " *(lens: ..., confidence: ...)*" suffix matches, not the
# first "**" it happens to see.
_COMMENT_HEADER_RE = re.compile(
    r"^\*\*(?P<summary>.*)\*\* \*\(lens: (?P<lens>[^,]+), "
    rf"confidence: (?P<confidence>{_CONFIDENCE_LEVELS})\)\*$"
)


def _reconstruct_finding(comment: PullRequestComment) -> Finding | None:
    """Rebuilds a Finding from its own already-posted inline comment.

    Reply-driven re-judgment (recurate_with_replies) needs a Finding object
    to re-curate — but under incremental review, a finding with a fresh
    reply may not be rediscovered by *this* run's (possibly empty) diff at
    all, since the file it's on might not be in scope this time. Without
    this, that reply would just be silently ignored: recurate_with_replies
    only ever looks at findings the current run actually produced. Rebuilt
    from the comment's own rendered text (see _comment_body) — best-effort,
    since only what that text captured is recoverable. Logs when it can't,
    since a reply that goes unconsidered should be visible, not silent."""
    body = comment.body or ""
    without_marker = _FP_MARKER.sub("", body, count=1).strip()
    header, _, detail = without_marker.partition("\n")
    match = _COMMENT_HEADER_RE.search(header)
    if not match:
        logger.warning(
            "could not reconstruct finding from posted comment on %s:%s — "
            "a reply on this thread will not be re-judged",
            comment.path,
            comment.line,
        )
        return None
    return Finding(
        lens=match.group("lens"),
        file=comment.path,
        line=comment.line,
        summary=match.group("summary"),
        detail=detail.strip(),
        confidence=match.group("confidence"),
        status="kept",
    )


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
    # Known limitation: unlike inline threads (which get resolved once a
    # finding is addressed), an overflow comment is never edited after
    # posting — a finding that's since fixed just stays listed here. Fixing
    # this properly means either editing the comment in place (reintroducing
    # the "invisible update" problem the old rolling summary had) or some
    # new per-finding resolution affordance; punting on both for now since
    # overflow only covers the minority of findings that can't be inlined.
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


def last_reviewed_sha(repo_full_name: str, pr_number: int, token: str) -> str | None:
    """The commit SHA Argus's most recent formal review was posted against,
    read back from a hidden marker in that review's body. Lets a later run
    diff only what's changed since then instead of re-diffing the whole PR
    again. Returns None (fall back to a full base diff) if Argus has never
    reviewed here, or the lookup itself fails."""
    try:
        gh = Github(token, timeout=30)
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        last_sha: str | None = None
        for review in pr.get_reviews():
            body = review.body or ""
            if "**Argus**" not in body:
                continue
            match = _REVIEWED_SHA_MARKER.search(body)
            if match:
                last_sha = match.group(1)
        return last_sha
    except Exception:
        logger.warning("failed to read last-reviewed SHA", exc_info=True)
        return None


def _resolve_addressed_threads(threads: list[dict], token: str, addressed_fps: set[str]) -> None:
    """Best-effort: collapse (resolve) the review threads whose finding is no
    longer raised. Takes already-fetched threads (see _fetch_review_threads)
    rather than fetching its own copy. Any failure here is non-fatal —
    resolution is housekeeping, not part of posting the review."""
    if not addressed_fps:
        return
    try:
        for thread in threads:
            if thread["isResolved"]:
                continue
            body = thread["firstBody"]
            match = _FP_MARKER.search(body or "")
            if match and match.group(1) in addressed_fps:
                _graphql_resolve_thread(thread["id"], token)
    except Exception:
        # Never let housekeeping break the run, but a persistent failure
        # here means threads silently pile up unresolved forever — worth a
        # log, same as the other best-effort calls in this module.
        logger.warning("failed to resolve addressed review threads", exc_info=True)


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
              comments(first:100){ nodes{ body author{ login } } }
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


def _fetch_review_threads(repo_full_name: str, pr_number: int, token: str) -> list[dict]:
    """Best-effort: the PR's review threads, fetched once and shared by both
    reply-awareness and addressed-thread resolution — they need the same
    GraphQL data, so this avoids each making its own round-trip for it. Any
    failure returns no threads rather than breaking the run; both consumers
    already treat an empty list as "nothing to do here"."""
    try:
        return _graphql_review_threads(repo_full_name, pr_number, token)
    except Exception:
        logger.warning("failed to fetch review threads", exc_info=True)
        return []


def _new_findings_sorted(candidates: list[Finding], posted_fps: set[str]) -> list[Finding]:
    """Findings from `candidates` not already posted (by fingerprint,
    checked against `posted_fps` — pass the union of inline- and
    overflow-posted fingerprints so a finding already surfaced on one
    surface is never posted again on the other), sorted highest-confidence
    first."""
    current_fps = {_fingerprint(f) for f in candidates}
    new_fps = current_fps - posted_fps
    new = [f for f in candidates if _fingerprint(f) in new_fps]
    new.sort(key=lambda f: _CONFIDENCE_RANK.get(f.confidence, 0), reverse=True)
    return new


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

    # Fetched once — reply-awareness and addressed-thread resolution both
    # need the same review-thread data, so share the one GraphQL round-trip
    # instead of each making their own.
    threads = _fetch_review_threads(repo_full_name, pr_number, token)
    posted_inline = _posted_inline_comments(pr)

    # A finding still flagged but with a reply on its thread (from someone
    # other than Argus) gets re-judged with that reply as context, before
    # anything else decides what's new/addressed this run. Under incremental
    # review this run's `findings` may not include a replied-to finding at
    # all — its file might be outside this run's (possibly empty) diff scope
    # even though the reply is brand new. Without rebuilding it here, that
    # reply would just be silently ignored, since recurate_with_replies can
    # only re-judge findings it's actually given.
    replies = thread_replies_by_fingerprint(threads)
    current_fps = {_fingerprint(f) for f in findings}
    stale_targets: list[Finding] = []
    for fp in replies:
        if fp in current_fps or fp not in posted_inline:
            continue
        reconstructed = _reconstruct_finding(posted_inline[fp])
        if reconstructed is not None:
            stale_targets.append(reconstructed)
    combined = recurate_with_replies(findings + stale_targets, replies, context, curator_model)
    # Sliced back out by position rather than relied on as the same objects
    # in place — recurate_with_replies mutates its Finding objects today,
    # but this way the split is correct even if that ever changes to
    # returning fresh ones instead.
    findings, stale_targets = combined[: len(findings)], combined[len(findings) :]
    reply_addressed_fps = {_fingerprint(f) for f in stale_targets if f.status == "dropped"}

    anchorable = commentable_lines(pr)
    postable = postable_findings(findings, posting)

    inline_findings: list[Finding] = []
    overflow_findings: list[Finding] = []
    for f in postable:
        if f.file is not None and f.line is not None and f.line in anchorable.get(f.file, set()):
            inline_findings.append(f)
        else:
            overflow_findings.append(f)

    # Fingerprints already posted on *either* surface. A finding can move
    # between surfaces across runs (a line becomes un-anchorable after a
    # force-push, or the reverse), so both "new" checks below use this same
    # union — otherwise a finding posted on one surface could be posted
    # again on the other.
    posted_fps = set(posted_inline)
    overflow_posted_fps = _posted_overflow_fingerprints(pr)
    all_posted_fps = posted_fps | overflow_posted_fps

    # A previously-inline finding only counts as "addressed" (safe to
    # resolve its thread) if it's absent from *both* surfaces this run —
    # not just no longer inline, since it may have simply relocated to
    # overflow rather than actually being fixed. Under incremental review
    # (context.changed_paths is scoped to since_sha...head, not the whole
    # PR) that alone isn't enough: a finding on a file this run never looked
    # at would also be "absent" simply because nothing re-examined it, not
    # because it's fixed. So a finding is only ever eligible to be marked
    # addressed if its file was actually in this run's diff scope — *unless*
    # a reply already got it dropped above (reply_addressed_fps), which is
    # fresh evidence in its own right, independent of whether this run's
    # diff happened to touch that file.
    all_current_fps = {_fingerprint(f) for f in inline_findings} | {
        _fingerprint(f) for f in overflow_findings
    }
    touched_paths = set(context.changed_paths)
    addressed_fps = {
        fp for fp in posted_fps - all_current_fps if posted_inline[fp].path in touched_paths
    } | reply_addressed_fps

    new_inline = _new_findings_sorted(inline_findings, all_posted_fps)

    # Limited by the hard lifetime cap. Once the PR already carries
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
    # treatment via a small standalone comment instead of an inline thread.
    overflow_candidates = overflow_findings + bumped_by_cap
    new_overflow = _new_findings_sorted(overflow_candidates, all_posted_fps)

    v = verdict(findings, posting)
    if v == "approve":
        event = "APPROVE" if posting.approve_reviews else "COMMENT"
    else:
        event = _EVENT_MAP[v]
    header = _VERDICT_HEADER[v]

    # Resolve threads for findings that have since been addressed (fixed in
    # the diff, or dismissed by a reply the curator accepted above).
    _resolve_addressed_threads(threads, token, addressed_fps)

    # Submit a formal review only when there is something new to say: a new
    # inline or overflow comment, or a verdict that differs from Argus's last
    # review. Otherwise stay quiet instead of stacking an identical review.
    #
    # Known trade-off: the reviewed-sha marker below only advances when a
    # review is actually posted, so a string of no-op runs (nothing new,
    # same verdict) doesn't advance it either. That's fine — the next run
    # just diffs a slightly wider (but still bounded) range, and any run
    # that does post immediately snaps the marker back to the true head.
    if not new_comments and not new_overflow and event == _last_bot_event(pr):
        return

    if overflow_findings or bumped_by_cap:
        review_body = f"{header} — see the inline and overflow comments below."
    elif inline_findings:
        review_body = f"{header} — see the inline comments below."
    else:
        review_body = header
    review_body = f"{review_body}\n\n<!-- argus:reviewed-sha:{pr.head.sha} -->"
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

    # Posted after the review, deliberately not caught: a failure here means
    # real findings didn't reach the PR at all, which should fail the run
    # loudly — but only once the independent, already-successful inline
    # comments above are safely posted, so an overflow failure doesn't take
    # those down as collateral damage.
    if new_overflow:
        pr.create_issue_comment(_overflow_comment_body(new_overflow))

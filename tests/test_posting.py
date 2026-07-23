from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from argus.config import PostingConfig
from argus.context.gather import Context
from argus.lenses.base import Finding
from argus.posting import github as ghmod
from argus.posting.github import (
    _fingerprint,
    _patch_new_lines,
    commentable_lines,
    partition_findings,
)


def _ctx() -> Context:
    return Context(diff="+x", changed_files=[])


def _finding(line: int) -> Finding:
    return Finding(
        lens="tests",
        file="a.py",
        line=line,
        summary="s",
        detail="d",
        confidence="high",
        status="kept",
    )


def _fake_pr(create_review_side_effect=None, posted_comments=None, prior_reviews=None):
    pr = MagicMock()
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1,1 +1,2 @@\n a\n+b\n"  # new-file lines {1, 2}
    pr.get_files.return_value = [changed]
    pr.get_review_comments.return_value = posted_comments or []
    pr.get_issue_comments.return_value = []  # no overflow comment yet
    pr.get_reviews.return_value = prior_reviews or []
    if create_review_side_effect is not None:
        pr.create_review.side_effect = create_review_side_effect
    return pr


def _patch_github(monkeypatch, pr):
    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(ghmod, "Github", lambda *a, **k: gh)
    # No replies by default — most tests aren't exercising reply-awareness,
    # and this also avoids a real network call to the GraphQL API.
    monkeypatch.setattr(ghmod, "fetch_thread_replies", lambda *a, **k: {})


def _post(pr_or_none_findings=None, findings=None, posting=None, **kwargs):
    return ghmod.post_to_github(
        "o/r",
        1,
        "tok",
        findings if findings is not None else [],
        posting or PostingConfig(min_confidence="low"),
        kwargs.pop("context", _ctx()),
        kwargs.pop("curator_model", "curator-model"),
    )


# ---- pure logic ----


def test_partition_splits_new_and_addressed():
    new, addressed = partition_findings({"a", "b"}, {"b", "c"})
    assert new == {"a"}
    assert addressed == {"c"}


def _f(summary: str, line: int = 1, confidence: str = "high") -> Finding:
    return Finding(
        lens="tests",
        file="a.py",
        line=line,
        summary=summary,
        detail="d",
        confidence=confidence,
        status="kept",
    )


def test_fingerprint_ignores_line_and_wording_drift():
    # same issue on a different line -> same fingerprint (line drift)
    assert _fingerprint(_f("SQL injection risk", line=2)) == _fingerprint(
        _f("SQL injection risk", line=40)
    )
    # same issue, reworded/repunctuated -> same fingerprint (wording drift)
    assert _fingerprint(_f("SQL injection risk")) == _fingerprint(_f("sql injection risk."))
    # a genuinely different issue -> different fingerprint
    assert _fingerprint(_f("SQL injection risk")) != _fingerprint(_f("missing timeout"))


def test_inline_comments_are_capped(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    findings = [_f("issue one", line=1), _f("issue two", line=2)]
    _post(findings=findings, posting=PostingConfig(min_confidence="low", max_inline_comments=1))

    posted = pr.create_review.call_args_list[0].kwargs["comments"]
    assert len(posted) == 1  # two new findings, but the cap holds it to one


def test_finding_bumped_by_cap_lands_in_overflow_not_lost(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    findings = [_f("issue one", line=1), _f("issue two", line=2)]
    _post(findings=findings, posting=PostingConfig(min_confidence="low", max_inline_comments=1))

    # "issue one" fit under the cap and was posted inline...
    inline_posted = pr.create_review.call_args_list[0].kwargs["comments"]
    assert len(inline_posted) == 1
    assert "issue one" in inline_posted[0]["body"]
    # ...while "issue two" was bumped, but isn't dropped: it surfaces in the
    # overflow comment instead of vanishing.
    pr.create_issue_comment.assert_called_once()
    overflow_body = pr.create_issue_comment.call_args.args[0]
    assert "argus:overflow" in overflow_body
    assert "issue two" in overflow_body
    assert "issue one" not in overflow_body


# ---- posting behaviour ----


def test_new_finding_is_posted_inline(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    _post(findings=[_finding(2)])

    assert pr.create_review.call_count == 1
    assert pr.create_review.call_args_list[0].kwargs["comments"]  # posted the new finding


def test_already_posted_finding_is_not_reposted(monkeypatch):
    f = _finding(2)
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{_fingerprint(f)} -->\npreviously posted"
    prior = MagicMock()
    prior.body = "❌ **Argus** — changes requested"
    prior.state = "CHANGES_REQUESTED"
    pr = _fake_pr(posted_comments=[existing], prior_reviews=[prior])
    _patch_github(monkeypatch, pr)

    _post(findings=[f])

    # nothing new, unchanged verdict, no reply on the thread -> stays silent
    pr.create_review.assert_not_called()
    pr.create_issue_comment.assert_not_called()


def test_finding_on_non_diff_line_goes_to_overflow_comment(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    _post(findings=[_finding(999)])

    # not inlined...
    assert not pr.create_review.call_args_list[0].kwargs.get("comments")
    # ...but surfaced in a new overflow comment
    pr.create_issue_comment.assert_called_once()
    body = pr.create_issue_comment.call_args.args[0]
    assert "argus:overflow" in body
    assert _fingerprint(_finding(999)) in body


def test_overflow_finding_already_surfaced_is_not_reposted(monkeypatch):
    f = _finding(999)
    existing = MagicMock()
    existing.body = f"<!-- argus:overflow -->\n<!-- argus:fp:{_fingerprint(f)} -->\nalready here"
    pr = _fake_pr([None])
    pr.get_issue_comments.return_value = [existing]
    _patch_github(monkeypatch, pr)

    _post(findings=[f])

    pr.create_issue_comment.assert_not_called()


def test_clean_pr_comments_by_default(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    _post(findings=[])

    assert pr.create_review.call_args_list[0].kwargs["event"] == "COMMENT"


def test_clean_pr_approves_when_enabled(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    _post(findings=[], posting=PostingConfig(min_confidence="low", approve_reviews=True))

    assert pr.create_review.call_args_list[0].kwargs["event"] == "APPROVE"


def test_approve_falls_back_to_comment_when_repo_disallows(monkeypatch):
    err = GithubException(
        422,
        data={"message": "GitHub Actions is not permitted to approve pull requests."},
        headers=None,
    )
    pr = _fake_pr([err, None])
    _patch_github(monkeypatch, pr)

    _post(findings=[], posting=PostingConfig(min_confidence="low", approve_reviews=True))

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[0].kwargs["event"] == "APPROVE"
    assert pr.create_review.call_args_list[1].kwargs["event"] == "COMMENT"


def test_falls_back_to_body_only_on_unresolvable_line(monkeypatch):
    err = GithubException(422, data={"message": "Line could not be resolved"}, headers=None)
    pr = _fake_pr([err, None])
    _patch_github(monkeypatch, pr)

    _post(findings=[_finding(2)])

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[0].kwargs["comments"]  # first tried inline
    assert not pr.create_review.call_args_list[1].kwargs.get("comments")  # retry body-only
    assert pr.create_review.call_args_list[1].kwargs["event"] == "COMMENT"  # never REQUEST_CHANGES


def test_own_pr_request_changes_falls_back_to_comment(monkeypatch):
    err = GithubException(
        422,
        data={"message": "Review Can not request changes on your own pull request"},
        headers=None,
    )
    pr = _fake_pr([err, None])
    _patch_github(monkeypatch, pr)

    _post(findings=[_finding(2)])

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[1].kwargs["event"] == "COMMENT"


def test_non_422_error_is_not_masked(monkeypatch):
    err = GithubException(401, data={"message": "Bad credentials"}, headers=None)
    pr = _fake_pr(err)
    _patch_github(monkeypatch, pr)

    with pytest.raises(GithubException):
        _post(findings=[_finding(2)])

    assert pr.create_review.call_count == 1  # no pointless retry on a real error


# ---- reply-aware re-curation ----


def test_reply_on_still_flagged_finding_triggers_recuration(monkeypatch):
    f = _finding(2)
    fp = _fingerprint(f)
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{fp} -->\npreviously posted"
    # Matches the verdict this run will produce ("comment", no postable
    # findings once dropped) so nothing NEW is what's actually being tested.
    prior = MagicMock()
    prior.body = "💬 **Argus** — a few notes"
    prior.state = "COMMENTED"
    pr = _fake_pr(posted_comments=[existing], prior_reviews=[prior])
    _patch_github(monkeypatch, pr)
    monkeypatch.setattr(ghmod, "fetch_thread_replies", lambda *a, **k: {fp: ["intentional, see X"]})
    monkeypatch.setattr(ghmod, "_graphql_review_threads", lambda *a: [])
    resolved: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    captured = {}
    monkeypatch.setattr(
        ghmod,
        "recurate_with_replies",
        lambda findings, replies, ctx, model: (
            captured.update(replies=replies, findings=findings) or []
        ),  # curator drops it entirely -> nothing left to post
    )

    _post(findings=[f])

    assert captured["replies"] == {fp: ["intentional, see X"]}
    # dropped by recuration, verdict unchanged from last review -> stays quiet
    pr.create_review.assert_not_called()


def test_recuration_skipped_when_no_replies(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    def boom(*a, **k):
        raise AssertionError("recurate_with_replies should be a no-op with no replies")

    # fetch_thread_replies already returns {} via _patch_github; recurate_with_replies
    # itself is the real function, which must no-op (not call curate_with_model) here.
    _post(findings=[_finding(2)])
    assert pr.create_review.call_count == 1


# ---- resolving addressed threads ----


def test_resolves_only_addressed_unresolved_threads(monkeypatch):
    threads = [
        {"id": "T1", "isResolved": False, "firstBody": "<!-- argus:fp:aaaaaaaaaaaa -->\nx"},
        {"id": "T2", "isResolved": False, "firstBody": "<!-- argus:fp:bbbbbbbbbbbb -->\ny"},
        {"id": "T3", "isResolved": True, "firstBody": "<!-- argus:fp:aaaaaaaaaaaa -->\nz"},
    ]
    resolved: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_review_threads", lambda *a: threads)
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    ghmod._resolve_addressed_threads("o/r", 1, "tok", {"aaaaaaaaaaaa"})

    # T1: addressed + unresolved -> resolve. T2: not addressed. T3: already resolved.
    assert resolved == ["T1"]


def test_resolve_addressed_noop_when_nothing_addressed(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(ghmod, "_graphql_review_threads", lambda *a: called.append(1) or [])
    ghmod._resolve_addressed_threads("o/r", 1, "tok", set())
    assert called == []  # no API call when there's nothing to resolve


def test_resolve_addressed_swallows_api_errors(monkeypatch):
    def boom(*args):
        raise RuntimeError("graphql down")

    monkeypatch.setattr(ghmod, "_graphql_review_threads", boom)
    # best-effort housekeeping must never break the run
    ghmod._resolve_addressed_threads("o/r", 1, "tok", {"aaaaaaaaaaaa"})


# ---- thread replies ----


def test_thread_replies_by_fingerprint_finds_reply_from_different_author():
    threads = [
        {
            "comments": [
                {"body": "<!-- argus:fp:aaaaaaaaaaaa -->\nfinding", "author": {"login": "argus"}},
                {"body": "actually this is fine", "author": {"login": "a-human"}},
            ]
        }
    ]
    assert ghmod.thread_replies_by_fingerprint(threads) == {
        "aaaaaaaaaaaa": ["actually this is fine"]
    }


def test_thread_replies_ignored_when_same_author_replies_to_self():
    threads = [
        {
            "comments": [
                {"body": "<!-- argus:fp:aaaaaaaaaaaa -->\nfinding", "author": {"login": "argus"}},
                {"body": "self note", "author": {"login": "argus"}},
            ]
        }
    ]
    assert ghmod.thread_replies_by_fingerprint(threads) == {}


def test_thread_replies_skips_threads_with_no_fingerprint_marker():
    threads = [{"comments": [{"body": "unrelated human comment", "author": {"login": "a-human"}}]}]
    assert ghmod.thread_replies_by_fingerprint(threads) == {}


def test_fetch_thread_replies_swallows_errors(monkeypatch):
    monkeypatch.setattr(
        ghmod,
        "_graphql_review_threads",
        lambda *a: (_ for _ in ()).throw(RuntimeError("down")),
    )
    assert ghmod.fetch_thread_replies("o/r", 1, "tok") == {}


# ---- overflow comment ----


def test_posted_overflow_fingerprints_only_reads_overflow_comments():
    overflow = MagicMock()
    overflow.body = "<!-- argus:overflow -->\n<!-- argus:fp:aaaaaaaaaaaa -->\nx"
    unrelated = MagicMock()
    unrelated.body = "just a human comment"
    pr = MagicMock()
    pr.get_issue_comments.return_value = [overflow, unrelated]

    assert ghmod._posted_overflow_fingerprints(pr) == {"aaaaaaaaaaaa"}


# ---- diff parsing ----


def test_added_and_context_lines_are_commentable():
    patch = "@@ -10,3 +10,4 @@ def f():\n context_a\n+added_b\n context_c\n-removed_d\n"
    assert _patch_new_lines(patch) == {10, 11, 12}


def test_removed_lines_do_not_advance_new_file_counter():
    patch = "@@ -5,4 +5,2 @@\n-gone_1\n-gone_2\n kept_5\n+new_6\n"
    assert _patch_new_lines(patch) == {5, 6}


def test_multiple_hunks():
    patch = "@@ -1,1 +1,1 @@\n+first\n@@ -20,1 +30,2 @@\n ctx\n+second\n"
    assert _patch_new_lines(patch) == {1, 30, 31}


def test_empty_or_none_patch():
    assert _patch_new_lines(None) == set()
    assert _patch_new_lines("") == set()


def test_file_header_lines_ignored():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n a\n+b\n"
    assert _patch_new_lines(patch) == {1, 2}


def test_commentable_lines_maps_each_file_to_its_diff_lines():
    f1 = MagicMock()
    f1.filename = "a.py"
    f1.patch = "@@ -1,1 +1,2 @@\n a\n+b\n"  # {1, 2}
    f2 = MagicMock()
    f2.filename = "b.py"
    f2.patch = "@@ -5,0 +5,1 @@\n+z\n"  # {5}
    pr = MagicMock()
    pr.get_files.return_value = [f1, f2]

    assert commentable_lines(pr) == {"a.py": {1, 2}, "b.py": {5}}

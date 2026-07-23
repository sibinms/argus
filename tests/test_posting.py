from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from argus.config import PostingConfig
from argus.context.gather import Context
from argus.lenses.base import Finding
from argus.posting import github as ghmod
from argus.posting.github import _fingerprint, _patch_new_lines, commentable_lines


def _ctx(changed_paths=None) -> Context:
    return Context(diff="+x", changed_files=[], changed_paths=changed_paths or ["a.py"])


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
    # No threads/replies by default — most tests aren't exercising
    # reply-awareness, and this also avoids a real network call.
    monkeypatch.setattr(ghmod, "_fetch_review_threads", lambda *a, **k: [])


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


def test_new_findings_sorted_returns_new_by_confidence():
    low = _f("low issue", confidence="low")
    high = _f("high issue", confidence="high")
    posted_fps = {_fingerprint(_f("unrelated stale issue"))}  # doesn't affect these two

    new = ghmod._new_findings_sorted([low, high], posted_fps)

    assert new == [high, low]  # highest confidence first


def test_new_findings_sorted_excludes_already_posted():
    f = _f("issue")
    new = ghmod._new_findings_sorted([f], {_fingerprint(f)})
    assert new == []


def test_inline_comments_are_capped(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    findings = [_f("issue one", line=1), _f("issue two", line=2)]
    _post(findings=findings, posting=PostingConfig(min_confidence="low", max_inline_comments=1))

    posted = pr.create_review.call_args_list[0].kwargs["comments"]
    assert len(posted) == 1  # two new findings, but the cap holds it to one
    # same confidence, so the cap keeps whichever came first in the list
    assert "issue one" in posted[0]["body"]


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


def test_finding_already_posted_inline_is_not_duplicated_to_overflow(monkeypatch):
    """If a finding's line becomes un-anchorable across runs (e.g. after a
    force-push), it lands in overflow_candidates this run — but it's already
    sitting inline from a previous run, so it must not be posted again."""
    f = _finding(2)  # was inline-anchorable when first posted
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{_fingerprint(f)} -->\npreviously posted"
    pr = _fake_pr(posted_comments=[existing])
    _patch_github(monkeypatch, pr)

    # Same underlying finding, but now on a line outside the diff -> overflow.
    orphaned = _finding(999)
    orphaned.summary = f.summary  # same fingerprint as f (summary drives it)

    _post(findings=[orphaned])

    pr.create_issue_comment.assert_not_called()


def test_finding_relocated_to_overflow_does_not_resolve_its_inline_thread(monkeypatch):
    """A finding still being raised — just no longer on an anchorable line —
    is not "addressed". Its old inline thread must stay open, not get
    resolved as if the underlying issue were fixed."""
    f = _finding(2)
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{_fingerprint(f)} -->\npreviously posted"
    pr = _fake_pr(posted_comments=[existing])
    _patch_github(monkeypatch, pr)
    resolved: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    orphaned = _finding(999)  # same issue, now off-diff -> overflow, not gone
    orphaned.summary = f.summary

    _post(findings=[orphaned])

    assert resolved == []  # still current (in overflow) -> thread stays open


def test_addressed_finding_resolves_when_its_file_was_touched_this_run(monkeypatch):
    """A finding no longer raised, whose file WAS in this run's diff scope,
    is genuinely fixed -- its thread should resolve."""
    f = _finding(2)
    fp = _fingerprint(f)
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{fp} -->\npreviously posted"
    existing.path = "a.py"
    pr = _fake_pr(posted_comments=[existing])
    _patch_github(monkeypatch, pr)
    monkeypatch.setattr(
        ghmod,
        "_fetch_review_threads",
        lambda *a, **k: [
            {"id": "T1", "isResolved": False, "firstBody": existing.body, "comments": []}
        ],
    )
    resolved: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    # default _ctx() has changed_paths=["a.py"] -- in scope this run, and the
    # finding is simply absent from the (empty) findings list -> fixed.
    _post(findings=[])

    assert resolved == ["T1"]


def test_addressed_finding_does_not_resolve_when_its_file_was_untouched_this_run(monkeypatch):
    """A finding absent from this run's results doesn't mean it's fixed if
    this run's diff scope never even looked at its file -- e.g. an
    incremental re-review that only covers the latest push. Its thread must
    stay open rather than being wrongly marked addressed."""
    f = _finding(2)
    fp = _fingerprint(f)
    existing = MagicMock()
    existing.body = f"<!-- argus:fp:{fp} -->\npreviously posted"
    existing.path = "b.py"  # not touched this run
    pr = _fake_pr(posted_comments=[existing])
    _patch_github(monkeypatch, pr)
    monkeypatch.setattr(
        ghmod,
        "_fetch_review_threads",
        lambda *a, **k: [
            {"id": "T1", "isResolved": False, "firstBody": existing.body, "comments": []}
        ],
    )
    resolved: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    _post(findings=[], context=_ctx(changed_paths=["a.py"]))  # b.py out of scope

    assert resolved == []


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


def test_overflow_comment_failure_does_not_block_inline_comments(monkeypatch):
    """The overflow comment is posted after the review — a transient failure
    creating it must not prevent inline comments (independent, already
    successfully prepared) from landing."""
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)
    pr.create_issue_comment.side_effect = GithubException(
        500, data={"message": "server error"}, headers=None
    )

    inline_finding = _f("inline issue", line=1)
    overflow_finding = _f("overflow issue", line=999)

    with pytest.raises(GithubException):
        _post(findings=[inline_finding, overflow_finding])

    # The inline review was posted successfully before the overflow comment
    # was attempted and failed.
    pr.create_review.assert_called_once()
    assert pr.create_review.call_args.kwargs["comments"]


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
    monkeypatch.setattr(
        ghmod, "thread_replies_by_fingerprint", lambda threads: {fp: ["intentional, see X"]}
    )
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

    # _fetch_review_threads already returns [] via _patch_github, so
    # thread_replies_by_fingerprint([]) == {}; recurate_with_replies itself
    # is the real function, which must no-op (not call curate_with_model).
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
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: resolved.append(tid))

    ghmod._resolve_addressed_threads(threads, "tok", {"aaaaaaaaaaaa"})

    # T1: addressed + unresolved -> resolve. T2: not addressed. T3: already resolved.
    assert resolved == ["T1"]


def test_resolve_addressed_noop_when_nothing_addressed(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", lambda tid, tok: called.append(tid))
    threads = [{"id": "T1", "isResolved": False, "firstBody": "<!-- argus:fp:aaaaaaaaaaaa -->\nx"}]

    ghmod._resolve_addressed_threads(threads, "tok", set())

    assert called == []  # no resolve mutation when there's nothing to resolve


def test_resolve_addressed_swallows_api_errors(monkeypatch, caplog):
    threads = [{"id": "T1", "isResolved": False, "firstBody": "<!-- argus:fp:aaaaaaaaaaaa -->\nx"}]

    def boom(tid, tok):
        raise RuntimeError("graphql down")

    monkeypatch.setattr(ghmod, "_graphql_resolve_thread", boom)
    # best-effort housekeeping must never break the run, but must log
    with caplog.at_level("WARNING"):
        ghmod._resolve_addressed_threads(threads, "tok", {"aaaaaaaaaaaa"})
    assert "failed to resolve addressed review threads" in caplog.text


# ---- last reviewed sha ----


def _reviews_pr(reviews):
    pr = MagicMock()
    pr.get_reviews.return_value = reviews
    return pr


def test_last_reviewed_sha_reads_marker_from_most_recent_argus_review(monkeypatch):
    older = MagicMock()
    older.body = f"❌ **Argus** — changes requested\n\n<!-- argus:reviewed-sha:{'a' * 40} -->"
    newer = MagicMock()
    newer.body = f"✅ **Argus** — looks good\n\n<!-- argus:reviewed-sha:{'b' * 40} -->"
    pr = _reviews_pr([older, newer])
    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(ghmod, "Github", lambda *a, **k: gh)

    assert ghmod.last_reviewed_sha("o/r", 1, "tok") == "b" * 40


def test_last_reviewed_sha_ignores_non_argus_reviews(monkeypatch):
    human = MagicMock()
    human.body = f"looks fine to me <!-- argus:reviewed-sha:{'a' * 40} -->"
    pr = _reviews_pr([human])
    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(ghmod, "Github", lambda *a, **k: gh)

    assert ghmod.last_reviewed_sha("o/r", 1, "tok") is None


def test_last_reviewed_sha_returns_none_when_no_reviews(monkeypatch):
    pr = _reviews_pr([])
    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(ghmod, "Github", lambda *a, **k: gh)

    assert ghmod.last_reviewed_sha("o/r", 1, "tok") is None


def test_last_reviewed_sha_swallows_errors(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ghmod, "Github", boom)

    with caplog.at_level("WARNING"):
        assert ghmod.last_reviewed_sha("o/r", 1, "tok") is None
    assert "failed to read last-reviewed SHA" in caplog.text


def test_post_to_github_stamps_reviewed_sha_into_the_review_body(monkeypatch):
    pr = _fake_pr([None])
    pr.head.sha = "d" * 40
    _patch_github(monkeypatch, pr)

    _post(findings=[_finding(2)])

    body = pr.create_review.call_args_list[0].kwargs["body"]
    assert f"<!-- argus:reviewed-sha:{'d' * 40} -->" in body


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


def test_fetch_review_threads_swallows_errors(monkeypatch, caplog):
    monkeypatch.setattr(
        ghmod,
        "_graphql_review_threads",
        lambda *a: (_ for _ in ()).throw(RuntimeError("down")),
    )
    with caplog.at_level("WARNING"):
        assert ghmod._fetch_review_threads("o/r", 1, "tok") == []
    assert "failed to fetch review threads" in caplog.text


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

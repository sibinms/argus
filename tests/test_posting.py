from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.posting import github as ghmod
from argus.posting.github import (
    _fingerprint,
    _patch_new_lines,
    commentable_lines,
    partition_findings,
)


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
    pr.get_issue_comments.return_value = []  # no rolling summary yet
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


# ---- pure logic ----


def test_partition_splits_new_and_addressed():
    new, addressed = partition_findings({"a", "b"}, {"b", "c"})
    assert new == {"a"}
    assert addressed == {"c"}


def test_fingerprint_is_stable_for_the_same_finding():
    assert _fingerprint(_finding(2)) == _fingerprint(_finding(2))
    assert _fingerprint(_finding(2)) != _fingerprint(_finding(3))


# ---- posting behaviour ----


def test_new_finding_is_posted_inline(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [_finding(2)], PostingConfig(min_confidence="low"))

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

    ghmod.post_to_github("o/r", 1, "tok", [f], PostingConfig(min_confidence="low"))

    # nothing new + unchanged verdict -> no new review stacked
    pr.create_review.assert_not_called()
    # but the rolling summary is still refreshed
    assert pr.create_issue_comment.called


def test_finding_on_non_diff_line_is_not_inline(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [_finding(999)], PostingConfig(min_confidence="low"))

    # verdict still set, but no inline comment on an off-diff line
    assert not pr.create_review.call_args_list[0].kwargs.get("comments")


def test_clean_pr_comments_by_default(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_args_list[0].kwargs["event"] == "COMMENT"


def test_clean_pr_approves_when_enabled(monkeypatch):
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github(
        "o/r", 1, "tok", [], PostingConfig(min_confidence="low", approve_reviews=True)
    )

    assert pr.create_review.call_args_list[0].kwargs["event"] == "APPROVE"


def test_approve_falls_back_to_comment_when_repo_disallows(monkeypatch):
    err = GithubException(
        422,
        data={"message": "GitHub Actions is not permitted to approve pull requests."},
        headers=None,
    )
    pr = _fake_pr([err, None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github(
        "o/r", 1, "tok", [], PostingConfig(min_confidence="low", approve_reviews=True)
    )

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[0].kwargs["event"] == "APPROVE"
    assert pr.create_review.call_args_list[1].kwargs["event"] == "COMMENT"


def test_falls_back_to_body_only_on_unresolvable_line(monkeypatch):
    err = GithubException(422, data={"message": "Line could not be resolved"}, headers=None)
    pr = _fake_pr([err, None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [_finding(2)], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[0].kwargs["comments"]  # first tried inline
    assert not pr.create_review.call_args_list[1].kwargs.get("comments")  # retry body-only


def test_non_422_error_is_not_masked(monkeypatch):
    err = GithubException(401, data={"message": "Bad credentials"}, headers=None)
    pr = _fake_pr(err)
    _patch_github(monkeypatch, pr)

    with pytest.raises(GithubException):
        ghmod.post_to_github("o/r", 1, "tok", [_finding(2)], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 1  # no pointless retry on a real error


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

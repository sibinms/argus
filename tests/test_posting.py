from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.posting import github as ghmod
from argus.posting.github import _patch_new_lines


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


def _fake_pr(create_review_side_effect):
    pr = MagicMock()
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1,1 +1,2 @@\n a\n+b\n"  # new-file lines {1, 2}
    pr.get_files.return_value = [changed]
    pr.create_review.side_effect = create_review_side_effect
    return pr


def _patch_github(monkeypatch, pr):
    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(ghmod, "Github", lambda *a, **k: gh)


def test_falls_back_to_body_only_on_422(monkeypatch):
    err = GithubException(422, data={"message": "Line could not be resolved"}, headers=None)
    pr = _fake_pr([err, None])  # first call (with comments) 422s, retry succeeds
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [_finding(2)], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 2
    assert pr.create_review.call_args_list[0].kwargs["comments"]  # first tried inline
    assert not pr.create_review.call_args_list[1].kwargs.get("comments")  # retry body-only


def test_finding_on_non_diff_line_is_excluded(monkeypatch):
    pr = _fake_pr([None])  # create_review succeeds
    _patch_github(monkeypatch, pr)

    # patch touches new-file lines {1, 2}; a finding on line 999 is off-diff
    ghmod.post_to_github("o/r", 1, "tok", [_finding(999)], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 1
    assert not pr.create_review.call_args_list[0].kwargs.get("comments")  # excluded


def test_clean_pr_posts_comment_not_approve(monkeypatch):
    # No findings -> "approve" verdict. It must post as COMMENT, because the
    # Actions token can't submit an APPROVE review (that 422s and fails the run).
    pr = _fake_pr([None])
    _patch_github(monkeypatch, pr)

    ghmod.post_to_github("o/r", 1, "tok", [], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 1
    assert pr.create_review.call_args_list[0].kwargs["event"] == "COMMENT"


def test_non_422_error_is_not_masked(monkeypatch):
    err = GithubException(401, data={"message": "Bad credentials"}, headers=None)
    pr = _fake_pr(err)
    _patch_github(monkeypatch, pr)

    with pytest.raises(GithubException):
        ghmod.post_to_github("o/r", 1, "tok", [_finding(2)], PostingConfig(min_confidence="low"))

    assert pr.create_review.call_count == 1  # no pointless retry on a real error


def test_added_and_context_lines_are_commentable():
    patch = "@@ -10,3 +10,4 @@ def f():\n context_a\n+added_b\n context_c\n-removed_d\n"
    # new-file numbering starts at 10: context_a=10, added_b=11, context_c=12
    assert _patch_new_lines(patch) == {10, 11, 12}


def test_removed_lines_do_not_advance_new_file_counter():
    patch = "@@ -5,4 +5,2 @@\n-gone_1\n-gone_2\n kept_5\n+new_6\n"
    # removed lines contribute no new-file line; kept_5=5, new_6=6
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

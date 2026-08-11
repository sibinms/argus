import subprocess
from unittest.mock import MagicMock

import github
import pytest
from github.GithubException import GithubException

from argus.config import ContextConfig
from argus.context.gather import (
    _MAX_PROJECT_STANDARDS_ATTEMPTS,
    _is_relative_import,
    _resolve_project_standards,
    gather_github,
    gather_local,
)


def test_gather_github_handles_get_contents_failure(monkeypatch):
    """If the API can't return a file's content, we keep the diff and set
    content to None rather than failing the whole review."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "abc123"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert len(ctx.changed_files) == 1
    assert ctx.changed_files[0].path == "a.py"
    assert ctx.changed_files[0].content is None


def test_gather_github_content_fetch_survives_a_non_github_error(monkeypatch):
    """File/project-standards content is optional context, not the review
    itself -- a transient failure that isn't a GithubException (a network
    timeout, DNS failure, rate limit, ...) must degrade to "no content" on
    both the changed-file loop and the project-standards fetch, not crash
    the whole run."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = TimeoutError("network is unreachable")

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert ctx.changed_files[0].content is None
    assert ctx.project_standards == ""
    # The failure must be scoped to content-fetching -- the diff itself
    # comes from pr_file.patch, never from get_contents, so it must survive
    # untouched even though every get_contents call raised.
    assert "@@ -1 +1 @@" in ctx.diff and "+x" in ctx.diff


def test_gather_github_content_fetch_propagates_a_programming_error(monkeypatch):
    """_fetch_content's except is bounded to GithubException and OSError
    (network/API failures), not bare Exception -- a genuine bug elsewhere in
    the call (e.g. a TypeError from an unexpected response shape) must fail
    loudly rather than silently degrade to "no content", or a real
    regression in this path would be invisible outside the test suite."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = TypeError("unexpected response shape")

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with pytest.raises(TypeError):
        gather_github("o/r", 1, "tok", ContextConfig())


def test_gather_github_content_fetch_survives_decoded_content_assertion_error(monkeypatch):
    """PyGithub's ContentFile.decoded_content asserts encoding == "base64",
    which GitHub's Contents API doesn't set for a file over ~1MB -- this
    specific, verified AssertionError must degrade to "no content" like any
    other optional-content failure, not crash the whole review."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    blob = MagicMock()
    type(blob).decoded_content = property(
        lambda self: (_ for _ in ()).throw(AssertionError("unsupported encoding: none"))
    )

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.return_value = blob
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert ctx.changed_files[0].content is None
    assert ctx.project_standards == ""


def test_gather_github_get_contents_assertion_error_still_propagates(monkeypatch):
    """The AssertionError guard is scoped to the documented decoded_content
    quirk specifically, not the get_contents call itself -- an AssertionError
    raised by get_contents (a different, unverified failure mode) must still
    fail loudly rather than silently degrade to "no content"."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = AssertionError("unexpected internal state")

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with pytest.raises(AssertionError):
        gather_github("o/r", 1, "tok", ContextConfig())


def test_gather_github_sets_a_client_timeout(monkeypatch):
    captured = {}

    def fake_github(*args, **kwargs):
        captured.update(kwargs)
        pr = MagicMock()
        pr.title = ""
        pr.body = ""
        pr.head.sha = "s"
        pr.get_files.return_value = []
        gh = MagicMock()
        gh.get_repo.return_value.get_pull.return_value = pr
        return gh

    monkeypatch.setattr(github, "Github", fake_github)
    gather_github("o/r", 1, "tok", ContextConfig())
    assert captured.get("timeout")


def test_gather_local_sets_subprocess_timeouts(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        result = MagicMock()
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    gather_local("base", "head", ContextConfig())
    assert calls and all("timeout" in kwargs for kwargs in calls)


def test_gather_github_excludes_ignored_files_from_the_diff_itself(monkeypatch):
    """ignore_globs must exclude a file's diff hunk, not just its optional
    full-file dump — otherwise a lens still reads the "ignored" file's
    changes via the raw diff text."""
    kept = MagicMock()
    kept.filename = "app.py"
    kept.patch = "@@ -1 +1 @@\n+real change\n"
    ignored = MagicMock()
    ignored.filename = "yarn.lock"
    ignored.patch = "@@ -1 +1 @@\n+lockfile noise\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "s"
    pr.get_files.return_value = [kept, ignored]
    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(ignore_globs=["yarn.lock"]))

    assert "real change" in ctx.diff
    assert "lockfile noise" not in ctx.diff


def test_gather_github_does_not_fetch_content_for_ignored_files(monkeypatch):
    """apply_budget drops ignored files from changed_files entirely, so
    fetching their content is a wasted API call -- it must be skipped, not
    fetched then discarded."""
    kept = MagicMock()
    kept.filename = "app.py"
    kept.patch = "@@ -1 +1 @@\n+real change\n"
    ignored = MagicMock()
    ignored.filename = "yarn.lock"
    ignored.patch = "@@ -1 +1 @@\n+lockfile noise\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "s"
    pr.get_files.return_value = [kept, ignored]
    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    gather_github(
        "o/r", 1, "tok", ContextConfig(ignore_globs=["yarn.lock"], project_standards_files=[])
    )

    fetched_paths = [call.args[0] for call in repo.get_contents.call_args_list]
    assert fetched_paths == ["app.py"]


def test_gather_github_since_sha_with_no_changes_yields_empty_context(monkeypatch):
    """since_sha valid but repo.compare returns no files (e.g. re-invoking
    Argus with no new commits since the last review) should produce an
    empty, not-erroring Context, not fall back to the full diff."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [MagicMock(filename="full-diff-only.py", patch="+full")]

    comparison = MagicMock()
    comparison.files = []
    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.compare.return_value = comparison
    repo.get_commit.return_value.parents = [MagicMock()]  # one parent -- not a merge commit

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="samesha")

    assert ctx.diff == ""
    assert ctx.changed_files == []
    assert ctx.changed_paths == []
    pr.get_files.assert_not_called()


def test_gather_github_scopes_to_since_sha_when_given(monkeypatch):
    """With since_sha, the diff should come from repo.compare(since_sha,
    head) instead of the PR's full base...head diff."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+incremental change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [MagicMock(filename="full-diff-only.py", patch="+full")]

    comparison = MagicMock()
    comparison.files = [changed]
    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.compare.return_value = comparison
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    repo.get_commit.return_value.parents = [MagicMock()]  # one parent -- not a merge commit

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    repo.compare.assert_called_once_with("oldsha", "headsha")
    assert ctx.changed_paths == ["a.py"]
    assert "incremental change" in ctx.diff
    pr.get_files.assert_not_called()


def test_gather_github_skips_incremental_diff_for_a_merge_commit_head(monkeypatch, caplog):
    """A two-dot compare (since_sha...head) isn't merge-base-aware the way
    GitHub's own three-dot PR diff is -- after a "merge base-branch into
    this branch" commit, it would silently include every commit that landed
    on the base branch since since_sha too, not just this PR's own work
    (see #65). A merge commit has more than one parent; when the head is
    one, skip repo.compare entirely and use the full base diff instead."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+full change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "mergesha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    repo.get_commit.return_value.parents = [MagicMock(), MagicMock()]  # two parents -- a merge

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    repo.compare.assert_not_called()
    pr.get_files.assert_called_once()
    assert ctx.changed_paths == ["a.py"]
    assert "merge commit" in caplog.text


def test_gather_github_falls_back_to_full_diff_when_get_commit_fails(monkeypatch, caplog):
    """get_commit(), not just compare(), can fail (network timeout, API
    error, permissions) -- it's in the same try block as compare, so it
    should fall back to the full diff the same way a failed compare does,
    rather than being an untested gap in the merge-commit check itself."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+full change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_commit.side_effect = GithubException(404, data={}, headers=None)
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    repo.compare.assert_not_called()
    pr.get_files.assert_called_once()
    assert ctx.changed_paths == ["a.py"]
    assert "failed to compare since_sha to head" in caplog.text


def test_gather_github_get_commit_swallows_non_github_errors(monkeypatch, caplog):
    """As with compare() (see test_gather_github_compare_swallows_non_github_errors),
    a non-GithubException failure (network timeout, etc.) from get_commit()
    must still fall back to the full diff, not crash the run."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+full change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_commit.side_effect = TimeoutError("connection timed out")
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    repo.compare.assert_not_called()
    pr.get_files.assert_called_once()
    assert ctx.changed_paths == ["a.py"]
    assert "failed to compare since_sha to head" in caplog.text


def test_gather_github_falls_back_to_full_diff_when_compare_fails(monkeypatch):
    """A since_sha that's no longer reachable (e.g. a force-push rewrote it
    out of history) shouldn't break the run -- fall back to the full PR
    diff, same as having no since_sha at all."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+full change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.compare.side_effect = GithubException(404, data={}, headers=None)
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    repo.get_commit.return_value.parents = [MagicMock()]  # one parent -- not a merge commit

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="gone-sha")

    pr.get_files.assert_called_once()
    assert ctx.changed_paths == ["a.py"]
    assert "full change" in ctx.diff


def test_gather_github_without_since_sha_uses_full_diff(monkeypatch):
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    repo.compare.assert_not_called()
    pr.get_files.assert_called_once()
    assert ctx.changed_paths == ["a.py"]


def test_gather_github_compare_swallows_non_github_errors(monkeypatch, caplog):
    """A network-level failure (not a GithubException) comparing since_sha
    to head must still fall back to the full diff, not crash the run --
    incremental diffing is an optimization, not the review itself."""
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+full change\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "headsha"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.compare.side_effect = TimeoutError("connection timed out")
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    repo.get_commit.return_value.parents = [MagicMock()]  # one parent -- not a merge commit

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    assert ctx.changed_paths == ["a.py"]
    assert "failed to compare since_sha to head" in caplog.text


def test_gather_github_changed_paths_excludes_ignored_files(monkeypatch):
    """changed_paths must reflect what a lens actually saw, not the raw diff
    -- an ignored file's patch is never included, so it must never count as
    "touched" either (posting uses this to decide whether a no-longer-raised
    finding is safe to treat as addressed)."""
    kept = MagicMock()
    kept.filename = "app.py"
    kept.patch = "@@ -1 +1 @@\n+real change\n"
    ignored = MagicMock()
    ignored.filename = "yarn.lock"
    ignored.patch = "@@ -1 +1 @@\n+lockfile noise\n"

    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "s"
    pr.get_files.return_value = [kept, ignored]
    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(ignore_globs=["yarn.lock"]))

    assert ctx.changed_paths == ["app.py"]


def test_gather_local_sets_changed_paths(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "app.py").write_text("old\n")
    run("add", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "app.py").write_text("new\n")
    run("add", "app.py")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    ctx = gather_local("base", "HEAD", ContextConfig())

    assert ctx.changed_paths == ["app.py"]


def test_gather_local_excludes_ignored_files_from_the_diff_itself(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "app.py").write_text("old\n")
    (tmp_path / "yarn.lock").write_text("old\n")
    run("add", "app.py", "yarn.lock")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "app.py").write_text("real change\n")
    (tmp_path / "yarn.lock").write_text("lockfile noise\n")
    run("add", "app.py", "yarn.lock")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    ctx = gather_local("base", "HEAD", ContextConfig(ignore_globs=["yarn.lock"]))

    assert "real change" in ctx.diff
    assert "lockfile noise" not in ctx.diff
    # the ignored file still appears in changed_files' path list via
    # apply_budget's own filtering — just not with diff/content leaked in.
    assert all(f.path != "yarn.lock" for f in ctx.changed_files)
    # changed_paths must match: a lens was never shown yarn.lock, so it must
    # never count as "touched" for posting's addressed-thread scoping.
    assert ctx.changed_paths == ["app.py"]


def test_gather_github_warns_on_non_404_standards_fetch_failure(monkeypatch, caplog):
    # A plain "file doesn't exist" (404) is the expected, common case for
    # most repos' project_standards_files -- but a real failure (rate limit,
    # 5xx) silently degrading the same way would make a transient error
    # indistinguishable from "no such file", so it must warn.
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    pr.get_files.return_value = []

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(403, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert ctx.project_standards == ""
    assert "CLAUDE.md" in caplog.text or "AGENTS.md" in caplog.text


def test_gather_github_stays_quiet_on_a_plain_404_for_standards(monkeypatch, caplog):
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    pr.get_files.return_value = []

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(404, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        gather_github("o/r", 1, "tok", ContextConfig())

    assert caplog.text == ""


def test_gather_github_stays_quiet_on_a_missing_changed_file_even_on_error(monkeypatch, caplog):
    # The lenient (changed-file) path must never warn -- a missing/renamed/
    # deleted file is routine on nearly every PR, unlike the standards path.
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    changed = MagicMock()
    changed.filename = "a.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = GithubException(403, data={}, headers=None)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    with caplog.at_level("WARNING"):
        gather_github("o/r", 1, "tok", ContextConfig(project_standards_files=[]))

    assert caplog.text == ""


def test_gather_github_reads_project_standards_from_base_sha_not_head(monkeypatch):
    """Same base-not-head security property as gather_local, but via the
    GitHub API: get_contents must be called with ref=pr.base.sha, and a
    version of AGENTS.md that only exists at head must never surface."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    pr.get_files.return_value = []

    def fake_get_contents(path, ref=None):
        if path == "AGENTS.md" and ref == "base-sha":
            blob = MagicMock()
            blob.decoded_content = b"Original rule: no console.log."
            return blob
        raise GithubException(404, data={}, headers=None)

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = fake_get_contents
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert "Original rule: no console.log." in ctx.project_standards


def test_gather_github_drops_non_utf8_standards_file_but_keeps_a_sibling(monkeypatch):
    """Regression test for the GitHub path specifically: a non-UTF-8
    CLAUDE.md must be skipped (matching gather_local's _read_at_base and
    _read_file), not garbled-included -- and, since this is a per-file
    fetch, a co-existing readable AGENTS.md must still load rather than the
    whole chain aborting on the first unreadable entry."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    pr.get_files.return_value = []

    def fake_get_contents(path, ref=None):
        blob = MagicMock()
        if path == "CLAUDE.md":
            blob.decoded_content = b"R\xe9sum\xe9 of the rules."  # invalid UTF-8
            return blob
        if path == "AGENTS.md":
            blob.decoded_content = b"Detailed standards."
            return blob
        raise GithubException(404, data={}, headers=None)

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.side_effect = fake_get_contents
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig())

    assert "# CLAUDE.md" not in ctx.project_standards
    assert "Detailed standards." in ctx.project_standards


def test_gather_github_changed_file_content_keeps_ignore_decode_for_non_utf8(monkeypatch):
    """The regular changed-file loop is informational lens context, not the
    project-standards trust boundary -- a non-UTF-8 changed file must still
    be included with invalid bytes stripped (the pre-existing behavior),
    not dropped entirely the way a non-UTF-8 standards file is."""
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    changed = MagicMock()
    changed.filename = "legacy.py"
    changed.patch = "@@ -1 +1 @@\n+x\n"
    pr.get_files.return_value = [changed]

    blob = MagicMock()
    blob.decoded_content = b"# R\xe9sum\xe9 comment\nprint(1)\n"  # invalid UTF-8

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_contents.return_value = blob
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(project_standards_files=[]))

    assert ctx.changed_files[0].content is not None
    assert "print(1)" in ctx.changed_files[0].content


def test_gather_github_project_standards_disabled_when_configured_empty(monkeypatch):
    pr = MagicMock()
    pr.title = "t"
    pr.body = "b"
    pr.head.sha = "head-sha"
    pr.base.sha = "base-sha"
    pr.get_files.return_value = []

    repo = MagicMock()
    repo.get_pull.return_value = pr
    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(project_standards_files=[]))

    assert ctx.project_standards == ""
    repo.get_contents.assert_not_called()


def test_gather_local_reads_project_standards_from_base_ref_not_head(tmp_path, monkeypatch):
    """A PR must not be able to rewrite its own review rules within the same
    diff being reviewed -- project standards come from base, not head, even
    though head is what's actually being checked out and reviewed."""

    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "AGENTS.md").write_text("Original rule: no console.log.")
    (tmp_path / "app.py").write_text("old\n")
    run("add", "AGENTS.md", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "AGENTS.md").write_text("Rewritten rule: console.log is fine now.")
    (tmp_path / "app.py").write_text("console.log('debug')\n")
    run("add", "AGENTS.md", "app.py")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    ctx = gather_local("base", "HEAD", ContextConfig())

    assert "Original rule: no console.log." in ctx.project_standards
    assert "Rewritten rule" not in ctx.project_standards


def test_gather_local_project_standards_disabled_when_configured_empty(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "AGENTS.md").write_text("Some rule.")
    (tmp_path / "app.py").write_text("old\n")
    run("add", "AGENTS.md", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "app.py").write_text("new\n")
    run("add", "app.py")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    ctx = gather_local("base", "HEAD", ContextConfig(project_standards_files=[]))

    assert ctx.project_standards == ""


def test_gather_local_project_standards_drops_non_utf8_file_but_keeps_a_sibling(
    tmp_path, monkeypatch
):
    """A CLAUDE.md/AGENTS.md that isn't valid UTF-8 is optional context, same
    as any other file gather_local reads -- it must degrade to "file
    skipped", matching _read_file's own UnicodeDecodeError handling, rather
    than crash the whole run or silently include corrupted bytes as if they
    were authoritative repo rules. A co-existing readable entry point must
    still load -- this is a per-file skip, not a whole-chain abort on the
    first unreadable file."""

    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "CLAUDE.md").write_text("Top-level rules.")
    # Latin-1 bytes that aren't valid UTF-8 (0xE9 alone is a continuation
    # byte with no valid lead byte).
    (tmp_path / "AGENTS.md").write_bytes(b"R\xe9sum\xe9 of the rules.\n")
    (tmp_path / "app.py").write_text("old\n")
    run("add", "CLAUDE.md", "AGENTS.md", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "app.py").write_text("new\n")
    run("add", "app.py")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    ctx = gather_local("base", "HEAD", ContextConfig())

    assert "Top-level rules." in ctx.project_standards
    assert "# AGENTS.md" not in ctx.project_standards


def test_gather_local_project_standards_survives_git_show_timeout(monkeypatch):
    """_read_at_base's own subprocess.run call must be guarded like every
    other optional-content read in this file -- a `git show` that times out
    or raises OSError on the standards file is a nice-to-have failing, not a
    reason to crash the whole review."""

    def fake_run(args, **kwargs):
        if args[:2] == ["git", "show"]:
            raise subprocess.TimeoutExpired(cmd=args, timeout=60)
        result = MagicMock()
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = gather_local("base", "head", ContextConfig())

    assert ctx.project_standards == ""


def test_gather_local_warns_on_git_show_timeout(monkeypatch, caplog):
    # A real process-level failure reading a standards file must not be
    # silently indistinguishable from "no such file" -- unlike a plain
    # nonexistent file (a nonzero git exit code), which stays quiet since
    # most repos don't have every entry in project_standards_files.
    def fake_run(args, **kwargs):
        if args[:2] == ["git", "show"]:
            raise subprocess.TimeoutExpired(cmd=args, timeout=60)
        result = MagicMock()
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    with caplog.at_level("WARNING"):
        gather_local("base", "head", ContextConfig())

    assert "CLAUDE.md" in caplog.text or "AGENTS.md" in caplog.text


def test_gather_local_stays_quiet_when_a_standards_file_simply_does_not_exist(
    tmp_path, monkeypatch, caplog
):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "app.py").write_text("old\n")
    run("add", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (tmp_path / "app.py").write_text("new\n")
    run("add", "app.py")
    run("commit", "-qm", "change")

    monkeypatch.chdir(tmp_path)
    with caplog.at_level("WARNING"):
        ctx = gather_local("base", "HEAD", ContextConfig())

    assert ctx.project_standards == ""
    assert caplog.text == ""


def test_resolve_project_standards_returns_empty_when_nothing_found():
    assert _resolve_project_standards(lambda path: None, ["CLAUDE.md", "AGENTS.md"]) == ""


def test_resolve_project_standards_reads_a_single_entry_point():
    files = {"AGENTS.md": "Be nice to reviewers."}
    result = _resolve_project_standards(files.get, ["AGENTS.md"])
    assert "AGENTS.md" in result
    assert "Be nice to reviewers." in result


def test_resolve_project_standards_follows_at_import_lines():
    files = {
        "CLAUDE.md": "Top-level rules.\n@AGENTS.md\n",
        "AGENTS.md": "Detailed standards.\n@./.nomod/content.md\n",
        ".nomod/content.md": "Voice and tone rules.",
    }
    result = _resolve_project_standards(files.get, ["CLAUDE.md"])
    assert "Top-level rules." in result
    assert "Detailed standards." in result
    assert "Voice and tone rules." in result


def test_resolve_project_standards_ignores_at_mentions_mid_line():
    # Only a line that is *just* "@path.md" counts as an import -- an
    # "@username" mention elsewhere in prose must not be treated as a file
    # to fetch.
    calls = []

    def read(path):
        calls.append(path)
        if path == "AGENTS.md":
            return "Thanks @octocat for the review, see docs/style.md for more."
        return None

    result = _resolve_project_standards(read, ["AGENTS.md"])
    assert calls == ["AGENTS.md"]
    assert "octocat" in result


def test_resolve_project_standards_ignores_at_prefixed_prose_ending_in_md():
    # A line can start with "@" and end in ".md" without being an import --
    # only a line that is *exactly* "@path.md" counts. This line both starts
    # with "@" and ends in ".md" (unlike the mid-line "@octocat" mention
    # covered above), so it's the case that actually exercises the guard.
    calls = []

    def read(path):
        calls.append(path)
        if path == "AGENTS.md":
            return "@octocat's notes are in other.md"
        return None

    result = _resolve_project_standards(read, ["AGENTS.md"])
    assert calls == ["AGENTS.md"]
    assert "octocat" in result


def test_resolve_project_standards_ignores_non_ascii_whitespace_in_at_line():
    # A line with an internal tab or non-breaking space still isn't *only*
    # "@path.md" -- checking for an absent ASCII space alone wouldn't catch
    # either of these.
    calls = []

    def read(path):
        calls.append(path)
        if path == "AGENTS.md":
            return "@docs/standards.md\tnotes.md\n@docs/other.md notes.md"
        return None

    result = _resolve_project_standards(read, ["AGENTS.md"])
    assert calls == ["AGENTS.md"]
    assert "AGENTS.md" in result


def test_resolve_project_standards_does_not_refetch_a_cycle():
    calls = []

    def read(path):
        calls.append(path)
        if path == "CLAUDE.md":
            return "@AGENTS.md"
        if path == "AGENTS.md":
            return "@CLAUDE.md"  # cycle back to the entry point
        return None

    result = _resolve_project_standards(read, ["CLAUDE.md"])
    assert calls.count("CLAUDE.md") == 1
    assert calls.count("AGENTS.md") == 1
    assert "CLAUDE.md" in result
    assert "AGENTS.md" in result


def test_resolve_project_standards_caps_total_files():
    # A long or self-referential chain must not fetch unbounded content.
    def read(path):
        n = int(path.removeprefix("f").removesuffix(".md"))
        return f"@f{n + 1}.md"

    result = _resolve_project_standards(read, ["f0.md"])
    assert result.count("# f") <= 8


def test_resolve_project_standards_missing_entry_point_does_not_consume_a_slot():
    # Regression test: a nonexistent CLAUDE.md (the common case -- most
    # repos only have AGENTS.md, but both are default entry points) must not
    # eat one of the 8 included-file slots, or a chain of 8 genuinely valid
    # imports from AGENTS.md would silently lose its last one to the
    # nonexistent CLAUDE.md contributing nothing.
    def read(path):
        if path == "CLAUDE.md":
            return None
        if path == "AGENTS.md":
            return "@f0.md"
        n = int(path.removeprefix("f").removesuffix(".md"))
        if n < 7:
            return f"@f{n + 1}.md"
        return "leaf"

    result = _resolve_project_standards(read, ["CLAUDE.md", "AGENTS.md"])
    assert result.count("# ") == 8  # AGENTS.md + f0..f6, all 8 real files
    assert "leaf" not in result  # f7 (the 9th real file) is correctly capped


def test_resolve_project_standards_caps_total_attempts_on_bogus_imports():
    # A single successfully-read file listing many nonexistent @imports
    # would never trip the included-files cap (which only counts
    # successes, and that one real file is the only success here) -- the
    # separate, more generous attempts cap must still bound total fetch
    # attempts so a long list of typo'd imports can't run away.
    calls = []
    bogus_imports = "\n".join(f"@missing{i}.md" for i in range(100))

    def read(path):
        calls.append(path)
        if path == "AGENTS.md":
            return bogus_imports
        return None  # every @missingN.md fails to resolve

    result = _resolve_project_standards(read, ["AGENTS.md"])
    assert "AGENTS.md" in result
    assert len(calls) <= _MAX_PROJECT_STANDARDS_ATTEMPTS
    assert len(calls) < 100  # proves the cap actually cut off the 100 bogus imports


def test_resolve_project_standards_strips_leading_dot_slash():
    files = {"docs/standards.md": "Team conventions."}
    result = _resolve_project_standards(files.get, ["./docs/standards.md"])
    assert "Team conventions." in result


def test_resolve_project_standards_ignores_parent_and_absolute_import_lines():
    # "@relative/path.md" is the documented shape -- "@../x.md" and "@/x.md"
    # both match the regex's \S+ but aren't relative paths within the repo,
    # so they must not be queued as imports at all.
    calls = []

    def read(path):
        calls.append(path)
        if path == "AGENTS.md":
            return "@../secrets.md\n@/etc/passwd.md\n@docs/style.md"
        if path == "docs/style.md":
            return "Real import."
        return None

    result = _resolve_project_standards(read, ["AGENTS.md"])
    assert calls == ["AGENTS.md", "docs/style.md"]
    assert "Real import." in result


def test_is_relative_import_accepts_ordinary_relative_paths():
    assert _is_relative_import("docs/style.md") is True
    assert _is_relative_import("style.md") is True


def test_is_relative_import_rejects_leading_slash():
    assert _is_relative_import("/etc/passwd.md") is False


def test_is_relative_import_rejects_tilde():
    assert _is_relative_import("~/x.md") is False


def test_is_relative_import_rejects_leading_dotdot():
    assert _is_relative_import("../secrets.md") is False


def test_is_relative_import_rejects_embedded_dotdot():
    assert _is_relative_import("a/../secrets.md") is False


def test_is_relative_import_rejects_backslash_dotdot():
    assert _is_relative_import("..\\secrets.md") is False
    assert _is_relative_import("a\\..\\secrets.md") is False

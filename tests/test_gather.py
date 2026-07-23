import subprocess
from unittest.mock import MagicMock

import github
from github.GithubException import GithubException

from argus.config import ContextConfig
from argus.context.gather import gather_github, gather_local


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

    gather_github("o/r", 1, "tok", ContextConfig(ignore_globs=["yarn.lock"]))

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

    gh = MagicMock()
    gh.get_repo.return_value = repo
    monkeypatch.setattr(github, "Github", lambda *a, **k: gh)

    ctx = gather_github("o/r", 1, "tok", ContextConfig(), since_sha="oldsha")

    repo.compare.assert_called_once_with("oldsha", "headsha")
    assert ctx.changed_paths == ["a.py"]
    assert "incremental change" in ctx.diff
    pr.get_files.assert_not_called()


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

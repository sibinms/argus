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

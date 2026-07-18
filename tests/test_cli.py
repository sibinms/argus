import subprocess

from click.testing import CliRunner

from argus import cli


def _make_repo_with_diff(path):
    def run(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    (path / "app.py").write_text("print('hello')\n")
    run("add", "app.py")
    run("commit", "-qm", "init")
    run("branch", "base")
    (path / "app.py").write_text("def foo(x):\n    return x.attr\n")
    run("add", "app.py")
    run("commit", "-qm", "change")


def test_active_mode_without_github_does_not_error(tmp_path, monkeypatch):
    """mode: active is now the default, but a local diff run has no PR to
    post to — it should fall back to a local report instead of erroring."""
    _make_repo_with_diff(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_review", lambda context, config: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["review", "--base", "base", "--head", "HEAD"])

    assert result.exit_code == 0
    assert "writing a local report instead" in result.output
    assert (tmp_path / "argus-report.md").exists()

import subprocess

from click.testing import CliRunner

from argus import cli
from argus.config import Config


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


def test_detect_github_pr_returns_none_on_malformed_event_file(tmp_path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text("{not valid json")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    assert cli._detect_github_pr() is None


def test_lens_and_curator_model_flags_override_config(tmp_path, monkeypatch):
    """--lens-model/--curator-model should let a user pick a model without
    committing .argus/config.yml at all."""
    _make_repo_with_diff(tmp_path)
    monkeypatch.chdir(tmp_path)

    seen_configs = []

    def fake_run_review(context, config):
        seen_configs.append(config)
        return []

    monkeypatch.setattr(cli, "run_review", fake_run_review)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "review",
            "--base",
            "base",
            "--head",
            "HEAD",
            "--lens-model",
            "gpt-4o-mini",
            "--curator-model",
            "gemini/gemini-2.5-pro",
        ],
    )

    assert result.exit_code == 0
    assert seen_configs[0].models.lens == "gpt-4o-mini"
    assert seen_configs[0].models.curator == "gemini/gemini-2.5-pro"


def test_model_flags_override_an_existing_config_file(tmp_path, monkeypatch):
    """The flags must win over a *committed* config, not just over the
    built-in defaults — otherwise a repo with its own .argus/config.yml
    couldn't be overridden for a quick test."""
    _make_repo_with_diff(tmp_path)
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("models:\n  lens: claude-haiku-4-5\n  curator: claude-opus-4-8\n")

    seen_configs = []
    monkeypatch.setattr(
        cli, "run_review", lambda context, config: seen_configs.append(config) or []
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "review",
            "--config",
            str(config_path),
            "--base",
            "base",
            "--head",
            "HEAD",
            "--lens-model",
            "gpt-4o-mini",
            "--curator-model",
            "gpt-4o",
        ],
    )

    assert result.exit_code == 0
    assert seen_configs[0].models.lens == "gpt-4o-mini"
    assert seen_configs[0].models.curator == "gpt-4o"


def test_model_flags_absent_leave_config_defaults(tmp_path, monkeypatch):
    _make_repo_with_diff(tmp_path)
    monkeypatch.chdir(tmp_path)

    seen_configs = []
    monkeypatch.setattr(
        cli, "run_review", lambda context, config: seen_configs.append(config) or []
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["review", "--base", "base", "--head", "HEAD"])

    assert result.exit_code == 0
    default = Config()
    assert seen_configs[0].models.lens == default.models.lens
    assert seen_configs[0].models.curator == default.models.curator

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import click

from argus.config import DEFAULT_CONFIG_PATH, load_config
from argus.context.gather import gather_github, gather_local
from argus.pipeline import run_review
from argus.posting.github import post_to_github
from argus.posting.shadow import write_shadow_report


def _detect_github_pr() -> tuple[str, int] | None:
    """Reads the repo + PR number out of the GitHub Actions event payload,
    so `argus review --github` needs no flags when run inside a workflow."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not repo or not event_path or not Path(event_path).exists():
        return None

    event = json.loads(Path(event_path).read_text())
    pr_number = event.get("pull_request", {}).get("number") or event.get("number")
    if not pr_number:
        return None
    return repo, int(pr_number)


@click.group()
def main():
    """Argus: an AI PR reviewer built from many narrow lenses and one careful curator."""


@main.command()
def init():
    """Copies the example config to .argus/config.yml in the current repo."""
    example = Path(__file__).parent.parent.parent / ".argus" / "config.yml.example"
    target = DEFAULT_CONFIG_PATH
    if target.exists():
        click.echo(f"{target} already exists, leaving it alone.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(example, target)
    click.echo(f"Wrote {target}. Edit it, then commit it to your repo.")


@main.command()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option(
    "--github", is_flag=True, help="Fetch context from a GitHub PR instead of a local diff."
)
@click.option(
    "--repo", default=None, help="owner/repo, required with --github unless running in Actions."
)
@click.option(
    "--pr",
    "pr_number",
    type=int,
    default=None,
    help="PR number, required with --github unless running in Actions.",
)
@click.option("--base", default="origin/main", help="Base ref for a local diff.")
@click.option("--head", default="HEAD", help="Head ref for a local diff.")
@click.option("--mode", "mode_override", type=click.Choice(["shadow", "active"]), default=None)
@click.option(
    "--lens-model",
    default=None,
    help="Overrides models.lens from .argus/config.yml — any model litellm supports.",
)
@click.option(
    "--curator-model",
    default=None,
    help="Overrides models.curator from .argus/config.yml — any model litellm supports.",
)
def review(
    config_path, github, repo, pr_number, base, head, mode_override, lens_model, curator_model
):
    """Runs the panel against a local diff or a GitHub PR."""
    config = load_config(config_path)
    if mode_override:
        config.mode = mode_override
    if lens_model:
        config.models.lens = lens_model
    if curator_model:
        config.models.curator = curator_model

    if github:
        if repo is None or pr_number is None:
            detected = _detect_github_pr()
            if detected is None:
                raise click.ClickException(
                    "Couldn't detect repo/PR from the environment; pass --repo and --pr."
                )
            repo, pr_number = detected
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise click.ClickException("GITHUB_TOKEN is not set.")
        context = gather_github(repo, pr_number, token, config.context)
    else:
        context = gather_local(base, head, config.context)

    findings = run_review(context, config)

    if config.is_active and github:
        post_to_github(repo, pr_number, token, findings, config.posting)
        click.echo(f"Posted review to {repo}#{pr_number}.")
    elif config.is_active and not github:
        click.echo(
            "mode: active has no effect without --github (there's no PR to post to) — writing a local report instead."
        )

    markdown = write_shadow_report(findings, config.posting)
    click.echo(markdown)


if __name__ == "__main__":
    main()

"""Assembles the Context a lens reviews against: the diff, the changed
files (budgeted), and the PR's own description of intent.

Two entry points: `gather_local` for running against a local git checkout
(diffing against a base ref), and `gather_github` for running inside a
GitHub Action against a real pull request.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only used to shell out to git with a fixed argv list
from dataclasses import dataclass

from argus.config import ContextConfig
from argus.context.budget import apply_budget, is_ignored


@dataclass
class ChangedFile:
    path: str
    content: str | None
    truncated: bool = False


@dataclass
class Context:
    diff: str
    changed_files: list[ChangedFile]
    pr_title: str = ""
    pr_body: str = ""


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def gather_local(base_ref: str, head_ref: str, config: ContextConfig) -> Context:
    """Diffs head_ref against base_ref in the current git checkout."""
    # Fixed argv list, no shell interpolation; "git" is resolved via PATH by design.
    changed_paths = [
        p
        for p in subprocess.run(  # nosec
            ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.splitlines()
        if p
    ]

    # ignore_globs (lockfiles, migrations, ...) are excluded from the diff
    # itself, not just the optional full-file dump below — otherwise a lens
    # still reads the ignored file's hunk as part of "# Diff", just without
    # the extra full-file context.
    included_paths = [p for p in changed_paths if not is_ignored(p, config.ignore_globs)]
    if included_paths:
        # Same rationale as above: fixed argv, no shell, "git" via PATH.
        diff = subprocess.run(  # nosec
            ["git", "diff", f"{base_ref}...{head_ref}", "--", *included_paths],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    else:
        diff = ""

    files = [ChangedFile(path=p, content=_read_file(p)) for p in changed_paths]
    files = apply_budget(files, config)

    return Context(diff=diff, changed_files=files)


def gather_github(
    repo_full_name: str, pr_number: int, token: str, config: ContextConfig
) -> Context:
    """Pulls the diff, changed files, and PR description from the GitHub API."""
    from github import Github
    from github.GithubException import GithubException

    gh = Github(token, timeout=30)
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    diff_parts = []
    files = []
    for pr_file in pr.get_files():
        if not is_ignored(pr_file.filename, config.ignore_globs):
            diff_parts.append(pr_file.patch or "")
        content = None
        try:
            blob = repo.get_contents(pr_file.filename, ref=pr.head.sha)
            if not isinstance(blob, list):
                content = blob.decoded_content.decode("utf-8", "ignore")
        except GithubException:
            # File content is optional context — the diff is always present.
            # If the API can't return the full file (too large, moved/deleted,
            # permissions), review without it rather than failing the run.
            content = None
        files.append(ChangedFile(path=pr_file.filename, content=content))

    files = apply_budget(files, config)

    return Context(
        diff="\n".join(diff_parts),
        changed_files=files,
        pr_title=pr.title or "",
        pr_body=pr.body or "",
    )

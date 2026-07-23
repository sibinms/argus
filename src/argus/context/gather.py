"""Assembles the Context a lens reviews against: the diff, the changed
files (budgeted), and the PR's own description of intent.

Two entry points: `gather_local` for running against a local git checkout
(diffing against a base ref), and `gather_github` for running inside a
GitHub Action against a real pull request.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only used to shell out to git with a fixed argv list
from dataclasses import dataclass, field

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
    pr_summary: str = ""  # planner output; injected into every lens's context
    # Every path in this run's diff scope, *before* budget trims changed_files
    # down to max_files — posting uses this to know which files were actually
    # looked at this run, so it doesn't resolve a thread for a finding whose
    # file was never re-examined (see gather_github's since_sha).
    changed_paths: list[str] = field(default_factory=list)


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

    # changed_paths on the Context is "what a lens actually saw", not every
    # file in the raw diff -- an ignored file's hunk is never in `diff`, so
    # counting it as touched would let posting wrongly resolve a still-open
    # finding on a file the lens was never shown this run.
    return Context(diff=diff, changed_files=files, changed_paths=included_paths)


def gather_github(
    repo_full_name: str,
    pr_number: int,
    token: str,
    config: ContextConfig,
    since_sha: str | None = None,
) -> Context:
    """Pulls the diff, changed files, and PR description from the GitHub API.

    since_sha, when given, scopes the diff to since_sha...head instead of the
    PR's full base...head — a re-review after a small fixup commit then only
    costs what that commit actually changed, not the whole PR again. Falls
    back to the full base diff if since_sha can't be compared (e.g. a
    force-push rewrote it out of the branch's history)."""
    from github import Github
    from github.GithubException import GithubException

    gh = Github(token, timeout=30)
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    pr_files = None
    if since_sha:
        try:
            pr_files = list(repo.compare(since_sha, pr.head.sha).files)
        except GithubException:
            pr_files = None
    if pr_files is None:
        pr_files = list(pr.get_files())

    diff_parts = []
    files = []
    for pr_file in pr_files:
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

    # As in gather_local: changed_paths is "what a lens actually saw", so it
    # excludes ignored files -- their patch is never in `diff`, and counting
    # them as touched would let posting wrongly resolve a still-open finding
    # on a file the lens was never shown this run.
    changed_paths = [
        pr_file.filename
        for pr_file in pr_files
        if not is_ignored(pr_file.filename, config.ignore_globs)
    ]
    files = apply_budget(files, config)

    return Context(
        diff="\n".join(diff_parts),
        changed_files=files,
        pr_title=pr.title or "",
        pr_body=pr.body or "",
        changed_paths=changed_paths,
    )

"""Assembles the Context a lens reviews against: the diff, the changed
files (budgeted), and the PR's own description of intent.

Two entry points: `gather_local` for running against a local git checkout
(diffing against a base ref), and `gather_github` for running inside a
GitHub Action against a real pull request.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404 - only used to shell out to git with a fixed argv list
from collections.abc import Callable
from dataclasses import dataclass, field

from argus.config import ContextConfig
from argus.context.budget import apply_budget, is_ignored

logger = logging.getLogger(__name__)

# Hard cap on how many project-standards files one review will follow
# through @import chains — a real ceiling against a typo'd self-reference or
# an unexpectedly long chain, not a number anyone should expect to hit.
_MAX_PROJECT_STANDARDS_FILES = 8


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
    # The repo's own CLAUDE.md/AGENTS.md (and anything they @import),
    # concatenated — see _resolve_project_standards. Empty if none exist or
    # context.project_standards_files is set to [].
    project_standards: str = ""


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _resolve_project_standards(
    read_file: Callable[[str], str | None], entry_points: list[str]
) -> str:
    """Reads entry_points (e.g. CLAUDE.md, AGENTS.md) and follows any line
    that is *only* "@relative/path.md" as an import, resolved relative to
    repo root — the convention this project's own CLAUDE.md -> AGENTS.md ->
    .nomod/content.md chain already uses. read_file is injected so the same
    logic works against a local git checkout (gather_local) or the GitHub
    API at a specific ref (gather_github); a missing file is just skipped,
    not an error, since most repos won't have all -- or any -- of these.

    Bounded by _MAX_PROJECT_STANDARDS_FILES total and cycle-safe (a path
    already fetched, including an entry point re-imported later, is never
    fetched twice) -- a typo'd self-reference can't loop or blow up context."""
    seen: set[str] = set()
    parts: list[str] = []
    queue = list(entry_points)
    while queue and len(seen) < _MAX_PROJECT_STANDARDS_FILES:
        path = queue.pop(0).removeprefix("./")
        if path in seen:
            continue
        seen.add(path)
        content = read_file(path)
        if content is None:
            continue
        parts.append(f"# {path}\n{content}")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("@") and stripped.endswith(".md"):
                queue.append(stripped[1:])
    return "\n\n".join(parts)


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

    def _read_at_base(path: str) -> str | None:
        # base_ref, not the working tree -- a PR shouldn't be able to
        # rewrite its own review rules within the same diff being reviewed.
        result = subprocess.run(  # nosec
            ["git", "show", f"{base_ref}:{path}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout if result.returncode == 0 else None

    project_standards = _resolve_project_standards(
        _read_at_base, list(config.project_standards_files)
    )

    # changed_paths on the Context is "what a lens actually saw", not every
    # file in the raw diff -- an ignored file's hunk is never in `diff`, so
    # counting it as touched would let posting wrongly resolve a still-open
    # finding on a file the lens was never shown this run.
    return Context(
        diff=diff,
        changed_files=files,
        changed_paths=included_paths,
        project_standards=project_standards,
    )


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
            head_commit = repo.get_commit(pr.head.sha)
            if len(head_commit.parents) > 1:
                # A two-dot compare (since_sha...head) isn't merge-base-aware
                # the way GitHub's own three-dot PR diff is. After a "merge
                # base-branch into this branch" commit, it silently includes
                # every commit that landed on the base branch since since_sha
                # too, not just this PR's own work — e.g. reviewing unrelated
                # code from someone else's already-shipped PR (see #65, found
                # live: an 8-file two-dot diff that was missing this PR's own
                # 2 changed files entirely, in favour of 6 unrelated ones from
                # the base branch). The full base diff below doesn't have
                # this problem by construction, so skip the incremental path
                # entirely for a merge-commit head rather than trusting it.
                logger.warning("head %s is a merge commit, skipping incremental diff", pr.head.sha)
            else:
                pr_files = list(repo.compare(since_sha, pr.head.sha).files)
        except Exception:
            # Incremental diffing is an optimization on top of the core
            # review, not the review itself — any failure here (a real API
            # error, but also a network timeout, DNS failure, or other
            # transient issue GithubException doesn't necessarily wrap)
            # should fall back to the full diff, not crash the run.
            logger.warning("failed to compare since_sha to head, falling back", exc_info=True)
            pr_files = None
    if pr_files is None:
        pr_files = list(pr.get_files())

    diff_parts = []
    files = []
    for pr_file in pr_files:
        # apply_budget drops ignored files from `files` entirely, so fetching
        # their content is a wasted API call — skip it up front instead of
        # fetching then throwing it away.
        if is_ignored(pr_file.filename, config.ignore_globs):
            continue
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
    # on a file the lens was never shown this run. Read off `files` (already
    # ignore-filtered above) before apply_budget trims it to max_files.
    changed_paths = [f.path for f in files]
    files = apply_budget(files, config)

    def _read_at_base(path: str) -> str | None:
        # pr.base.sha, not pr.head.sha -- a PR shouldn't be able to rewrite
        # its own review rules within the same diff being reviewed.
        try:
            blob = repo.get_contents(path, ref=pr.base.sha)
            if isinstance(blob, list):
                return None
            return blob.decoded_content.decode("utf-8", "ignore")
        except GithubException:
            return None

    project_standards = _resolve_project_standards(
        _read_at_base, list(config.project_standards_files)
    )

    return Context(
        diff="\n".join(diff_parts),
        changed_files=files,
        pr_title=pr.title or "",
        pr_body=pr.body or "",
        changed_paths=changed_paths,
        project_standards=project_standards,
    )

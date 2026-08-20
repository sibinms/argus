"""Assembles the Context a lens reviews against: the diff, the changed
files (budgeted), and the PR's own description of intent.

Two entry points: `gather_local` for running against a local git checkout
(diffing against a base ref), and `gather_github` for running inside a
GitHub Action against a real pull request.
"""

from __future__ import annotations

import logging
import re
import subprocess  # nosec B404 - only used to shell out to git with a fixed argv list
from collections.abc import Callable
from dataclasses import dataclass, field

from argus.config import ContextConfig
from argus.context.budget import apply_budget, is_ignored

logger = logging.getLogger(__name__)

# Hard cap on how many project-standards files one review will actually
# include -- a missing/unreadable file (e.g. the default list's CLAUDE.md,
# on the common repo that only has AGENTS.md) doesn't consume a slot here,
# only a genuinely included one does; see _MAX_PROJECT_STANDARDS_ATTEMPTS
# for the separate bound on total fetch attempts.
_MAX_PROJECT_STANDARDS_FILES = 8

# Separate, more generous cap on total fetch attempts (successful or not) --
# without this, a chain of bogus/missing @imports would never trip
# _MAX_PROJECT_STANDARDS_FILES (which only counts successes) and could fetch
# unbounded nonexistent paths. Not expected to matter in practice; it exists
# purely as a backstop against a typo'd self-reference or pathological chain.
_MAX_PROJECT_STANDARDS_ATTEMPTS = 32

# A line counts as an import only if it is *exactly* "@relative/path.md" --
# fullmatch against \S (not just "no ASCII space") so a tab or non-breaking
# space embedded in an otherwise-prose line can't slip through as a bogus
# fetch path either. \S alone also matches "/" and ".", so "@../x.md" or
# "@/x.md" match the regex too; _is_relative_import rejects those since
# they aren't the documented "relative/path.md" shape -- git/GitHub already
# refuse to resolve either outside the repo, so this isn't a traversal risk,
# just a wasted fetch and import slot on a line that isn't a real import.
_IMPORT_LINE = re.compile(r"@(\S+\.md)")


def _is_relative_import(path: str) -> bool:
    if path.startswith(("/", "~")):
        return False
    # Split on both separators: git/GitHub always address paths with "/"
    # regardless of host OS, so a literal "\" in an import line is never a
    # real directory separator to either -- but reject it here too, rather
    # than relying on that being harmless, so "..\x.md"/"a\..\x.md" don't
    # slip past this check the same way "../x.md"/"a/../x.md" don't.
    return ".." not in re.split(r"[/\\]", path)


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
    # GitHub's own language breakdown for the repo, e.g. "87% Python, 9%
    # TypeScript" — see _detect_tech_stack. Empty on gather_local (no API to
    # ask) or if context.tech_stack is False.
    tech_stack: str = ""


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

    Bounded by _MAX_PROJECT_STANDARDS_FILES *included* files and cycle-safe
    (a path already attempted, including an entry point re-imported later,
    is never fetched twice) -- a typo'd self-reference can't loop or blow up
    context. A missing/unreadable file doesn't count against that cap (see
    _MAX_PROJECT_STANDARDS_ATTEMPTS for the separate, more generous bound on
    total attempts) -- otherwise the default entry points alone would
    silently shrink a repo's real budget: on a repo with only AGENTS.md, the
    nonexistent CLAUDE.md would take a slot that contributes nothing."""
    seen: set[str] = set()
    parts: list[str] = []
    included = 0
    queue = list(entry_points)
    while (
        queue
        and included < _MAX_PROJECT_STANDARDS_FILES
        and len(seen) < _MAX_PROJECT_STANDARDS_ATTEMPTS
    ):
        path = queue.pop(0).removeprefix("./")
        if path in seen:
            continue
        seen.add(path)
        content = read_file(path)
        if content is None:
            continue
        included += 1
        parts.append(f"# {path}\n{content}")
        for line in content.splitlines():
            # "only" means the whole line, not just a line that happens to
            # start with @ and end in .md -- e.g. "@octocat's notes are in
            # other.md" starts and ends right but is prose, not an import.
            match = _IMPORT_LINE.fullmatch(line.strip())
            if match and _is_relative_import(match.group(1)):
                queue.append(match.group(1))
    return "\n\n".join(parts)


_MIN_LANGUAGE_SHARE_PERCENT = 1
_MAX_LANGUAGES_SHOWN = 6


def _format_languages(languages: dict[str, int]) -> str:
    """Turns GitHub's bytes-per-language breakdown into a short, human line
    like "87% Python, 9% TypeScript". Languages under
    _MIN_LANGUAGE_SHARE_PERCENT are dropped as noise (a repo's one stray
    Dockerfile shouldn't show up next to its actual stack), and the list is
    capped at _MAX_LANGUAGES_SHOWN so a polyglot monorepo doesn't turn into
    an unreadable wall of percentages."""
    total = sum(languages.values())
    if total <= 0:
        return ""
    ranked = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)
    parts = []
    for name, size in ranked:
        pct = round(size / total * 100)
        if pct < _MIN_LANGUAGE_SHARE_PERCENT:
            continue
        parts.append(f"{pct}% {name}")
        if len(parts) == _MAX_LANGUAGES_SHOWN:
            break
    return ", ".join(parts)


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
        # Guarded the same way _read_file is: a non-UTF-8 or otherwise
        # unreadable standards file is optional context, and must degrade to
        # "file skipped" (never garbled, never a crash) rather than take
        # down the whole run over a nice-to-have. A plain nonexistent file
        # (returncode != 0) is the expected, common case -- most repos don't
        # have every entry in project_standards_files -- so that alone stays
        # quiet. A real failure (the process itself erroring, or content
        # that exists but isn't readable) is different: silently degrading
        # this project's binding conventions to "" with no signal would
        # make a transient failure indistinguishable from "no such file",
        # so warn instead.
        try:
            result = subprocess.run(  # nosec
                ["git", "show", f"{base_ref}:{path}"],
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning(
                "couldn't read project standards file %s at %s", path, base_ref, exc_info=True
            )
            return None
        if result.returncode != 0:
            return None
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "project standards file %s at %s is not valid UTF-8, skipping", path, base_ref
            )
            return None

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

    tech_stack = ""
    if config.tech_stack:
        # Optional context, same treatment as project standards: a rate
        # limit, a network blip, or the endpoint being unreachable for
        # whatever reason degrades to "" rather than failing the whole
        # review over a nice-to-have.
        try:
            tech_stack = _format_languages(repo.get_languages())
        except GithubException:
            logger.debug("couldn't fetch repo languages", exc_info=True)
        except OSError:
            logger.debug("couldn't fetch repo languages (network error)", exc_info=True)

    def _fetch_content(path: str, ref: str, *, strict: bool = False) -> str | None:
        # Shared by the changed-file loop and the project-standards fetch
        # below (base vs head is the only real difference between the two
        # call sites, plus strict for the latter -- see below). Content is
        # always optional here — the diff is what matters, and project
        # standards are a nice-to-have — so a failure degrades to "no
        # content" rather than crashing the whole review. Bounded to
        # GithubException (the API's own error type) and OSError (network
        # timeouts, DNS failures, connection resets -- requests' own
        # exception classes all subclass OSError) for the fetch itself,
        # rather than bare Exception, so an actual programming bug here
        # (TypeError, AttributeError from an unexpected response shape)
        # still fails loudly instead of silently degrading to "file not
        # found".
        # A plain 404 is the expected, common case for the standards fetch --
        # most repos don't have every entry in project_standards_files -- so
        # that alone stays quiet regardless of strict/lenient. Any other
        # failure on the strict (standards) path is different: silently
        # degrading this project's binding conventions to "" with no signal
        # would make a transient failure (rate limit, 5xx, network error)
        # indistinguishable from "no such file", so warn instead. The
        # lenient (changed-file) path stays at debug either way -- a missing/
        # renamed/deleted file is routine on nearly every PR, and warning on
        # every one of those would be pure noise.
        try:
            blob = repo.get_contents(path, ref=ref)
        except GithubException as e:
            if strict and e.status != 404:
                logger.warning(
                    "couldn't fetch project standards file %s at %s (status %s)",
                    path,
                    ref,
                    e.status,
                )
            else:
                logger.debug("couldn't fetch %s at %s", path, ref, exc_info=True)
            return None
        except OSError:
            if strict:
                logger.warning(
                    "couldn't fetch project standards file %s at %s (network error)",
                    path,
                    ref,
                    exc_info=True,
                )
            else:
                logger.debug("couldn't fetch %s at %s", path, ref, exc_info=True)
            return None
        if isinstance(blob, list):
            return None
        try:
            content = blob.decoded_content
        except AssertionError:
            # PyGithub's ContentFile.decoded_content asserts encoding ==
            # "base64", which GitHub's Contents API doesn't set for a file
            # over ~1MB -- scoped narrowly to this specific property access,
            # not the get_contents call above, so a genuine PyGithub/API bug
            # elsewhere still fails loudly rather than silently degrading.
            # Kept at debug even for strict: an oversized file is an
            # expected limitation, not a transient failure worth a warning.
            logger.debug("couldn't decode content for %s at %s", path, ref, exc_info=True)
            return None
        if strict:
            # Standards are cited to lenses/the curator as this project's
            # authoritative, binding rules -- a non-UTF-8 byte silently
            # dropped mid-file (the lenient path below) could mangle a
            # stated rule with no signal, so drop the whole file instead,
            # matching _read_file's own treatment of an unreadable file.
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    "project standards file %s at %s is not valid UTF-8, skipping", path, ref
                )
                return None
        # The regular changed-file loop is informational lens context, not a
        # trust boundary -- a few garbled bytes from a Latin-1/Windows-1252
        # source file are a smaller loss than dropping the file's content
        # entirely, so keep the pre-existing "ignore" behavior here.
        return content.decode("utf-8", "ignore")

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
        content = _fetch_content(pr_file.filename, pr.head.sha)
        files.append(ChangedFile(path=pr_file.filename, content=content))

    # As in gather_local: changed_paths is "what a lens actually saw", so it
    # excludes ignored files -- their patch is never in `diff`, and counting
    # them as touched would let posting wrongly resolve a still-open finding
    # on a file the lens was never shown this run. Read off `files` (already
    # ignore-filtered above) before apply_budget trims it to max_files.
    changed_paths = [f.path for f in files]
    files = apply_budget(files, config)

    # pr.base.sha, not pr.head.sha -- a PR shouldn't be able to rewrite its
    # own review rules within the same diff being reviewed.
    project_standards = _resolve_project_standards(
        lambda path: _fetch_content(path, pr.base.sha, strict=True),
        list(config.project_standards_files),
    )

    return Context(
        diff="\n".join(diff_parts),
        changed_files=files,
        pr_title=pr.title or "",
        pr_body=pr.body or "",
        changed_paths=changed_paths,
        project_standards=project_standards,
        tech_stack=tech_stack,
    )

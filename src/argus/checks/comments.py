"""Deterministic, model-free check for long inline comments.

Scans the lines a PR *adds* for runs of consecutive comment lines in a
language that has a docstring (or doc-comment) idiom, and emits one warning
per run longer than MAX_INLINE_COMMENT_LINES: that much explanation belongs
in the docstring of the function or class it describes, where help(), IDE
hover, and doc tooling surface it and where it is reviewed alongside the
signature it documents -- not in a wall of `#` lines that only a reader
scrolling past that exact spot will ever see, and that nothing flags when
the code beneath it changes and the comment quietly goes stale.

Pure function over the diff: no model call, no network. Its findings are
emitted already marked "kept" and never go through the curator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from argus.context.gather import Context
from argus.lenses.base import Finding

NAME = "comments"

# A run of MORE than this many consecutive added comment lines is flagged.
# One line is room for a real "why" at a specific statement; past that,
# it is documentation and wants a docstring.
MAX_INLINE_COMMENT_LINES = 1

# A run whose first line sits at or above this line number is a file header
# (license, encoding, shebang block) and is never flagged.
_HEADER_LINES = 3

# Only languages where a function/class-level docstring or doc comment is
# the idiomatic home for this text. YAML, TOML, shell, Dockerfiles, CI
# workflows, Markdown and the like have no such construct, so a long
# comment there is not misplaced -- they are deliberately absent here.
_LINE_COMMENT_MARKERS: dict[str, str] = {
    ".py": "#",
    ".pyi": "#",
    ".rb": "#",
    ".ex": "#",
    ".exs": "#",
    ".js": "//",
    ".jsx": "//",
    ".mjs": "//",
    ".cjs": "//",
    ".ts": "//",
    ".tsx": "//",
    ".java": "//",
    ".kt": "//",
    ".kts": "//",
    ".scala": "//",
    ".go": "//",
    ".rs": "//",
    ".c": "//",
    ".h": "//",
    ".cc": "//",
    ".cpp": "//",
    ".hpp": "//",
    ".cs": "//",
    ".swift": "//",
    ".php": "//",
    ".dart": "//",
    ".m": "//",
}

# Lines that look like comments but are something else: doc comments (the
# very thing we want people to write), shebangs, encoding cookies, and
# linter / type-checker pragmas. They break a run rather than extend it.
_NOT_A_COMMENT_PREFIXES = (
    "#!",
    "# -*-",
    "#:",
    "# noqa",
    "# type:",
    "# pragma",
    "# pylint",
    "# fmt:",
    "# mypy:",
    "# ruff:",
    "# nosec",
    "# isort",
    "///",
    "//!",
    "/**",
    "// eslint",
    "// @ts-",
    "// prettier",
    "// nolint",
    "//go:",
    "// biome",
    "// tslint",
)

# A run that opens with a task marker is a to-do note, not documentation.
_TASK_MARKER_RE = re.compile(r"^(?:#|//|/\*)+\s*(?:TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Best-effort, multi-language "this line opens a function or class" matcher,
# used only to name the docstring the comment should move into.
_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?:(?:export|default|pub(?:\([^)]*\))?|async|static|public|private|protected"
    r"|abstract|final|override|const)\s+)*"
    r"(?:def|class|function\*?|fn|func|struct|enum|interface|trait|impl|object|record)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

_PATCH_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)


@dataclass
class _Run:
    start_line: int  # new-file line number of the first comment line
    indent: str
    lines: list[str] = field(default_factory=list)  # raw text, diff '+' stripped
    # First non-comment line after the run (added or context), for spotting
    # a comment that sits directly above the def/class it describes.
    following: str | None = None
    # Nearest def/class seen earlier in the same hunk with a shallower
    # indent, as a fallback when the full file content isn't available.
    enclosing_from_patch: str | None = None


def _is_comment(text: str, marker: str, in_block: bool) -> tuple[bool, bool]:
    """Returns (is_comment, in_block_after_this_line)."""
    stripped = text.strip()
    if in_block:
        return True, "*/" not in stripped
    if stripped.startswith(_NOT_A_COMMENT_PREFIXES):
        return False, False
    if marker == "//" and stripped.startswith("/*"):
        return True, "*/" not in stripped[2:]
    return stripped.startswith(marker), False


def _comment_runs(patch: str, marker: str) -> list[_Run]:
    runs: list[_Run] = []
    current: _Run | None = None
    new_line = 0
    in_hunk = False
    in_block = False
    # (indent width, name) of def/class lines seen in this hunk, innermost last.
    defs_seen: list[tuple[int, str]] = []

    def close(following: str | None) -> None:
        nonlocal current
        if current is not None:
            current.following = following
            runs.append(current)
            current = None

    for raw in patch.splitlines():
        hunk = _HUNK_RE.match(raw)
        if hunk:
            close(None)
            in_hunk = True
            in_block = False
            defs_seen.clear()
            new_line = int(hunk.group(1))
            continue
        if not in_hunk or raw.startswith("\\"):
            continue  # file headers before the first hunk; "\ No newline at end of file"
        if raw.startswith("-"):
            continue  # removed lines don't exist in the new file: neither break nor extend a run

        text = raw[1:] if raw[:1] in ("+", " ") else raw
        line_no = new_line
        new_line += 1

        def_match = _DEF_RE.match(text)
        if def_match:
            width = len(def_match.group("indent").expandtabs(4))
            defs_seen[:] = [d for d in defs_seen if d[0] < width]
            defs_seen.append((width, def_match.group("name")))

        if not raw.startswith("+"):
            close(text)  # context line: the new file has a non-added line here
            in_block = False
            continue

        is_comment, in_block = _is_comment(text, marker, in_block)
        if not is_comment:
            close(text)
            continue
        if current is None:
            indent = text[: len(text) - len(text.lstrip())]
            width = len(indent.expandtabs(4))
            enclosing = next((name for w, name in reversed(defs_seen) if w < width), None)
            current = _Run(start_line=line_no, indent=indent, enclosing_from_patch=enclosing)
        current.lines.append(text)

    close(None)
    return runs


def _docstring_target(run: _Run, file_content: str | None) -> str | None:
    """Names the function/class whose docstring should hold this comment:
    the def it sits directly above, else the def enclosing it."""
    if run.following is not None:
        m = _DEF_RE.match(run.following)
        if m and len(m.group("indent").expandtabs(4)) <= len(run.indent.expandtabs(4)):
            return m.group("name")
    width = len(run.indent.expandtabs(4))
    if width == 0:
        return None  # top-level: nothing encloses it
    if file_content:
        for text in reversed(file_content.splitlines()[: run.start_line - 1]):
            m = _DEF_RE.match(text)
            if m and len(m.group("indent").expandtabs(4)) < width:
                return m.group("name")
    return run.enclosing_from_patch


def _snippet(lines: list[str], marker: str) -> str:
    """First few words of the comment, for a summary that stays unique per
    block (the fingerprint drops digits, so the line number alone wouldn't)."""
    for line in lines:
        text = line.strip()
        for prefix in (marker, "/*", "*"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        text = text.strip()
        if text:
            return text if len(text) <= 40 else text[:39].rstrip() + "…"
    return ""


def _finding(path: str, run: _Run, marker: str, file_content: str | None) -> Finding:
    n = len(run.lines)
    target = _docstring_target(run, file_content)
    if target:
        where = f"the docstring of `{target}`"
    elif run.indent:
        where = "the docstring of the enclosing function or class"
    else:
        where = "the module docstring (or the docstring of the function/class it describes)"
    snippet = _snippet(run.lines, marker)
    return Finding(
        lens=NAME,
        file=path,
        line=run.start_line,
        summary=f'Long inline comment ({n} lines) — move "{snippet}" into {where}',
        detail=(
            f"Warning: long inline comments are not recommended. These {n} consecutive "
            f'comment lines explain more than a local "why", so they belong in {where} at '
            "function/class level, where `help()`, IDE hover and doc tooling surface them "
            "and where they are reviewed alongside the signature they describe. Keep at most "
            "a single-line inline note for what is genuinely specific to this spot.\n\n"
            f"(Deterministic check: an added inline comment longer than "
            f"{MAX_INLINE_COMMENT_LINES} line(s) in a language with a docstring idiom. "
            "No model involved.)"
        ),
        confidence="medium",
        quote=run.lines[0],
        status="kept",
    )


def _file_patches(context: Context) -> list[tuple[str, str]]:
    if context.file_patches:
        return list(context.file_patches)
    # Older callers (and tests) that only fill `diff`: recover per-file
    # patches from the git-style headers, if they are there to recover.
    patches = []
    for part in ("\n" + context.diff).split("\ndiff --git ")[1:]:
        part = "diff --git " + part
        m = _PATCH_PATH_RE.search(part) or _DIFF_GIT_RE.search(part)
        if m:
            patches.append((m.group(m.lastindex or 1), part))
    return patches


def check_long_inline_comments(context: Context) -> list[Finding]:
    contents = {f.path: f.content for f in context.changed_files}
    findings: list[Finding] = []
    for path, patch in _file_patches(context):
        marker = _LINE_COMMENT_MARKERS.get(PurePosixPath(path).suffix.lower())
        if marker is None:
            continue
        for run in _comment_runs(patch, marker):
            if len(run.lines) <= MAX_INLINE_COMMENT_LINES:
                continue
            if run.start_line <= _HEADER_LINES:
                continue
            if _TASK_MARKER_RE.match(run.lines[0].strip()):
                continue
            findings.append(_finding(path, run, marker, contents.get(path)))
    return findings

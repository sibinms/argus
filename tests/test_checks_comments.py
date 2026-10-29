from argus.checks import load_checks
from argus.checks.comments import check_long_inline_comments
from argus.context.gather import ChangedFile, Context

import pytest


def _patch(path: str, body: str, start: int = 10) -> tuple[str, str]:
    """One hunk starting at new-file line `start`; body lines are diff lines
    ('+', ' ', '-' prefixed)."""
    return (
        path,
        f"@@ -{start},{len(body.splitlines())} +{start},{len(body.splitlines())} @@\n{body}",
    )


def _ctx(*patches: tuple[str, str], files: list[ChangedFile] | None = None) -> Context:
    return Context(
        diff="\n".join(p for _, p in patches), changed_files=files or [], file_patches=list(patches)
    )


def test_flags_run_of_four_added_comment_lines_in_python():
    ctx = _ctx(
        _patch(
            "app/service.py",
            "+def handle(event):\n"
            "+    # This handler exists because the upstream webhook retries on\n"
            "+    # any non-2xx, so we must acknowledge first and process later,\n"
            "+    # otherwise a slow DB write turns into a retry storm that\n"
            "+    # duplicates every event several times over.\n"
            "+    ack(event)\n",
        )
    )
    findings = check_long_inline_comments(ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.lens == "comments"
    assert f.file == "app/service.py"
    assert f.line == 11
    assert f.status == "kept"
    assert f.confidence == "medium"
    assert "4 lines" in f.summary
    assert "docstring of `handle`" in f.summary
    assert "not recommended" in f.detail
    assert f.quote == "    # This handler exists because the upstream webhook retries on"


def test_single_line_comment_is_fine():
    ctx = _ctx(_patch("a.py", "+    # one\n+    x = 1\n+    # two\n+    y = 2\n"))
    assert check_long_inline_comments(ctx) == []


def test_two_consecutive_lines_are_flagged():
    ctx = _ctx(_patch("a.py", "+def f():\n+    # one\n+    # two\n+    x = 1\n"))
    findings = check_long_inline_comments(ctx)
    assert len(findings) == 1
    assert "2 lines" in findings[0].summary


def test_line_numbers_account_for_removed_and_context_lines():
    ctx = _ctx(
        _patch(
            "a.py",
            " import os\n"  # 10
            "-old = 1\n"  # not in new file
            " def f():\n"  # 11
            "+    # a\n"  # 12
            "+    # b\n"
            "+    # c\n"
            "+    # d\n"
            "+    return 1\n",
        )
    )
    findings = check_long_inline_comments(ctx)
    assert [f.line for f in findings] == [12]
    assert "docstring of `f`" in findings[0].summary


def test_context_line_breaks_a_run():
    # One added comment line, an unchanged one, one more added: no run of
    # two *added* lines, so nothing is flagged.
    ctx = _ctx(_patch("a.py", "+    # a\n     # c (unchanged)\n+    # d\n+    x = 1\n"))
    assert check_long_inline_comments(ctx) == []


def test_comment_directly_above_a_def_names_that_def():
    ctx = _ctx(
        _patch(
            "a.py",
            "+# Parses the config file, applying the documented defaults for\n"
            "+# every key the user left out. Raises ValueError for a key of\n"
            "+# the wrong type so a typo in YAML fails loudly rather than\n"
            "+# being silently coerced.\n"
            "+def load(path):\n"
            "+    ...\n",
        )
    )
    findings = check_long_inline_comments(ctx)
    assert len(findings) == 1
    assert "docstring of `load`" in findings[0].summary


def test_top_level_comment_with_no_def_points_at_module_docstring():
    ctx = _ctx(_patch("a.py", "+# a\n+# b\n+# c\n+# d\n+X = 1\n"))
    findings = check_long_inline_comments(ctx)
    assert len(findings) == 1
    assert "module docstring" in findings[0].summary


def test_enclosing_def_is_found_in_full_file_content_when_hunk_lacks_it():
    content = "class Thing:\n    def run(self):\n" + "        x = 1\n" * 20
    ctx = _ctx(
        _patch(
            "a.py",
            "+        # a\n+        # b\n+        # c\n+        # d\n+        y = 2\n",
            start=15,
        ),
        files=[ChangedFile(path="a.py", content=content)],
    )
    findings = check_long_inline_comments(ctx)
    assert len(findings) == 1
    assert "docstring of `run`" in findings[0].summary


def test_file_header_is_never_flagged():
    ctx = _ctx(
        _patch(
            "a.py", "+# Copyright\n+# Licensed under\n+# the MIT\n+# license\n+import os\n", start=1
        )
    )
    assert check_long_inline_comments(ctx) == []


def test_pragmas_shebang_and_doc_comments_do_not_count():
    ctx = _ctx(
        _patch(
            "a.py",
            "+    # noqa: E501\n+    # type: ignore\n+    # pragma: no cover\n+    # fmt: off\n+    x = 1\n",
        ),
        _patch(
            "b.ts", "+  /** doc\n+   * comment\n+   * is fine\n+   * really\n+   */\n+  foo();\n"
        ),
    )
    assert check_long_inline_comments(ctx) == []


def test_todo_block_is_not_flagged():
    ctx = _ctx(
        _patch("a.py", "+    # TODO: one\n+    # two\n+    # three\n+    # four\n+    x = 1\n")
    )
    assert check_long_inline_comments(ctx) == []


@pytest.mark.parametrize(
    "path", ["config.yml", "deploy.sh", "Dockerfile", "README.md", ".github/workflows/ci.yml"]
)
def test_files_without_a_docstring_idiom_are_skipped(path):
    ctx = _ctx(_patch(path, "+# a\n+# b\n+# c\n+# d\n+key: value\n"))
    assert check_long_inline_comments(ctx) == []


def test_js_line_and_block_comments():
    ctx = _ctx(
        _patch(
            "web/app.ts",
            "+export function boot() {\n"
            "+  // We defer mounting until fonts are ready because the layout\n"
            "+  // shift otherwise pushes the CTA below the fold on mobile and\n"
            "+  // the analytics event fires against the wrong viewport, which\n"
            "+  // skewed last quarter's conversion numbers.\n"
            "+  mount();\n"
            "+  /* block\n"
            "+     comment\n"
            "+     four\n"
            "+     lines */\n"
            "+  go();\n"
            "+}\n",
        )
    )
    findings = check_long_inline_comments(ctx)
    assert [f.line for f in findings] == [11, 16]
    assert all("docstring of `boot`" in f.summary for f in findings)


def test_falls_back_to_parsing_git_headers_when_file_patches_is_empty():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -5,0 +5,5 @@\n+# a\n+# b\n+# c\n+# d\n+X = 1\n"
        "diff --git a/b.yml b/b.yml\n--- a/b.yml\n+++ b/b.yml\n"
        "@@ -5,0 +5,5 @@\n+# a\n+# b\n+# c\n+# d\n+k: v\n"
    )
    findings = check_long_inline_comments(Context(diff=diff, changed_files=[]))
    assert [(f.file, f.line) for f in findings] == [("a.py", 5)]


def test_load_checks_rejects_unknown_names():
    assert [name for name, _ in load_checks(["comments"])] == ["comments"]
    with pytest.raises(ValueError):
        load_checks(["not-a-check"])
    with pytest.raises(ValueError):
        load_checks([{"custom": "x.py"}])


def test_def_from_an_earlier_hunk_is_not_assumed_to_enclose_a_later_one():
    patch = (
        "@@ -1,2 +1,2 @@\n def outer():\n+    pass\n"
        "@@ -40,5 +40,5 @@\n+    # a\n+    # b\n+    # c\n+    # d\n+    y = 2\n"
    )
    findings = check_long_inline_comments(_ctx(("a.py", patch)))
    assert len(findings) == 1
    assert "enclosing function or class" in findings[0].summary
    assert "outer" not in findings[0].summary


def test_snippet_skips_a_leading_bare_comment_marker():
    ctx = _ctx(
        _patch("a.py", "+    #\n+    # Real text here\n+    # more\n+    # more\n+    x = 1\n")
    )
    findings = check_long_inline_comments(ctx)
    assert 'move "Real text here"' in findings[0].summary

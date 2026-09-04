from argus.config import ContextConfig
from argus.context.budget import apply_budget, is_ignored, truncate_diff_parts
from argus.context.gather import ChangedFile


def test_truncate_diff_parts_keeps_everything_under_the_cap():
    parts = ["diff --git a/x b/x\n+1", "diff --git a/y b/y\n+2"]
    kept, truncated = truncate_diff_parts(parts, max_bytes=1000)
    assert kept == parts
    assert truncated is False


def test_truncate_diff_parts_drops_whole_files_that_dont_fit():
    parts = ["a" * 50, "b" * 50, "c" * 50]
    kept, truncated = truncate_diff_parts(parts, max_bytes=100)
    assert kept == ["a" * 50, "b" * 50]
    assert truncated is True


def test_truncate_diff_parts_never_cuts_a_part_mid_way():
    # Even a single oversized file is kept whole, not sliced -- a half-hunk
    # is worse than no hunk (a lens can't tell truncated context from a real
    # change), and there must always be at least one part when the input is
    # non-empty so a genuinely tiny diff is never reported as "truncated to
    # nothing".
    parts = ["x" * 500]
    kept, truncated = truncate_diff_parts(parts, max_bytes=100)
    assert kept == ["x" * 500]
    assert truncated is False


def test_truncate_diff_parts_empty_input():
    kept, truncated = truncate_diff_parts([], max_bytes=100)
    assert kept == []
    assert truncated is False


def test_is_ignored_matches_glob():
    assert is_ignored("package-lock.json", ["*.lock", "package-lock.json"])
    assert is_ignored("app/migrations/0001_initial.py", ["*/migrations/*"])
    assert not is_ignored("app/models.py", ["*/migrations/*"])


def test_apply_budget_filters_ignored_files():
    files = [
        ChangedFile(path="src/foo.py", content="print(1)"),
        ChangedFile(path="yarn.lock", content="lockdata"),
    ]
    config = ContextConfig(ignore_globs=["yarn.lock"])
    kept = apply_budget(files, config)
    assert [f.path for f in kept] == ["src/foo.py"]


def test_apply_budget_truncates_oversized_files():
    config = ContextConfig(max_bytes_per_file=10)
    files = [ChangedFile(path="big.py", content="x" * 100)]
    kept = apply_budget(files, config)
    assert kept[0].truncated is True
    assert len(kept[0].content) == 10


def test_apply_budget_caps_file_count():
    config = ContextConfig(max_files=2)
    files = [ChangedFile(path=f"f{i}.py", content="x") for i in range(5)]
    kept = apply_budget(files, config)
    assert len(kept) == 2

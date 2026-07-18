from argus.config import ContextConfig
from argus.context.budget import apply_budget, is_ignored
from argus.context.gather import ChangedFile


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

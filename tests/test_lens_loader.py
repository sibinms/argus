import pytest

from argus.lenses.loader import load_lenses


def test_loads_builtin_lenses():
    lenses = load_lenses(["security", "tests"])
    assert [lens.name for lens in lenses] == ["security", "tests"]
    assert "Security" in lenses[0].instructions


def test_loads_custom_lens(tmp_path):
    custom = tmp_path / "payment-safety.md"
    custom.write_text("## Payment safety\n\nCheck for double charges.")
    lenses = load_lenses([{"custom": str(custom)}])
    assert lenses[0].name == "payment-safety"
    assert "double charges" in lenses[0].instructions


def test_unknown_builtin_raises():
    with pytest.raises(ValueError):
        load_lenses(["not-a-real-lens"])


def test_missing_custom_file_raises(tmp_path):
    with pytest.raises(ValueError):
        load_lenses([{"custom": str(tmp_path / "missing.md")}])

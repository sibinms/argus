import pytest
from pathlib import Path

from argus.config import BUILTIN_LENSES, Config, load_config


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "does-not-exist.yml")
    assert config == Config()


def test_approve_reviews_defaults_off_and_loads_when_set(tmp_path):
    assert Config().posting.approve_reviews is False

    path = tmp_path / "config.yml"
    path.write_text("posting:\n  approve_reviews: true\n")
    assert load_config(path).posting.approve_reviews is True


def test_max_inline_comments_default_and_loads(tmp_path):
    assert Config().posting.max_inline_comments == 10

    path = tmp_path / "config.yml"
    path.write_text("posting:\n  max_inline_comments: 3\n")
    assert load_config(path).posting.max_inline_comments == 3


def test_load_config_reads_values(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        """
mode: active
models:
  lens: some-cheap-model
  curator: some-expensive-model
lenses:
  - security
context:
  max_files: 5
posting:
  min_confidence: high
"""
    )
    config = load_config(path)
    assert config.mode == "active"
    assert config.is_active
    assert config.models.lens == "some-cheap-model"
    assert config.models.curator == "some-expensive-model"
    assert config.lenses == ["security"]
    assert config.context.max_files == 5
    assert config.posting.min_confidence == "high"


def test_example_config_parses(tmp_path):
    example = Path(__file__).parent.parent / ".argus" / "config.yml.example"
    config = load_config(example)
    assert config.mode == "active"
    assert "security" in config.lenses


def test_correctness_in_builtin_lenses_and_default_config():
    assert "correctness" in BUILTIN_LENSES
    assert "correctness" in Config().lenses


def test_load_config_rejects_wrong_types(tmp_path):
    cases = [
        ("lenses: security\n", "lenses"),
        ("context:\n  max_files: thirty\n", "context.max_files"),
        ("context:\n  max_bytes_per_file: big\n", "context.max_bytes_per_file"),
        ("context:\n  ignore_globs: '*.log'\n", "context.ignore_globs"),
        ("posting:\n  max_inline_comments: ten\n", "posting.max_inline_comments"),
    ]
    for yaml_text, key in cases:
        path = tmp_path / "config.yml"
        path.write_text(yaml_text)
        with pytest.raises(ValueError, match=key):
            load_config(path)

from pathlib import Path

from argus.config import Config, load_config


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "does-not-exist.yml")
    assert config == Config()


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

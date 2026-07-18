"""Loads .argus/config.yml and fills in the defaults documented in
.argus/config.yml.example. Kept deliberately small: this is the one file a
user is expected to read before trusting the tool with their repo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(".argus/config.yml")

BUILTIN_LENSES = ["security", "tests", "error_handling", "contracts"]


@dataclass
class ModelConfig:
    lens: str = "claude-haiku-4-5"
    curator: str = "claude-opus-4-8"


@dataclass
class ContextConfig:
    max_files: int = 15
    max_bytes_per_file: int = 20_000
    include_neighbors: bool = False
    ignore_globs: list[str] = field(
        default_factory=lambda: [
            "*.lock",
            "*.min.js",
            "*/migrations/*",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ]
    )


@dataclass
class PostingConfig:
    min_confidence: str = "medium"  # low | medium | high
    show_dropped_reasoning: bool = True


@dataclass
class Config:
    mode: str = "shadow"  # shadow | active
    models: ModelConfig = field(default_factory=ModelConfig)
    lenses: list[str] = field(default_factory=lambda: list(BUILTIN_LENSES))
    context: ContextConfig = field(default_factory=ContextConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)

    @property
    def is_active(self) -> bool:
        return self.mode == "active"


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()

    raw = yaml.safe_load(path.read_text()) or {}

    models_raw = raw.get("models", {})
    context_raw = raw.get("context", {})
    posting_raw = raw.get("posting", {})

    return Config(
        mode=raw.get("mode", "shadow"),
        models=ModelConfig(
            lens=models_raw.get("lens", ModelConfig.lens),
            curator=models_raw.get("curator", ModelConfig.curator),
        ),
        lenses=raw.get("lenses", list(BUILTIN_LENSES)),
        context=ContextConfig(
            max_files=context_raw.get("max_files", ContextConfig.max_files),
            max_bytes_per_file=context_raw.get(
                "max_bytes_per_file", ContextConfig.max_bytes_per_file
            ),
            include_neighbors=context_raw.get(
                "include_neighbors", ContextConfig.include_neighbors
            ),
            ignore_globs=context_raw.get("ignore_globs")
            or ContextConfig().ignore_globs,
        ),
        posting=PostingConfig(
            min_confidence=posting_raw.get(
                "min_confidence", PostingConfig.min_confidence
            ),
            show_dropped_reasoning=posting_raw.get(
                "show_dropped_reasoning", PostingConfig.show_dropped_reasoning
            ),
        ),
    )

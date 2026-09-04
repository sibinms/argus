"""Loads .argus/config.yml and fills in the defaults documented in
.argus/config.yml.example. Kept deliberately small: this is the one file a
user is expected to read before trusting the tool with their repo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(".argus/config.yml")

BUILTIN_LENSES = [
    "security",
    "tests",
    "error_handling",
    "contracts",
    "correctness",
    "deleted_code",
    "reuse",
    "efficiency",
]


@dataclass
class ModelConfig:
    lens: str = "claude-haiku-4-5"
    curator: str = "claude-opus-4-8"
    # OpenRouter-only: additional models OpenRouter will automatically try,
    # in order, if the primary model errors -- including a rate limit, which
    # is exactly what this exists for (see the team's own #argus-alerts
    # incident that prompted this feature). Has no effect unless the
    # matching lens/curator model string starts with "openrouter/" -- see
    # _openrouter_models_field's docstring in models/client.py -- so these
    # defaults are a harmless no-op for the vast majority of setups (the
    # lens/curator defaults just above are plain Anthropic models, not
    # OpenRouter at all) and only take effect once a repo opts into
    # OpenRouter for one of those two roles. Each is a model backed by many
    # independent inference providers (the property that actually helps
    # here -- see the README's "OpenRouter fallback models" section), not
    # necessarily the cheapest option available. Override per-repo via
    # .argus/config.yml if a different fallback chain fits better.
    lens_fallbacks: list[str] = field(default_factory=lambda: ["openrouter/z-ai/glm-4.7-flash"])
    curator_fallbacks: list[str] = field(
        default_factory=lambda: [
            "openrouter/meta-llama/llama-4-maverick",
            "openrouter/minimax/minimax-m2.5",
        ]
    )


@dataclass
class ContextConfig:
    max_files: int = 15
    max_bytes_per_file: int = 20_000
    # Hard ceiling on the diff itself, in bytes, independent of whether the
    # configured model is one litellm has pricing/context-window metadata
    # for -- see truncate_diff_parts's docstring in context/budget.py for
    # why this can't be left to the per-model token-budget check alone.
    max_diff_bytes: int = 200_000
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
    # Repo-root files read (from the PR's base branch, not head — a PR
    # shouldn't be able to rewrite its own review rules) and fed to every
    # lens, the curator, and the planner as project-specific standards. Each
    # is scanned for "@relative/path.md"-only lines and those are pulled in
    # too, so an existing CLAUDE.md -> AGENTS.md -> docs/standards.md chain
    # reaches Argus the same way it reaches a human or another agent. Set to
    # an empty list to disable.
    project_standards_files: list[str] = field(default_factory=lambda: ["CLAUDE.md", "AGENTS.md"])
    # Fetches the repo's language breakdown from GitHub (bytes per language,
    # e.g. "87% Python, 9% TypeScript") and feeds it to every lens, the
    # curator, and the planner as a one-line "# Tech stack" section — cheap
    # signal that steers generic advice toward what the stack actually makes
    # a real footgun. GitHub-only: gather_local has no API to ask, so this is
    # always "" there regardless of the setting. Set to False to disable.
    tech_stack: bool = True


@dataclass
class PostingConfig:
    min_confidence: str = "medium"  # low | medium | high
    show_dropped_reasoning: bool = True
    # When True, a clean PR gets a real APPROVE review instead of a plain
    # comment. Requires the repo's "Allow GitHub Actions to approve pull
    # requests" setting to be on; if it isn't, posting falls back to a comment
    # rather than failing. Off by default so no one's clean PR errors out.
    approve_reviews: bool = False
    # Hard lifetime cap on inline comments Argus will place on a PR. Once
    # reached, further findings appear in the overflow comment instead — so
    # the PR can never fill up with comments no matter how many times Argus runs.
    max_inline_comments: int = 10


@dataclass
class Config:
    mode: str = "active"  # shadow | active
    models: ModelConfig = field(default_factory=ModelConfig)
    lenses: list[str] = field(default_factory=lambda: list(BUILTIN_LENSES))
    context: ContextConfig = field(default_factory=ContextConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)

    @property
    def is_active(self) -> bool:
        return self.mode == "active"


def _require_type(value: object, expected: type, key: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(
            f"Config key '{key}' must be {expected.__name__}, got {type(value).__name__}"
        )


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()

    raw = yaml.safe_load(path.read_text()) or {}

    models_raw = raw.get("models", {})
    context_raw = raw.get("context", {})
    posting_raw = raw.get("posting", {})

    if "lenses" in raw:
        _require_type(raw["lenses"], list, "lenses")
    if "lens_fallbacks" in models_raw and models_raw["lens_fallbacks"] is not None:
        _require_type(models_raw["lens_fallbacks"], list, "models.lens_fallbacks")
    if "curator_fallbacks" in models_raw and models_raw["curator_fallbacks"] is not None:
        _require_type(models_raw["curator_fallbacks"], list, "models.curator_fallbacks")
    if "max_files" in context_raw:
        _require_type(context_raw["max_files"], int, "context.max_files")
    if "max_bytes_per_file" in context_raw:
        _require_type(context_raw["max_bytes_per_file"], int, "context.max_bytes_per_file")
    if "max_diff_bytes" in context_raw:
        _require_type(context_raw["max_diff_bytes"], int, "context.max_diff_bytes")
    if "ignore_globs" in context_raw and context_raw["ignore_globs"] is not None:
        _require_type(context_raw["ignore_globs"], list, "context.ignore_globs")
    if "tech_stack" in context_raw:
        _require_type(context_raw["tech_stack"], bool, "context.tech_stack")
    if (
        "project_standards_files" in context_raw
        and context_raw["project_standards_files"] is not None
    ):
        _require_type(
            context_raw["project_standards_files"], list, "context.project_standards_files"
        )
    if "max_inline_comments" in posting_raw:
        _require_type(posting_raw["max_inline_comments"], int, "posting.max_inline_comments")

    return Config(
        mode=raw.get("mode", "active"),
        models=ModelConfig(
            lens=models_raw.get("lens", ModelConfig.lens),
            curator=models_raw.get("curator", ModelConfig.curator),
            # Absent or explicit null both mean "unset" and fall back to
            # ModelConfig's own (non-empty) defaults; only a present,
            # non-null value -- including [] to explicitly disable
            # fallbacks -- is treated as an explicit override. Plain `or`
            # would wrongly collapse "key absent" to [], discarding the
            # class default entirely -- same trap project_standards_files
            # below already guards against.
            lens_fallbacks=(
                models_raw["lens_fallbacks"]
                if models_raw.get("lens_fallbacks") is not None
                else ModelConfig().lens_fallbacks
            ),
            curator_fallbacks=(
                models_raw["curator_fallbacks"]
                if models_raw.get("curator_fallbacks") is not None
                else ModelConfig().curator_fallbacks
            ),
        ),
        lenses=raw.get("lenses", list(BUILTIN_LENSES)),
        context=ContextConfig(
            max_files=context_raw.get("max_files", ContextConfig.max_files),
            max_bytes_per_file=context_raw.get(
                "max_bytes_per_file", ContextConfig.max_bytes_per_file
            ),
            max_diff_bytes=context_raw.get("max_diff_bytes", ContextConfig.max_diff_bytes),
            include_neighbors=context_raw.get("include_neighbors", ContextConfig.include_neighbors),
            tech_stack=context_raw.get("tech_stack", ContextConfig.tech_stack),
            ignore_globs=context_raw.get("ignore_globs") or ContextConfig().ignore_globs,
            # Unlike ignore_globs above, an explicit empty list here is a
            # real, meaningful setting (disable project-standards context
            # entirely) rather than "unset" -- plain `or` would collapse it
            # back to the default, so check for None specifically instead.
            # Absent and explicit null (`project_standards_files:` with no
            # value) both mean "unset" and fall back to the default; only a
            # present, non-null value (including []) is treated as explicit.
            project_standards_files=(
                context_raw["project_standards_files"]
                if context_raw.get("project_standards_files") is not None
                else ContextConfig().project_standards_files
            ),
        ),
        posting=PostingConfig(
            min_confidence=posting_raw.get("min_confidence", PostingConfig.min_confidence),
            show_dropped_reasoning=posting_raw.get(
                "show_dropped_reasoning", PostingConfig.show_dropped_reasoning
            ),
            approve_reviews=posting_raw.get("approve_reviews", PostingConfig.approve_reviews),
            max_inline_comments=posting_raw.get(
                "max_inline_comments", PostingConfig.max_inline_comments
            ),
        ),
    )

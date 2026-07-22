# AGENTS.md - Argus

Guidance for coding agents working in this repository.

## Project Overview

Argus is a Python 3.10+ CLI and GitHub Action for AI pull request review. It
gathers PR context, runs multiple narrow review "lenses" in parallel, sends
their proposed findings through an evidence-based curator, writes a local
report, and, for active GitHub runs, also posts a GitHub review.

The design is recall-first: lenses are allowed to over-report concrete
suspicions, while the curator is responsible for merging duplicates and dropping
only findings it can justify.

## Repository Layout

- `src/argus/cli.py` defines the `argus` CLI (`init` and `review`), gathers the
  selected context, optionally posts an active GitHub review, and always writes
  the local report.
- `src/argus/config.py` owns defaults and `.argus/config.yml` loading.
- `src/argus/context/` gathers local Git diffs or GitHub PR context and applies
  context budgets.
- `src/argus/lenses/` defines lens prompts and loads built-in or custom lenses.
- `src/argus/models/client.py` wraps LiteLLM calls for the planner, lenses, and
  curator.
- `src/argus/curator/` deduplicates and validates curator drop decisions against
  quoted evidence.
- `src/argus/pipeline.py` orchestrates the planner and parallel lenses, curates
  their findings, and returns the curated result to the caller.
- `src/argus/posting/` writes shadow reports or posts idempotent GitHub reviews.
- `src/argus/github_app.py` exchanges the GitHub App private key for a
  short-lived installation token used for GitHub auth.
- `src/argus/report.py` renders markdown reports and computes review verdicts.
- `tests/` contains unit tests for CLI, config, context, curation, posting, and
  reporting behavior.
- `eval/` contains the recall harness and seed bug fixtures.
- `.argus/config.yml.example` documents user-facing config. `.argus/config.yml`
  is this repo's dogfooding config.
- `action.yml` is the composite GitHub Action wrapper.

## Development Commands

Set up a local editable environment:

```bash
pip install -e ".[dev]"
```

Run the locally reproducible checks used by CI:

```bash
ruff check src tests eval scripts
ruff format --check src tests eval scripts
python scripts/check_readme_version.py
mypy src
bandit -r src
pip-audit --skip-editable
pytest
```

CI also runs CodeQL and the informational recall eval. CodeQL runs only in
GitHub Actions; run the eval locally as described below when changing review
behavior.

Run the recall evaluation when changing prompts, lenses, curation, or model
behavior:

```bash
python eval/run_eval.py
```

The eval constructs `Config()` directly; it does not load `.argus/config.yml`.
Without overrides it therefore uses the default Anthropic models and requires
their provider key. To select different eval models without editing code:

```bash
ARGUS_EVAL_LENS_MODEL=gemini/gemini-2.5-flash \
ARGUS_EVAL_CURATOR_MODEL=gemini/gemini-2.5-flash \
python eval/run_eval.py
```

## Coding Conventions

- Keep code compatible with Python 3.10+.
- Follow Ruff formatting with a 100-character line length.
- Prefer small, typed dataclasses and functions; the current code intentionally
  keeps modules direct and easy to audit.
- Use fixed argv lists for subprocess calls. Avoid shell interpolation for Git
  commands.
- Treat provider/network failures deliberately. Planner failure, malformed lens
  JSON, malformed or mismatched curator JSON, and GitHub thread-resolution
  failure are fail-open by design. Provider exceptions from lens or curator
  requests currently propagate and fail the review; do not change either policy
  accidentally.
- Make fail-open logs actionable: identify the failed stage or lens and retain
  useful exception context, while never including credentials or other secret
  values.
- Give every model-provider call, GitHub/API request, raw HTTP request, and
  subprocess invocation an explicit finite timeout so an unresponsive external
  dependency cannot stall the review indefinitely.
- Do not broaden context collection casually. Narrow context limits are part of
  the product behavior, not just an optimization.

## Important Behavior To Preserve

- `mode: active` is the default. Local active runs cannot post to a PR and should
  fall back to writing a shadow report.
- CLI `--lens-model` and `--curator-model` override config only when non-empty.
  Empty strings from Action inputs must be no-ops.
- GitHub auth prefers `GITHUB_APP_ID` plus `GITHUB_APP_PRIVATE_KEY` when both are
  present, otherwise falls back to `GITHUB_TOKEN`.
- Treat `GITHUB_APP_PRIVATE_KEY`, provider API keys, and GitHub tokens as
  secrets: source them only from environment variables or the CI secrets store,
  never hardcode or commit them, and never include their values in logs or error
  messages.
- The planner summary is useful but optional. If planner generation fails, log
  and continue with lenses.
- Lens output that cannot be parsed should be skipped with a warning, not crash
  the review.
- Curator output that cannot be parsed should keep findings rather than drop
  them silently.
- A factual curator `drop` must include evidence that appears in the diff or
  changed-file context. If evidence is missing, keep the finding downgraded.
- `drop_noise` is for non-problems, duplicate narration, or mis-scoped impact
  claims and does not require a code quote.
- Posting must remain idempotent: one rolling summary, stable fingerprints for
  inline comments, best-effort resolution of addressed threads, and a cap on
  inline comments.
- GitHub inline comments may only target lines accepted by the PR diff. If a
  422 occurs for inline comments, retry body-only instead of failing the run.
- Bot approvals can fail depending on repo settings or token owner. Fall back
  to a comment for those GitHub 422 cases.

## Lenses And Prompts

Built-in lenses live in `src/argus/lenses/builtin/*.md`. Custom lenses are plain
Markdown and are referenced from config as:

```yaml
lenses:
  - security
  - custom: .argus/lenses/payment-safety.md
```

When editing lenses:

- Keep each lens narrow and explicit about what it should ignore.
- A finding must assert a concrete problem, not praise or describe a change.
- Preserve the JSON-only contract expected by `run_lens`.
- Allow omissions to use `quote: null` when there is no changed line to anchor.
- Run `python eval/run_eval.py` and report recall changes when prompt behavior is
  affected.

## Configuration Notes

`Config` defaults live in `src/argus/config.py`; keep those aligned with
`.argus/config.yml.example` and README examples.

This repo's `.argus/config.yml` intentionally uses Gemini flash models for
dogfooding and ignores `eval/seed_bugs/*`, because those fixtures contain
intentional bugs.

## Testing Guidance

- Add focused unit tests for behavior changes in the corresponding `tests/`
  module.
- For GitHub posting changes, preserve the coverage in `tests/test_posting.py`
  for idempotency, fingerprinting, verdict mapping, commentability, approval 422
  fallback to a comment, inline-comment 422 retry without inline comments, and
  propagation of non-422 errors.
- For config changes, test missing config defaults, type validation, and override
  behavior.
- For context changes, test both local diff behavior and ignore/budget logic.
- For model parsing or curation changes, preserve the coverage in
  `tests/test_client.py` that malformed lens output is logged and skipped and
  malformed or mismatched curator output keeps every finding.
- For prompt/lens/curator behavior changes, run the eval harness in addition to
  unit tests.

## Common Tasks

- Add or change a CLI option in `src/argus/cli.py`, thread it into `Config` or
  the relevant call site, then cover it with `click.testing.CliRunner` tests.
- Add a config key in `src/argus/config.py`, document it in
  `.argus/config.yml.example`, and test defaults plus explicit values.
- Add a built-in lens by creating `src/argus/lenses/builtin/<name>.md`, adding
  it to `BUILTIN_LENSES`, and updating docs/config examples when it should be
  enabled by default.
- Change GitHub posting in `src/argus/posting/github.py` with care. Mock GitHub
  objects in tests rather than calling the live API.
- Change model prompts in `src/argus/models/client.py` or lens Markdown with an
  eval run and a note about any recall movement.
- When releasing, update the version in `pyproject.toml`,
  `src/argus/__init__.py`, and every version-pinned README installation or
  Action example together, then run `python scripts/check_readme_version.py`.

## Release And CI Notes

CI installs `pip install -e ".[dev]"` and runs lint, format check, README
version check, mypy, Bandit, pip-audit, pytest, CodeQL, and an informational
eval job.

The GitHub Action in `action.yml` installs the package from the action checkout
and runs `argus review --github --repo "$GITHUB_REPOSITORY"`, adding optional
`--pr`, `--mode`, `--lens-model`, and `--curator-model` flags when inputs are
provided.

The `release-tag.yml` workflow moves the floating major tag and creates a
GitHub Release when a `vX.Y.Z` tag is pushed.

Keep this file true. If project behavior, commands, or release flow changes,
update `AGENTS.md` in the same PR so future agents inherit the current map.

<p align="center">
  <img src="assets/logo.svg" width="120" alt="Argus logo" />
</p>

<h1 align="center">Argus</h1>

<p align="center">
  Many narrow lenses over-report. One curator drops a finding only if it can quote the diff proving it wrong.
</p>

<p align="center">
  <a href="https://github.com/sibinms/argus/actions/workflows/ci.yml"><img src="https://github.com/sibinms/argus/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <a href="https://github.com/sibinms/argus/releases"><img src="https://img.shields.io/github/v/release/sibinms/argus" alt="Latest release" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+" />
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/code%20style-ruff-261230.svg" alt="Code style: ruff" /></a>
</p>

---

Argus runs multiple specialized AI reviewers in parallel, then uses an
evidence-based curator to verify findings before posting review comments
on your pull requests.

**Highlights**

-   🧭 A planner briefs every reviewer up front on intent and invariants
-   🔍 Eight parallel specialized reviewers ("lenses"), plus your own
-   🧠 Evidence-based curator
-   🤖 Bring your own LLM (OpenAI, Anthropic, Gemini, OpenRouter, any LiteLLM provider)
-   🔒 Runs entirely in your GitHub Action or locally
-   📝 Markdown-based custom lenses
-   📊 Built-in recall evaluation

------------------------------------------------------------------------

## Why Argus?

Most AI code review tools optimize for **precision**.

Argus optimizes for **recall**.

Instead of relying on a single model to review an entire pull request,
Argus runs multiple focused reviewers in parallel. Each reviewer
intentionally looks for a specific class of problems.

A final curator merges duplicate findings, verifies evidence, and only
dismisses issues when it can support the decision with code from the
diff.

The goal is simple:

> Find more real bugs while keeping false positives manageable.

------------------------------------------------------------------------

## Architecture

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/architecture-dark.svg" />
    <img src="assets/architecture-light.svg" alt="Argus pipeline: a pull request is read by a planner that writes a shared review brief, which fans out to nine parallel lenses on a cheap model, converges on a curator on a strong model that drops findings only with a cited quote, and posts one GitHub review verdict." width="100%" />
  </picture>
</p>

A pull request first goes to a **planner** — one cheap call that reads
the diff and writes a short brief: what the PR is trying to do, what
invariants must still hold, and specific yes/no questions worth
checking (including catching typo'd dictionary-key strings, a common
silent-failure bug). That brief is handed to all eight built-in lenses
(plus any of your own), which review in parallel on a cheap model and
are told to over-report — the planner gives them shared context so a
cross-file or invariant-breaking bug doesn't slip through a narrow
angle. The curator — on your strong model — merges duplicates and
drops a finding only when it can quote the diff proving it wrong, then
posts one verdict as a GitHub review. The `…your own` lens is you: add
reviewers as plain Markdown (see [Writing Custom Lenses](#writing-custom-lenses)).

------------------------------------------------------------------------

## Features

| Feature | Description |
| --- | --- |
| Planner | One cheap call reads the PR first and briefs every lens on intent, invariants and what to check. |
| Eight Parallel Lenses | Independent reviewers, each focused on a different problem domain, plus any you add. |
| Evidence-Based Curation | Findings are removed only when evidence contradicts them. |
| Provider Agnostic | Works with OpenAI, Anthropic, Gemini, OpenRouter and any LiteLLM provider. |
| Custom Lenses | Create new reviewers using Markdown. |
| Shadow Mode | Generate reports without commenting on PRs. |
| Active Mode | Publish inline comments and review verdicts. |
| Recall Evaluation | Benchmark prompt changes against known bugs. |

------------------------------------------------------------------------

## Quick Start

### GitHub Action

Pick your provider and pass that provider's key. The model itself can be
set either way: commit `.argus/config.yml` (see [Configuration](#configuration)),
or skip the file entirely and pass `lens-model`/`curator-model` right in
the workflow, as shown below.

**Anthropic**

``` yaml
- uses: sibinms/argus@v1.2.26
  with:
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

**OpenAI**

``` yaml
- uses: sibinms/argus@v1.2.26
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  with:
    lens-model: gpt-4o-mini
    curator-model: gpt-4o
```

**Gemini**

``` yaml
- uses: sibinms/argus@v1.2.26
  env:
    GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
  with:
    lens-model: gemini/gemini-3.5-flash
    curator-model: gemini/gemini-3.1-pro-preview
```

**OpenRouter** — one key, hundreds of models across providers.

``` yaml
- uses: sibinms/argus@v1.2.26
  env:
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
  with:
    lens-model: openrouter/anthropic/claude-3.5-haiku
    curator-model: openrouter/anthropic/claude-3.5-sonnet
```

Any other provider [LiteLLM](https://docs.litellm.ai/docs/providers)
supports works the same way: that provider's env var, that provider's
model string. Lens and curator can each use a different one — the
`lens-model`/`curator-model` inputs override `.argus/config.yml` when
both are present, so a workflow-level pick always wins for a quick test.

> **`mode: active` is the default.** Argus posts real inline comments
> and a verdict on the first PR it runs on. Set `mode: shadow` in
> `.argus/config.yml` to generate a report without commenting until
> you're happy with what it finds.

------------------------------------------------------------------------

### CLI

``` bash
pip install "git+https://github.com/sibinms/argus.git@v1.2.26"
argus init

export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY, GEMINI_API_KEY, ...

argus review --base origin/main --head HEAD
```

Not on PyPI yet, so install from the tagged commit. If it fails with a
Rust/`maturin` build error, add `--only-binary=:all:` to the `pip`
command (a LiteLLM dependency is trying to build from source).

Pick a model on the command line without touching `.argus/config.yml`:

``` bash
argus review --base origin/main --head HEAD \
  --lens-model gpt-4o-mini --curator-model gpt-4o
```

------------------------------------------------------------------------

### Other CI (Cloud Build, etc.)

`action.yml` is only a convenience wrapper for GitHub Actions — the CLI
itself has no GitHub Actions dependency and runs on any CI. On Google
Cloud Build, install and invoke it directly, passing the repo and PR
number as Cloud Build substitution variables:

``` yaml
steps:
  - name: python:3.12
    entrypoint: bash
    args:
      - -c
      - |
        pip install "git+https://github.com/sibinms/argus.git@v1.2.26"
        argus review \
          --github \
          --repo $$REPO_FULL_NAME \
          --pr $$_PR_NUMBER \
          --mode active
    secretEnv:
      - GITHUB_TOKEN
      - GEMINI_API_KEY
```

`GITHUB_TOKEN` reads the PR diff/files and posts the review comment;
set it — and your model provider's own key — as a Cloud Build secret.
Any other CI works the same way: the only GitHub Actions-specific bit
is auto-detecting `--repo`/`--pr` from the Actions event payload, and
every other CI already exposes its own equivalent build variables for
that.

**Posting as a bot, not a person.** A personal access token works, but
every comment then shows up as posted by whoever owns that token — and
if that person happens to also be the PR's author, GitHub rejects a
`REQUEST_CHANGES`-style review outright (Argus falls back to a plain
comment in that case, but a dedicated identity avoids the problem
entirely). For a real bot identity, register a
[GitHub App](https://docs.github.com/en/apps/creating-github-apps),
grant it **Pull requests: read & write** and **Contents: read**, install
it on your repos, and set two secrets instead of `GITHUB_TOKEN`:

``` bash
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY="$(cat argus-app-private-key.pem)"
```

When both are set, `argus review --github` mints a short-lived
installation access token itself (via PyGithub's `GithubIntegration`,
already a dependency — no extra library needed) and posts under the
App's identity; `GITHUB_TOKEN` is only used as a fallback when App
credentials aren't present.

------------------------------------------------------------------------

## Example Review

``` text
❌ Possible SQL Injection

Severity: High

Reason
User input is interpolated directly into an SQL query.

Evidence
db.execute(
    f"SELECT * FROM users WHERE id={user_id}"
)

Suggestion
Use parameterized queries instead.
```

------------------------------------------------------------------------

## Configuration

Configure:

-   Models
-   Lenses
-   Context limits
-   Confidence thresholds
-   Review mode (shadow / active)
-   Whether a clean PR gets a real **Approved** review (`approve_reviews`)

See `.argus/config.yml.example`.

`models.lens`/`models.curator` can also be set without a config file at
all — the Action's `lens-model`/`curator-model` inputs and the CLI's
`--lens-model`/`--curator-model` flags override whatever `.argus/config.yml`
says (or the defaults, if there's no file). Useful for a quick test of a
different model; commit the config file once you've settled on one.

### Approving pull requests

By default Argus posts its verdict as a comment. To have a clean PR receive a
real **Approved** review, set `approve_reviews: true` in `.argus/config.yml`
**and** enable *Settings → Actions → General → "Allow GitHub Actions to approve
pull requests"* on the repo. Without that setting the GitHub Actions token
can't approve, so Argus falls back to a comment rather than failing the run. A
bot approval shows as Approved but doesn't count toward a branch-protection
"require N approvals" rule.

### No comment pile-up

Argus reviews every push, but it moderates itself rather than stacking
comments — and posts a genuinely new comment only when there's something new
to say, never an invisible edit to something old:

- **Each finding is posted inline once** (fingerprinted so re-wording or line
  drift doesn't create duplicates), on the diff line it applies to. A finding
  that can't attach to a line (a file-level or architectural concern) goes in
  a small separate comment instead, under the same one-time rule.
- **Replies count.** If a finding is still flagged but someone's replied on
  its thread, Argus re-runs the curator with that reply as context before
  deciding what's new — so explaining why a finding doesn't apply can change
  the verdict on the next run instead of the same comment reappearing forever.
  A reply can downgrade or dismiss a finding, but can't out-argue a real quote
  from the diff — that's still the only way to fully drop one.
- A finding's thread is **resolved** once the finding is addressed in the code
  (or dismissed via a reply the curator accepts).
- A **hard cap** (`max_inline_comments`, default 10) bounds inline comments for
  the life of the PR.
- A new review is submitted **only when something changed** — otherwise Argus
  stays quiet.

------------------------------------------------------------------------

## Writing Custom Lenses

Lenses are plain Markdown.

Example:

``` md
# Performance

Look for:

- unnecessary allocations
- N+1 queries
- repeated database calls

Ignore stylistic issues.
```

No Python required. Full guide: [`docs/writing-a-lens.md`](docs/writing-a-lens.md).

------------------------------------------------------------------------

## Evaluation

Argus includes an evaluation harness for measuring recall.

``` bash
python eval/run_eval.py
```

Benchmark prompt changes using real bug-fix history instead of
intuition.

------------------------------------------------------------------------

## Privacy

Argus never uses a hosted backend.

Everything runs:

-   in your GitHub Action
-   or on your local machine

Your chosen LLM provider receives only the context required for review —
this now includes replies left on a finding's thread, since the curator
reads them to decide whether a finding still stands.

------------------------------------------------------------------------

## Roadmap

-   [x] GitHub Action
-   [x] CLI
-   [x] Markdown lenses
-   [x] Evidence-based curator
-   [x] Recall evaluation
-   [ ] GitLab support
-   [ ] Bitbucket support
-   [ ] Azure DevOps support
-   [ ] VS Code extension

------------------------------------------------------------------------

## Contributing

Pull requests are welcome.

Before submitting changes, run the same checks CI enforces:

``` bash
ruff check src tests eval scripts
ruff format --check src tests eval scripts
python scripts/check_readme_version.py
mypy src
bandit -r src && pip-audit --skip-editable
pytest
```

If you change a lens or the curator, run `python eval/run_eval.py` and
include the recall change in your PR description — CI also runs it on
every PR (`eval` job) and reports the number, but informationally only,
since LLM output is non-deterministic and a hard recall gate would be
flaky. Don't rely on CI alone here; state what you measured.

------------------------------------------------------------------------

## License

MIT

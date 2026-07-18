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

-   🔍 Parallel specialized reviewers ("lenses")
-   🧠 Evidence-based curator
-   🤖 Bring your own LLM (OpenAI, Anthropic, Gemini, LiteLLM)
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

``` text
Pull Request
      │
      ▼
 Context Builder
      │
      ▼
 ┌───────────────────────────────┐
 │ Security │ Tests │ Contracts │
 │ Errors   │ Performance │ ... │
 └───────────────────────────────┘
             │
             ▼
      Evidence Curator
             │
             ▼
 GitHub Review + Verdict
```

------------------------------------------------------------------------

## Features

  -----------------------------------------------------------------------
  Feature                     Description
  --------------------------- -------------------------------------------
  Parallel Lenses             Independent reviewers focused on different
                              problem domains.

  Evidence-Based Curation     Findings are removed only when evidence
                              contradicts them.

  Provider Agnostic           Works with OpenAI, Anthropic, Gemini and
                              any LiteLLM provider.

  Custom Lenses               Create new reviewers using Markdown.

  Shadow Mode                 Generate reports without commenting on PRs.

  Active Mode                 Publish inline comments and review
                              verdicts.

  Recall Evaluation           Benchmark prompt changes against known
                              bugs.
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## Quick Start

### GitHub Action

``` yaml
- uses: sibinms/argus@v1.2.3
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Create:

``` text
.argus/config.yml
```

Choose your preferred models and run the workflow.

------------------------------------------------------------------------

### CLI

``` bash
pip install "git+https://github.com/sibinms/argus.git@v1.2.3"

argus init

export OPENAI_API_KEY=...

argus review --base origin/main --head HEAD
```

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

See `.argus/config.yml.example`.

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

No Python required.

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

Your chosen LLM provider receives only the context required for review.

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

Before submitting changes:

``` bash
ruff check
mypy src
pytest
python eval/run_eval.py
```

If you change prompts, include recall improvements where possible.

------------------------------------------------------------------------

## License

MIT

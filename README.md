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

## Contents

- [Quick Start](#quick-start)
- [Features](#features)
- [Configuration](#configuration)
- [Writing your own lenses](#writing-your-own-lenses)
- [Releases](#releases)
- [Contributing](#contributing)

## Quick Start

### GitHub Action — pick a provider

**Anthropic**

```yaml
- uses: sibinms/argus@v1.2.3
  with:
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

**OpenAI**

```yaml
- uses: sibinms/argus@v1.2.3
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Then point `.argus/config.yml` at an OpenAI model — e.g. `models.lens: gpt-4o-mini`, `models.curator: gpt-4o`.

**Gemini**

```yaml
- uses: sibinms/argus@v1.2.3
  env:
    GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

Then point `.argus/config.yml` at a Gemini model — e.g. `models.lens: gemini/gemini-1.5-flash`, `models.curator: gemini/gemini-1.5-pro`.

Any other provider [litellm](https://docs.litellm.ai/docs/providers) supports works the same way: that provider's env var, that provider's model string. Lens and curator don't need to match — mix freely.

> **Heads up: `mode: active` is the default.** As soon as this workflow runs on a pull request, Argus posts real inline comments and a verdict (approve, comment, or request changes) — no separate opt-in step. Set `mode: shadow` in `.argus/config.yml` first if you want to see what it would say before it says anything on a real PR: it writes a job summary and changes nothing on the PR.

### CLI (local runs)

```bash
pip install "git+https://github.com/sibinms/argus.git@v1.2.3"
argus init                 # writes .argus/config.yml
export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY, GEMINI_API_KEY, ... — whatever
                                   # provider the models in .argus/config.yml point at
argus review --base origin/main --head HEAD
```

Not on PyPI yet, so this installs straight from the tagged commit. Posting only ever happens with `--github` against a real PR — a local run like this always just writes `argus-report.md`.

<details>
<summary>Install troubleshooting, other platforms, and what Argus actually does with your data</summary>

**Rust/`maturin` build error.** A transitive dependency (`tokenizers`, via litellm) is trying to compile from source because no prebuilt wheel matched your platform/Python version. Add `--only-binary=:all:` to the pip command above, or run `rustup update` first if you'd rather build it. (The package name is `argus-pr-review`, not `argus-review` — that name's taken on PyPI by an unrelated project.)

**Other platforms.** GitHub is the only one supported right now (GitHub Action + GitHub review API). GitLab/Bitbucket/Azure DevOps isn't built — contributions welcome, see [Contributing](#contributing).

**Data privacy.** Argus runs in *your* Action or *your* CLI on *your* API key, and doesn't call home to any Argus-operated service — there isn't one. The diff, the changed files (subject to your `context` budget in `.argus/config.yml`), and the PR title/description go to whichever provider your `models.lens`/`models.curator` point at, via litellm — nothing else leaves your CI runner or machine. Whether that data trains a model is governed by that provider's own policy, not by Argus. Argus itself stores nothing: no database, no telemetry — the only output is the report file and, in active mode, the comments it posts via the GitHub token you provide.

</details>

## Features

| Lens | What it flags |
|---|---|
| `security` | Injection, broken auth, hardcoded secrets, unsafe deserialization, sensitive data in logs |
| `tests` | New logic with no test, bug fixes with no regression test, weak assertions, silently skipped tests |
| `error_handling` | Swallowed exceptions, missing timeouts, unreleased resources, retries with no idempotency |
| `contracts` | Breaking API/schema changes, callers still using old assumptions, changed defaults |
| *(yours)* | Anything — lenses are plain markdown, see [Writing your own lenses](#writing-your-own-lenses) |

| Capability | Support |
|---|---|
| Shadow mode (report only, never posts) | ✅ |
| Active mode (inline comments + verdict) | ✅ |
| Evidence-checked curator (can't drop without a citation) | ✅ |
| Custom lenses via markdown, no code | ✅ |
| Recall eval harness against seed bugs | ✅ |
| Any provider litellm supports (Anthropic, OpenAI, Gemini, ...), mixed per role | ✅ |
| GitHub Action + CLI | ✅ |
| GitLab / Bitbucket / Azure DevOps | ❌ (contributions welcome) |

<details>
<summary>Why use Argus?</summary>

- **Built to actually find things.** Every lens is explicitly told to over-report, because a single cautious reviewer that never writes down a suspicion also never catches the real bug behind it.
- **Precision is enforced once, not everywhere.** The curator is the only place a finding can be dropped, and it can't just be "unsure" — it has to quote real text from the diff that contradicts the finding, or the finding survives (downgraded, not deleted).
- **Cheap where it should be, careful where it matters.** Lenses run on a cheap model for volume; the curator can run on your strongest model, since judgment — not generation — is where quality actually pays off.
- **Measured, not vibes-checked.** `eval/run_eval.py` replays known bugs through the full pipeline and reports recall, so a prompt or context change is judged on whether it actually catches more, not on how it reads.
- **Open, no vendor lock-in.** Bring your own API key for whichever provider you prefer, run it as a GitHub Action or the CLI, and read every prompt in `src/argus/lenses/builtin/` — nothing about what it looks for is hidden.

</details>

<details>
<summary>How it works</summary>

```mermaid
flowchart LR
    ctx["diff + files<br/>+ PR intent"]

    subgraph panel ["lenses, run in parallel, told to over-report"]
        l1[security]
        l2[tests]
        l3[error handling]
        l4[contracts]
        l5[...yours]
    end

    ctx --> l1
    ctx --> l2
    ctx --> l3
    ctx --> l4
    ctx --> l5

    curator["curator<br/>merge duplicates, verify,<br/>drop only with a cited quote"]

    l1 --> curator
    l2 --> curator
    l3 --> curator
    l4 --> curator
    l5 --> curator

    curator --> out["posted findings<br/>+ verdict"]
```

Lenses run in parallel on a cheap model and are told to over-report. The curator — the only place precision is enforced — merges duplicates and can only drop a finding by quoting real text from the diff that contradicts it; see Design notes further down for why that's checked in code, not just asked for in a prompt.

</details>

## Configuration

See [`.argus/config.yml.example`](.argus/config.yml.example) for every option: which model plays lens vs. curator, which lenses run, context size limits, and the confidence floor for posting. Copy it to `.argus/config.yml` and commit it — this file is the one piece of the tool a user should read before trusting it with their repo.

## Writing your own lenses

Lenses are plain markdown, no code. See [`docs/writing-a-lens.md`](docs/writing-a-lens.md).

<details>
<summary>Measuring recall, not silence</summary>

A reviewer that finds nothing and a reviewer that's actually correct look identical from the outside — dashboards that count posted/filtered findings miss that a finding was never generated in the first place. `eval/run_eval.py` replays a small set of known bugs (see `eval/seed_bugs/`) through the full pipeline and reports recall: how many of them it actually catches.

```bash
export ANTHROPIC_API_KEY=sk-...   # or whichever provider key your config needs
python eval/run_eval.py
```

Add your own seeds pulled from your repo's real bug-fix history — a `diff.patch` plus an `expected.yml` describing what a good review should have caught. Run the eval before and after any change to prompts, context budgets, or lenses. If a change doesn't move recall up, don't ship it on intuition alone.

</details>

<details>
<summary>Quality and security checks (CI)</summary>

Every push and pull request runs through [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

| Job | What it checks |
|---|---|
| `lint` | [Ruff](https://github.com/astral-sh/ruff) — style, formatting, and that the README's pinned version matches `pyproject.toml` |
| `typecheck` | [mypy](https://mypy-lang.org/) against `src/` |
| `security` | [Bandit](https://bandit.readthedocs.io/) (static analysis) and [pip-audit](https://github.com/pypa/pip-audit) (known CVEs in dependencies) |
| `codeql` | [GitHub CodeQL](https://codeql.github.com/), also scheduled weekly so new advisories get caught between pushes |
| `test` | the `pytest` suite |

Run the same checks locally before pushing:

```bash
pip install -e ".[dev]"
ruff check src tests eval scripts && ruff format --check src tests eval scripts
python scripts/check_readme_version.py
mypy src
bandit -r src && pip-audit --skip-editable
pytest
```

</details>

## Releases

Tags follow semver (`v1.2.3`, ...). Pin the Action to a specific tag rather than `@main` — `@main` tracks whatever's newest, including changes to lens prompts or curator behaviour that could shift what gets posted on your PRs. See [Releases](https://github.com/sibinms/argus/releases) for the changelog on each version.

<details>
<summary>Design notes — a few decisions that aren't obvious from the code</summary>

- **Context is deliberately narrow.** No "explore the repo" agent mode, no full-file dumps beyond the changed files, no auto-included caller context. Wide context repeatedly measured *worse* recall in testing: models read bulk usage as reassurance ("this must be handled somewhere") rather than evidence. Widen it in your own config if you've measured it helping for your codebase — don't assume more context is free.
- **The curator's drops are checked, not trusted.** `curator/evidence.py` verifies that any quote the curator offers as grounds for dropping a finding actually appears in the diff or files. If it can't be verified, the finding is kept (downgraded) instead of silently deleted.
- **Cheap model for volume, expensive model for judgment.** Put your strongest model on `curator`, not `lens` — a lens's job is to generate candidates, not to be right the first time.
- **Active by default, on purpose.** A reviewer that only ever writes to a report file nobody reads doesn't help anyone. Defaulting to `mode: active` means the tool does its actual job — posting a real verdict — the moment it's added to a repo, instead of asking every new user to find the config option that turns it on. The tradeoff is real: it will comment on your very first PR. Use `mode: shadow` if you'd rather watch it work before it's aimed at anything.

</details>

## Contributing

Issues and pull requests are welcome. If you're adding a lens, see [`docs/writing-a-lens.md`](docs/writing-a-lens.md) and run `eval/run_eval.py` before/after to show it moves recall. If you're changing curator or context behaviour, the same applies: the eval harness is the thing to check, not intuition. Support for other git platforms (GitLab, Bitbucket, Azure DevOps) is open territory — nothing in `src/argus/posting/` assumes GitHub beyond that one module.

## License

MIT, see [LICENSE](LICENSE).

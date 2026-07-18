<p align="center">
  <img src="assets/logo.svg" width="120" alt="Argus logo" />
</p>

<h1 align="center">Argus</h1>

<p align="center">
  An open source AI PR reviewer built from many narrow lenses and one careful curator.
</p>

---

Most AI code reviewers are one cautious model doing two jobs at once:
proposing problems, and deciding which ones are real. Caution wins, and the
reviewer goes quiet — approving pull requests it never actually looked hard
at, because it was never willing to write down the suspicion in the first
place.

Argus splits the two jobs:

- **Lenses** are small, narrow reviewers, each briefed on one angle
  (security, missing tests, error handling, contract breaks, or whatever you
  add). They run on a cheap model and are explicitly told to over-report.
- **The curator** looks at everything the lenses raised, merges duplicates,
  and can only drop a finding if it can quote real text from the diff that
  contradicts it. "I doubt it" isn't a reason. If it can't back the claim,
  the finding survives, downgraded rather than deleted.

```
   diff + files + PR intent
              │
     ┌────────┼────────┬─────────┬──────────┐
     ▼        ▼        ▼         ▼          ▼
 security   tests   errors   contracts   (yours)     ← lenses, run in parallel,
     │        │        │         │          │           told to over-report
     └────────┴────────┴─────────┴──────────┘
                        │
                    curator                          ← merges, verifies,
                        │                                can only drop with
                        ▼                                a cited quote
              posted findings + verdict
```

## Quick start

```bash
pip install argus-review
argus init                 # writes .argus/config.yml
export ANTHROPIC_API_KEY=sk-...
argus review --base origin/main --head HEAD
```

This runs against a local diff and writes `argus-report.md`. Nothing gets
posted anywhere until `mode: active` in `.argus/config.yml`, or `--mode
active`, and you're running against a real PR with `--github`.

## As a GitHub Action

```yaml
# .github/workflows/argus.yml
name: Argus review
on: pull_request

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: your-org/argus@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Start with `mode: shadow` in `.argus/config.yml` — it writes a job summary
and changes nothing on the PR. Once you trust the recall (see below), switch
to `mode: active` and it posts inline comments plus a verdict (approve,
comment, or request changes) using the GitHub review API.

## Configuration

See [`.argus/config.yml.example`](.argus/config.yml.example) for every
option: which model plays lens vs. curator, which lenses run, context size
limits, and the confidence floor for posting. Copy it to `.argus/config.yml`
and commit it — this file is the one piece of the tool a user should read
before trusting it with their repo.

## Writing your own lenses

Lenses are plain markdown, no code. See
[`docs/writing-a-lens.md`](docs/writing-a-lens.md).

## Measuring recall, not silence

A reviewer that finds nothing and a reviewer that's actually correct look
identical from the outside — dashboards that count posted/filtered findings
miss that a finding was never generated in the first place. `eval/run_eval.py`
replays a small set of known bugs (see `eval/seed_bugs/`) through the full
pipeline and reports recall: how many of them it actually catches.

```bash
export ANTHROPIC_API_KEY=sk-...
python eval/run_eval.py
```

Add your own seeds pulled from your repo's real bug-fix history — a
`diff.patch` plus an `expected.yml` describing what a good review should
have caught. Run the eval before and after any change to prompts, context
budgets, or lenses. If a change doesn't move recall up, don't ship it on
intuition alone.

## Design notes

A few decisions that aren't obvious from the code:

- **Context is deliberately narrow.** No "explore the repo" agent mode, no
  full-file dumps beyond the changed files, no auto-included caller context.
  Wide context repeatedly measured *worse* recall in testing: models read
  bulk usage as reassurance ("this must be handled somewhere") rather than
  evidence. Widen it in your own config if you've measured it helping for
  your codebase — don't assume more context is free.
- **The curator's drops are checked, not trusted.** `curator/evidence.py`
  verifies that any quote the curator offers as grounds for dropping a
  finding actually appears in the diff or files. If it can't be verified,
  the finding is kept (downgraded) instead of silently deleted.
- **Cheap model for volume, expensive model for judgment.** Put your
  strongest model on `curator`, not `lens` — a lens's job is to generate
  candidates, not to be right the first time.

## License

MIT, see [LICENSE](LICENSE).

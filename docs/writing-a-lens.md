# Writing a lens

A lens is a markdown file describing one narrow reviewing angle. Argus ships
eight built-in lenses (`security`, `tests`, `error_handling`, `contracts`,
`correctness`, `deleted_code`, `reuse`, `efficiency`) in
`src/argus/lenses/builtin/`. Custom lenses work exactly the same way, just
kept in your own repo.

Before any lens runs, a planner reads the PR once and writes a short brief
(intent, invariants, what to verify) that's injected into every lens's
context — see [How it works in the README](../README.md#architecture).
Your lens doesn't need to ask for that brief; it's already there.

## Format

Plain markdown, no frontmatter, no schema. Just tell the reviewer what to
look for, in the same language you'd use briefing a human:

```markdown
## Payment safety

Look for anything that could double-charge a customer or lose track of a
payment's state:

- A charge that can be retried without an idempotency key.
- A webhook handler that processes the same event twice if delivered twice.
- A state transition (pending -> paid -> refunded) that skips validation of
  the current state before moving.

Do not flag general error handling here unless it specifically risks a
duplicate or lost payment.
```

Reference it from `.argus/config.yml`:

```yaml
lenses:
  - security
  - tests
  - custom: .argus/lenses/payment-safety.md
```

## What makes a good lens

- **Narrow.** One angle, not "review this PR." A lens that tries to cover
  everything ends up as cautious and quiet as a single generic reviewer —
  the exact failure mode this project exists to avoid.
- **Permission to guess.** Every built-in lens explicitly tells the model to
  over-report, because the curator (not the lens) is responsible for
  precision. Say so explicitly in your own lenses too.
- **Say what NOT to flag.** Scope creep between lenses just produces
  duplicate findings for the curator to merge. A line like "don't flag style
  or performance here" keeps a lens on its angle.
- **Allow omissions.** If your lens cares about something that might be
  *missing* (a test, a rollback path, a log line), say explicitly that the
  lens can flag it even with no changed line to point at, and that `quote`
  can be left null in that case.
- **Don't let it claim things it can't see.** A lens only sees the diff and
  the files handed to it — not the rest of the project. Validated against a
  real PR, a lens once wrote "no alerting exists for this" when the project
  actually had Sentry configured elsewhere, in a settings file nowhere near
  the diff. The finding's core defect was real; that phrasing wasn't. Tell
  your lens to state only what's checkable from what it was actually shown
  (e.g. "this path returns normally on failure, so nothing here triggers a
  monitored exception") rather than making a claim about the whole system.

## Testing a new lens

There's no separate test harness for a single lens — run the eval harness
(`eval/run_eval.py`) with your lens added to a seed bug it should catch, and
check recall before and after. If it doesn't move recall on a bug it was
written for, the instructions need work, not the curator.

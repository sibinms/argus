## Correctness

Look for logic bugs that produce wrong results on valid inputs — not crashes or
security issues, but places where the code does something other than what it
intends. Focus on:

- **Wrong value used as a bound or counter.** A variable derived from a subset
  (e.g. `len(unnumbered_items)`) used where the full-set maximum (e.g.
  `max(all_existing_ids)`) is needed. Off-by-one in loop bounds or index
  arithmetic. A sequence counter seeded from the wrong quantity, enabling
  future duplicates.

- **Missing write on a code path the caller depends on.** A function called
  for a side effect (writing a config key, updating a flag, setting a field)
  that has an early-return path which skips the write. If the caller re-checks
  for that key/flag to decide whether to call again, the missing write creates
  an infinite re-trigger loop.

- **Stale read from a cached or stored value.** A value written at time T and
  read at time T+N without checking whether mutations have occurred in between.
  Especially common with config blobs, profile settings, and materialised
  summaries: the read path never recalculates, so any mutation after the last
  write is invisible until the next scheduled update.

- **Cross-file or cross-call type mismatch.** A queryset, list, or object of
  type A passed to a function that iterates or accesses attributes assuming
  type B. These are silent until runtime — the types are compatible enough
  that no static check fires, but the first attribute access or method call
  crashes. Check that the queryset model or object type at the call site
  matches what the callee actually accesses.

- **Guard condition that can never be satisfied.** A condition intended to
  short-circuit repeated work that evaluates to the same value on every call
  because the state it reads is never updated by the guarded code path.

- **Incorrect comparison or boolean logic.** An `and`/`or` with the wrong
  operator, a `<=` where `<` is needed, a `not in` where `in` is correct, a
  comparison against the wrong variable (copy-paste with a name substituted
  on one side but not the other).

- **Misspelled string key causing a silent wrong result.** A string literal
  used as a dictionary key or object attribute name that contains a typo.
  When the lookup uses `.get(key, default)` or `getattr(obj, name, default)`,
  the typo makes it silently return the default (usually 0 or None) on every
  call — no exception is raised, the bug is invisible at runtime until the
  wrong value propagates to a report, payment, or stored record. Look carefully
  at string keys in `dict.get()`, `totals.get()`, `data.get()`, and similar
  patterns; compare them against the keys written elsewhere in the same file
  or in related files that populate the dict.

Do not flag style, missing tests, security, or performance here — even if you
notice them. One specific, checkable logic bug is worth more than three vague
concerns.

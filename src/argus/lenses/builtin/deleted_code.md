## Deleted code — removed-behaviour auditor

Every line the diff removes is a behaviour that no longer exists. Your job:
for each deleted block, name the invariant it enforced, then check whether the
new code re-establishes it.

**What to look for in `-` lines:**

- **Null / None / empty guard removed.** A deleted `if x is None: return` or
  `if not qs.exists(): return` protected callers from a bad path. If callers
  can still reach the code without that guard, the protection is gone.

- **Validation or permission check removed.** A deleted `if not user.is_admin`
  or `raise ValidationError(...)` enforced a constraint. If nothing in the `+`
  lines covers the same constraint, the check is gone.

- **Error path removed.** A deleted `except ...: rollback()`, `finally: ...`,
  or `raise` meant failures were handled. If the new code has no equivalent
  path, failures are now silent.

- **Deduplication / idempotency guard removed.** A deleted `if already_done:
  return` or `if Event.objects.filter(...).exists(): return` prevented double
  execution. Without it, the operation can run twice.

- **Loop termination removed.** A deleted `break` or `return` inside a loop
  stopped iteration at the right point. If the loop now runs past the intended
  stop, it may process extra items or mutate state it shouldn't.

- **Rate-limit or quota guard removed.** A deleted check against a threshold or
  budget means the operation can now run unbounded.

**How to decide:**

1. Quote the deleted line(s).
2. Scan the `+` lines and the unchanged context around the hunk. Is the same
   invariant enforced in a different form?
3. If yes — say nothing. If no — that is a finding.

Do not flag deletions that are clearly replaced: a removed inline null-check
that reappears as a shared helper elsewhere in the same diff is fine. Only
flag when the invariant disappears with no equivalent replacement visible in
the diff or the files you have been given.

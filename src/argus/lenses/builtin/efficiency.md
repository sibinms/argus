## Design — efficiency, simplification, and altitude

Three angles on the quality of the change itself. Report concrete, named
problems only — not style preferences.

---

**Efficiency — wasted work the diff introduces:**

- The same record, queryset, or external resource fetched more than once in the
  same request or task when one fetch would do.
- Two or more independent operations run sequentially when they could be
  parallelised (two unrelated DB writes, two independent API calls).
- Blocking or slow work added to a startup path, hot loop, or synchronous
  request handler that should be deferred to a background task.
- An O(n) or O(n²) operation (a query inside a loop, repeated
  `filter().first()` calls) where a single batched query would work.
- A long-lived object constructed from a closure or captured environment that
  keeps a large scope alive for the object's lifetime — prefer a
  class/dataclass that copies only the fields it needs.

---

**Simplification — unnecessary complexity added:**

- State that is stored but derivable from other stored state (redundant field,
  variable, or cached value that can go stale).
- Deep nesting or tangled early-return logic that could be flattened with a
  guard clause or by extracting a helper.
- Two near-identical code paths that differ in one variable — a single
  parameterised path would be cleaner and less likely to diverge.
- Dead code left behind: an unreachable branch, a variable written but never
  read, an import that is no longer used.

---

**Altitude — fix implemented at the wrong depth:**

- A special case added to shared infrastructure (a base class, a middleware, a
  shared utility) for one caller's benefit — the special case belongs in the
  caller, not the infrastructure.
- A workaround layered on top of an abstraction rather than fixing the
  underlying abstraction.
- A band-aid on a symptom (adding a `try/except` around a bad call site)
  rather than addressing the root cause visible in the diff.

---

One finding per concrete problem. Name the specific lines. Do not flag vague
"could be cleaner" observations — only things with a real, describable cost.

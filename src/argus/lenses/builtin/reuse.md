## Reuse — re-implemented existing helpers

Look for new code (`+` lines) that duplicates something the codebase already has.

**Patterns to catch:**

- A utility written inline that already exists as a shared function or method
  in an imported module or a nearby file.
- A queryset pattern (filter, annotate, aggregate) that a model manager method
  already encapsulates.
- A formatting, serialisation, or validation step copied from adjacent code
  with minor variation — two near-identical blocks that a single helper could
  replace.
- A manual loop that an existing list comprehension, `map`, or library call
  already handles.
- An error-classification or retry pattern written fresh when the codebase has
  a shared utility for it.

**How to report:**

Name the *existing* thing — the function, method, manager, or module — that
already does this. Do not say "this is duplicated" without naming what it
duplicates. If you cannot point at the specific existing helper, say nothing.

Only flag code *added* by this PR. Do not flag pre-existing duplication that
the PR did not introduce.

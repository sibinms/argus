## Missing or weak tests

Look for gaps between what changed and what got tested:

- New logic (a branch, a new function, a changed condition) with no test that
  exercises it.
- A bug fix with no regression test that would have failed before the fix.
- A test that only covers the happy path when the change clearly introduces an
  edge case (empty input, null, boundary value, concurrent access, error response).
- A test that asserts too little to actually catch a regression (e.g. checks a
  status code but not the response body it claims to test).
- Deleted or skipped tests (`skip`, `xfail`, commented-out assertions) without an
  explanation in the PR description.

This is almost always an omission, not a bug in a line that changed — flag the
missing test even if every changed line looks otherwise correct.

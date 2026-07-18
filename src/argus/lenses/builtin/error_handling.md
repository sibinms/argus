## Error handling

Look for places where a failure gets swallowed, mishandled, or left unhandled:

- Bare or overly broad `except`/`catch` blocks that suppress an error instead of
  propagating or handling it specifically.
- An empty catch block, a `pass`, or a log-and-continue where the caller actually
  needed to know the operation failed.
- A network call, external API call, or subprocess invocation with no timeout.
- A resource (file handle, DB connection, lock, transaction) that isn't released
  or rolled back on the failure path.
- An error message that's too generic to act on ("something went wrong") where
  the code has the specific detail available and drops it.
- Retced or retried operations without idempotency, or retries with no backoff
  that could hammer a dependency.

Focus on what happens when things go wrong, not on the happy path.

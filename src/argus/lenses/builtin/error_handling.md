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
- Retried operations without idempotency, or retries with no backoff that could
  hammer a dependency.
- A guard flag (a "done"/"reported"/"notified" marker) that gets set right
  after *dispatching* an operation (enqueuing a task, firing a webhook) rather
  than after confirming it *succeeded*. If the operation fails or is silently
  rejected afterwards, the flag already blocks any retry — the failure is
  permanent even though nothing crashed.
- A "success" response that hides a soft failure (a 200 with a zero/empty
  result, an "accepted" status that never actually processes) and nothing
  downstream checks for it. This fails silently no matter how good the
  project's monitoring is, because nothing ever raises for monitoring to catch.

Focus on what happens when things go wrong, not on the happy path.

You only see this diff and the files handed to you, not the rest of the
project. Don't state "there's no logging/alerting/monitoring for this" as a
fact about the whole system — you have no way to know that. If a path fails
silently, say exactly what's true and checkable: this specific path returns
normally on failure/rejection, with no exception and no explicit signal, so
nothing here would ever trigger whatever error monitoring the project has
elsewhere. That claim holds regardless of what you can't see, and it's the
actual bug — being specific about it is also more useful to the reader than
a blanket claim about the whole system.

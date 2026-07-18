## API and contract changes

Look for changes that break an implicit or explicit contract with a caller:

- A function signature, return type, or response shape that changed without
  updating every caller in this diff.
- A public API, serializer, or schema field that changed meaning, type, or
  nullability in a way that could break existing consumers.
- A default value that changed, silently changing behaviour for callers who
  relied on the old default.
- Backwards-incompatible changes to a migration, config format, or webhook
  payload with no versioning or migration path.
- A changed function that is still called elsewhere in this diff using the old
  assumptions (old argument order, old exception type, old None-handling).

If the PR description says the break is intentional, still flag it, but note
that it appears intentional so the curator can weigh it accordingly.

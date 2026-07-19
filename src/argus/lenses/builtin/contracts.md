## Breaking changes to a public contract

Flag changes that break something *outside this change* that depends on the
code — a real consumer, on a real interface. For every finding, name who
breaks and how. If you can't name a specific external consumer that breaks,
it is not a contract finding.

A "public contract" is a surface something else relies on:

- An exported/public function, method, or class: its signature, return type,
  raised exceptions, or None-handling changed, with callers left on the old
  assumption.
- A REST endpoint, serializer, or API response: a field's type, meaning, or
  nullability changed in a way that breaks existing clients.
- A CLI flag, a shipped/published config schema or its documented defaults, a
  GitHub Action's declared `inputs`/`outputs`, or an environment variable
  consumers are told to set.
- A database migration, or a webhook/event payload, changed with no versioning
  or migration path.
- A default value that changed and silently alters behaviour for callers who
  relied on the old one.

Out of scope — do NOT flag these as contract breaks:

- A repository's own internal files that ship to no one: its own tool config
  (e.g. a committed `.<tool>/config.yml`), its CI or workflow files, its dev
  tooling. Changing how *this* repo configures or runs itself is not a change
  to anyone else's contract.
- Tests, fixtures, comments, documentation.
- Private/internal helpers (e.g. underscore-prefixed) whose callers are all
  inside this same diff.
- Backwards-compatible additions: a new optional argument, a new optional
  action input, a new field. Adding something optional breaks no one.

Don't narrate the change ("the default changed", "an input was removed").
Assert the break: what depended on it, and what now fails for them. Distinguish
the public interface from its internal callers — updating every caller in the
same diff means nothing breaks. If the PR description says a break is
intentional, still flag it, but note that so the curator can weigh it.

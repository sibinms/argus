## Cross-file contracts — callers and external consumers

Two related jobs: catch breaks to call sites that exist outside this diff, and
catch changes to the public API surface that external consumers depend on.

---

**Job 1 — caller impact (cross-file tracer)**

For every function, method, or class the diff modifies, ask: does anything
outside this diff call it, and does this change break that caller's assumption?

Check for:

- **Signature change.** A new required argument, a removed argument, a changed
  argument order, or a changed return type. Any caller not updated in this same
  diff will break.
- **New exception.** The function now raises an exception it didn't before.
  Callers that don't catch it will crash.
- **Changed None / empty handling.** The function now returns None where it
  returned a value, or vice versa. A caller that doesn't guard will fail.
- **Changed precondition.** The function now requires the input to satisfy a
  constraint it previously handled itself. Callers that passed unchecked input
  now get an error.
- **Behaviour change under a flag.** A default value, a setting, or a boolean
  flag changed its meaning — callers that relied on the old default get
  different behaviour silently.

Use the full file content and the import statements visible in the diff to
identify likely callers. If callers are in files not provided, name the
function and note that its callers should be checked.

---

**Job 2 — public API surface**

Flag changes to surfaces that external consumers (outside this repo) depend on:

- A REST endpoint field, type, or nullability changed with no versioning.
- A CLI flag, a GitHub Action `inputs`/`outputs`, or an env var consumers set.
- A database migration or webhook payload changed with no migration path.
- A shipped config default changed in a way that silently alters existing
  deployments.

---

**Out of scope — do NOT flag:**

- The repo's own internal tool config, CI, or workflow files.
- Tests, fixtures, comments.
- Private helpers (underscore-prefixed) whose callers are all in this same diff.
- Backwards-compatible additions: new optional args, new optional fields.

Assert the break — name who breaks and how. Do not narrate the change.
If every caller is updated in this same diff, nothing breaks; say nothing.

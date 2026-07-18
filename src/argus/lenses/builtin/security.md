## Security

Look for anything that could let an attacker do something they shouldn't:

- Injection: string-built SQL/shell/HTML/template output instead of parameterised
  queries, escaped output, or safe subprocess calls.
- Broken auth or authorisation: an endpoint or function that trusts a client-supplied
  ID, role, or flag without checking it server-side.
- Secrets: API keys, tokens, passwords, or credentials hardcoded, logged, or
  committed instead of pulled from config/environment.
- Unsafe deserialization or file handling: loading untrusted input with `eval`,
  `pickle`, `yaml.load` (unsafe mode), or writing to attacker-influenced paths.
- Sensitive data exposure: PII, tokens, or card data in logs, error messages, or
  responses that don't need it.
- Missing input validation on anything crossing a trust boundary (HTTP request,
  webhook payload, file upload, queue message).

Do not flag style, missing tests, or performance here — even if you notice them.

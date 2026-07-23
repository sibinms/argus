# Security Policy

## Supported Versions

Argus is released as a single rolling line — only the latest release
(the `v1` floating tag) is supported. Please upgrade before reporting an
issue that might already be fixed.

## Reporting a Vulnerability

**Don't open a public issue for a security vulnerability.**

Argus runs with access to your repo's `GITHUB_TOKEN` (or a GitHub App
private key) and whichever LLM provider key you configure, so a
vulnerability here could affect real credentials in real CI runs.

Report it privately instead:

- [Open a private security advisory](https://github.com/sibinms/argus/security/advisories/new)
  (GitHub Security tab → "Report a vulnerability"), or
- Contact the maintainer directly through their GitHub profile.

Please include:

- What the vulnerability is and its potential impact (e.g. credential
  exposure, arbitrary code execution, prompt injection leading to a
  malicious review comment)
- Steps to reproduce it
- Which version of Argus you tested against

You should get an initial response within a few days. Please give a
reasonable amount of time to fix the issue before any public disclosure.

## Scope

Particularly interested in reports involving:

- Leaking `GITHUB_TOKEN`, GitHub App private keys, or LLM provider API keys
  (via logs, comment bodies, error messages, or otherwise)
- A malicious PR diff, file content, or comment reply causing Argus to take
  an unintended action (posting to the wrong PR, executing unintended code,
  prompt-injecting the curator into approving something it shouldn't)
- Privilege escalation via the GitHub App installation

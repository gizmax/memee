# Security Policy

## Reporting a vulnerability

Please email **security@memee.eu** (fallback: **info@memee.eu**).

Include:

- A description of the issue and its impact.
- Steps to reproduce (or a proof-of-concept).
- The version/commit you tested against.
- Your preferred contact and whether you want public credit on the fix.

If you prefer encrypted mail, ask and we will send a PGP key.

## What to expect

- Triage acknowledgement within **48 hours**.
- A fix (or a written mitigation plan) for confirmed issues within 14 days.
- Coordinated disclosure: please give us a window to ship a fix before
  publishing the details. We will credit you in the release notes unless
  you ask otherwise.

## Supported versions

Only the latest `1.x` release receives security fixes.

## Out of scope

- No bug bounty at this stage.
- Vulnerabilities in the proprietary `memee-team` package belong to its
  own channel — contact `info@memee.eu`.

## Autoresearch executes shell commands

The `memee research` feature (see `src/memee/engine/research.py`) runs each
experiment's `verify_command` and `guard_command` through `subprocess.run(...,
shell=True)`. This is intentional: the whole point of autoresearch is that an
agent proposes a metric and a verify step, then iterates against it, so the
commands have to be whatever the experiment creator configured — test runners,
linters, coverage tools, custom shell pipelines.

Consequence: **do not run autoresearch experiments from untrusted sources
without reviewing the `verify_command` and `guard_command` fields first.** An
experiment authored by a malicious party can run arbitrary shell on the host
when you invoke `memee research run <id>`. In shared or multi-tenant
environments, either restrict who can create experiments or sandbox the
`memee` process (containers, seccomp, unprivileged user). This is not
considered a vulnerability in Memee — it is the documented behaviour of the
feature — so it is out of scope for the disclosure process above.

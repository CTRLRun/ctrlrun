# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately to **contact@arpanghoshal.com**. Please do not open a public
issue for a security report.

Include what you need to make the problem reproducible: the version, the policy file, and the
sequence of actions. A failing test is the fastest possible report.

Expect an acknowledgement within 72 hours and an assessment within seven days. If a fix is
warranted you will be credited in the release notes unless you ask not to be.

## Supported versions

CTRLRun is pre-1.0. Only the latest release receives fixes.

| Version | Supported |
|---|---|
| 0.1.x | yes |
| < 0.1 | no |

## What counts as a vulnerability

CTRLRun sits in the execution path of consequential actions. Treat anything that breaks one of
these as a security issue, not a bug:

- An action executes that policy should have denied.
- An approval authorizes an action other than the exact one a human saw.
- A consumed, expired, or mismatched approval is accepted.
- The same logical effect is reserved twice, in any interleaving, across threads or processes.
- An unknown execution outcome is recorded as `failed` rather than `ambiguous`.
- A receipt does not reflect what happened.

`docs/THREAT_MODEL.md` states what is deliberately out of scope — a compromised host, a
malicious administrator with write access to the state database, a lying external service, or
code that bypasses the decorator entirely. Those are documented limits rather than
vulnerabilities, but if you think one is stated too generously, say so.

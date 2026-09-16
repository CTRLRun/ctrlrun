# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately to **contact@arpanghoshal.com**. Please do not open a public
issue for a security report.

Include what you need to make the problem reproducible: the version, the policy file, and the
sequence of actions. A failing test is the fastest possible report.

Expect an acknowledgement within 72 hours and an assessment within seven days. If a fix is
warranted you will be credited in the release notes unless you ask not to be.

## Response process

What happens to a report, in order, and the reporter hears from us at each step:

1. **Acknowledgement within 72 hours** of the report arriving, from the maintainer, with a
   tracking reference that stays private.
2. **Assessment within seven days.** We reproduce the problem, place it against the threat
   model at https://ctrlrun.dev/docs/THREAT_MODEL (in scope, out of scope, or a documented
   limitation), and tell the reporter which and why. An out-of-scope report gets a written
   reason, not silence.
3. **A fix on a private branch**, with the regression test that fails before it and passes
   after, reviewed by the other maintainer role holder before it merges. The fix ships in a
   patch release on the supported line.
4. **Disclosure.** The release's CHANGELOG entry names the fix. For a report in scope we
   publish a GitHub Security Advisory on the repository and request a CVE through it. We aim
   to release within 90 days of the report; if a fix takes longer, the reporter is told when
   and why, and the advisory is published no later than the fix.
5. **Credit** in the release notes and the advisory, unless the reporter asks not to be named.

Reports may also be filed through GitHub's private vulnerability reporting on the repository;
the same process applies.

## Provenance

Releases carry PyPI provenance attestations from GitHub Actions. Distributions are published
through trusted publishing, so there is no API token to leak or replay, and each wheel and sdist
carries an attestation naming the workflow that built it. Every GitHub Action the workflows use
is pinned to a commit. `https://ctrlrun.dev/docs/how-this-is-built` says what has and has not been reviewed.

From v0.6.0, every GitHub Release also carries signed SLSA build provenance for the same
distributions — `ctrlrun-<version>.intoto.jsonl` (the DSSE envelopes) and
`ctrlrun-<version>.sigstore.json` (the Sigstore bundle). Signing is against the workflow's OIDC
identity, so there is no signing key in this repository, in its secrets, or in anyone's shell.

### Verifying a release

```sh
gh release download v0.6.1 --repo CTRLRun/ctrlrun
gh attestation verify ctrlrun-0.6.1.tar.gz --repo CTRLRun/ctrlrun
```

`gh attestation verify` fetches the attestation from GitHub. To check without a network round
trip, point it at the bundle the release carries:

```sh
gh attestation verify ctrlrun-0.6.1.tar.gz \
  --repo CTRLRun/ctrlrun --bundle ctrlrun-0.6.1.sigstore.json
```

Either way, what is verified is that these bytes were built by the `release.yml` workflow in
this repository at the tagged commit. It says nothing about whether the code is correct — that
is what the specifications and the test suite are for. Releases before v0.6.0 carry no
provenance and cannot be checked this way.

## Supported versions

ctrlrun is pre-1.0. Only the latest release receives fixes.

| Version | Supported |
|---|---|
| the latest release line (the newest `0.x` on PyPI) | yes |
| earlier lines | no |

The upgrade path is `pip install --upgrade ctrlrun`. Every change to a public name and every
store migration is listed in [CHANGELOG.md](CHANGELOG.md) under the release that made it,
with what to do about it; migrations run forward on first use and are numbered, and a
migration that cannot be reversed says so in its entry.

## What counts as a vulnerability

ctrlrun sits in the execution path of consequential actions. Treat anything that breaks one of
these as a security issue, not a bug:

- An action executes that policy should have denied.
- An approval authorizes an action other than the exact one a human saw.
- A consumed, expired, or mismatched approval is accepted.
- The same logical effect is reserved twice, in any interleaving, across threads or processes.
- An unknown execution outcome is recorded as `failed` rather than `ambiguous`.
- A receipt does not reflect what happened.

`https://ctrlrun.dev/docs/THREAT_MODEL` states what is deliberately out of scope — a compromised host, a
malicious administrator with write access to the state database, a lying external service, or
code that bypasses the decorator entirely. Those are documented limits rather than
vulnerabilities, but if you think one is stated too generously, say so.

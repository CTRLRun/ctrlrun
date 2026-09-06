# How this is built

CTRLRun is built specification-first, every requirement in it is mutation-tested, anything
that touches authorization is reviewed by a session that did not write it, and every sentence
in the README maps to a test. That discipline is the reason to trust the code, and it is also
what makes it safe that AI coding agents write most of it. This page says both, plainly, with
the numbers that are written down and a pointer to where each one is.

## Specification first

Each version starts as a document: `SPEC-v0.1.md` through `SPEC-v0.6.md` under `docs/`, each a
delta over the ones before it and each still binding in full. A spec names its acceptance
tests, freezes its public names, and lists what is out of scope. The tests are written from the
acceptance section before the implementation exists, so the suite is red first and the
implementation's job is to make it green.

A new entry point, table, column, event or error is a specification amendment before it is
code. That rule has a date on it. `Control.delegate` once let an expired credential mint
permanent, re-delegable authority, not because the expiry check was wrong but because the
spec listed it against one method and nothing enumerated the others. `SPEC-v0.3.md` §4.3.1 now
lists every entry point by name, and every one since has added its row first.

## Every requirement is mutation-tested

Before an item is called done, each MUST in its spec sections is removed, the test named for it
is confirmed red, and the guard is restored. The table goes in the pull request. A row that
stays green is not a passing check; it is a check nothing exercises, and the four shapes that
produce one are listed in `CONTRIBUTING.md` so every table is read against them.

The numbers that are recorded:

| Version | What the discipline found | Where it is written |
|---|---|---|
| v0.2 | The mutation tables found roughly thirty-five gaps, almost all of one of four shapes: subsumed guards, orphaned handlers, negative tests against behaviour the library refuses anyway, and windows not actually reproduced | `CONTRIBUTING.md`, "The four shapes of a false green" |
| v0.3 | A self-review of the specification found four defects; an independent review of the same two sections found two authorization holes the author had missed, both visible only from a file the spec did not mention | `CHANGELOG.md`, 0.3.0 |
| v0.5 | An independent review of the adapter contract found five defects that would each have produced an insecure or unimplementable adapter. Three further reviews and a third adapter written against the contract alone found five authorization defects in one reference adapter before it shipped, all of the same shape | `CHANGELOG.md`, 0.5.0; `docs/adapters.md`, the fourteen questions |
| v0.6 | An independent review of the store work found twenty-one defects, four of them blockers, three of which were invisible from the diff and visible only from the shipped code | `CHANGELOG.md`, 0.6.0 |

The suite today is 1,625 test functions, 2,442 cases with parametrisation, run on two Python
versions, against SQLite and Postgres, with the adapter suites run against real installations
of both frameworks and asserted not to have skipped.

## Independent review

Anything touching authorization, identity, delegation, the gateway, an adapter or the store is
reviewed before its pull request opens, by a session that did not write it, reading the
specification and every file that calls into the changed code rather than the diff. That is
where the defects in the table above were found. When a review declines a finding, the
reasoning goes in the document, and a guard that turns out to be attribution rather than
prevention is renamed everywhere it was called a defence.

## Every claim maps to a test

`docs/CLAIMS.md` maps every sentence in the README to the code that implements it and the
test that proves it. A sentence with no row is cut; a row whose test disappears takes its
sentence with it. A test resolves every line number in the table against the line it cites and
fails if the named symbol is not there. It found nine stale references the first time it ran,
after the table had been maintained by hand for three releases, which is the argument for the
test.

`ctrlrun verify` applies the same standard to your configuration: a guarantee it cannot
exercise is reported not applicable, with the reason, and never counted as a pass.

## What is not done yet

There has been no external security audit and no third-party review of the kernel. Every
review so far was run inside this project, by sessions that did not write the code under
review but that follow the same specifications and the same rules. An external audit is on the
roadmap for v0.8 to v0.9. Until it happens, the evidence for the guarantees is the suite, the
mutation tables, the review records in the changelog, and `ctrlrun verify` against your own
configuration. Read `docs/THREAT_MODEL.md` for what the guarantees do not cover.

## AI coding agents, and the constraints that make that safe

AI coding agents write most of the code, the tests and the documentation in this repository.
That is stated here once, beside the discipline above, because a reader who discovers it alone
trusts less and a reader who is told trusts more, and because the discipline is what makes it
safe:

- The specification comes first, written and read by a person, and the tests are written from
  it before any implementation.
- Every MUST is mutation-tested, and the table is read against the four shapes of a false green
  before it is believed.
- Anything touching authorization is reviewed by a separate session that did not write it, and
  the findings are recorded in the changelog with what was done about each.
- Every default fails closed. There is no flag that makes a consequential action permissive,
  in the kernel, in verify or in an adapter.
- A person tags and publishes every release, reads every measurement before it is published,
  and merges nothing under `src/` on green CI alone.

The tooling is a detail of the process. The verification is the story.

## Provenance

Releases are published to PyPI through trusted publishing from GitHub Actions: there is no API
token anywhere, and each distribution carries a PyPI provenance attestation from the workflow
that built it. The workflows pin every action to a commit, the OpenSSF Scorecard workflow
publishes a third party's reading of the repository's practices, and `release.yml` attaches the
same distributions to a GitHub Release with the tag's changelog entry.

## Next

- [`docs/THREAT_MODEL.md`](THREAT_MODEL.md): what the guarantees do not cover.
- [`docs/verify.md`](verify.md): running the guarantees against your own configuration.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md): the rules, in the form a contributor follows them.

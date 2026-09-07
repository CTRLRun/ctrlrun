# Contributing

CTRLRun sits in the execution path of actions that move money, delete infrastructure and
grant permissions. The rules below exist so that a change to it is evidence rather than
intention. They are short to state and long to live with.

## Where to start

Not every contribution carries every rule below. In rough order of what they ask of you:

- **[`good first issue`](https://github.com/CTRLRun/ctrlrun/labels/good%20first%20issue)** is
  the current list, and each issue says what *done* means for it rather than leaving you to
  infer it from this file.
- **Two of them want a comment, not a patch.** Running `ctrlrun scan` against a real
  application tree and reporting what it got wrong is a measurement this project does not have
  and cannot take for itself; so is telling us where the policy template for your sector is
  wrong. Neither needs the suite installed.
- **Documentation** — a cookbook recipe, a `ctrlrun verify` snippet for a CI that is not GitHub
  Actions — is held to `docs/STYLE.md` and the audit below, and not to the mutation table.
- **Anything under `src/`** is where the rest of this file applies in full, and a maintainer
  reads it whatever CI says.

Claim an issue in a comment before you start, so two people do not write the same page. An
issue not on the list is welcome; say what you intend to change before you change it, because
the specification comes first and a pull request that arrives without one has the harder half
still ahead of it.

A security problem is not an issue. Report it privately, per [SECURITY.md](SECURITY.md).

## Run the suite

```bash
git clone https://github.com/CTRLRun/ctrlrun && cd ctrlrun
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,gateway,otel,identity,postgres]"
PYTHON=.venv/bin/python scripts/check.sh
```

`scripts/check.sh` is exactly what CI runs: `ruff format --check`, `ruff check`,
`mypy --strict src`, `pytest`. The Postgres tests skip unless `CTRLRUN_TEST_POSTGRES` names a
server, and CI supplies one, so a skip on your machine is a configuration and a skip in CI is a
failure. Two adapter test modules need their frameworks installed; CI's `adapters` job does
that and asserts nothing skipped.

Use the project's own interpreter for every check. A bare `python` from elsewhere gives two
spurious failures, one of which is a false green.

## Specification first

Every version is a specification before it is code: `docs/SPEC-v0.1.md` through
`docs/SPEC-v0.6.md`, each a delta over the ones before, each still binding in full. Tests are
derived from each spec's acceptance section, public names are frozen in its names section, and
anything not in the document is out of scope for that version.

So a change starts with the section it implements. A new public name, a new entry point, a new
table or column, a new event or error is a spec amendment first, in the same pull request. The
entry-point rule has a reason with a date on it: `Control.delegate` once let an expired
credential mint permanent authority, not because the expiry check was wrong but because the
spec listed the check against one method and nothing enumerated the others. `docs/SPEC-v0.3.md`
§4.3.1 now lists every entry point by name, and a new one adds its row before its code.

## Tests first

Write the acceptance tests before the implementation. A red suite is the specification; make
it green. Then:

- **Every MUST is mutation-tested.** Remove the check, confirm the named test fails, restore
  it, and put the table in the pull request. A row that stays green is a guard nothing
  exercises, and the four shapes that produce one are listed below.
- **Behind every expected refusal, an `else` that fails.** An example or fixture asserting a
  refusal raises on the path where the refusal did not happen. Documentation that quietly
  starts succeeding is worse than none.
- **Test doubles refuse everything the real thing refuses.** A double that can grant, reserve
  or commit where the real store would not invalidates every test that uses it. There are no
  mocks for SQLite; tests use real temporary databases, and every store test runs against
  every backend from one file.
- **Bound every wait.** A polling test bounds its fake clock or iteration count, so a broken
  check fails red instead of hanging CI.
- **Turn a claim about the environment into a test of it.** "Runs with no network" is a claim
  until a subprocess whose `sitecustomize` refuses every socket proves it.

### The four shapes of a false green

The v0.2 mutation tables found roughly thirty-five gaps, and almost all were one of these.
Check every row against the list before reporting it.

1. **Subsumed guards.** A guard that can only fire when a later guard would also fire, with
   the same observable result, is documentation. Collapse the branches or have the tests
   assert which message they got.
2. **Orphaned handlers.** An earlier check can leave a later handler unreachable while every
   behavioural test stays green.
3. **Negative tests against behaviour the library refuses anyway.** Check that the environment
   does not already prevent the thing you forbid, and that the observer could see it if it
   happened.
4. **Windows not actually reproduced.** A test for a rare interleaving has to open that
   interleaving: a held transaction, an injected failure. "Something similar happened" is not
   the same thing.

And three ways the harness lies: stale bytecode (run mutations with
`PYTHONDONTWRITEBYTECODE=1` and clear `__pycache__`), ambiguous anchor strings (anchor on
enough lines to be unique, and treat a patch that did not apply as a failure), and equivalent
mutants (say so in the table instead of claiming to have closed one).

## Every claim maps to a test

`docs/docs/CLAIMS.md` maps every sentence in the README to the code that implements it and the
test that proves it. A sentence with no row is cut. A row whose test disappears takes its
sentence with it in the same commit. A test resolves every `file.py:NNN` in the table against
the line it cites and fails if the named symbol is not on it.

The same standard applies to *not applicable*: `ctrlrun verify` reports a guarantee a
configuration cannot exercise as `N/A` with the reason, never as a pass, and there is no flag
that folds one into the count.

## Independent review

Anything touching authorization, identity, delegation, the gateway, an adapter or the store
gets a review by somebody who did not write it, before the pull request opens, reading the
specification and every file that calls into the changed code rather than the diff alone. That
is where this project's authorization defects have been found: two holes in v0.3's spec that a
self-review missed, five defects in v0.5's contract, five in one adapter, twenty-one in v0.6's
store work. When a review declines a finding, the reasoning goes in the document, and a guard
downgraded from prevention to attribution is renamed everywhere it was called a defence.

## How documentation pull requests are checked

The words are held to the same standard as the code, by `tools/docs_audit/`:

- every fenced block marked `runnable` is executed offline, with a socket guard, and must
  exit 0; a sample either runs or is not marked;
- a forbidden-words lint refuses compliance and standards claims, sector products, and social
  proof that does not exist, with an allowlist that carries a reason per entry;
- internal links and anchors must resolve;
- the capability tables in the README and the docs are rendered from
  `docs/capabilities.yaml`, and a hand edit to a rendered copy fails CI.

`docs/STYLE.md` has the writing rules. Run the four checks from its last section before
opening the pull request.

## Pull requests

- One build-list item per branch and per pull request. `main` is never pushed directly;
  branch protection requires the `check`, `package` and review gates.
- Green CI is necessary and not sufficient for anything under `src/`: a maintainer reads it.
  A pull request touching only tooling, docs or CI merges on green.
- Merge stacked pull requests bottom-up, and never delete a branch another open pull request
  targets.
- Commit messages say what changed and why, in prose. No attribution trailers.

## Releases

A release is a tag. `publish.yml` builds the distributions, re-runs the suite from inside the
sdist, installs the wheel into a fresh environment and runs the quick start there, and then
publishes to PyPI through trusted publishing: there is no API token anywhere, and each
distribution carries a PyPI provenance attestation from the workflow that built it.
`release.yml` creates the GitHub Release with the tag's CHANGELOG entry and the same
distributions attached. Release verification runs from a fresh clone of the tagged commit,
never from a working tree: a green build from the working tree is not evidence that a file is
tracked.

Adapters ship on their own version line (`adapters-langgraph-1.0`), from `adapters/`, and gate
no kernel release.

## Reporting a vulnerability

Privately, per [SECURITY.md](SECURITY.md). If an action ran that policy should have refused,
an approval matched an action other than the one a human saw, an effect happened twice, or an
unknown outcome was recorded as `failed`, that is a security report and not a bug.

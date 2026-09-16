# Contributing

ctrlrun sits in the execution path of actions that move money, delete infrastructure and
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
  Actions — is held to `CTRLRun/ctrlrun-docs`'s `STYLE.md` and the audit below, and not to the mutation table.
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

`CTRLRUN_COVERAGE=1 scripts/check.sh` measures coverage as well and writes `coverage.json`.
CI does that on Python 3.12 and holds two floors with `scripts/coverage_floor.py`: **90% of
statements and 80% of branches**, subprocess workers included. A change that drops either is
red on its own pull request.

CI does not install the extras the way the block above does. It installs from
`requirements/*.txt`, hash-pinned locks that `scripts/lock.sh` writes with `uv pip compile`,
and then the checkout with `--no-deps`; the floors in `pyproject.toml` are unchanged by that.
When a dependency or an extra changes, run `scripts/lock.sh` and commit what it rewrote, or CI
installs the old resolution against the new declaration.

## Coding standards

Python, 3.11 and later, in the style `ruff` enforces from `pyproject.toml`:

- **Formatting** is `ruff format` (Black-compatible, line length 100). There is no
  discretion here: a file either matches the formatter's output or CI is red.
- **Linting** is `ruff check` with the rule sets `E`, `F`, `W` (pycodestyle and pyflakes),
  `I` (import order), `N` (PEP 8 naming), `UP` (no deprecated syntax or APIs), `B` (bugbear's
  likely defects), `ANN` (every signature annotated), `SIM` and `RUF`.
- **Types** are checked by `mypy --strict` over `src/`, with `warn_unreachable` on. Untyped
  code does not merge.
- **Exceptions are rare and carry their reason.** A `# noqa: <rule>` names the rule and says
  why on the same line; a per-file ignore lives in `pyproject.toml` with a comment above it.
  A reviewer may ask for either to be removed.

`scripts/check.sh` runs all three before the tests, cheapest first, and CI runs that same
script, so a style failure is found on your machine in a second rather than in CI in ten
minutes.

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
it green.
**The policy, stated once so that it can be pointed at:** new functionality MUST arrive with
tests for it, in the same pull request, in the automated suite that CI runs. A pull request
that adds behaviour without tests for that behaviour does not merge, whatever else it does
well. A bug fix MUST arrive with the regression test that failed before the fix.

Then:

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

`CLAIMS.md`, in [CTRLRun/ctrlrun-docs](https://github.com/CTRLRun/ctrlrun-docs), maps every sentence in
the README to the code that implements it and the test that proves it. A sentence with no row is cut. A row whose test disappears takes its
sentence with it in the same commit. A test resolves every `file.py:NNN` in the table against
the line it cites and fails if the named symbol is not on it.

The same standard applies to *not applicable*: `ctrlrun verify` reports a guarantee a
configuration cannot exercise as `N/A` with the reason, never as a pass, and there is no flag
that folds one into the count.

## Code review

Every pull request is reviewed on GitHub by a person other than its author before it merges,
and nobody merges their own. That is the whole of the rule for who; this section is what the
review checks and what makes a change acceptable.

**How it is conducted.** The reviewer reads the pull request against the specification
section it claims to implement, not against the diff alone, and writes findings as review
comments on the lines they concern. A finding is answered in the pull request, by a change or
by a written reason; a declined finding keeps its reasoning in the thread. Automated review
comments (CodeRabbit, CodeQL) are read and answered the same way, and none of them counts as
the human review.

**What must be checked.**

- The specification section exists and the change does what it says, no more.
- The tests came first and the mutation table is in the description for every MUST the change
  touches (*Tests first* above, including the four shapes of a false green).
- Every public name is frozen in the spec's names section, every entry point is enumerated,
  and `CLAIMS.md` in `CTRLRun/ctrlrun-docs` still holds for every sentence the change affects.
- The change does not weaken a fail-closed rule, widen a default, add an unpinned install, a
  write permission to a workflow, a binary, or a dependency that is not in the locks.
- The commits are signed off and the message says what changed and why.

**What is required to be acceptable.** One approving review from a person who did not write
the change; the required checks green (`check` on every Python version, `package`, `gate`);
every finding answered; and, for anything under `src/`, the maintainer's read. The independent
review below is in addition to this for the parts of the kernel where a defect is a security
defect.

## Independent review

Anything touching authorization, identity, delegation, the gateway, an adapter or the store
gets a review by somebody who did not write it, before the pull request opens, reading the
specification and every file that calls into the changed code rather than the diff alone. That
is where this project's authorization defects have been found: two holes in v0.3's spec that a
self-review missed, five defects in v0.5's contract, five in one adapter, twenty-one in v0.6's
store work. When a review declines a finding, the reasoning goes in the document, and a guard
downgraded from prevention to attribution is renamed everywhere it was called a defence.

## How documentation pull requests are checked

The documentation is [CTRLRun/ctrlrun-docs](https://github.com/CTRLRun/ctrlrun-docs), and the words
there are held to the same standard as the code here, by that repository's `tools/docs_audit/`.
The checks read **both** trees -- a page that says the CLI prints X is only true if the CLI
prints X -- so they run in two places: on a pull request there, and from the `docs` job of
this repository's CI against the commit you are proposing. A change to the code that makes a
page wrong is red here.

Which checkout of the documentation that job reads is decided by name: a `CTRLRun/ctrlrun-docs`
branch called the same as yours when there is one, `main` otherwise. So a change that alters a
docstring or a `--help` text comes with a documentation branch of the same name, regenerated
against it (its `README.md` lists the renderers), and the two merge together, the code first.
`main` is checked against `main`, and the pages there are expected to match it at all times.

What they check:

- every fenced block marked `runnable` is executed offline, with a socket guard, and must
  exit 0; a sample either runs or is not marked;
- a forbidden-words lint refuses compliance and standards claims, sector products, and social
  proof that does not exist, with an allowlist that carries a reason per entry;
- internal links and anchors must resolve;
- the capability tables in the README and the docs are rendered from
  `CTRLRun/ctrlrun-docs`'s `capabilities.yaml`, and a hand edit to a rendered copy fails CI.

`CTRLRun/ctrlrun-docs`'s `STYLE.md` has the writing rules. Run the four checks from its last section before
opening the pull request.

## Pull requests

- One build-list item per branch and per pull request. `main` is never pushed directly;
  branch protection requires the `check`, `package` and review gates.
- Green CI is necessary and not sufficient for anything under `src/`: a maintainer reads it.
  A pull request touching only tooling, docs or CI merges on green.
- Merge stacked pull requests bottom-up, and never delete a branch another open pull request
  targets.
- Commit messages say what changed and why, in prose, and end with the `Signed-off-by`
  trailer described below. No attribution trailers.

## Sign your work

Every commit carries a `Signed-off-by` trailer, and the trailer certifies the
[Developer Certificate of Origin](DCO): that you wrote the change or have the right to submit
it under the licence in [LICENSE](LICENSE), and that you know the contribution and its record
are public. That is the whole agreement. There is no contributor licence agreement and no
copyright assignment: your copyright stays yours, and what you contribute goes out under
Apache-2.0 like the rest of the code.

`git commit -s` adds the trailer from your git identity:

    Signed-off-by: Your Name <you@example.com>

The email has to be the commit's author email. CI's `dco` job reads every commit on a pull
request and names any that lack the trailer; `git rebase --signoff main` adds it to a whole
branch at once. Only git's own trailer block counts, and nobody is exempt, bots included:
dependabot signs its commits off already.

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

**A release can be rebuilt by anyone and compared byte for byte.** The workflows set
`SOURCE_DATE_EPOCH` to the tagged commit's timestamp and normalise the sdist with
`scripts/normalize_sdist.py`, so from a clean clone of the tag, with the interpreter series
CI uses (3.11):

```bash
pip install --require-hashes -r requirements/build.txt
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
python -m build --no-isolation
python scripts/normalize_sdist.py dist/*.tar.gz
sha256sum dist/*
```

The hashes match the distributions on PyPI and on the GitHub Release, for every release cut
after this was added. CI's `package` job builds every pull request twice and fails if the two
differ, so the property is checked before a tag rather than claimed after one.

## Reporting a vulnerability

Privately, per [SECURITY.md](SECURITY.md). If an action ran that policy should have refused,
an approval matched an action other than the one a human saw, an effect happened twice, or an
unknown outcome was recorded as `failed`, that is a security report and not a bug.

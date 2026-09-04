# `ctrlrun verify`

Everything CTRLRun guarantees is proven by this repository's tests against this repository's
configurations. That is the right place to start and the wrong place to stop, because the thing
you deploy is *your* policy, *your* grants and *your* store — and a guarantee that has never
been exercised against those is a guarantee nobody has checked.

`ctrlrun verify` runs the kernel's own failure scenarios against the configuration in front of
it and reports what passed, what failed, and — the part that makes the number mean anything —
what could not be tested at all.

```console
$ ctrlrun verify
CTRLRun verify — ctrlrun 0.4.0, catalogue ctrlrun.guarantees/v1
policy     examples/authority/payments.yaml (ctrlrun.policy/v3, mode: enforce)
authority  same document, 3 grants
store      sqlite, scratch (created and destroyed for this run)

G1   mutated approval refused         PASS  stripe.refund
G2   replayed approval refused        PASS  stripe.refund
G3   duplicate effect refused         PASS  stripe.refund
G4   one winner under concurrency     PASS  stripe.refund (8 processes)
G5   ambiguous blocks a blind retry   PASS  stripe.refund
G6   unknown action refused           PASS
G7   no principal refused             PASS  stripe.refund
G8   expired authority refused        PASS  head-of-support
G9   delegation cannot escalate       PASS  head-of-support (6 of 6 dimensions)
G10  unknown exception is ambiguous   PASS  stripe.refund

10/10 declared guarantees pass. 0 not applicable.
```

It reads the policy document — `$CTRLRUN_CONFIG`, else `./ctrlrun.yaml` — and the authority
document beside it. It executes nothing real: every executor is an in-process fake, no scenario
opens a socket, and it writes nothing outside a temporary directory.

---

## What the badge means

> The badge means the **declared guarantees pass**: every guarantee in the catalogue that this
> configuration can exercise was exercised, and none of them failed.

That is the whole claim. It is not a statement that your system is secure, that your policy is
a good policy, or that CTRLRun has audited anything. A configuration that permits everything
and constrains nobody can pass all ten guarantees, because the guarantees are about **the
kernel doing what it says under that configuration** — not about whether the configuration is
wise.

### What it does not mean

Verify sees **the configuration, not the code**. It does not check:

- **Your executors.** The function behind `@protect` is never called. An executor that raises
  `NotExecuted` when the remote *did* act — `THREAT_MODEL.md` calls this an integration bug,
  and it is the most dangerous one available — is invisible here, because verify supplies its
  own executors and never imports your module.
- **Your `reconcile` hooks**, for the same reason: a hook is a Python callable passed to
  `@protect`, and it does not appear in any file verify reads.
- **Where you put the decorator.** Code that calls the raw function bypasses CTRLRun entirely,
  and no amount of configuration-reading finds that.
- **Your deployment.** Whether the proxy in front of `HeaderIdentityProvider` overwrites the
  header, whether `$CTRLRUN_STATE` points where you think, whether two gateways share a state
  file — none of it is in the document.
- **Whether your policy is the *right* policy.** Verify has no opinion on whether
  `stripe.refund` should be autonomous to €500 or to €5. It is not a linter, it does not score,
  and it will never tell you a configuration is too permissive. That judgment belongs to the
  person who wrote it, and a tool that pretended otherwise would be handing out an
  authoritative-looking opinion it has no basis for.

The words **secure**, **safe**, **compliant**, **certified** and **audited** do not appear as
claims about CTRLRun or about your system on the badge, in its JSON, in the job summary, or on
this page.

---

## Not applicable is not a pass

A configuration with no `approve` rule cannot exercise the approval-binding guarantees. Verify
reports them `N/A` with the reason that made them inapplicable, **excludes them from the
denominator**, and shows them separately:

```
G3   duplicate effect refused         N/A   no action declares an `effect:` template
                                            (in a `ctrlrun.policy/v1` document the template
                                            lives in the @protect decorator, which verify does
                                            not read)
G4   one winner under concurrency     N/A   no action declares an `effect:` template
G5   ambiguous blocks a blind retry   N/A   no action declares an `effect:` template
G8   expired authority refused        N/A   no authority section
G9   delegation cannot escalate       N/A   no authority section

5/5 declared guarantees pass. 5 not applicable: G3, G4, G5, G8, G9.
```

That run is `5/5`, never `10/10`. There is no flag that folds an N/A into the count, and there
will not be one: a number that counts guarantees nobody exercised is a number that means
nothing.

An N/A is always a statement about your **document**, derived from it. A scenario verify could
not build for any other reason is an internal error and exits 3 — never an N/A, and never a
failure attributed to your kernel.

---

## The guarantees

Ten, in `ctrlrun.guarantees/v1`. Every one is the deployed form of an acceptance test that
already exists and passes in this repository; verify adds no guarantee of its own and weakens
none.

| id | invariant | N/A when |
|---|---|---|
| **G1** | An approval is bound to one `action_hash`; presenting it for any other action is refused, and the approval is not consumed. | No action reaches `approve` under any satisfiable argument vector. |
| **G2** | An approval is single-use; the second presentation is refused and does not execute. | As G1. |
| **G3** | A second attempt on an effect key whose record is `COMMITTED` is refused, and the remote is not called. | No action declares an `effect:` template. |
| **G4** | Reservation is atomic **across processes**, not merely across threads. | As G3, or the store backend cannot span processes. |
| **G5** | An executor that raises anything other than `NotExecuted` leaves the effect `AMBIGUOUS`, and the retry is refused rather than executed. | As G3. |
| **G6** | Unknown action → DENY. There is no default-allow. | `actions:` is empty. |
| **G7** | No principal, no action: an action proposed outside `context()` with no identity provider is refused, and no receipt and no events are written. | No action in the policy can run at all. |
| **G8** | A grant is authority only until its `expires_at`; after that the action it covered is denied, by name. | No `authority:` section, no grant with an `expires_at`, or no grant matching any action the policy lists. |
| **G9** | A delegated grant is valid only if it is provably a subset of its parent on every dimension — and a child that **drops** a dimension its parent constrains is rejected rather than treated as unconstrained. | No `authority:` section, or no grant is delegable. |
| **G10** | `NotExecuted` is the only outcome that means "the remote did nothing". Everything else, timeouts included, is `AMBIGUOUS`. | Every action in the policy is denied. |

G9 reports **which dimensions it exercised**. A parent that constrains one dimension does not
score as though it had covered six, because that would be the N/A rule violated one level down.

Two of these are worth a sentence on how they are exercised, because the answer is not the
obvious one; `SPEC-v0.4.md` §12 argues both at length.

**G6 asserts the behaviour, not one reason string.** An action your policy does not list never
executes — but *which* check refuses it depends on your configuration. With an `authority:`
section, no grant covers it and authority refuses first, before policy is reached; without one,
policy refuses it as `unknown_action`. Both are the guarantee holding, so the report names the
reason that fired in `detail.refused_by` and the set it was checked against in
`detail.reachable_reasons`. A reason your configuration cannot produce is a failure.

**G7 is `N/A` for a policy in which nothing can run at all**, because its control is "the same
call inside `context()` runs" and there is no such call. It is applicable to every configuration
in which anything can run, with or without grants.

### Every guarantee carries a positive control

A guarantee is a refusal, and "the second attempt was refused" is satisfied just as well by a
scenario in which **nothing ever ran**. Such a scenario would report PASS, every time, against
a kernel with the guard deleted.

So every scenario runs a companion that establishes the observable would have been visible had
the guard not fired: the unmutated action commits, the first attempt reaches `COMMITTED`, eight
processes on eight distinct keys all commit, an executor raising `NotExecuted` **is** retried
and does execute. If the control does not behave as specified, the guarantee is reported
`FAIL` with `reason: "control failed"`. It is never a pass, and it is never an N/A: an N/A is a
statement about the configuration, and a failed control is a statement about the run.

---

## Verify never touches your store

Every scenario runs against a scratch store of the same backend type, created for the run and
destroyed with it. Your `.ctrlrun/state.db` is byte-identical before and after, and it is never
created where it did not exist. Verify does not call `state_path()`, does not read
`$CTRLRUN_STATE`, and does not use `Control.from_file()`.

It writes no evidence files either. Evidence of a scenario that never happened, filed beside
evidence of actions that did, is a receipt trail nobody can read.

---

## Options

```
ctrlrun verify [--authority PATH] [--json] [--junit PATH] [--only G1,G3] [--store-url URL]
```

| | |
|---|---|
| `--authority PATH` | A standalone authority document, the one `ctrlrun gateway --authority` already accepts. Declaring authority in two places is refused, naming both. |
| `--json` | One `ctrlrun.verify/v1` document on stdout, carrying the SHA-256 of both documents verify read, and on each failure a counterexample: the ordered events, receipts and effect records that show the violation. |
| `--junit PATH` | A JUnit XML file for CI. An N/A is `<skipped>` and never a pass. |
| `--only G1,G3` | Runs exactly those. Everything else is `skipped`, the report carries `"partial": true`, and **no badge is written** — a fraction computed over a subset somebody chose is a false green in a different costume. |
| `--store-url URL` | Reserved. v0.4 accepts the SQLite backend; anything else exits 2 naming v0.6. |

There is **no flag that relaxes a check**. No argument and no environment variable makes
verify's `Control` behave differently from the one your deployment runs. The moment one exists,
the thing being verified is not the thing that ships.

### Exit codes

| | |
|---|---|
| **0** | Every applicable guarantee passed, and at least one was applicable. |
| **1** | At least one guarantee FAILED. |
| **2** | The configuration was refused or is unusable — a missing or malformed policy, authority declared twice, `mode: observe`, an unknown `--only` id, an unsupported `--store-url`, or **zero applicable guarantees**. |
| **3** | An internal error in verify itself. Never reported as a FAIL, and never as an N/A. |

`mode: observe` is refused rather than run. Observe mode enforces nothing, so every refusal
these guarantees assert would be recorded rather than made; running the scenarios and reporting
ten failures would be true and useless, and running them in a synthetic enforce mode would
report guarantees about a configuration nobody deployed.

---

## In CI

```yaml
name: CTRLRun verify

on: [push, pull_request]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: CTRLRun/ctrlrun@main
        with:
          policy: ctrlrun.yaml
```

The action installs `ctrlrun`, runs `ctrlrun verify --json --junit`, renders the job summary
and the badge JSON **from that report** — not from a second run, so they cannot disagree — and
uploads the three files as one artifact.

It fails the job when a guarantee failed and when the configuration was refused, and succeeds
when guarantees are N/A. N/A is not a failure and it is not a pass; the job's green means
"nothing that could be checked was wrong", which is exactly what the badge says.

**There is no input that makes a failure not fail the job.** A workflow that wants to tolerate
one puts `continue-on-error` on the step, where it is visible in the workflow rather than
hidden in an action's defaults.

### Inputs

| | |
|---|---|
| `policy` | Path to the policy document. Default `ctrlrun.yaml`. |
| `authority` | Path to a standalone authority document. Default: the policy document carries it. |
| `only` | Comma-separated guarantee ids. A partial run writes no badge. |
| `python-version` | Default `3.11`. |
| `install` | The pip requirement to install. Default `ctrlrun`; set it to `.` to verify with the checkout. |
| `badge-path` | Where to write the Shields endpoint JSON. Default `verify-badge.json`. |

Outputs: `passed`, `failed`, `applicable`, `not-applicable`, `badge-message`, `report-path`.

### Publishing the badge

The action **writes** the endpoint JSON and never publishes it. Publishing it needs
`contents: write`, and asking for write access to your repository as the price of a
verification badge is a bad trade for a tool whose subject is least privilege. So the cost is
here, visible, once — and it is your decision.

This repository publishes its own badge, and this is the job it uses. Three things about it are
load-bearing:

- **`contents: write` is job-level.** The workflow itself is `contents: read`, so nothing else
  in it can write to the repository. A workflow-level grant would hand every job write access
  to buy one file.
- **It runs on a push to `main` and nothing else.** A pull request never reaches it. A PR from
  a fork gets a read-only token anyway, but relying on that is relying on a default rather than
  refusing.
- **It publishes the badge the verify job already produced**, downloaded as an artifact rather
  than regenerated — so the badge, the job summary and the uploaded report all come from one
  verify run and cannot disagree.

```yaml
permissions:
  contents: read          # every job, unless it says otherwise

jobs:
  badge:
    needs: verify
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: write     # the one exception, and only here
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: ctrlrun-verify
          path: badge
      - name: Publish to the badges branch
        run: |
          set -eu
          test -s badge/verify-badge.json
          message=$(python -c 'import json;print(json.load(open("badge/verify-badge.json"))["message"])')
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git fetch origin badges 2>/dev/null && git switch badges || git switch --orphan badges
          git rm -rq --cached . 2>/dev/null || true
          cp badge/verify-badge.json verify-badge.json
          git add -f verify-badge.json
          git commit -m "verify: $message" || { echo "badge unchanged"; exit 0; }
          git push origin badges
```

Then the badge is:

```markdown
[![CTRLRun](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/OWNER/REPO/badges/verify-badge.json)](docs/verify.md#what-the-badge-means)
```

It renders as **CTRLRun verified N/M**, where `N` is passes and `M` is **applicable**
guarantees — never the catalogue size. It is `brightgreen` when nothing failed and `red`
otherwise; there is no amber for N/A, because the badge's colour is about failures and the N/A
count lives in the report the badge links to.

A partial run (`--only`) and a run that exited 2 or 3 write **no badge at all**.

---

## Related

- [`SPEC-v0.4.md`](SPEC-v0.4.md) — the contract this implements, guarantee by guarantee.
- [`OWASP-AGENTIC-TOP10.md`](OWASP-AGENTIC-TOP10.md) — a reading of somebody else's taxonomy
  against these guarantees, with the entries CTRLRun does not address listed by name.
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — what fail-closed means here, and what is out of scope.

# CTRLRun v0.4 Specification

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md), [`SPEC-v0.2.md`](SPEC-v0.2.md) and
[`SPEC-v0.3.md`](SPEC-v0.3.md)**. Everything in all three still holds; this document states
only what v0.4 adds or changes. A reference to an earlier contract is written `v0.1 §5.4`,
`v0.2 §6.5` or `v0.3 §4.3.1`; a bare `§5` is a section of this document. Section numbers
exist in all four, so the prefix is not decoration — an unprefixed reference to an earlier
spec is a defect.

Tests are derived from §8. Public names added here are frozen in §9. Anything not in this
document or in v0.1/v0.2/v0.3 is out of scope for v0.4.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

v0.4 answers a question the first three releases could not: **does it hold in *my* setup?**
Everything CTRLRun guarantees is proven today by this repository's own tests against this
repository's own configurations. That is the right place to start and the wrong place to
stop, because the thing an operator deploys is *their* policy, *their* grants and *their*
store — and a guarantee that has never been exercised against those is a guarantee nobody
has checked. `ctrlrun verify` runs the failure scenarios of `v0.1 §7`, `v0.2 §10` and
`v0.3 §10` against the configuration in front of it and reports what passed, what failed,
and — the part that makes the number mean anything — **what could not be tested at all**.

Three rules govern everything below.

**Not applicable is not a pass.** A configuration with no `approve` rule cannot exercise the
approval-binding guarantees. Verify reports them `N/A` with the reason that made them
inapplicable, excludes them from the denominator, and shows them separately. A configuration
with three applicable guarantees is reported as `3/3 (5 not applicable)` and never as `8/8`.
There is no flag that folds an N/A into the count.

**Verify never touches the operator's store.** Every scenario runs against a scratch store of
the same backend type, created for the run and destroyed with it. A verify that reserved a
real effect key would be a defect of exactly the class the demo colliding with live work
would be. The operator's `.ctrlrun/state.db` MUST be byte-identical before and after (T103).

**The badge means "declared guarantees pass".** That phrase, on the badge's link target, and
no other. Not "secure", not "safe", not "compliant", not "audited". A configuration that
permits everything and constrains nobody can pass all ten guarantees, because the guarantees
are about the *kernel doing what it says* under that configuration — not about whether the
configuration is wise. §5 fixes the badge's text; §6 refuses the vocabulary.

---

## 1. Scope

v0.4 delivers seven things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | `ctrlrun.verify`: the guarantee registry, the scenario engine, G1–G6 and G10 | core | §2, §3 |
| 2 | The authority guarantees G7–G9 and config-derived selection | core | §2, §3.4 |
| 3 | Reporting: human, `--json`, `--junit`, exit codes | core | §4 |
| 4 | The GitHub Action, the badge, `docs/verify.md` | — | §5 |
| 5 | `docs/OWASP-AGENTIC-TOP10.md` | — | §6 |
| 6 | `research/framework-probe/` | not packaged | §7 |
| 7 | Release 0.4.0 | — | — |

The dependency rule of `v0.2 §1.1` and `v0.3 §1` is unchanged and binding: `pip install
ctrlrun` MUST continue to install nothing but `pyyaml` and `click`. **The whole of `verify`
is core**, and this is not an accident of packaging — a verification tool that needed an
extra installed to run would be a verification tool that half the deployments never run. It
is stdlib plus the YAML parser that is already there: the scenarios drive `Control` against a
temporary SQLite file, and the report is `json`, `xml.etree` and text. `import ctrlrun` MUST
NOT import `jwt`, `httpx` or any `opentelemetry` module (T30, `v0.3 §10` T92), **and MUST NOT
import `ctrlrun.verify`** (T125b): verify is an operator's tool, not part of the action path,
and nothing in the execution path may come to depend on it.

### 1.1 What verify is, in one paragraph

`ctrlrun verify` reads the operator's policy document and, where there is one, the authority
document beside it. From those it derives concrete actions, principals and delegations that
the configuration actually admits, runs the kernel's own failure scenarios against them in a
scratch store with in-process fake executors, and asserts the refusals the specification
requires. It executes nothing real: every executor is a fake this process created, and no
scenario opens a socket (§3.7). It writes nothing outside a temporary directory. It exits 0
when every applicable guarantee passed.

### 1.2 What verify does not verify, stated before anything else

The list matters more than the feature does. Verify sees **the configuration, not the code**.

- **Not the operator's executors.** The function behind `@protect` is never called. An
  executor that raises `NotExecuted` when the remote *did* act — `THREAT_MODEL.md` calls this
  an integration bug, and it is the most dangerous one available — is invisible here, because
  verify supplies its own executors and never imports the operator's module. The threat
  model's sentence *"v0.4 `verify` will include a check for it where reconciliation exists"*
  was written before this document and is **wrong**; it is amended in item 7 (§9.4).
- **Not the operator's `reconcile` hooks**, for the same reason: a hook is a Python callable
  passed to `@protect`, and it does not appear in any file verify reads.
- **Not where the decorator was placed.** Code that calls the raw function bypasses CTRLRun
  entirely (`THREAT_MODEL.md`, "Out of scope"), and no amount of configuration-reading finds
  that.
- **Not the deployment.** Whether the proxy in front of `HeaderIdentityProvider` overwrites
  the header, whether `$CTRLRUN_STATE` points where the operator thinks, whether two gateways
  share a state file — none of it is in the document.
- **Not whether the policy is the *right* policy.** Verify has no opinion on whether
  `stripe.refund` should be autonomous to €500 or to €5. It is not a linter, it does not
  score, and it never says a configuration is too permissive. That judgment belongs to the
  person who wrote it, and a tool that pretended otherwise would be handing out an
  authoritative-looking opinion it has no basis for.

`docs/verify.md` (item 4) carries this list on the same page as the badge, not three clicks
away.

### 1.3 The fourth rule: every guarantee carries a positive control

The three rules above are about honesty toward the operator. This one is about honesty toward
verify itself. It exists because of the rule every negative test in this repository is written
against: *a negative test proves nothing unless the thing it forbids would otherwise happen.*

A guarantee is a refusal. "The second attempt was refused" is satisfied just as well by a
scenario in which **nothing ever ran** — an action the policy denied outright, an argument
vector that matched no rule, an executor never reached. Such a scenario would report PASS,
every time, on a kernel with the guard deleted.

So: **every guarantee's scenario MUST include a positive control** — a companion run,
against the same configuration and the same scratch store, that establishes the observable
would have been visible had the guard not fired. §2.2 names the control for each guarantee.
If the control does not behave as specified, the guarantee is reported **FAIL with
`reason: "control failed"`** and a counterexample. It is never PASS, and it is never N/A: an
N/A is a statement about the *configuration*, and a failed control is a statement about the
*run*.

### 1.4 What was read

Read on **2026-09-04** unless a document states otherwise.

- **OWASP Top 10 for Agentic Applications (2026)**, OWASP GenAI Security Project, announced
  **2025-12-09** (<https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/>).
  Ten entries, coded `ASI01:2026` – `ASI10:2026`. §6 fixes the structure of the mapping
  document; **item 5 re-derives every entry code and title from the published document
  itself** rather than from this section, and records the version string and date it read.
  The entry titles quoted in §6 are from secondary summaries and are marked provisional for
  exactly that reason.
- **JUnit XML** has no normative schema. There is no OASIS or IEEE document behind it; every
  CI parser reads a dialect of what Ant emitted. §4.3 therefore specifies the shape by hand
  and validates against a checked-in copy of the widely-used `junit-10.xsd`, and the real
  requirement — *the file is accepted by the parsers people actually run* — is stated as
  such rather than dressed up as conformance.
- **Shields.io endpoint badges** (<https://shields.io/badges/endpoint-badge>): a badge
  rendered from a JSON document the project hosts, fetched by Shields at render time.
  §5 chooses this mechanism and argues why.
- `v0.1 §7`, `v0.2 §10`, `v0.3 §9`–`§10`, `docs/ROADMAP.md` (v0.4), `docs/THREAT_MODEL.md`.

**No compliance, conformance, certification or alignment claim** is made in this document, in
`docs/OWASP-AGENTIC-TOP10.md`, in the README, in a docstring, in CLI output or on the badge.
`ROADMAP.md`'s standards rule holds unchanged: *integrate first, map second, never claim
compliance.* A mapping table is a reading of somebody else's taxonomy, and §6 says so on its
first line.

---

## 2. The guarantee catalogue

### 2.1 What a guarantee entry states

Each entry in the registry fixes six things. All six are normative, and all six appear in the
report (§4).

| Field | Meaning |
|---|---|
| **id** | `G<n>`. Stable forever; never reused, never renumbered (§2.4) |
| **invariant** | One sentence, in the present tense, about what the kernel refuses |
| **descends from** | The acceptance tests in `v0.1 §7` / `v0.2 §10` / `v0.3 §10` this is the deployed form of |
| **requires** | The configuration features the scenario needs in order to exist at all |
| **N/A when** | The condition, checkable from the configuration alone, under which the scenario cannot be built. It is a property of the document, never of a failure to build one (§3.8) |
| **observable** | The exception, receipt field, effect state or event that proves the refusal — asserted **by name**, never by exception class alone |
| **control** | The companion run of §1.3, and what it establishes |

The **observable** column is where a guarantee earns its keep. `v0.3 §10`'s rule applies here
unchanged and for the same reason: five refusals that all raise `AuthorityDenied` are five
guards a check asserting only the type cannot tell apart, and a verify that asserted only the
type would report PASS against a kernel that had collapsed them into one.

### 2.2 The catalogue

Ten guarantees, `ctrlrun.guarantees/v1`.

---

#### G1 — A mutated action cannot present a granted approval

**Invariant.** An approval is bound to one `action_hash`; presenting it for any other action
is refused, and the approval is not consumed.

**Descends from.** `v0.1 §7` T2.

**Requires.** One action whose policy can be driven to `approve` by a satisfiable argument
vector (§3.3).

**N/A when.** No action in the policy reaches `approve` under any satisfiable argument
vector. Reason: `no action requires approval`.

**Observable.** The mutated call, made inside `with_approval(<request_id>)`, raises
`ApprovalMismatch` with `reason == "mismatch"`; the executor's call count is 0; the approval
record is still `granted` and not `consumed`; `APPROVAL_INVALIDATED` is appended.

**Control.** The **unmutated** action, presenting the same approval, executes and commits.
Without it, an action that could never execute at all would score a perfect G1.

---

#### G2 — A consumed approval cannot be presented again

**Invariant.** An approval is single-use; the second presentation is refused and does not
execute.

**Descends from.** `v0.1 §7` T4, T12.

**Requires.** As G1.

**N/A when.** As G1.

**Observable.** The identical action executed twice under the same `with_approval`: the first
commits; the second raises `ApprovalMismatch` with `reason == "consumed"`; the fake
executor's call count across both is exactly 1.

**Control.** The first execution's `committed` receipt is the control — it is what makes the
second refusal a *replay* refusal rather than a refusal of everything.

`v0.1 §7` T4's note holds and is asserted: where the action also carries an effect key,
`DuplicateEffect` would apply as well, and the approval check runs first, so `consumed` is
the reason reported.

---

#### G3 — A committed effect cannot be executed again

**Invariant.** A second attempt on an effect key whose record is `COMMITTED` is refused, and
the remote is not called.

**Descends from.** `v0.1 §5.3` E2, `v0.2 §10` T14.

**Requires.** One action declaring an `effect:` template (`ctrlrun.policy/v2` or later) whose
placeholders the synthesized arguments resolve.

**N/A when.** No action declares an `effect:` template. Reason: `no action declares an
effect: template`. The report adds the sentence that makes this actionable rather than
mysterious: *in a `ctrlrun.policy/v1` document the template lives in the `@protect` decorator,
which verify does not read (§1.2)*.

**Observable.** The second attempt raises `DuplicateEffect` with `state == "committed"`; the
executor's call count is 1; the effect record still carries `attempt == 1` and the first
attempt's `action_id`.

**Control.** The first attempt commits and the record reaches `COMMITTED`.

---

#### G4 — Concurrent attempts on one effect key produce exactly one winner

**Invariant.** Reservation is atomic **across processes**, not merely across threads.

**Descends from.** `v0.1 §7` T3.

**Requires.** As G3, **and** a store backend that spans processes.

**N/A when.** No `effect:` template (as G3); or the backend cannot span processes. Reason for
the second: `the configured store backend is per-connection and cannot reserve across
processes`. In v0.4 the only backend that reaches this is an in-memory store, which
`SQLiteStateStore` already refuses to be (`state.py`); the row exists so that a v0.6 backend
which cannot make the guarantee reports N/A rather than a green it did not earn.

**Observable.** `_PROCESSES` (8) OS processes each execute the identical action on one effect
key. Exactly one `committed` receipt and seven refusals exist in the scratch store; the fake
executor ran exactly once, counted by `O_CREAT|O_EXCL` file creation in a scratch directory
so the count survives process boundaries; every child's PID differs from the parent's.

**Control.** The same 8 processes, on **8 distinct** effect keys, all commit. Without this the
guarantee is satisfied by 8 children that failed to start, and the report would say so in
green.

The children run under the **real** clock, not the injected one of §3.6: a callable is not
picklable and timing is not what this guarantee is about. Every other scenario uses the
injected clock.

---

#### G5 — An ambiguous outcome blocks a blind retry

The signature guarantee of the whole library, in the operator's own configuration.

**Invariant.** An executor that raises anything other than `NotExecuted` leaves the effect
`AMBIGUOUS`, and the retry is refused rather than executed.

**Descends from.** `v0.1 §7` T1, T8.

**Requires.** As G3.

**N/A when.** As G3.

**Observable.** A fake that commits at the fake remote and then raises `TimeoutError`: the
record is `AMBIGUOUS`, the receipt is `result == "ambiguous"`. The identical action retried
raises `AmbiguousEffect`; the fake remote's call count is 1; the retry's receipt is
`result == "blocked"`; the record is **still** `AMBIGUOUS` afterwards.

**Control.** The same scenario with a fake that raises `NotExecuted` instead: the record
reaches `FAILED`, the retry **is** admitted, and it executes — call count 2. This control
carries more weight than any other in the catalogue, because it is the only thing separating
"CTRLRun blocks blind retries" from "CTRLRun blocks retries", and the second sentence
describes a library nobody can deploy.

---

#### G6 — An action the policy does not list is refused

**Invariant.** Unknown action → DENY. There is no default-allow.

**Descends from.** `v0.1 §7` T6.

**Requires.** At least one action listed in the policy (for the control).

**N/A when.** `actions:` is empty. Reason: `the policy lists no action, so an unknown action
is indistinguishable from a known one`.

**Observable.** An action named `ctrlrun.verify.absent.<digest>` — derived from the policy's
own action names so that it cannot collide with one — raises `ActionDenied` with
`reason == "unknown_action"`; a `denied` receipt is written; the executor's call count is 0.

**Control.** A listed action evaluates to something other than `unknown_action`.

---

#### G7 — An action with no principal is refused

**Invariant.** No principal, no action: an action proposed outside `context()` with no
`IdentityProvider` configured is refused, and **no receipt and no events are written**.

**Descends from.** `v0.1 §2.1`, `v0.2 §10` T21, `v0.3 §10` T62.

**Requires.** Nothing. Applicable to every loadable configuration, with or without an
`authority:` section — this is a v0.1 rule and does not depend on the authority model.

**N/A when.** Never.

**Observable.** Through `protect(..., control=<the verify Control>)` called outside
`context()`: `ActionDenied` with `reason == "no_principal"`; the scratch store holds no
receipt and no event for that action; the executor's call count is 0.

**Control.** The identical call inside `context()` runs, so the refusal is attributable to the
missing principal and not to the action.

---

#### G8 — An expired grant refuses an action it would otherwise permit

**Invariant.** A grant is authority only until its `expires_at`; after that the action it
covered is denied, by name.

**Descends from.** `v0.3 §10` T71.

**Requires.** An `authority:` section; at least one grant carrying `expires_at` whose subject,
`actions`, `resources`, `environments` and `constraints` a synthesizable action satisfies
(§3.4).

**N/A when.** No `authority:` section (reason: `no authority section`); or no grant carries
`expires_at` (reason: `no grant declares an expires_at`); or no grant matches any action the
policy lists (reason: `no grant matches any action in the policy` — T111, and it is N/A and
not FAIL: a configuration whose grants and whose policy do not overlap is a configuration in
which nothing is authorized, which is a fail-closed state, not a broken guarantee).

**Observable.** With the injected clock at `expires_at` exactly, the action passes authority
and `AUTHORITY_RESOLVED` names the grant. One microsecond later, `AuthorityDenied` with
`reason == "authority_expired"`, a `denied` receipt, and the executor's call count still 0.

**Control.** The before-expiry half is the control, and it is why the two halves are one
scenario: a kernel that denied *everything* would satisfy the after half alone.

---

#### G9 — A delegation cannot widen its parent, and omission is not inheritance

**Invariant.** A delegated grant is valid only if it is provably a subset of its parent on
every dimension, at creation and at every evaluation; a child that **drops** a dimension its
parent constrains is rejected rather than treated as unconstrained.

**Descends from.** `v0.3 §10` T76, T81, T75.

**Requires.** An `authority:` section with at least one grant carrying `delegable: true`.
(`v0.3 §4.2` requires such a grant to carry `expires_at`, so a delegable parent always
constrains at least `actions` and `expires_at`.)

**N/A when.** No `authority:` section (reason: `no authority section`); or no grant carries
`delegable: true` (reason: `no grant is delegable`).

**Observable.** For **each dimension the parent actually constrains** — `subject`, `actions`,
`resources`, `constraints`, `environments`, `expires_at` — two attempts, everything else held
contained:

1. a child **widened** on that dimension alone, and
2. a child **omitting** that dimension altogether,

each raising `AuthorityEscalation` with `reason == "containment"` and `data.dimension` naming
that dimension, each appending exactly one `DELEGATION_REJECTED`, and
`store.get_delegation(<the id that was not created>)` returning `None`.

A dimension the parent does not constrain is not exercised and is reported as such: the
guarantee's line names the dimensions it covered and the dimensions the parent left
unconstrained. Reporting `G9 PASS` for a parent that constrains one dimension as though it
had covered six is the N/A rule violated one level down.

**Control.** A child narrowed on every dimension the parent constrains is **accepted**,
`DELEGATION_CREATED` is appended and reaches a registered sink, and an action within the
child's limits passes authority naming the delegation. Without it, a `Control.delegate` that
refused everything would score a perfect G9 — a negative test against behaviour the library
refuses anyway, which is no test at all.

---

#### G10 — An unknown exception is an AMBIGUOUS outcome, never FAILED

**Invariant.** `NotExecuted` is the only outcome that means "the remote did nothing".
Everything else, timeouts included, is `AMBIGUOUS`.

**Descends from.** `v0.1 §5.5`, `v0.1 §7` T1, T8.

**Requires.** One action the configuration can drive to `allow`, or to `approve` (verify
grants its own approval — §3.5). No effect template is needed: the receipt's `result` carries
the mapping whether or not there is a key.

**N/A when.** No action can be driven to `allow` or `approve`. Reason: `every action in the
policy is denied`.

**Observable.** Parametrized over three executors, each asserted separately:
`TimeoutError` → receipt `ambiguous`; a bare `RuntimeError` → receipt `ambiguous`; and — the
row that must not be dropped — `NotExecuted` → receipt `failed`. Where the action also has an
effect key, the record is `AMBIGUOUS`, `AMBIGUOUS` and `FAILED` respectively.

**Control.** The `NotExecuted` row is the control: a kernel that mapped *everything* to
`AMBIGUOUS` would pass the first two rows and fail this one, and a kernel that mapped
everything to `FAILED` would fail the first two. The guarantee is the asymmetry, so both
directions are asserted or neither is.

---

### 2.3 The registry is closed, ordered and versioned

`G1`–`G10` are the whole of `ctrlrun.guarantees/v1`. The catalogue identifier appears in
every report (§4.2) and on nothing else.

- **Ids are permanent.** A guarantee that is removed leaves its number retired; a guarantee
  that is added takes the next one. `G4` means the same sentence in every version of CTRLRun
  that ever emitted it.
- **Adding a guarantee is a specification amendment**, and it moves the denominator. A badge
  reading `8/8` from one release and `8/10` from the next is not a regression, and the report
  carries `catalogue: "ctrlrun.guarantees/v1"` and `ctrlrun_version` so a reader can tell the
  two apart without guessing. Said here because the alternative — quietly holding the
  denominator still so badges do not move — is how a count stops meaning anything.
- **A guarantee is a refusal the specification already requires.** Verify adds no guarantee of
  its own and weakens none: every entry above descends from a test that already exists and
  passes in this repository. If a scenario cannot be expressed as a deployed form of an
  existing acceptance test, it is not a guarantee, it is a feature request.

---

## 3. The scenario engine

### 3.1 What verify is handed, and what it reads

```
ctrlrun verify [--authority PATH] [--json] [--junit PATH] [--only G1,G3] [--store-url URL]
```

The policy document is discovered exactly as every other command discovers it — `$CTRLRUN_CONFIG`,
else `./ctrlrun.yaml` (`v0.1 §3.1`, `discover_policy_path`). There is no `--config` flag, and
adding one would leave two answers to "which policy is in force" on one machine.

`--authority PATH` names a standalone authority document, the one `ctrlrun gateway --authority`
already accepts (`v0.3 §8.3`), loaded with `standalone=True`. Where the `authority:` section is
in the policy document itself, the flag is omitted and verify reads it there. **Declaring
authority in two places is refused**, naming both, exactly as the gateway refuses it.

Verify reads **those two documents and nothing else**. It does not import the operator's
package, does not read `$CTRLRUN_STATE`, and does not open the operator's store (§3.5).

`--store-url URL` is reserved. In v0.4 the only accepted value is one naming the SQLite
backend; anything else exits **2** naming v0.6. The flag exists now so that the Postgres store
lands against a stated interface rather than growing one, and so that the N/A row of G4 has
something to be about.

### 3.2 Selecting an action, deterministically

For each guarantee, in catalogue order, over the policy's action names **sorted by codepoint**:
take the first action for which every one of the guarantee's `requires` holds and for which an
argument vector can be synthesized (§3.3). No randomness is involved anywhere in verify — not
seeded randomness, none. Two runs against one document choose the same actions, in the same
order, with the same arguments (T105).

The chosen action's name appears on the guarantee's line in every output format. A report that
said `G5 PASS` without saying *against what* would be unauditable, and an operator's first
question on reading a green report is which of their forty actions it actually ran.

Where no action satisfies the requirements, the guarantee is **N/A** with the reason from
§2.2's `N/A when` row — a statement derived from the document. Where the derivation itself
raises, that is an **internal error**: exit 3, never N/A (§3.8, §4.4).

### 3.3 Synthesizing arguments

The engine drives a policy rule by constructing an argument mapping that satisfies it. Every
value is an `int`, a `str`, a `bool` or `None` — never a `float`, which `v0.1 §2.3` rejects at
construction and which verify therefore cannot use even accidentally.

**Per-argument derivation**, from the conditions of the target rule:

| Condition | Value chosen |
|---|---|
| `X_eq: v` | `v` |
| `X_in: [a, b, …]` | `a`, the first element |
| `X_gte: n` | lower bound `n` |
| `X_gt: n` | lower bound `n + 1` |
| `X_lte: n` | upper bound `n` |
| `X_lt: n` | upper bound `n - 1` |
| `X_neq: v` | `v + 1` for an `int`; `v + "-x"` for a `str`; `"ctrlrun-verify"` for `None` or a `bool` |
| bounded on both ends | the **lower** bound of the satisfiable integer interval; the upper where there is no lower |
| named only by an effect or resource template placeholder | the literal string `ctrlrun-verify-<name>` |

The last row is deliberate and visible: every value verify invents is prefixed
`ctrlrun-verify`, so a value that ever appeared anywhere it should not have is recognizable on
sight.

**Reaching a rule that is not the first.** `v0.1 §3.2` is first-match-wins, so an argument
vector that satisfies rule *i* must also **fail every rule before it**. Negating a rule's
conjunction gives a disjunction, so the engine tries, in a fixed order, the negation of each
single condition of each earlier rule:

| Condition | Its negation |
|---|---|
| `X_gte: n` | upper bound `n - 1` |
| `X_gt: n` | upper bound `n` |
| `X_lte: n` | lower bound `n + 1` |
| `X_lt: n` | lower bound `n` |
| `X_eq: v` | `X_neq: v` |
| `X_neq: v` | `X_eq: v` |
| `X_in: […]` | a value outside the list, derived as `X_neq` of its first element |

Candidate vectors are enumerated in a fixed order and the search is **bounded at 64
candidates** per action. A bound rather than a solver: this is constraint satisfaction over a
tiny language, the bound makes the work finite and the failure mode legible, and a
configuration whose rules cannot be driven within it reports N/A naming the action rather than
running long. Every bound in this repository is asserted by a test, so a broken search fails
red instead of hanging CI: a timeout is not a test failure.

**Unsatisfiable** is a normal outcome, not an error: contradictory bounds (`amount_gte: 100`
with `amount_lte: 10`), an argument bounded numerically *and* fixed to a string, or an
exhausted candidate list all mean "this action cannot be driven to that decision", and the
engine moves to the next action.

**The vector is checked before it is used.** Having built one, the engine calls
`Policy.evaluate` on the action it just constructed and asserts the decision is the one it was
aiming for. If it is not, the synthesis is wrong and that is an internal error — exit 3. A
scenario built on a vector that lands in a different rule than intended is the "window not
actually reproduced" failure in its purest form: it would run, refuse something, and report a
guarantee that was never exercised.

### 3.4 Synthesizing a principal, and a delegation

**The principal** comes from the grant under test, never from the policy:

- `subject.agent` literal → used verbatim; `"*"` → `ctrlrun-verify`; a prefix wildcard
  `finance-*` → `finance-ctrlrun-verify`.
- `subject.user` absent → the principal has no user; present → the same derivation.
- `expires_at`, `issuer` and `claims` are not set on a synthesized principal: they are not
  part of the action hash (`v0.3 §2.2`) and nothing in the catalogue depends on them.

Where there is no `authority:` section, the principal is `Principal(agent="ctrlrun-verify")`
and the guarantees that need one (G8, G9) are N/A.

**The environment.** `v0.3 §2.5` puts the environment on the `Control`, and verify sets it to
the first entry of the grant's `environments` where there is one, else the document's
`environment:`, else `production`. It is never taken from a synthesized argument, because
there is no such thing: the environment is not an argument.

**The delegation** of G9 is created through `Control.delegate`, the real entry point, with
`by=` the synthesized principal of the parent's subject — `v0.3 §5.3` rule 4 requires the
creator to be the parent's subject, and a scenario that bypassed `delegate` to write a row
directly would be a test double that can grant where the real code would not, which invalidates
every scenario that uses it.
The child grants are derived from the parent, dimension by dimension:

| Dimension | Narrowed child (the control) | Widened child | Omitting child |
|---|---|---|---|
| `subject` | parent's subject verbatim | `agent: "*"` | `user` dropped where the parent names one |
| `actions` | the parent's first pattern, made literal where it is a wildcard | the pattern with a `*` appended segment-wise | `actions: []` — refused at construction, so the omission case is the grant with the key absent |
| `resources` | as `actions` | as `actions` | key absent |
| `constraints` | each numeric bound tightened by 1; `eq`/`in` left as the parent's | each numeric bound loosened by 1 | key absent |
| `environments` | the parent's first entry alone | the parent's entries plus `ctrlrun-verify-env` | key absent |
| `expires_at` | parent's minus one hour | parent's plus one hour | key absent |

`delegable` is never set on a child, so no scenario creates a chain deeper than one link and
no scenario leaves re-delegable authority behind in a scratch store that is about to be
deleted anyway.

### 3.5 The scratch store, and the operator's store

**One scratch store per guarantee**, created in a temporary directory that is removed when the
run ends — including when it ends by exception. The backend is the one `--store-url` names,
which in v0.4 is SQLite: a file, because `SQLiteStateStore` refuses `:memory:` precisely so
that G4 can mean something (`state.py`).

The operator's store is **not opened, not read and not created**. Verify never calls
`state_path()`, and `Control.from_file()` — which would open it — is not used: verify builds
its `Control` from a `Policy` it loaded itself, a `SQLiteStateStore` on the scratch path, and
its own sinks. T103 hashes the operator's `.ctrlrun/state.db` before and after and requires
the digests to be equal, and asserts the file is not created where it did not exist.

**Sinks.** One in-memory recording sink, so the counterexample of §4.5 has the ordered events
to show. No `JSONLEventSink`: verify writes no evidence files, because evidence of a scenario
that never happened, filed beside evidence of actions that did, is a receipt trail nobody can
read.

**Approvals.** The scenarios that need to get past an `approve` gate grant their own, through
the store's `grant_approval` — the same call `ctrlrun approve` makes. G1 and G2 are *about*
that mechanism; every other scenario that meets an approval gate uses it to get on with the
guarantee it is actually testing, and the report names the action as approved so a reader is
not left thinking a human was involved.

### 3.6 Determinism, clocks and bounds

- **No sleeps, anywhere.** Every scenario that needs time to pass advances an injected clock.
- **The base instant `T0`** is derived from the document, so two runs agree: the earliest
  `expires_at` among the grants minus one hour, or `2026-01-01T00:00:00Z` where no grant
  carries one. The clock is a counter over `T0`, advanced explicitly by the scenario that
  needs it. G8's "one microsecond after `expires_at`" is a step, not a wait.
- **The real clock is used in exactly one place**, G4's children (§2.2), and the reason is
  stated there.
- **Every loop is bounded**: the candidate search at 64 (§3.3), the process pool at 8, the
  chain depth at 1 (§3.4). *A timeout is not a test failure*, and that applies to verify itself:
  T105 and T106 are written so a broken bound fails red.
- **Timestamps are the only non-reproducible field** in the JSON output, and T105 asserts two
  runs are byte-identical once they are removed.

### 3.7 Verify reaches no network

No scenario opens a socket. That is a claim about the environment, and a claim about the
environment is worth nothing until something takes the environment away — so it is a test:
T107 runs a verify in a subprocess whose `sitecustomize`
replaces `socket.socket`, `socket.create_connection` and `socket.getaddrinfo` with a refusal —
the guard `examples/` and T86 already use — and the run must complete normally. The fake
executors never call out, and neither does anything verify does to reach them.

### 3.8 Observe mode, and the configurations verify refuses

**`mode: observe` is refused**: verify prints the `v0.3 §6.5` banner, states that observe mode
enforces nothing and that there is therefore nothing to verify, and exits **2**. It does not
run the scenarios and report ten failures, which would be true and useless; and it does not
run them in a synthetic enforce mode, which would report guarantees about a configuration
nobody deployed. The message names the one-line edit.

Everything else that makes a configuration unusable exits **2** as well, with the message
saying which: a missing or malformed policy (`PolicyError`); authority declared twice; an
unreadable authority document; a `--store-url` naming a backend v0.4 does not have; an
`--only` naming a guarantee id that is not in the registry; and — the rule that keeps a
degenerate configuration from producing a green badge — **zero applicable guarantees**. A
report of `0/0` is not a pass, for the same reason `8/8` is not one when five were N/A.

An **internal error** — a scenario that raised where the specification says it cannot, a
synthesized vector that landed in the wrong rule (§3.3), a store that would not open — exits
**3**. It is never reported as a FAIL and never as an N/A: a defect in verify must not read as
a defect in the kernel, and it must not read as a property of the configuration either.

### 3.9 Verify is not a new entry point

`v0.3 §4.3.1` requires a new entry point to be a specification amendment before it is code, and
it is the enumeration. **`ctrlrun.verify.run` does not add a row**, and the reason
is worth stating rather than assuming: verify proposes no action of its own. It constructs
`Control` objects and calls the entry points that already exist — `@protect`,
`Control.execute`, `Control.evaluate`, `Control.delegate` — against a scratch store, and it
asserts that each of them applies the checks `v0.3 §4.3.1` requires. A verify that reached
past `Control` to reserve, commit or grant for itself would be a second composer of the kernel
(`ARCHITECTURE.md` §6) and would be verifying something other than what runs in production.

The table nevertheless gains one row, because a reader will look for it:

| Entry point | Builds an `Action` | Resolves identity | Evaluates authority |
|---|---|---|---|
| `ctrlrun.verify.run` | no — it drives the rows above | no — it synthesizes principals for a scratch store (§3.4) | no — it asserts that the rows above do |

**Verify MUST NOT be given a way to relax a check.** There is no flag, argument or environment
variable that makes a scenario skip a guard, and there is none that makes verify's `Control`
behave differently from the operator's. The moment one exists, the thing being verified is not
the thing that ships.

---

## 4. Output

Four statuses, and they are the closed set: `pass`, `fail`, `not_applicable`, `skipped`.
`skipped` exists only under `--only` (§4.6). Enums render **by value** in every format, and
T117 reuses the existing guard rather than writing a second one.

### 4.1 The human report

Written to stdout. One line per guarantee, in catalogue order, and the summary is the last
line so that a `tail -1` is meaningful.

```
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
G7   no principal refused             PASS
G8   expired authority refused        PASS  head-of-support
G9   delegation cannot escalate       PASS  head-of-support (6 of 6 dimensions)
G10  unknown exception is ambiguous   PASS  stripe.refund

10/10 declared guarantees pass. 0 not applicable.
```

And where the configuration cannot exercise everything — this is
`examples/policies/payments.yaml`, a `ctrlrun.policy/v1` document with no effect templates and
no grants, and it is the output item 4 puts in CI so the N/A path is dogfooded and not merely
described:

```
G3   duplicate effect refused         N/A   no action declares an `effect:` template
                                            (in a v1 document the template lives in the
                                            @protect decorator, which verify does not read)
G4   one winner under concurrency     N/A   no action declares an `effect:` template
G5   ambiguous blocks a blind retry   N/A   no action declares an `effect:` template
G8   expired authority refused        N/A   no authority section
G9   delegation cannot escalate       N/A   no authority section

5/5 declared guarantees pass. 5 not applicable: G3, G4, G5, G8, G9.
```

Rules the format MUST keep:

- The count is **passes over applicable**. `not applicable` is a separate sentence with the
  ids named, never a parenthesis inside the fraction, and never summed into it.
- Every N/A carries its reason on the same line. An N/A without a reason is indistinguishable
  from a guarantee somebody switched off.
- A FAIL line names the action and is followed by the counterexample of §4.5, indented.
- The observe-mode banner of `v0.3 §6.5` goes to **stderr**, and so does every refusal message
  of §3.8, so `--json` stdout stays parseable.

### 4.2 `--json`, schema `ctrlrun.verify/v1`

One object on stdout. Field for field:

```json
{
  "schema": "ctrlrun.verify/v1",
  "catalogue": "ctrlrun.guarantees/v1",
  "ctrlrun_version": "0.4.0",
  "started_at": "2026-01-01T00:00:00+00:00",
  "finished_at": "2026-01-01T00:00:07+00:00",
  "policy": {
    "path": "examples/authority/payments.yaml",
    "sha256": "<hex>",
    "schema": "ctrlrun.policy/v3",
    "mode": "enforce",
    "actions": 4
  },
  "authority": {
    "path": "examples/authority/payments.yaml",
    "sha256": "<hex>",
    "grants": 3,
    "max_delegation_depth": 3
  },
  "store": {"backend": "sqlite", "scratch": true},
  "partial": false,
  "summary": {
    "passed": 10,
    "failed": 0,
    "applicable": 10,
    "not_applicable": 0,
    "skipped": 0,
    "badge": "10/10"
  },
  "guarantees": [
    {
      "id": "G5",
      "title": "ambiguous blocks a blind retry",
      "status": "pass",
      "reason": null,
      "action": "stripe.refund",
      "arguments": {"amount": 0, "payment_id": "ctrlrun-verify-payment_id"},
      "effect_key": "refund:ctrlrun-verify-payment_id",
      "grant_id": null,
      "descends_from": ["v0.1 §7 T1", "v0.1 §7 T8"],
      "detail": {},
      "counterexample": null
    }
  ]
}
```

- `authority` is `null` where the configuration has no section.
- `reason` is non-null for `not_applicable`, for `fail`, and for `skipped`; `null` for `pass`.
- `arguments`, `effect_key` and `grant_id` are `null` where the guarantee does not use one.
- `detail` carries the per-guarantee facts a reader would otherwise have to infer: G4's
  `processes`, G9's `dimensions_exercised` and `dimensions_unconstrained`, G10's per-executor
  rows.
- **`counterexample` is present only on `fail`** and is `null` on every other status (T114).
  A counterexample on a pass would be evidence of a failure that did not happen.
- The two `sha256` digests are of the documents verify actually read. They are what make a
  report attributable to a configuration: a report and a policy that do not hash the same are
  a report about something else.

### 4.3 `--junit PATH`

A JUnit XML file, one `<testsuite name="ctrlrun.verify">` containing one `<testcase>` per
guarantee, `classname="ctrlrun.guarantees"`, `name="<id> <title>"`:

- `pass` → an empty `<testcase>`.
- `fail` → `<failure message="<one line>" type="<guarantee id>">` whose text is the rendered
  counterexample.
- `not_applicable` → `<skipped message="<reason>">`. Reported as skipped and **not** as a
  pass, which is the same rule as everywhere else, expressed in the vocabulary a CI dashboard
  already has.
- `skipped` (under `--only`) → `<skipped message="not selected">`.

`tests` counts every guarantee, `failures` the failures, `skipped` the N/A and unselected
together; `time` is the wall duration.

JUnit XML has **no normative schema** (§1.4). T115 validates the file against a checked-in
copy of the de-facto `junit-10.xsd`, with its provenance and licence recorded beside it, and
the spec says plainly that the requirement is acceptance by the parsers people run rather than
conformance to a standard that does not exist. `xmlschema` joins the `dev` extra for that
test and for nothing else; it is not a runtime dependency and `ctrlrun` does not import it.

### 4.4 Exit codes

| Code | Meaning |
|---|---|
| 0 | Every applicable guarantee passed, and at least one was applicable |
| 1 | At least one guarantee FAILED |
| 2 | The configuration was refused or is unusable (§3.8), including `mode: observe`, an unknown `--only` id, and zero applicable guarantees |
| 3 | Internal error in verify itself |

N/A never changes the exit code by itself. A run of five passes and five N/As exits 0; a run
of zero applicable exits 2.

### 4.5 The counterexample

A FAIL MUST carry enough evidence to reproduce the violation without rerunning verify. The
counterexample is an object:

```json
{
  "expected": "AmbiguousEffect on the second attempt",
  "observed": "the second attempt executed; the fake remote was called twice",
  "receipts": [ {...}, {...} ],
  "events":   [ {...}, ... ],
  "effects":  [ {...} ]
}
```

`receipts` and `events` are the portable JSON of `receipt.py` — the same documents
`ctrlrun inspect` emits — **in `event_id` order**, for the scenario's actions only. `effects`
is each effect record the scenario touched. T104 requires both receipts to be present for a
G3 failure, because one receipt cannot show a double execution.

`expected` and `observed` are one line each and name the observable of §2.2, not the exception
class alone.

### 4.6 `--only`

`--only G1,G3` runs exactly the named guarantees. Every other guarantee is reported `skipped`
with `reason: "not selected"`, the report carries `"partial": true`, and:

- the exit code is decided over the selected guarantees only;
- **a partial report never produces a badge.** The GitHub Action refuses to write badge data
  for one (§5), because a fraction computed over a subset somebody chose is the false green
  this document exists to prevent, wearing a different costume.

Guarantees are independent — each builds its own scratch store and its own scenario — so
`--only` runs no other guarantee's scenario, and T112 asserts that by the store's contents and
not merely by the report's rows.

---

## 5. The GitHub Action and the badge

### 5.1 `action.yml` — a composite action at the repository root

```yaml
inputs:
  policy:          # path to the policy document. Default: ctrlrun.yaml
  authority:       # path to a standalone authority document. Default: "" (same document)
  only:            # comma-separated guarantee ids. Default: "" (all)
  python-version:  # Default: "3.11"
  install:         # pip requirement to install. Default: "ctrlrun"
  badge-path:      # where to write the shields endpoint JSON. Default: verify-badge.json
outputs:
  passed | failed | applicable | not-applicable | badge-message | report-path
```

Steps, in order: set up Python; `pip install "${{ inputs.install }}"`; run
`ctrlrun verify --json --junit verify-report.xml > verify-report.json` with `CTRLRUN_CONFIG`
set from `policy` and `--authority` passed where given; render the job summary into
`$GITHUB_STEP_SUMMARY` **from that JSON**; write the badge JSON from the same document; upload
the three files as one artifact.

The summary and the badge are rendered from the report rather than from a second run, so the
badge and the artifact can never disagree about what happened.

**There is no input that makes a failure not fail the job.** A `continue-on-error`-shaped flag
here would be a flag that makes a consequential thing permissive by default, which `v0.1 §3.4`
refuses everywhere else; a workflow that wants to tolerate a failure has
`continue-on-error` on the step already, where it is visible in the workflow rather than
hidden in an action's defaults.

`install` defaults to the published package and is set to `.` by this repository's own
workflow, so the action dogfoods the checkout rather than the last release.

**The action fails the job when any guarantee FAILED (exit 1) and when the configuration was
refused (exit 2), and succeeds when guarantees are N/A** (T120). N/A is not a failure and it
is not a pass; the job's green means "nothing that could be checked was wrong", which is
exactly what the badge says.

### 5.2 The badge mechanism, and why this one

**Chosen: a Shields.io endpoint JSON document, written by the action, published by the project
that wants a badge.** The alternatives were considered and rejected:

- **A static SVG uploaded as an artifact.** A workflow artifact is not publicly addressable
  and expires. A badge nobody can link to is not a badge.
- **The action committing badge data to a branch on every run.** That needs `contents: write`
  in every consumer's workflow. Asking for write access to the repository as the price of a
  verification badge is a bad trade for a tool whose subject is least privilege, and it would
  be the single most privileged thing this project asks anyone to do.

So the action **writes** the endpoint JSON and never publishes it. Publishing is the project's
own decision, in its own workflow, with whatever permissions it chooses. `docs/verify.md`
shows the one-job pattern for a `badges` branch and says what permission it needs, once,
where the reader can see the cost.

The document is Shields' endpoint schema:

```json
{"schemaVersion": 1, "label": "CTRLRun", "message": "verified 10/10", "color": "brightgreen"}
```

- **Rendered text is exactly `CTRLRun verified N/M`** — label, a space, message. T119 asserts
  the concatenation and asserts `message` against `^verified \d+/\d+$`, a regex rather than a
  word list, so no adjective can be appended to it later.
- `N` is passes, `M` is **applicable** guarantees. Never the catalogue size.
- `color` is `brightgreen` when `failed == 0`, `red` otherwise. There is no amber for N/A: the
  badge's colour is about failures, and the N/A count lives in the report the badge links to.
- **No badge is written for a partial run** (§4.6) or for a run that exited 2 or 3.

### 5.3 What the badge links to

The badge's link target is `docs/verify.md#what-the-badge-means`, and that section's first
sentence is:

> The badge means the **declared guarantees pass**: every guarantee in the catalogue that this
> configuration can exercise was exercised, and none of them failed.

followed, on the same screen, by what it does not mean — §1.2's list — and by the N/A count
for the run. T119 asserts the exact phrase `declared guarantees pass` is present in the link
target.

The words **secure**, **safe**, **compliant**, **certified** and **audited** do not appear as
claims about CTRLRun or about the operator's system anywhere in the badge, its JSON, the job
summary or `docs/verify.md`.

### 5.4 The job summary

A Markdown table — id, title, status, action, reason — plus the same summary line as §4.1. It
is the report an operator sees without downloading an artifact, and it carries the N/A rows in
full. A summary that listed only failures would make an all-N/A run look like a clean one.

---

## 6. `docs/OWASP-AGENTIC-TOP10.md` (item 5)

### 6.1 What the document is, and what it is not

Its first line, before any table:

> This is a **reading** of somebody else's taxonomy against the guarantees CTRLRun tests. It
> is not a compliance claim, a conformance claim, a certification, or a statement that CTRLRun
> covers the OWASP Top 10 for Agentic Applications. Four of the ten entries are not addressed
> by CTRLRun at all, and they are listed by name below.

`ROADMAP.md`'s standards rule is the reason this document can exist at all: *integrate first,
map second, never claim compliance.* Every row maps a `G` to an entry, and every `G` is
already backed by a passing acceptance test — so the mapping points at code and at a test, and
a row whose test disappears is a row that comes out.

### 6.2 Structure

1. The disclaimer above.
2. The **version read**: `OWASP Top 10 for Agentic Applications`, edition and publication date
   as they appear in the published document, with the URL and the date it was read. Item 5
   sources this by web search and re-derives every code and title from the document itself —
   the codes and titles below come from secondary summaries and are **provisional**.
3. **Table: guarantee → entries mitigated.** Columns: `G`, invariant, ASI entries, one
   sentence on *how* — the mechanism, not a restatement of the entry.
4. **Table: entries with no guarantee**, under the heading `Not covered by CTRLRun`, each with
   one honest sentence: out of scope, or a milestone it waits on. An entry that is partly
   addressed goes in **both** tables, with the partial half stated in the second — the honest
   place for a hedge is next to the thing it qualifies.
5. A closing line: this document is regenerated when the catalogue changes or when OWASP
   publishes a new edition, and it names the edition it was written against.

The provisional entry list, for item 5 to confirm or correct: `ASI01` Agent Goal Hijack ·
`ASI02` Tool Misuse & Exploitation · `ASI03` Agent Identity & Privilege Abuse · `ASI04`
Agentic Supply Chain Compromise · `ASI05` Unexpected Code Execution · `ASI06` Memory & Context
Poisoning · `ASI07` Insecure Inter-Agent Communication · `ASI08` Cascading Agent Failures ·
`ASI09` Human-Agent Trust Exploitation · `ASI10` Rogue Agents.

The expected shape of the second table, which is the half that makes the first one credible:
supply chain, code execution and memory/context poisoning are **not** CTRLRun's subject —
nothing in this library reads a model's memory, inspects a package, or sandboxes an
interpreter — and inter-agent communication waits on v0.7. Item 5 states each in one sentence
and adds nothing aspirational.

Linked from the README's documentation table and from `docs/verify.md`.

---

## 7. `research/framework-probe/` (item 6)

### 7.1 What it is

A research harness that drives two scenarios through third-party agent frameworks against a
fake remote, records what each framework does **by default**, and emits a table. It answers a
question this project has so far only asserted: *what actually happens when the response is
lost and the framework retries?*

It lives in `research/framework-probe/`, **outside `src/`**. It is not part of the package, is
never imported by `ctrlrun`, and its per-framework dependencies are never installed by
`ctrlrun` or by any of its extras. T124b asserts `research` is not an importable package after
`pip install ctrlrun`.

### 7.2 The scenarios, and the fake remote

Two, both already in `examples/`:

- **double-refund** — the remote commits and then the response is lost. Does the framework
  retry, and does the effect land twice?
- **approval-mutation** — a human approves one action; the agent then proposes a mutated one.
  Is the mutation executed?

One fake remote serves every framework: a local HTTP/MCP server with three behaviours —
commit-then-timeout, commit-then-drop-the-connection, and capture-what-was-approved. It counts
effects by identity, not by request, so "executed twice" means two effects and not two HTTP
calls.

### 7.3 Fairness rules — normative for the harness

The result of this harness is a table with other projects' names in it, so the rules are part
of the specification and not a note in a README.

1. **The same fake remote** for every framework, with the same behaviour, on the same port
   discipline.
2. **The same scenario text** — the prompt, tool schema and tool names are byte-identical
   across adapters wherever the framework's API admits it, and the diff is recorded where it
   does not.
3. **Framework defaults.** No retry setting is changed, no timeout tuned, no guard added.
4. **At most one configuration change per framework**, permitted only where the framework
   cannot run the scenario at all without it, and it appears **in the results table** in a
   `config_deviation` column — not in a footnote, not in prose.
5. **The framework's version is read at runtime**, not written down by hand (T123).
6. **The table reports behaviour, not quality.** A framework that retries a lost response is
   doing what its documentation says it does. The finding is about what an agent stack does
   *without* an effect-level guard, and the harness's README says so in its first paragraph.
7. `research/framework-probe/README.md` cites each framework's documented retry and approval
   defaults, with links and the date read.

### 7.4 The result schema

`research/framework-probe/results/<YYYY-MM-DD>.json`, schema
`ctrlrun.framework-probe/v1`:

```json
{
  "schema": "ctrlrun.framework-probe/v1",
  "run_at": "2026-09-04T12:00:00+00:00",
  "python": "3.11.9",
  "remote": "fake-mcp/1",
  "results": [
    {
      "framework": "langgraph",
      "version": "<read at runtime>",
      "adapter": "research/framework-probe/adapters/langgraph.py",
      "scenario": "double-refund",
      "outcome": "executed_twice",
      "effects_observed": 2,
      "requests_observed": 2,
      "config_deviation": null,
      "notes": ""
    }
  ]
}
```

`outcome` is a closed set: `executed_once`, `executed_twice`, `refused`, `error`. The Markdown
table is rendered from this file and never written by hand — framework, version, double-refund
outcome, approval-mutation outcome, `config_deviation`, notes.

Adapters: LangGraph, CrewAI, OpenAI Agents SDK, AutoGen, and a plain MCP client against a
local MCP server. Each declares its framework version at runtime and records it.

**Item 6 ships the harness and no results.** The runs are made and published separately, by
the maintainer; a PR that carried a results file would be publishing findings about other
projects that nobody had reviewed.

---

## 8. Acceptance tests

Each MUST exist as a pytest test carrying the given ID in its name, as `v0.1 §7`, `v0.2 §10`
and `v0.3 §10` require. All MUST pass for v0.4, and every test of the three earlier suites MUST
still pass — with the one amendment §9.4 names.

The item-0 brief scopes this section as **T100–T125**. Numbers are stable and never reused;
suffixed ids (T101b, T124b…) fill in between them where a rule earned its own test after the
brief was written. **T125 and T125b belong to item 1** despite their position: they are the
two guards on verify lying about itself, and the spare number at the end of the range is where
they landed.

**Every test that asserts a refusal asserts it by name** — the reason, the receipt field, the
event type — never the exception class alone. And every test that asserts a guarantee **PASSES**
must also show it can FAIL: a verify whose every guarantee is hard-coded to pass satisfies
T100 perfectly.

### Item 1 — The registry, the engine, G1–G6 and G10 (§2, §3)

#### T100 — Verify against this repository's own configurations
Two documents, one test, parametrized. Against `examples/authority/payments.yaml`
(`ctrlrun.policy/v3`, effect templates, three grants): G1–G6 and G10 are **applicable and
PASS**, and the run exits 0. Against `ctrlrun.example.yaml` (`v1`, no templates, no grants):
G1, G2, G6 and G10 are applicable and pass. Each asserts the chosen action by name, so a
selection that silently changed which action it ran fails here. G7 lands with item 2 and the
test grows by one row there — it is a v0.1 rule with an authority-shaped scenario, and putting
it in item 1's expectations before its scenario exists would make T100 red for a reason that is
not a defect.

#### T101 — A policy with no `approve` rule makes G1 and G2 N/A
G1 and G2 are `not_applicable` with `reason == "no action requires approval"`; the summary's
`applicable` excludes them; the printed count is passes over applicable, and the string `8/8`
appears nowhere in the output. The `else` branch — either guarantee having been reported
`pass` — fails the test.

#### T101b — Zero applicable guarantees is not a pass
A configuration in which every guarantee is N/A exits **2**, prints no badge data, and its
summary is not `0/0` reported as success. Built by making `actions:` empty, which takes G6 and
G10 out along with the rest (§2.2).

#### T102 — A policy with no effect templates makes G3, G4 and G5 N/A
Each with `reason == "no action declares an `effect:` template"`, each carrying the sentence
naming the `@protect` decorator, and G1, G2, G6, G7 and G10 still applicable in the same run —
so a blanket "nothing applies" cannot pass this test.

#### T103 — Verify does not touch the operator's store
A `.ctrlrun/state.db` seeded with a receipt, an effect record and a delegation. Its SHA-256 and
its `st_mtime_ns` are equal before and after a full verify run, and the JSONL evidence files
beside it are unchanged. Repeated for the case where the file does **not** exist: it still does
not exist afterwards, and neither does the directory. Repeated with `$CTRLRUN_STATE` pointing
somewhere else, which is the path an operator actually deploys.

#### T104 — A broken kernel FAILS, with a counterexample that shows the violation
A `Control` injected with a store whose `reserve_effect` always succeeds. G3 and G4 report
`fail`; the exit code is 1; each counterexample carries **both** receipts and the effect
record, `expected` names the exception that did not come and `observed` names what did happen,
and the JSON validates against §4.2. The other guarantees in the same run are unaffected,
which is what makes the two failures attributable.

#### T105 — Two runs produce identical JSON
Byte-identical once `started_at`, `finished_at` and any duration are removed — including the
synthesized arguments, the chosen actions and their order. Asserted on a configuration with
several actions per guarantee to choose from, because a single-candidate document is
deterministic by accident.

#### T106 — G4 uses real processes
The children's PIDs are recorded through the scratch directory and every one differs from the
parent's, and from each other. A threads-only implementation fails this test. The executor
count is read from `O_CREAT|O_EXCL` files rather than from process memory, and the control
half — 8 distinct effect keys, 8 commits — is asserted in the same test, so a run in which no
child started cannot pass.

#### T107 — Verify reaches no network
A full run in a subprocess whose `sitecustomize` replaces `socket.socket`,
`socket.create_connection` and `socket.getaddrinfo` with a refusal completes normally and
exits 0. The guard is asserted to be live in the same subprocess — a network guard that was
not installed proves nothing (§1.3).

#### T125 — A failed control is a FAIL, never a PASS
The guard on §1.3. For each guarantee, a scenario whose positive control is broken — the
executor never reached, the first attempt refused, the delegation the control creates rejected
— reports `fail` with `reason == "control failed"` and a counterexample. Asserted per
guarantee and not once, because a control is a per-guarantee claim. The `else` branch — the
guarantee reported `pass` or `not_applicable` — fails the test with the id that got it.

#### T125b — `import ctrlrun` does not import `ctrlrun.verify`
In a subprocess, as T30 does: `ctrlrun.verify` is absent from `sys.modules` after
`import ctrlrun`, and importing it pulls in no module from any extra. T30's assertion list
grows by one name.

### Item 2 — The authority guarantees (§2, §3.4)

#### T108 — A configuration with grants exercises G7–G9
Against `examples/authority/payments.yaml`: G7, G8 and G9 are applicable and PASS; G8 names
the grant it used and G9 names the dimensions it exercised; `AUTHORITY_RESOLVED` appears in
G8's before-expiry half and `AUTHORITY_DENIED` with `reason == "authority_expired"` in its
after half — asserted by reason, so a kernel that denied for any other cause fails.

#### T109 — No `authority:` section makes G8 and G9 N/A, and leaves G7 applicable
`reason == "no authority section"` for both, and **G7 is still applicable and passes**: no
principal is a v0.1 rule and does not depend on the authority model. The pair is one test,
because reporting all three N/A is the plausible wrong answer.

#### T110 — G9 exercises every dimension the parent constrains, including omission
Parametrized over `subject`, `actions`, `resources`, `constraints`, `environments` and
`expires_at`: for each, the widened child **and** the omitting child are refused with
`AuthorityEscalation(reason="containment")` and `data.dimension` naming that dimension, and
`get_delegation` returns `None` for the id that was not created. Then the mutation half: a
kernel in which omission means "unlimited" makes G9 **FAIL**, and the counterexample carries
the offending delegation — the child grant, the parent, and the dimension. A dimension the
parent does not constrain is reported as unexercised and does not silently count as covered.

#### T111 — Grants that reach no action are N/A, not FAIL
An `authority:` section whose grants name actions the policy does not list. G8 and G9 are
`not_applicable` with `reason == "no grant matches any action in the policy"`, the exit code
is 0 where the other guarantees pass, and neither is reported `fail`. A configuration in which
nothing is authorized is fail-closed, and reporting it as a failed guarantee would train an
operator to ignore red.

#### T112 — `--only` runs what it names and nothing else
`--only G9`: G9 runs; every other guarantee is `skipped` with `reason == "not selected"`; the
report carries `"partial": true`; **no badge data is written**; and — the assertion that
matters — the scratch store holds no receipt for any other guarantee's action, so "it did not
run" is proven by the store and not by the report describing itself. `--only G99` exits 2
naming the unknown id.

### Item 3 — Reporting (§4)

#### T113 — The human report
One line per guarantee in catalogue order; every N/A line carries its reason; the summary is
the last line, reads `N/N declared guarantees pass`, and names the N/A ids separately. A
report with five N/As does not contain the string `10/10`. Parametrized over a passing, a
failing and an all-N/A configuration.

#### T114 — `--json` validates, and the counterexample is conditional
The document validates against §4.2 field for field, `schema` and `catalogue` are exact, the
two `sha256` values match digests computed independently in the test, and `counterexample` is
non-null **exactly** on the `fail` rows — asserted in both directions, on a run containing a
pass, a fail and an N/A.

#### T115 — `--junit` produces a file CI parsers accept
Validated against the checked-in `junit-10.xsd`. N/A rows are `<skipped>` and never absent and
never passes; `tests`, `failures` and `skipped` are counted correctly; the failure text
contains the rendered counterexample. Parsed back with `xml.etree` and asserted structurally
as well, so a schema that happens to be permissive is not the only check.

#### T116 — Every exit code is reachable
Parametrized: 0 (all applicable pass), 1 (a FAIL), 2 (a malformed policy, `mode: observe`, an
unknown `--only` id, a `--store-url` v0.4 does not support, zero applicable), 3 (an injected
internal error). The observe-mode case additionally asserts the `v0.3 §6.5` banner on stderr,
that stdout is empty, and that **no scenario ran** — the scratch directory was never created.

#### T117 — Enums render by value
The existing guard, applied to `ctrlrun.verify/v1`: every status, decision, receipt result and
effect state in the JSON and JUnit output is a value and never a `Status.PASS` repr.

### Item 4 — The Action and the badge (§5)

#### T118 — The action runs in this repository's CI
A job runs the composite action against `examples/policies/payments.yaml` and against
`examples/authority/payments.yaml`, uploads the report artifact, and writes a job summary. The
first is the N/A dogfood — five applicable, five N/A — and the assertion is on that shape, so a
change that made verify silently count N/As is caught in CI rather than in a badge.

#### T119 — The badge says what it is allowed to say
`label + " " + message == "CTRLRun verified N/M"` exactly; `message` matches
`^verified \d+/\d+$`; `M` equals the report's `applicable` and never the catalogue size; the
link target contains the exact phrase `declared guarantees pass`; and the badge JSON, the job
summary and `docs/verify.md` make no claim about CTRLRun using the words `secure`, `safe`,
`compliant`, `certified` or `audited`.

#### T120 — The job fails on FAIL and succeeds on N/A
Three runs of the action: an all-pass configuration (job succeeds, badge written), a
configuration with N/As (job succeeds, badge written, `M` reduced), and one with a FAIL (job
fails, badge written red). Plus: a `--only` run writes **no** badge, and an exit-2
configuration fails the job and writes no badge.

### Item 5 — The OWASP mapping (§6)

#### T121 — The mapping is complete in both directions
Every `G` in the registry appears in `docs/OWASP-AGENTIC-TOP10.md`; every ASI code in the
document is one of the ten in the cited edition and matches `^ASI\d{2}:\d{4}$`; every ASI code
in the cited edition appears either in the mapping table or in `Not covered by CTRLRun`, and
none appears only in neither. The document contains no compliance claim, asserted against the
word list of `v0.2 §10` T31.

### Item 6 — The research harness (§7)

#### T122 — The harness runs end-to-end against a stub framework
A stub "framework" that deliberately retries a lost response drives the double-refund scenario
against the fake remote, and the harness reports `executed_twice` with `effects_observed == 2`.
Its pair, a stub that does not retry, reports `executed_once` — without it the harness could
report `executed_twice` unconditionally and pass.

#### T123 — Every adapter declares its framework version at runtime
For each adapter present, the version recorded in the result is read from the installed
package and is not a literal in the adapter's source, asserted by comparing against
`importlib.metadata.version`. An adapter whose framework is not installed is skipped by name,
and the skip appears in the results file rather than as a silent absence.

#### T124 — The results document validates
Against `ctrlrun.framework-probe/v1`: `outcome` is in the closed set, `config_deviation` is
present on every row (null or a string), and the rendered Markdown table has one row per
result and is regenerated from the JSON rather than stored.

#### T124b — The harness is not part of the package
After `pip install .` in a temporary environment, `import research` fails and no
`framework_probe` module is importable; `ctrlrun`'s dependencies and extras name no framework.

---

## 9. Public API and CLI additions (frozen for v0.4)

### 9.1 The module

```python
# ctrlrun/verify/__init__.py — core (stdlib + pyyaml + click), NOT re-exported from `ctrlrun`

def run(
    config: str | os.PathLike[str] | None = None,
    *,
    authority: str | os.PathLike[str] | None = None,
    only: Sequence[str] = (),
    store_url: str | None = None,
) -> Report: ...

class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    SKIPPED = "skipped"

@dataclass(frozen=True)
class Counterexample:
    expected: str
    observed: str
    receipts: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    effects: tuple[Mapping[str, Any], ...]

@dataclass(frozen=True)
class GuaranteeResult:
    id: str
    title: str
    status: Status
    reason: str | None = None
    action: str | None = None
    arguments: Mapping[str, Any] | None = None
    effect_key: str | None = None
    grant_id: str | None = None
    descends_from: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)
    counterexample: Counterexample | None = None

@dataclass(frozen=True)
class Report:
    guarantees: tuple[GuaranteeResult, ...]
    ...
    @property
    def exit_code(self) -> int: ...      # §4.4
    def to_dict(self) -> dict[str, Any]: ...
    def to_json(self) -> str: ...        # ctrlrun.verify/v1
    def to_junit(self) -> str: ...       # §4.3
    def to_text(self) -> str: ...        # §4.1
```

**And no other public name.** The guarantee registry, the scenario builders, the argument
synthesizer and the process worker are internal to the package: they are the parts most likely
to change as the catalogue grows, and an operator's script has no business reaching into them.

`Report` is a value: `run()` performs the work, and rendering it costs nothing and can be done
more than once. `exit_code` is on the report rather than in the CLI so the rule of §4.4 has one
implementation.

`run()` is **not** re-exported from `ctrlrun/__init__.py` and `ctrlrun` does not import it
(T125b). It is `import ctrlrun.verify`, explicitly, by a caller who wants it.

### 9.2 The CLI

```
ctrlrun verify [--authority PATH] [--json] [--junit PATH] [--only G1,G3] [--store-url URL]
```

`--authority` is **an addition to the line the item-0 brief froze**, and the reason is recorded
here rather than in a commit message. A deployment that keeps its grants in a separate document
— which `ctrlrun gateway --authority` exists for — would otherwise have G8 and G9 reported
`not_applicable` with the reason `no authority section` while the operator is looking at one.
That is a false N/A, which is the same defect as a false pass wearing the other costume, and
the N/A rule is the thing this release is for.

`--json` and `--junit` may be combined; `--json` writes stdout and `--junit` writes the path.
Without either, the human report goes to stdout. Every diagnostic — the observe-mode banner,
every §3.8 refusal — goes to stderr.

`ctrlrun verify` remains in the left column of `v0.3 §6.5`'s banner table: it loads the
operator's policy, so it prints the banner under `mode: observe`, and then exits 2 (§3.8).

### 9.3 Packaging

`dependencies` is unchanged: `pyyaml` and `click`. No extra is added. The `dev` extra gains
`xmlschema`, used by T115 and by nothing else. `research/` is outside `src/` and is packaged
by neither the wheel nor an import path (T124b).

**Module map** (`ARCHITECTURE.md` §6) gains one row, and the dependency direction is unchanged
— downward only, with `Control` still the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `verify/` | the guarantee registry, scenario derivation, the scratch store, reporting | the gateway, `otel`, `jwt_identity`; anything from an extra |

`verify/` sits **above** `control.py`, beside `cli/`: it composes `Control`, `Policy` and
`Authority` the way an application does, and nothing in the kernel imports it. `control.py`
importing `verify` would be a cycle and, worse, would put a testing tool in the execution path.

### 9.4 What v0.4 amends in v0.1–v0.3

Four amendments, each in the item that makes it true.

1. **`v0.3 §6.5`'s stub is superseded** (item 1). `ctrlrun verify` no longer exits 2
   unconditionally: under `mode: enforce` it runs and exits 0, 1 or 3; under `mode: observe` it
   still prints the banner and exits 2, now with a message that says why rather than a message
   that says "not yet".
2. **`v0.3 §10` T85 changes in the same commit.** Its final sentence — *"`ctrlrun verify` exits
   2 in both modes with a message naming v0.4"* — becomes: exits 2 under `mode: observe` with a
   message naming the mode; runs under `mode: enforce`. The banner assertions for every other
   command are untouched. A frozen test whose subject was explicitly temporary is amended
   rather than deleted, and this line is the record of it.
3. **`v0.3 §13`'s out-of-scope entry is discharged**, and `v0.3 §4.3.1` gains the
   informational row of §3.9.
4. **`docs/THREAT_MODEL.md` is corrected** (item 7). Its "Out of scope" bullet reads *"v0.4
   `verify` will include a check for it"* about an executor that raises `NotExecuted`
   incorrectly. It will not, and cannot: verify never calls the operator's executor (§1.2). The
   sentence is rewritten to say so, and a **Known v0.4 limitations** section carries §1.2's list
   in full. A guard documented as prevention and implemented as attribution is the false-green
   problem in prose form; a guard documented as existing and not implemented at all is the same
   problem with a longer fuse.

No schema changes: `ctrlrun.policy/v3`, `ctrlrun.receipt/v2`, `ctrlrun.action/v1` and
`ctrlrun.inspection/v2` are untouched. **v0.4 adds no table and no column to any store** — it
writes only to a scratch store it created — so `v0.3 §5.2`'s "there is still no migration
story" holds unchanged. Three schema strings are new and belong to documents, not to storage:
`ctrlrun.verify/v1` (§4.2), `ctrlrun.guarantees/v1` (§2.3) and `ctrlrun.framework-probe/v1`
(§7.4).

---

## 10. Fail-closed table for v0.4

`v0.1 §3.4`, `v0.2 §6.11` and `v0.3 §9` hold in full and unchanged. These rows are verify's
own, and none of them is configurable.

| Condition | Result |
|---|---|
| `mode: observe` | banner on stderr, exit **2**; no scenario runs, no scratch store is created |
| Policy missing or malformed | `PolicyError`, exit **2**; no scenario runs |
| Authority declared in both the policy document and `--authority` | exit **2**, naming both |
| `--authority` document malformed, or containing `actions:` / `mode:` | exit **2** (`v0.3 §4.8`) |
| `--only` naming an id outside the registry | exit **2**, naming the id and the registry |
| `--store-url` naming a backend v0.4 does not have | exit **2**, naming v0.6 |
| Zero applicable guarantees | exit **2**; no badge data; never reported as `0/0` passing |
| A guarantee's positive control did not behave as specified | that guarantee is `fail`, `reason == "control failed"`, with a counterexample — never `pass`, never `not_applicable` |
| A synthesized argument vector lands in a different rule than intended | exit **3** — an internal error, never a FAIL and never an N/A |
| A scenario raises where the specification says it cannot | exit **3** |
| Any write attempted outside the scratch directory | exit **3**; the operator's store is never opened (T103) |
| A partial run (`--only`) | no badge data is written, whatever the result |
| An exit of 2 or 3 | no badge data is written |

---

## 11. Explicitly out of scope for v0.4

Everything in `v0.1 §9`, `v0.2 §12` and `v0.3 §13` that v0.4 does not deliver, and
specifically:

- **Verifying the operator's code.** Executors, `reconcile` hooks, decorator placement, and
  whether anything calls the raw function are all invisible to verify (§1.2). No plugin, no
  import hook, no `--module` flag.
- **Grading a configuration.** No score, no letter, no "your policy is permissive", no
  suggested rules, no linting. Verify reports whether the kernel's refusals hold; it has no
  opinion on the policy they hold under, and acquiring one would mean shipping judgment
  disguised as measurement.
- **Fixing anything.** Verify never writes to the operator's policy, authority document or
  store. There is no `--fix` and no `--init`.
- **A hosted badge, a results service, or a dashboard.** The action writes a JSON document;
  where it goes is the project's decision (§5.2).
- **Continuous verification.** Verify is a command and a CI job. There is no daemon, no
  scheduled in-process self-check, and no runtime assertion mode.
- **Signed reports.** A report is portable JSON with two document digests in it. Signing it is
  the same problem as signing a receipt, and that is v0.6.
- **Postgres**, and any second store backend. `--store-url` accepts one value and names v0.6
  for the rest (§3.1).
- **New guarantees that are not deployed forms of existing acceptance tests** (§2.3). In
  particular: no guarantee about the gateway's wire behaviour, about JWT verification, about
  the OTel sink, or about anything in an extra — verify is core and stays core, and a
  guarantee it could only check with `httpx` installed is a guarantee half the deployments
  would see as N/A for a reason that has nothing to do with their configuration.
- **Framework adapters.** `research/framework-probe/` is research: it is not packaged, not
  supported, not versioned with the kernel, and not the v0.5 adapter contract (§7.1).
- **Publishing framework results** in the item that builds the harness (§7.4).
- **Any compliance, conformance, certification or alignment claim**, including mapping tables
  presented as coverage, badges, and "aligned with" language, in this document,
  `docs/OWASP-AGENTIC-TOP10.md`, `docs/verify.md`, the README, docstrings, CLI output or the
  badge (§1.4, §6.1).
- Anything in `VISION.md`.

---

## 12. What building v0.4 settled

Three readings the implementation had to take, recorded here because a specification that
disagrees with the code it describes is worse than one that admits a gap. Each is argued in a
`# SPEC:` comment where it lives; this section is the index, in the form `§9.4` takes for the
earlier contracts.

### 12.1 G6 drives a `Control` composed from the policy alone

§2.2 fixes G6's observable as `ActionDenied` with `reason == "unknown_action"`. But `v0.3 §4.3`
evaluates **authority before policy**, so under an `authority:` section an action name no grant
covers is refused by the authority axis and the policy axis is never reached: the observable
would be `AuthorityDenied(reason="no_authority")`, every time, for every configuration with
grants in it. The two sentences cannot both hold.

G6 descends from `v0.1 §7` T6, which is about the policy axis and predates the authority model,
so its scenario builds its `Control` from the policy and no `Authority`. The kernel code under
test is unchanged and the observable is the one §2.2 fixes; what is left out is a second,
independent refusal — and that refusal is not left unexercised, because G7 and G8 drive it
directly. The report carries `detail.axis = "policy"` so a reader is not left to infer it.

An alternative was considered and rejected: deriving an absent action name that some grant's
wildcard *does* cover. It works only for configurations whose grants carry wildcards, so G6
would exercise a different thing in different deployments, which is worse than exercising one
thing everywhere and saying which.

### 12.2 G7 is N/A where no action in the policy can run

§2.2's `N/A when` row for G7 reads **Never**, and §1.3 requires every guarantee to carry a
positive control. G7's control is *"the identical call inside `context()` runs"*. For a policy
in which nothing can be driven to `allow` or `approve` there is no such call, so the two
requirements cannot both hold.

T101b settles it: it requires a configuration with an empty `actions:` to leave **zero**
applicable guarantees, and G7 reported applicable would leave one. So G7 is `not_applicable`
when no action can be driven to `allow` or `approve`, with G10's reason — `every action in the
policy is denied`. It remains applicable to every configuration in which anything can run,
including every configuration with no `authority:` section, which is what §2.2's row was
protecting and what T109 asserts.

### 12.3 G8 gains one N/A reason

§2.2 lists three conditions under which G8 is N/A. A fourth exists and §2.2 does not name it,
because it does not arise in a single-grant configuration: where a **second** grant covers the
same action past the first one's expiry, the expired grant refuses nothing observable, and G8
would report a failure that is really a property of a layered document.

The reason is `no grant's expiry is the last authority for an action it covers; another grant
still permits the action after it lapses`. The engine tries every grant carrying an
`expires_at`, in id order, and reports this only when none of them is decisive.

The pre-check asks only **whether** authority still passes one microsecond after the expiry —
never **why** it stopped. Filtering on the reason would turn a kernel that denied for the wrong
cause into an N/A, and a false N/A is a false pass wearing the other costume (§9.2). A kernel
whose expiry denial reports the wrong reason therefore FAILs, which is what `v0.3 §10` T71 is
for and what T108 asserts by injection.

### 12.4 G9's control names the delegation only where it can

§2.2's control for G9 requires *"an action within the child's limits passes authority naming
the delegation"*. A child is contained in its parent on every dimension, so the parent matches
every action the child does — and `v0.3 §4.6` picks the lowest grant id among those that
passed, which is not the delegation's.

`v0.3 §5.4` leaves the concrete agent a child names unconstrained: that is what delegation is
for. So the narrowed child's subject names an agent the parent's subject does **not** match,
and the delegation becomes the only authority for the action — `AUTHORITY_RESOLVED` then
carries the `delegation_id` and the control asserts it. Where the parent's subject is a
wildcard that matches the child's agent too, the delegation cannot be isolated; the control
asserts that authority passed and the report records `detail.delegation_isolated = false`,
because asserting a grant id the model does not promise would be asserting nothing.

### 12.5 G4's children are subprocesses, not `multiprocessing`

§2.2 requires `_PROCESSES` OS processes and says why they cannot share the injected clock. It
does not say how they are started, and `multiprocessing` with the `spawn` method — the default
on macOS and Windows — **re-imports the caller's `__main__` module in every child**. A caller
who ran `ctrlrun.verify.run()` from an unguarded script would fork-bomb itself, and requiring
an `if __name__ == "__main__"` guard in the program under verification is not a trade a
verification tool gets to make.

The worker is reached as `python -m ctrlrun.verify.worker` with its payload on stdin. It is
still a module-level function taking one picklable argument, still eight OS processes, and the
executor count still comes from `O_CREAT|O_EXCL` files so it survives the process boundary —
which is the whole of what the guarantee is about.

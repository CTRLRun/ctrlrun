# CTRLRun v0.6 Specification — Durable runtime

A **delta** over `SPEC-v0.1.md`, `SPEC-v0.2.md`, `SPEC-v0.3.md`, `SPEC-v0.4.md` and
`SPEC-v0.5.md`. All five remain binding in full; nothing here relaxes one. Tests are derived
from §8. Public names are frozen in §9. Anything not in this document is out of scope for v0.6.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

v0.5 asked *can somebody else implement this?* v0.6 asks: **does it still hold when the process
dies, the host goes away, and the database is somewhere else?**

Every guarantee shipped so far is a guarantee about one process holding one SQLite file.
`BEGIN IMMEDIATE` is a whole-database write lock on a local file, and `v0.1 §5.3 E1` — *at most
one caller per effect key, across threads and processes* — has been true because of it. Take the
file away, put the store on another host, and the promise has to be re-earned with a different
mechanism. That is the milestone.

---

## 1. Scope

v0.6 delivers eight things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | The store conformance suite, `ctrlrun.conformance.store` | core, no new dependency | §2 |
| 2 | Schema version and the forward-only migration runner | core | §3 |
| 3 | `PostgresStateStore` | `ctrlrun[postgres]`, lazy | §4 |
| 4 | Cross-host concurrency and failure injection | not packaged | §4, §8 |
| 5 | Recovery on restart | core | §5 |
| 6 | Receipt integrity — the hash chain and the tamper report | core | §6 |
| 7 | Policy versioning, the control registry, the data-scope primitives | core | §7 |
| 8 | The soak | `research/soak/`, never packaged | §8 |
| 9 | Release 0.6.0 | — | — |

The dependency rule of `v0.2 §1.1`, `v0.3 §1`, `v0.4 §1` and `v0.5 §1` is unchanged and binding:
`pip install ctrlrun` MUST continue to install nothing but `pyyaml` and `click`. The Postgres
backend is the one thing here that needs a third-party driver, so it is an extra
(`ctrlrun[postgres]`), imported lazily, raising `MissingDependency` with the install line when
absent. **Everything else in this milestone is core and stdlib** — the migration runner, the
chain, the policy hash, the control registry, the store conformance suite — for `v0.4 §1`'s
reason, which has not weakened: a check somebody has to remember to install is a check that does
not run, and a *migration* somebody has to remember to install is worse than a check.

### 1.1 What a second backend is not, stated before anything else

`v0.4 §1.2` put its list of non-guarantees before its list of guarantees, and `v0.5 §1.1` did the
same. This document does it a third time, for the same reason: the list matters more than the
feature does.

- **Not a new protocol.** The `StateStore` protocol of `v0.1 §5.3` — as extended by `v0.2 §6.9`
  (`hold_continuation`, `take_continuation`, `continuation_rounds`, `extend_lease`) and
  `v0.3 §5.2` (the delegation methods) — is **frozen**. `StateStore` is one of the six contracts
  v1.0 freezes. A backend that needs a method the protocol does not have is a **specification
  amendment first**: the name goes in §9 with its argument, every backend's answer to it is
  written down, and only then is there code. This is `v0.3 §4.3.1`'s rule about entry points,
  one layer down, and it exists for the same reason — the hole that rule was written for was a
  missing enumeration, not a missing check.
- **Not a chance to add a method.** The expected number of new `StateStore` methods in v0.6 is
  **zero** (§9.2). If item 3 disagrees, item 3 stops.
- **Not a place where `FAILED` and `AMBIGUOUS` may mean something different.** `FAILED` means the
  thing definitely did not happen. `AMBIGUOUS` means nobody knows. `v0.1 §5.5` fixes the
  asymmetry and `v0.2 §6.8` already applies it to a network hop; §4 applies it to the store. A
  backend that reported `FAILED` for a write whose outcome it could not confirm would be the
  exact failure this library exists to prevent, arriving through its own storage layer.
- **Not a reason for a second suite.** A backend graded against a suite written for that backend
  has marked its own homework. §2's suite predates the backend it grades, which is why item 1
  comes before item 3.
- **Not an opportunity to relax a check.** `v0.4 §3.9` and `v0.5 §3.8` forbid a flag that makes
  the thing being tested differ from the thing that ships. **A store is under the same rule.**
  There is no `--skip-migration`, no `allow_unknown_schema`, no development mode that reserves
  more freely than production does.

### 1.2 The three rules of v0.6

Every item is measured against these.

- **The guarantee is the test, not the mechanism.** `BEGIN IMMEDIATE` and
  `INSERT … ON CONFLICT DO NOTHING` are two implementations of one promise, and the promise is
  `v0.1 §7 T3`. **One suite, every backend**, and the suite is the one that already exists (§2).
- **A schema version nobody checks is a schema version that lies.** From v0.6 a store refuses a
  database it does not recognise, in **both** directions: a newer binary migrates or refuses, and
  an **older** binary against a newer schema refuses immediately rather than reading columns it
  does not understand. The second direction is the one that gets forgotten and the one that
  corrupts (§3).
- **Receipt integrity detects tampering. It does not prove authorship.** A hash chain says the log
  was not altered after the fact; it says nothing about who wrote it. No README, changelog,
  docstring or mapping table may blur the two, and signing is not in this milestone (§6, §11).

And a fourth, inherited from `v0.1 §5.5` and sharper here than anywhere it has been: **never map
an unknown exception to `FAILED`.** Over a local file, *"the write did not happen"* and *"I could
not tell whether the write happened"* nearly coincide. Over a network they emphatically do not: a
client whose connection dies during `COMMIT` has no idea whether Postgres committed, and Postgres
very often did.

### 1.3 The distinction that makes it tractable

**The store is reconcilable by re-reading; the remote is not.**

A Postgres transaction is atomic, so an ambiguous *store write* has exactly one truth and the
store can go and look at it: re-read the record, and if it carries our `action_id` we hold the
reservation, if it is absent we retry the insert, if it is another's we are blocked. Only if the
**re-read itself** fails does the store refuse to let execution proceed, writing no effect state
and failing closed.

An ambiguous *remote effect* has no such move. No re-read settles whether the refund landed,
which is why `AMBIGUOUS` exists as a terminal state at all and why only a human (`ctrlrun
resolve`) or a reconcile hook (`v0.2 §2`) moves a record out of it.

**Two ambiguities, one word, different remedies.** §4's tables carry both paths separately with
their own T-numbers. An implementation that collapses them will look correct and will either
refuse work it could have recovered or retry work it could not.

### 1.4 What was read

Read on **2026-09-05** unless a document states otherwise.

- `v0.1 §2.3` (canonicalization and `action_hash`), `§4.2` (the approval invariants), `§5.3`
  (the `StateStore` protocol and E1–E3), `§5.5` (the outcome asymmetry), `§7`, `§8`;
  `v0.2 §2` (the reconcile hook), `§3.3`, `§6.8` (the wire's outcome table), `§6.9`
  (continuations), `§10`, `§11`; `v0.3 §4.3.1`, `§4.5` (reserved condition names), `§5.2`,
  `§5.6`, `§10`, `§11`, `§12`; `v0.4 §1.2`, `§2.2`, `§3.1`, `§3.5`, `§3.8`, `§3.9`, `§4`, `§9`,
  `§12`; `v0.5 §5` (the adapter conformance kit, whose shape §2 reuses one layer down), `§9`,
  `§12`.
- `src/ctrlrun/state.py` **end to end** — the whole milestone is a delta on that file's
  guarantees — plus `effect.py`'s `plan_reservation` / `plan_lease_extension`, `approval.py`'s
  `ApprovalStore` and `check_consumable`, and `control.py`'s `_secure` / `_take` / `_presented`.
- `docs/ROADMAP.md` (v0.6, and the sector-packs track), `docs/ARCHITECTURE.md` §4.6, §5, §6,
  `docs/THREAT_MODEL.md`.

**Two documents are corrected in the same commit as this one**, on the rule `v0.4 §9.4` set and
`v0.5 §9` followed — a correction recorded in the specification is findable; one made only in a
diff is not:

- `docs/ROADMAP.md`'s v0.6 bullet reads *"receipt integrity (hash chain / signatures)"*. Signing
  is **out of scope** (§11) and the slash is exactly the blur the third rule forbids. It becomes
  *"receipt integrity (hash chain — alteration, not authorship)"*.
- `docs/THREAT_MODEL.md`'s v0.1 limitation reads *"Receipts are not signed; a database admin can
  alter history (v0.6)"*. Half of that stays true after v0.6 and half does not: receipts are
  still not signed, and a database admin with write access to **every** row including the chain
  head can still rewrite history. The line is rewritten to say which half v0.6 closes (§6.4).

**No compliance, conformance, certification or alignment claim** is made in this document. "Store
conformance suite" names a suite of this repository's own acceptance tests, run against a
`StateStore` implementation, and §2.1 says so on its first line.

---

## 2. The store conformance suite

### 2.1 What it is, and what it is not

`ctrlrun.conformance.store` runs **this repository's own acceptance tests** — the cases of
`v0.1 §7`, `v0.2 §10` and `v0.3 §10` that are statements about `StateStore` rather than about
`Control` — against any backend. It is not a conformance programme, it certifies nothing, and
passing it is not a claim about a backend's quality. It answers one question: *does this backend
refuse what SQLite refuses, in the same way, for the same reason?*

This is `v0.5 §5`'s shape applied one layer down and its argument transfers unchanged. It ships
in **core**, needing nothing `pip install ctrlrun` does not already install: the suites construct
records, call methods and compare exceptions, which is `assert` and `try`. A backend author runs
the report inside their own pytest — `assert run(backend).ok` — rather than through one.
`import ctrlrun` MUST NOT import `ctrlrun.conformance` (T134 already asserts it, and T140f
extends the assertion to the store subpackage).

### 2.2 What a backend hands the suite

```python
# ctrlrun/conformance/store/backends.py — core, stdlib

class StoreBackend(Protocol):
    """A live backend the suite may open, reopen and describe to a subprocess."""

    name: str

    def open(self) -> StateStore:
        """A store on this backend's storage. The first call creates it empty."""

    def reopen(self) -> StateStore:
        """A second, independent store on the SAME storage as `open()`.

        Two handles, not one shared object: `durability` is about what survives a process
        losing its handle, and a backend that returned the same object would pass it by
        holding the answer in memory.
        """

    def url(self) -> str | None:
        """An address the conformance *worker subprocess* can open this storage by, or `None`.

        Deliberately **not** described as a `--store-url` value: that flag is verify's, it
        names a database an operator owns, and §4.1 has the rules about what may be done to
        one. This string is addressed to `python -m ctrlrun.conformance.store.worker` and to
        nothing else.

        `None` means the storage cannot be reached from another OS process, which is a
        property of the backend and not of the harness — `InMemoryStateStore` says so in its
        own docstring. `reservation/e1-cross-process` is then `not_applicable` with that
        reason, and §2.6's `falsely-declares-no-url` is what keeps the declaration honest.
        """

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        """A store on this backend's storage, reading time from `clock`.

        The suite's **only** seam, and one every shipped store already takes. `v0.1 §5.3 E3`
        is about a lease expiring and `v0.4 §3.6`'s rule -- no sleeps, anywhere -- applies
        here in full. It is not a relaxation under §1.1: nothing about what the store
        *refuses* changes, only what time it thinks it is.
        """

    def reset(self) -> None:
        """Discard everything. Called between cases; the suite never reuses state."""
```

**`open_with_clock` is five methods, not four**, and an earlier draft of this block listed only
four while the code required all five — so a backend written to this section verbatim failed ten
cases. It is declared here because the paragraph below already argued for it; what was missing
was the line.

The suite is driven by `run(backend, *, only=(), processes=8) -> StoreReport`. It takes **no
fault-injection hook, no clock override that the backend does not already accept, and no flag
that skips a case**. The one seam it has is the injected clock every store already takes
(`SQLiteStateStore(clock=...)`, `InMemoryStateStore(clock=...)`), because `v0.1 §5.3 E3` is about
a lease expiring and `v0.4 §3.6`'s rule — *no sleeps, anywhere* — applies here in full.

### 2.3 Which acceptance tests are statements about `StateStore`

The distinction is not cosmetic. A case is in this suite when its subject is a store method's
behaviour; it stays where it is when its subject is `Control`'s composition of them.

| Suite | Sourced from | What it drives |
|---|---|---|
| `reservation` | `v0.1 §7` T1, T3, T8, T9 | E1 **in one process** and E1 **across processes** (§2.4) · the §5.4 retry table, every row · a `FAILED` record renews with `attempt + 1` · an expired lease becomes `AMBIGUOUS` and is never released |
| `approval` | `v0.1 §7` T2, T4, T5, T12 | `consume_approval` refuses a different `action_hash`, a consumed one, an expired one · a refused reservation leaves the approval `granted` · the approval is checked first when both would refuse |
| `resolution` | `v0.1 §7` T10 | only an `AMBIGUOUS` record resolves, and only to `COMMITTED` or `FAILED` |
| `outcome` | `v0.1 §7` T1, and `v0.1 §5.5` | **no store method writes `FAILED` on a refusal path, and no store method raises `NotExecuted`** (§2.5) |
| `durability` | new; the statement `v0.1 §5.2` makes and no test drives | every terminal record — `AMBIGUOUS` above all — survives a `reopen()`, and a blind retry is still refused after one. `not_applicable` where `reopen()` returns `None` (§2.4) |
| `evidence` | `v0.1 §7` T7 · `v0.3 §10` T60c | an `Action` round-trips through the store byte-identically: same `action_hash`, same argument **types**, principal claims, issuer and expiry intact · `append_event` returns the event with the id it stored · receipts round-trip |
| `continuation` | `v0.2 §10` T26, T26b | a reservation held across a round trip · one suspension admits exactly one resumption · `extend_lease` refuses an expired lease, another action's record, and a non-`EXECUTING` one |
| `delegation` | `v0.3 §10` T75b, T78 · `v0.3 §5.2` | `put_delegation` inserts and never upserts · `revoke_delegation` is atomic, returns `False` when already revoked, and raises `InvalidArgument` on an unknown id · `grant_json` round-trips with its UTC offset retained |

**And which are deliberately absent**, stated rather than left to be noticed:

- `v0.1 §7` T6 (unknown action), T7's *hash* half, T11 (the demo) are about `policy.py`,
  `action.py` and the CLI. A store has no opinion on any of them.
- `v0.3 §10`'s authority cases — T66 through T81 — are about `authority.py` and `policy.py`.
  What a store owes them is the `delegations` row and the `grant_json` round trip, which is
  `delegation` above; the containment walk is not a store statement and driving it here would
  test `authority.py` twice.
- **`v0.2` is in the table and the brief listed only `v0.1 §7` and `v0.3 §10`.** The protocol is
  frozen as a whole (§1.1), and `hold_continuation` / `take_continuation` /
  `continuation_rounds` / `extend_lease` are on it. A suite covering two-thirds of the protocol
  grades two-thirds of a backend, and the third it skips is the one holding a reservation open
  across a round trip the kernel does not control — which is precisely where a second host
  changes the picture (§5.4). The addition is recorded here rather than made silently.

### 2.4 E1 twice, and the two honest N/As

**E1 is driven as two cases, and the reason is that a broken-store fixture can only fail one of
them.** This was found by review, and it is `v0.5 §5.4`'s finding one layer down: a suite no
fixture can fail reports `pass` for every backend ever written.

- **`reservation/e1-in-process`** — the deterministic one. N threads contend for one key against
  one store object, released together from a barrier so every contender is running when it calls.
  Exactly one reservation is granted and N−1 are refused with `v0.1 §5.4`'s errors.
  **Applicable to every backend, and never N/A.**

  **It is not a race test, and §2.7 says why the stronger wording was withdrawn.** The barrier
  cannot be released *between* the store's read and its write — that is inside `reserve_effect`,
  and getting there needs a hook §2.5 refuses to add. What this case establishes is that **the
  store serializes at all**.

  **It repeats the contention `ROUNDS` times, and the number is not decoration.** A store with
  *no* check is caught in one round every time. The realistic bug — a **check-then-act**
  `reserve_effect` that reads the record and then inserts without a lock — was measured at
  **8 catches in 20 runs** with a single round: `reservation` would have been a flaky grade for
  the store shape it most needs to catch, and CI would eventually have shown a red that was not a
  regression. Twelve rounds puts the miss below one in a hundred thousand and still finishes in
  well under a second; the same store is now caught 20 times in 20.
- **`reservation/e1-cross-process`** — `v0.1 §7` T3's standard unchanged: **N contenders, one
  winner**, the fake remote called exactly once, one committed record and N−1 refused. It runs in
  `processes` OS processes started as `python -m ctrlrun.conformance.store.worker` with the
  backend's `url()` and the payload on stdin — subprocesses rather than `multiprocessing`, for
  `v0.4 §12.5`'s reason: `spawn` re-imports the caller's `__main__`, and requiring an
  `if __name__ == "__main__"` guard in a backend author's test file is not a trade a conformance
  suite gets to make.

**Why both.** A broken-store fixture is an in-process wrapper around a real backend. A subprocess
opening that backend's `url()` gets the *correct* store, not the fixture — so `two-winners` and
`releases-an-expired-lease` could never fail the cross-process case, and T140, the only check
that `reservation` can fail at all, could not pass. The in-process case is what a fixture can
fail; the cross-process case is what a *backend* can fail, and only it distinguishes a store whose
atomicity is a Python lock from one whose atomicity is the database's. Neither alone is the
guarantee.

**Two N/As are legitimate, and they are the only two**, each a property of the backend rather
than of the harness — which is the whole test of an honest N/A:

| Case | N/A when | Reason reported |
|---|---|---|
| `reservation/e1-cross-process` | `url()` is `None` | `this backend's storage cannot be opened from another process` |
| `durability` (every case) | `reopen()` is `None` | `this backend's storage does not outlive the object that holds it` |

Both describe `InMemoryStateStore`, which says so in its own docstring — *"Nothing here survives
the process, and nothing here is shared between processes"* — and neither is available to a
backend that has storage and simply did not implement the hook: `url()` and `reopen()` returning
`None` is a **declaration**, and §2.6's fixtures include one that declares falsely.

`v0.4 §3.8`'s rule holds in full: **not applicable is not a pass**, the denominator counts
applicable **cases** — not suites — and there is no flag that folds one into the count. A backend
reporting any *third* N/A has failed.

**Cases and not suites, and the difference is not cosmetic.** An N/A case inside a suite that
otherwise passes is invisible to a suite-level fraction: the in-memory backend reported
`7/7 (1 not applicable)` while `e1-cross-process` sat N/A inside a `PASS` suite, which is
`v0.4 §3.8`'s *"6/6 with two uncounted"* wearing the other costume. It now reports `20/20 (3 not
applicable)`. `StoreReport.ok` is case-level for the same reason.

**What the honesty check can and cannot establish, stated rather than implied.** It opens two
independent handles, writes through one and reads through the other. That catches a backend whose
storage is plainly shared — which is what `falsely-declares-no-url` is. It does **not** catch a
backend whose `open()` returns a *fresh, isolated, durable* store each call: to the probe that is
indistinguishable from storage confined to the object, and a Postgres backend that made a private
schema per `open()` would have exactly that shape. No in-process test can separate the two, because
the only means the protocol offers are `open()` and `reopen()` and both come back empty either way.
The residual is recorded here rather than left for a reader to assume the declaration is proved.
The check that *is* airtight is the one on the other side: where `url()` is **not** `None`,
`e1-cross-process` opens the storage from eight real subprocesses and the declaration is tested by
use rather than by assertion.

### 2.5 `outcome`, and why it needs no fault injection

The `outcome` suite is the store-layer expression of `v0.1 §5.5`'s asymmetry, and it is the one
suite whose subject is a *negative*: what a store must never do.

- **No `StateStore` method raises `ctrlrun.NotExecuted`.** `NotExecuted` is the executor's opt-in
  to `FAILED` and the one exception an agent may read as permission to retry. A store that raised
  it would be asserting something about a remote it has never spoken to.
- **No `StateStore` method writes `FAILED` to an effect record on a refusal path.** Two methods
  write `FAILED` and both do it as their *purpose*: `fail_effect`, which records that the
  executor proved nothing happened (`v0.1 §5.5`), and `resolve_effect`, which is
  `ctrlrun resolve --failed` — a human answering what the executor could not, and the only route
  out of `AMBIGUOUS` (`v0.1 §5.2`, §7 T10). **Neither is a refusal path.** What the suite drives
  is every *refusal*: a duplicate reservation, a consumed approval, a transition from the wrong
  state, a lease that lapsed, a broken continuation, an unknown delegation id — and after each
  the record is read back and MUST NOT be `FAILED`.

  The earlier wording of this bullet said "except `fail_effect`", which was false of both shipped
  backends: it would have failed `resolve_effect` and, worse, invited an implementer to "fix" the
  one transition that lets a human release an ambiguous effect. It is recorded rather than
  quietly corrected, because a suite that fails a correct backend and a suite that passes a
  broken one are the same defect facing opposite ways.
- **`Control` never maps a store exception through `v0.1 §5.5`'s table.** That table is about the
  *executor*. Applying it to the store would let a storage failure look like a proven
  non-execution, which is the shape of a double refund. This is asserted at the `Control` layer
  (T154d) as well as here, because the store cannot enforce it alone.

The suite therefore needs no fault-injection hook, which matters: a hook would be a seam in
shipping code that exists only for a test, and §1.1's last bullet forbids one. §4's re-read cases
— which *do* need a broken connection — are item 4's, run against a real Postgres under real
failure injection, and are `not_applicable` for a backend with no connection to break, with the
reason **`this backend's commit outcome is observable in the same call`**.

### 2.6 The broken-store fixtures, and why they are written first

A suite that only ever passes is a suite nothing exercises. The suite's own tests drive it against
stores that are broken **in one named way each**, and each MUST fail the suite named for it, **by
name** (T140).

| Fixture | What it does wrong | Fails |
|---|---|---|
| `two-winners` | Drops the uniqueness check: both contenders reserve one key | `reservation` — the **in-process** case (§2.4) |
| `releases-an-expired-lease` | Treats a lease-expired `RESERVED` record as free and re-reserves it instead of moving it to `AMBIGUOUS` | `reservation` |
| `grants-twice` | `consume_approval` returns the `Approval` and leaves the record `granted` | `approval` |
| `consumes-before-reserving` | Sequences `consume_approval` then `reserve_effect` in two transactions, so a refused reservation has already spent the approval | `approval` — T12's case |
| `resolves-anything` | `resolve_effect` moves a `COMMITTED` record | `resolution` |
| `guesses-failed` | Writes `FAILED` when a write refuses, rather than leaving the record alone | `outcome` |
| `raises-not-executed` | Raises `NotExecuted` from `commit_effect` when the write refuses | `outcome` |
| `forgets-the-unknown` | Holds `AMBIGUOUS` in memory and loses it across a `reopen()` | `durability` |
| `coerces-an-argument` | Truncates a string argument on the way back out, so the stored `Action` no longer hashes to what was approved | `evidence` |
| `renumbers-events` | `append_event` returns an event carrying an id other than the one it stored | `evidence` |
| `admits-two-resumptions` | `take_continuation` does not consume the token in the transaction that admits it | `continuation` |
| `upserts-a-delegation` | `put_delegation` upserts on a duplicate id, clearing `revoked_at` — unrevoking by another door | `delegation` |
| `refuses-with-the-wrong-error` | Refuses a duplicate reservation with a plain `RuntimeError` | `reservation` — the **taxonomy**, which an agent loop's `except DuplicateEffect` depends on |
| `falsely-declares-no-url` | Has durable, shareable storage and returns `None` from `url()` and `reopen()`, so `e1-cross-process` and `durability` report `not_applicable` and it scores full marks | `reservation` and `durability` — **the declarations**, caught by two handles seeing each other's writes |

**Every suite is named by at least one fixture, and every fixture names a suite that exists.**
Both directions are asserted (T140), on `v0.5 §5.4`'s finding: a fixture pointed at a renamed
suite passes its own test by never being checked against anything, and a suite no fixture fails
would report `pass` for every backend ever written.

`guesses-failed` and `raises-not-executed` are two fixtures for one suite because `outcome` has
two checks and a single fixture would leave one subsumed — `v0.5 §5.4`'s `denial` finding, one
layer down. `releases-an-expired-lease` exists because `two-winners` fails `reservation`
incidentally on the lease case too, and a check whose only fixture reaches it by accident is a
check nothing is aimed at.

**`falsely-declares-no-url` exists because §2.4's two N/As are the only declarations on this
surface, and a declaration that turns a suite off is a setting that relaxes a check** — which
§1.1's last bullet forbids by name. It is `v0.5 §5.4`'s `falsely-refuses-before-invoking` one
layer down, and the discriminator is the same shape: the kit does not take the declaration on
trust, it **tries the thing the declaration says is impossible**. Where `url()` is `None` the kit
attempts to open the backend from a subprocess by every other means the `StoreBackend` exposes;
where `reopen()` is `None` it asks for a second `open()` and checks whether the two see each
other's writes. A backend whose storage genuinely does not outlive its object fails both attempts,
which is what makes the N/A honest; one that quietly has a file or a socket is caught.

**The fourteen fixtures are the floor and not the ceiling.** T140 requires every suite to be
named by at least one fixture and every fixture to name a suite that exists, in both directions —
because a fixture pointed at a renamed suite passes its own test by never being checked against
anything. Each fixture also carries the **reason** its case must fail with, and T140 asserts it:
a fixture pinned only to the case is a subsumed guard the moment that case grows a second check.

**Two of `e1-cross-process`'s checks have no fixture and cannot have one**, and saying so is the
alternative to a mutation table that reads green for nothing. The fake-remote count and the
winner's error are asserted about **subprocesses**, and a fixture is an in-process wrapper: a
child opening the backend's `url()` gets the real store, never the broken one. That asymmetry is
the same one §2.4 relies on to make the two E1 cases non-redundant, and it cuts both ways. The
in-process case is where a fixture bites, which is why `two-winners` and
`refuses-with-the-wrong-error` are aimed there.

Each fixture fails **the suite named in its row**. Several also fail others incidentally, and the
assertion is on the named suite rather than on an exclusive one. What T140 forbids is a fixture
that fails *nothing* and a fixture whose named suite passed.

These are written **before** any Postgres exists (build-list order: item 1 precedes item 3),
because "the Postgres backend passes the suite" is this milestone's central claim and it means
nothing until the suite can fail.

### 2.7 What the suite is expected to find on its first run

**A suite that passes on both existing backends on its first run is a suite that is not asking
anything.** `InMemoryStateStore` and `SQLiteStateStore` have never been held to one standard.
Every divergence is either a bug in one of them or a place the protocol was never specified, and
**both are item 1's deliverable**: the bugs are fixed, and the unspecified cases are written into
this section with what they now say. If item 1 finds nothing, the finding is about the suite.

One such case is already known, before item 1 runs, and is specified here so the suite has an
answer to assert:

**`close()` is a release of resources, not a fence.** `SQLiteStateStore.close()` closes every
connection it opened and a thread that uses the store afterwards gets a fresh one;
`InMemoryStateStore.close()` releases nothing because it holds nothing. Both are therefore usable
after `close()`, and that is the specified behaviour: `close()` MUST release what the store holds
open and MUST NOT make the store refuse later calls. A backend for which reopening is expensive
may reconnect lazily; none may raise. The alternative — `close()` as a fence — would have made
`close()` the fault-injection hook §2.5 refuses to add, and would have made the shipped stores
wrong rather than the suite.

**Three more were found by writing the suite, and each is specified here with the case that pins
it:**

**§2.4's barrier could not open the window it described.** An earlier draft said the in-process
contenders are *"released after every one of them has read the record and before any has
written"*. There is no way to do that: reading and writing happen **inside** `reserve_effect`,
and getting between them would need a hook in shipping code, which §2.5 refuses to add. What the
suite actually controls is that every contender is *running and released simultaneously* when it
calls, which is the strongest deterministic statement available from outside the store. The
weaker claim is the true one and is what T143 asserts.

**`extend_lease`'s refusal taxonomy was never written down.** §2.3's row said it *"refuses an
expired lease, another action's record, and a non-`EXECUTING` one"* without saying with what, and
three different exceptions would each have satisfied that sentence. The suite pins the shipped
behaviour, which is now the specification:

| What `extend_lease` meets | Raises |
|---|---|
| A `RESERVED` record, held by this very action | `DuplicateEffect` |
| An `EXECUTING` record held by another action | `DuplicateEffect` |
| An `EXECUTING` record whose lease has already lapsed | **`AmbiguousEffect`** |

The third is the one worth stating: an expired lease is extendable by nothing and an expired
reservation is released by nobody, so the refusal names the **unknown** rather than a live holder
(`v0.2 §6.9.4`). A backend that raised `DuplicateEffect` there would be asserting somebody else
holds the key, when what is true is that nobody knows.

**A backend must isolate its own storage.** Two `SQLiteBackend`s handed the same root directory
shared one database file, so a test comparing two fixtures saw the first fixture's rows through
the second's store — and reported a failure attributed to the wrong fixture, which is the worst
kind because it reads as a finding. Each backend now takes a private subdirectory. The general
rule for a backend author: **`open()` must not be able to observe another backend instance's
writes**, whatever root it was handed.

### 2.7.1 What the suite did *not* find, and why that is the result

**`SQLiteStateStore` and `InMemoryStateStore` do not diverge.** Thirty-seven differential probes
— refusal taxonomy on every method, `list_effects` and `events()` and `receipts()` and
`delegations()` ordering, the ids `append_event` assigns, result round-tripping including `None`
and an unencodable object, `created_at` preserved across a `FAILED` retry, and eight threads on
one key — produce byte-identical outcomes from both.

§2.7 says a suite that passes on both backends on its first run is a suite that is not asking
anything, and that if item 1 finds nothing the finding is about the suite. **That reading is
wrong here, and the reason is worth recording rather than explaining away.** The two stores agree
because they were built to: `plan_reservation`, `plan_lease_extension`, `check_consumable` and
`check_answerable` are pure functions in `effect.py` and `approval.py`, and *both stores decide
with them and then only write*. There is no second copy of `v0.1 §5.4`'s retry table to drift.

Which relocates what this suite is for. It is **not** primarily a difference-detector between the
two backends that exist; against those it is a regression guard on a property the architecture
already provides. It is the thing that will grade **Postgres**, which reuses those same pure
functions but replaces every *write* path — `BEGIN IMMEDIATE` for compare-and-set, one
transaction for another — and where §4.3's two re-read tables have no shared implementation to
inherit correctness from.

**The zero is only worth reporting because the probe has a positive control.** Run against a
store that quietly reverses `events()`, `list_effects()` and `receipts()`, it reports three
divergences; without that, "no divergences" would be indistinguishable from a probe that compares
nothing. `v0.4 §1.3`'s rule applies to a measurement as much as to a guarantee.

### 2.7.2 The protocol was short by two methods

The largest finding of item 1 is not a divergence between the backends but a gap in what they
were being graded against: **`events()` and `receipts()` are not on the `StateStore` protocol**,
and have never been, while both shipped stores implement them and four callers depend on them.

It surfaced the way such things do — the suite's `evidence` cases are written against
`StateStore`, so type-checking them asked the protocol for a method it does not declare. A
backend author reading `v0.1 §5.3` and implementing exactly what it lists would produce a store
that satisfies every declared method and breaks `ctrlrun receipts`, `ctrlrun inspect`,
`ctrlrun verify` and the v0.5 adapter kit.

§9.2 carries the amendment and its argument. It is the clearest possible vindication of item 1
coming before item 3: had Postgres been written first, this would have been found by an operator
running `ctrlrun inspect` against it.

Where a later item finds others, each is added to this section with the same shape: the
divergence, the decision, and the case number that pins it.

---

## 3. Schema version and migrations

Six places across `v0.2`, `v0.3`, `v0.4` and `v0.5` say *"there is still no migration story —
that is v0.6."* This is it, and it comes **before** Postgres, because adding a second schema
before the versioning exists means two schemas drifting with no marker to tell you.

### 3.1 The record

A new table, in every backend:

```sql
CREATE TABLE IF NOT EXISTS schema_version(
  migration_id    TEXT PRIMARY KEY,   -- "0001_baseline", "0002_receipt_chain", …
  applied_at      TEXT NOT NULL,      -- ISO-8601, UTC
  ctrlrun_version TEXT NOT NULL       -- the distribution version that applied it
);
```

**The version is recorded, never inferred.** A store MUST NOT decide what version a database is
by looking for a column. `PRAGMA table_info` and `information_schema` answer *what is there*,
which is not the same question as *what has been applied*, and the two diverge the moment a
migration does anything a column list cannot show — a backfill, a constraint, a data repair.

A migration id is `NNNN_snake_name`: four digits, zero-padded, so lexicographic order is
application order. The **known set** is an ordered tuple compiled into the binary. `head` is its
last element.

### 3.2 Adoption: what a pre-v0.6 database is

A database opened by a v0.6 binary is classified once, before anything is written:

| What is there | Classification | What happens |
|---|---|---|
| No `schema_version`, and no `effects` table | **empty** | every known migration is applied in order, and each is recorded |
| No `schema_version`, and an `effects` table | **baseline** — a v0.1–v0.5 database | `0001_baseline`'s DDL is **run**, then recorded; every later migration is then applied in order |
| `schema_version` present | **versioned** | §3.3 |

**`0001_baseline`'s DDL runs on the baseline path, and skipping it would be a defect.** An
earlier draft of this section said it should be *recorded without running*, on the ground that
its tables already exist. They do not. "Baseline" matches a v0.1, v0.2, v0.3, v0.4 **or** v0.5
database equally — all five have an `effects` table and no `schema_version` — and the tables
differ: `continuations` and its unique index arrived in v0.2, `delegations` and its index in
v0.3. Both reached databases that already existed **precisely because** the store runs its whole
schema script on every open, which is the mechanism `v0.3 §5.2` names when it says a new table
*"is what `CREATE TABLE IF NOT EXISTS` handles on a database that already exists"*. Skipping the
DDL would leave a v0.1 or v0.2 database with no `continuations` and no `delegations` table, and
every continuation and delegation call would then die on `no such table`.

So `0001_baseline` **is** the `_SCHEMA` script v0.5 shipped, `CREATE TABLE IF NOT EXISTS`
throughout, and idempotence is the reason it is safe to run rather than a reason to skip it. The
distinction the classification draws is about *which migrations run afterwards*, not about
whether the baseline does.

This is recorded rather than silently corrected because §3.5 item 2 asks a migration to be proved
against *the previous release's own code*, and the singular is what hid the defect: proving
`0001_baseline` against a v0.5 database proves nothing about a v0.2 one. **T152 therefore builds
its fixtures from v0.1, v0.2, v0.3 and v0.5**, and the plural is the point.

**A database with neither `schema_version` nor `effects` but with other tables present is
refused**, naming what it found. It is somebody else's database, and creating CTRLRun's tables in
it is not a recovery.

### 3.3 Refusal, in both directions

Let `known` be the binary's ordered migration ids and `applied` be the set read from
`schema_version`.

The classification is over the **whole shape** of `applied`, and every shape has an answer.
Leaving one undefined is how a store ends up guessing, and the natural guess here is fail-open:

- **Up to date.** `applied == known`: nothing is applied, the store opens.
- **Forward — a newer binary, an older database.** `applied` is a **proper prefix** of `known` —
  every recorded id is known, and they are exactly the first *n* of them, in order, with no gap:
  apply the rest in order, recording each. This is the ordinary case and it happens
  **automatically, at open**, with no flag to suppress it (§3.6).
- **Gapped.** Every recorded id is known, but they are **not a prefix** — `0001` and `0003`
  recorded, `0002` not. **Refuse**, naming the gap. This is not the forward case and MUST NOT be
  treated as one: applying `0002` to a database `0003` has already run against runs a migration
  out of order, against a schema it was never written for. It is the shape an interrupted
  hand-repair leaves, and the one an earlier draft of this section left undefined.
- **Backward — an older binary, a newer database.** `applied` contains an id that is not in
  `known`: **refuse at open**, before reading any row from any other table. The message names the
  unknown migration id, the binary's `ctrlrun` version, and the version recorded in
  `ctrlrun_version` for that migration, so the operator is told *which build wrote this*.
  `SchemaMismatch` (§9.3).

  **This is the direction that gets forgotten and the one that corrupts.** A binary that reads a
  table it half understands does not fail; it succeeds, with the columns it knows, silently
  dropping the ones it does not — and a `receipts` row whose chain fields it never wrote is a
  gap in the chain that nobody caused deliberately. Refusing to start is the cheap failure.
- **Divergent.** `applied` is missing an id in `known` **and** contains one that is not:
  refuse, naming both sides. The store MUST NOT "fill the gap" by applying what is missing. Two
  build lineages have written to this database and neither is authoritative.

**Those five shapes are exhaustive** — up to date, proper prefix, gapped, unknown id, both — and
a store MUST classify into exactly one. Anything an implementation cannot place is refused, not
opened: a sixth shape nobody thought of is a database nobody has reasoned about.

Every refusal is at **open**. A `StateStore` constructor that classified a database and then
proceeded to serve reads while refusing writes would be a store in a state no caller can reason
about.

### 3.4 What half-applies, and why nothing does

Each migration runs in **one transaction**. SQLite and Postgres both have transactional DDL, so a
migration that raises part way **rolls back to the old version by construction** — including the
`INSERT` into `schema_version`, which is inside the same transaction as the DDL it records.

This is a claim about the database engines, and a claim is worth nothing until something takes it
away, so item 2 **ships a deliberately broken migration in the test suite** — one whose second
statement raises — and proves the store refuses to open, the database is still at the old
version, and every row is intact (T149). It is not proved by argument.

Two consequences follow and are stated rather than left implicit:

- **A migration MUST NOT do anything outside that transaction.** No file writes, no network, no
  `VACUUM`, no `CREATE INDEX CONCURRENTLY`. A step that cannot be rolled back is not a migration
  step; it is a second migration, applied and recorded separately.
- **A migration MUST NOT depend on the binary's Python objects.** It reads and writes columns.
  A migration that called `Receipt.from_dict` would break the moment `Receipt` changed, which is
  the same afternoon.

### 3.5 What a migration must prove before it ships

Six things, and each is a test:

1. It runs in one transaction, and a failure part way leaves the database at its old version
   (T149).
2. It is proved against a database **built by the previous release's own code**, not by a fixture
   somebody wrote by hand (T147). A hand-written fixture asserts what its author believed the old
   schema was; a database built by v0.5's code asserts what it actually was.
3. **Every row survives**, asserted by content and not only by count (T147). A migration that
   dropped and recreated a table would pass a count.
4. An **older** binary refuses the migrated database, naming the version (T148).
5. Re-opening does not re-run it (T150).
6. A database at an **unknown** version is refused, not guessed at (T151).

### 3.6 Forward-only, automatic, and not configurable

**Forward-only.** There is no downgrade. A downgrade that has never been run against a real
database is a button that does not work, and it will be found out on the worst day. The
supported way back is the backup taken before the upgrade, which is a thing operators already
have and already test.

**Automatic at open**, because the alternative is a deployment where the binary and the schema
disagree for as long as it takes somebody to run a command, and `v0.4 §3.9`'s rule — the thing
being verified must be the thing that ships — has a storage-shaped corollary: **there is no flag
that opens a database without migrating it.** A store that could run un-migrated is a second
configuration nobody tested.

Two consequences the operator must be told about, and `docs/postgres.md` (item 9) says both:

- **The database user needs DDL rights** on the CTRLRun schema, at least on the first start after
  an upgrade. Where it does not have them, the migration fails and the store refuses to open,
  naming the missing privilege rather than the SQL that failed.
- **Concurrent starts are safe and are not clever.** The migration transaction takes the
  backend's own write lock, so a second process either finds the work done or waits for it. There
  is no advisory lock, no leader election, and no retry loop; §4's argument against session-scoped
  locks applies here too.

`ctrlrun verify` is unaffected: it never opens the operator's store (`v0.4 §3.5`), so it never
migrates one.

### 3.7 The migrations v0.6 itself ships

| Id | Adds | For |
|---|---|---|
| `0001_baseline` | the v0.5 tables, unchanged | §3.2 |
| `0002_receipt_chain` | `receipts.seq`, `receipts.prev_hash`, `receipts.hash`; the `receipt_chain` head row, inserted at `seq = 0` with the genesis hash | §6 |
| `0003_resolved_by` | `effects.resolved_by` | §5.3 |
| `0004_policy_provenance` | `approvals.policy_hash_at_approval` | §7.1 |

**Only a field the store *queries* becomes a column.** A receipt's other new fields —
`policy_hash`, `policy_version`, `controls` — live in the `json` column `receipts` already has,
because `Receipt.from_dict` reads them from there and nothing selects on them. `seq`,
`prev_hash` and `hash` are columns because the chain walk selects on them, and
`approvals.policy_hash_at_approval` is one because `ApprovalRecord` is rebuilt from columns
(`v0.3 §5.2`'s rule that a store persists rows rather than objects, unchanged). A migration
that added a column for every new field would be adding schema for the pleasure of it, and
every one of them is a column some later release has to keep.

**This is the first time this project adds a column to an existing table.** `v0.2 §2.2` refused
to, `v0.3 §5.2` added a new table specifically to avoid it, and `v0.4 §9.4` added neither. Doing
it here is the point of the item, and doing it *first* — before Postgres introduces a second
schema — is the ordering the build list argues for.

**The head starts at `seq = 0` carrying the genesis hash** — `"sha256:" + "00" * 32` — so the
first chained receipt is `seq = 1` with that value as its `prev_hash`, whether the database was
empty or already held a thousand unchained receipts. A `head_mismatch` (§6.5) MUST NOT fire on a
database whose only receipts are `unchained`: a head at `seq = 0` and no chained receipt is a
consistent chain of length zero, not a truncated one.

Pre-chain receipts keep `seq`, `prev_hash` and `hash` `NULL`. `0002` does **not** backfill them:
a chain computed over rows that were written before the chain existed would assert an integrity
property nobody can have. §6.5 says what a reader does with them, and the answer is never "pass".

---

## 4. The Postgres backend

The most important section in this document.

### 4.1 What it is

`PostgresStateStore`, in `src/ctrlrun/postgres.py`, implementing the frozen `v0.1 §5.3` protocol
and extending it by **nothing**. `ctrlrun[postgres]`, lazily imported, raising `MissingDependency`
with the install line when the driver is absent. `import ctrlrun` MUST NOT import any `psycopg`
module, asserted in a subprocess beside T30, T92, T125b and T134 (T153).

`--store-url` gains its second accepted value. `v0.4 §3.1` reserved the flag and exits **2**
naming v0.6 for anything but SQLite; that message and its test change here, and the flag now
accepts a `postgresql://` URL as well.

**And that flag is on exactly one command — `ctrlrun verify` — which makes it a hazard, not a
convenience.** `v0.4 §3.5` builds one scratch store per guarantee *on the backend `--store-url`
names*, and `v0.4 §3.1` promises verify *"does not open the operator's store"*. A
`postgresql://` URL names a database an operator owns. Left unqualified, verify would open it,
**migrate it automatically with no flag to prevent that** (§3.6), and create and drop scratch
tables inside it — contradicting §3.6's own sentence and §6.6's, and defeating `v0.4`'s T103,
which hashes `.ctrlrun/state.db` before and after and has no Postgres analogue.

So the scratch store is defined rather than left to be discovered:

- **Verify creates its own schema** — `ctrlrun_verify_<16 hex>` — for each guarantee, creates the
  CTRLRun tables inside it at `head`, and **drops it** when the run ends, including when the run
  ends by exception. That is what `PostgresStateStore(url, schema=…)` (§9.1) is for.
- **It never touches any other schema.** It does not migrate `public`, does not read it, and does
  not create anything in it. A migration inside a schema verify created a moment ago is verify's
  own and is not the operator's database changing.
- **It refuses a URL it cannot do that with**: no privilege to `CREATE SCHEMA`, or a
  `search_path` that already names a CTRLRun schema. Exit **2**, naming the reason — `v0.4 §3.8`'s
  treatment for a configuration verify will not run against, never a silent fallback to `public`.
- **T154e asserts all three**, including that a CTRLRun database in `public` is byte-identical
  before and after a verify run — T103's guarantee, carried across to the backend that made it
  hard.

**A second, quieter conflation is removed with it.** `StoreBackend.url()` (§2.2) is **not** a
`--store-url` value and must not be described as one: it is an argument to
`python -m ctrlrun.conformance.store.worker`, addressed to a conformance subprocess, and the two
have different rules about whose database they may touch. One flag meaning two things is how the
hazard above got written in the first place.

The decision-making stays where it is. `plan_reservation`, `plan_lease_extension` and
`check_consumable` are pure functions in `effect.py` and `approval.py`, and **both existing
backends already decide with them and then only write**. Postgres does the same. `v0.1 §5.4`'s
retry table therefore has one implementation, not three, and a backend cannot drift into
permitting something SQLite refuses — which is the property `§2`'s suite exists to check and
this structure exists to make true.

### 4.2 The reservation mechanism

**`UNIQUE(effect_key)` plus `INSERT … ON CONFLICT DO NOTHING`, under `READ COMMITTED`.** Every
later transition is a compare-and-set — `UPDATE … WHERE effect_key = ? AND state = ? AND
action_id = ?` — **with the row count checked**.

`READ COMMITTED` is Postgres's default and the store does **not** set it. The guarantee is the
unique index, not the isolation level.

The shape of one reservation, inside one transaction:

1. `SELECT` the record.
2. `plan_reservation(record, …)` — the same pure function SQLite calls.
3. If the plan refuses, raise — **after** performing whatever partial write the refusal itself
   requires (§4.2.2). For most refusals nothing has been written and the transaction rolls back.
4. If the plan grants a **new** reservation: `INSERT … ON CONFLICT (effect_key) DO NOTHING`.
   `rowcount == 1` wins.
5. If `rowcount == 0`, another transaction inserted between steps 1 and 4. **Re-read and re-plan,
   once.** The second plan sees the winner's record and produces the correct refusal —
   `DuplicateEffect(state=in_progress)` for a live reservation, and `v0.1 §5.4`'s answer for
   anything else.
6. If the plan grants a **renewal** (`v0.1 §5.4`'s only automatic retry, a `FAILED` record):
   `UPDATE … WHERE effect_key = ? AND state = 'failed'`, `rowcount` checked; `0` raises
   `DuplicateEffect(state=in_progress)`, because the record changed under us. SQLite already does
   exactly this, and the `WHERE` clause is the same one.

**The retry at step 5 is bounded at one**, and the bound is not a performance choice. A second
zero would mean the winner's record vanished between the re-read and the insert, which nothing in
this protocol can do — there is no `DELETE` on `effects` anywhere in the codebase — so a second
zero is a corrupted database or somebody at a `psql` prompt, and the fail-closed answer is to
raise rather than to loop. Every loop in this project is bounded (`v0.4 §3.6`); this one is
bounded at one and says why.

The same shape covers every other transition. `begin_execution`, `commit_effect`, `fail_effect`,
`mark_ambiguous`, `resolve_effect`, `extend_lease` and `hold_continuation` each become a
conditional `UPDATE` with the row count checked, and a `rowcount == 0` **re-reads and calls the
same `_checked` predicate SQLite calls**, so the exception taxonomy is preserved: `AmbiguousEffect`
where the record went ambiguous, `DuplicateEffect` where another attempt holds it,
`InvalidArgument` where the transition was never possible. That taxonomy is asserted by tests
written for SQLite that now run against both backends (§2), which is why they had to be written
first.

`consume_approval_and_reserve` stays **one transaction**. A unique violation inside it aborts the
whole thing, so the approval is untouched and `v0.1 §4.2 A4` holds unchanged; the approval is
still checked before the reservation is decided, so T4's ordering holds too.

#### 4.2.2 The two refusals that write before they raise

"Nothing has been written" is true of most refusals and **false of two**, and an implementer who
took the general sentence literally would produce a Postgres store that diverges from SQLite on
`v0.1 §5.3 E3`. Both already exist in the shipped store and both are deliberate:

| Refusal | What is written first, and kept | Why |
|---|---|---|
| A reservation meeting a **lease that has expired** | the existing record is moved to `AMBIGUOUS` | `v0.1 §5.3 E3`. The expired lease is evidence; the attempt that discovered it is refused, but the discovery is not thrown away. Without this write the lapse is never recorded, and the *next* contender rediscovers it, forever |
| A consumption or answer meeting an **approval past its `expires_at`** | the approval's status is moved to `EXPIRED` | *A lapsed approval is evidence; keep it, then refuse.* Otherwise the record stays `granted` in the table for something that can never be granted |

Both are **committed before the refusal is raised**, which under one transaction means they are
performed in a transaction of their own, ordered before the refusing one. They are the only two,
they are additions to the evidence rather than to what is permitted, and neither can turn a
refusal into an admission. §4.3's Table A applies to each of them as it applies to any other store
write — a lost `COMMIT` on the ambiguate write is an ambiguous store write, resolved by the same
re-read.

A backend that skipped them would pass every test that asserts *the attempt was refused* and fail
`reservation`'s lease case, which is why §2.3's row says **"an expired lease becomes `AMBIGUOUS`
and is never released"** rather than only "is refused".

### 4.2.1 The three rejected alternatives

Recorded with their reasons, because a decision without its argument is one the next session
re-litigates.

| Rejected | Because |
|---|---|
| **Advisory locks** (`pg_advisory_lock`) | Session-scoped: released **exactly** when the connection dies, which is the failure case this milestone is about. It fails open in the one scenario it would be bought for. It also pins the store to session-mode pooling, and a store that holds nothing session-scoped works behind pgbouncer in transaction mode — a second, independent reason. |
| **`SERIALIZABLE`** | Retry storms under contention, and the unique index already produces one winner. Paying isolation cost for a guarantee the schema already gives. |
| **`SELECT … FOR UPDATE`** | Holds a row lock across the executor's run, which can be an hour (`v0.1 §5.3`'s lease default is five minutes and is deliberately configurable upward). A human approval or a slow remote would block every other contender for the duration — and a lock held across work the database cannot see is the thing leases exist instead of. |

### 4.3 Two tables, because there are two ambiguities

#### Table A — the store-write path. Reconcilable by re-reading.

| What the store observed | The store write is | What the store does | Effect state written |
|---|---|---|---|
| Any exception raised **before** `COMMIT` is issued — connect failure, statement error, constraint violation, statement timeout | **`FAILED`** — the transaction never committed | roll back; raise; the caller may retry immediately | none |
| SQLSTATE `40001` (serialization failure) or `40P01` (deadlock detected) at any point, `COMMIT` included | **`FAILED`** | as above (§4.3.1) | none |
| An exception **during or after** `COMMIT` — connection lost mid-`COMMIT`, timeout on `COMMIT`, the socket closing before the command tag arrives | **ambiguous** | **re-read the record on a fresh connection** (§4.3.2) | as the re-read resolves |
| The **re-read itself** fails | **unresolvable** | refuse to let execution proceed; write **no** effect state; raise. Fail closed | none |

#### Table B — the remote-effect path. Not reconcilable.

`v0.1 §5.5` unchanged, restated so the two are never read as one:

| Executor behaviour | Effect state |
|---|---|
| returns normally | `COMMITTED` |
| raises `ctrlrun.NotExecuted` | `FAILED` |
| raises anything else, `BaseException` included | `AMBIGUOUS` |

**No re-read settles Table B.** There is no query that establishes whether the refund landed,
which is why `AMBIGUOUS` is terminal there and why only a human (`ctrlrun resolve`) or a
reconcile hook (`v0.2 §2`, and only where its answer points) moves a record out of it.

An implementation that collapses the two tables will look correct and will be wrong in one of two
directions: reading Table A as Table B refuses work a single query could have recovered, and
reading Table B as Table A invents a reconciliation that does not exist and retries an effect
that already landed. The T-numbers are separate for exactly this reason (§8, T155 against T156).

### 4.3.1 Why a stated abort is `FAILED`

`40001` and `40P01` are the peer telling us, **in band**, that it rolled the transaction back.
That is the closest thing Postgres offers to an executor raising `NotExecuted`, and it is the
same rule `v0.2 §6.8` applies to the wire: *a protocol-level error the specification defines as
emitted before dispatch is a statement of non-execution; everything a peer says after dispatch is
an outcome.* The re-read path is for the **transport** — a connection that stopped answering —
and not for an answer the server gave.

The set is closed and small, and adding to it is a specification change. `57014`
(`query_canceled`) is **not** in it: a statement timeout on `COMMIT` is exactly the ambiguous
case, and a cancellation that arrived while the server was committing may or may not have
prevented it.

### 4.3.2 The re-read

On an ambiguous `COMMIT`, on a **fresh connection** — the old one is not trustworthy and may not
be usable. There are **two tables**, because a lost `COMMIT` on an `INSERT` and a lost `COMMIT` on
a compare-and-set `UPDATE` leave different evidence and a single table gives the wrong answer for
one of them.

#### Table A1 — a lost `COMMIT` on the reservation `INSERT`

| What the re-read finds | Conclusion |
|---|---|
| A record **identical to the row we attempted to write** (§4.3.3) | the commit landed; we hold it; proceed |
| **No** record | the commit did not land; retry the insert, **once**, then §4.2 step 5 |
| **Any other record** | feed it back through `plan_reservation` and obey what it says |

**The first row is an identity check on the whole row, not a match on `action_id`, and the
difference is a double execution.** `Action.action_id` is caller-supplyable — it has a default
factory, not a mandatory generator — and §5.1 explicitly contemplates two attempts sharing one id
where a caller rebuilt the same `Action` after a restart. So *"a record carrying our
`action_id`"*, which is what an earlier draft of this row said, is satisfied by **another
process's live reservation** made under the same id: that process is executing, this one concludes
it holds the key, and one logical effect is executed twice through the storage layer.

Worse, that row was *weaker than the pure planner it is supposed to be reusing*.
`plan_reservation` refuses a record whose lease is live **without consulting `action_id` at all** —
`DuplicateEffect(state=in_progress)` — so the draft introduced the only place in the codebase
where "it carries our id" meant proceed. The third row now says the opposite: anything that is not
byte-for-byte our own write goes back through the planner, which is the function §4.1 promises
every backend decides with.

"Identical" is defined in §4.3.3 rather than left to an implementer, because "same row" is exactly
the kind of phrase that becomes "same primary key" in code.

#### Table A2 — a lost `COMMIT` on a compare-and-set `UPDATE`

Every transition after the reservation — `begin_execution`, `commit_effect`, `fail_effect`,
`mark_ambiguous`, `resolve_effect`, `extend_lease`, `hold_continuation`, `take_continuation` — is
an `UPDATE`, and for those the record **always exists** and the interesting finding is the one
Table A1 does not have.

| What the re-read finds | Conclusion |
|---|---|
| The record in the state we were writing, still ours | the commit landed; proceed |
| The record **unchanged** — the pre-state we matched on, and ours where the operation has an owner (§4.3.4) | the commit did **not** land; **re-issue the same `UPDATE`**, once, then refuse |
| The record moved on, or no longer ours | feed it back through the same `_checked` predicate the transition uses, and raise what it raises |

**Row 2 is the one that matters and the one a single table gets wrong.** A lost `COMMIT` on an
`UPDATE` leaves the record in its pre-state, which is *"our `action_id`, a different state from
the one we were writing"* — and reading that as "somebody moved it, refuse" would be wrong in the
two most expensive directions available:

- on **`commit_effect`**, an effect that committed at the remote becomes a refusal, ages out by
  its lease, and costs a human — for a write that simply needs re-issuing;
- on **`fail_effect`**, a **proven** non-execution becomes `AMBIGUOUS`, throwing away the one
  automatic retry `v0.1 §5.4` grants and turning `NotExecuted` into a human's problem.

Neither is unsafe, and that is why it is worth naming: both fail *closed*, and both would make the
store useless in exactly the situation it was built for. The re-issue is safe because the `UPDATE`
is conditional — it matches on the pre-state — so if the first one did land, the second matches
nothing and the first row of this table is what the next re-read sees.

#### The re-read that fails

**Only if the re-read itself fails** does the store refuse and write nothing. The cost of that is
stated plainly rather than hedged, because it is the operational consequence of the whole design:
if the lost `COMMIT` did land, an effect key is now `RESERVED` by an attempt that will never
execute, its lease will expire, and the next contender will make it `AMBIGUOUS` and need a human
(§5). **That costs availability. It never costs a double execution**, and that is the trade this
library exists to make.

### 4.3.3 What "identical to the row we attempted to write" means

Every column the store was about to write, compared as stored: `effect_key`, `state`, `action_id`,
`attempt`, `lease_expires_at`, `created_at` and `updated_at`. The store computed each of them
before issuing the `INSERT`, so it holds the expected values without a second query.

`attempt` and `lease_expires_at` are the two that carry the weight and neither may be dropped:

- **`attempt`** separates our reservation from a *different* attempt on the same key by the same
  `action_id` — a retry after a `FAILED` record, which renews with `attempt + 1` (`v0.1 §5.4`).
- **`lease_expires_at`** is `now + lease` from this store's clock at the moment it planned, so two
  contenders sharing an `action_id` agree on it only if they also planned at the same instant with
  the same lease.

**Where two attempts are identical in every column, no comparison can separate them, and the
earlier draft of this paragraph asked for something unimplementable.** It said the store MUST
fail closed on the indistinguishable case — a frozen clock, one lease, one `action_id`, two
processes. But the identical case *is* the indistinguishable case: failing closed there would
refuse every ambiguous commit that actually landed, which is every ordinary use of this path, and
would make the re-read pointless.

So the residual is stated instead of legislated away. **What closes it is upstream: `action_id`
identifies one attempt.** `Action` generates a fresh one per attempt, and two *concurrent*
attempts sharing one `action_id` is a caller-side violation no store can defend against —
`action_id` is the store's entire notion of who holds a key, so a caller that reuses one has
already told the store the two are the same attempt.

What the seven columns buy is that the **realistic** collision is caught: a second attempt under
a reused `action_id` at any other instant, with any other lease, or at a different `attempt`
number differs in a column and is refused. Only a clock frozen *across processes* — which is a
test harness, not a deployment — produces two rows a store cannot tell apart.

### 4.3.4 Which row ran is observable

A store that takes any branch of §4.3.2 MUST say which one, on the `ctrlrun.postgres` logger, at
`WARNING`, with a `branch` attribute drawn from this closed set:

| `branch` | Meaning |
|---|---|
| `a1.row1.ours` | Table A1: the record is identical to the row we attempted to write (§4.3.3); the commit landed and we hold the key |
| `a1.row2.reinsert` | Table A1: no record; the commit did not land, and the same operation is re-issued once |
| `a1.row3.refuse` | Table A1: a record that is not ours; back through `plan_reservation`, which refuses |
| `a2.row1.landed` | Table A2: the record is in the state we were writing, and ours; the commit landed |
| `a2.row2.reissue` | Table A2: the record is still in a pre-state the operation may be issued from; the conditional `UPDATE` is re-issued |
| `a2.row3.refuse` | Table A2: anything else; back through the same predicate, which refuses |

**`a2.row2.reissue` does not mean "still ours", and saying so would claim more than the code
checks.** On a *transition* the row is ours — `action_id` and a pre-state in `expected`. On a
*renewal* — a reservation over a `FAILED` record, `v0.1 §5.4`'s one automatic retry — the pre-state
is `FAILED` and there is **no ownership test at all**, deliberately: a `FAILED` record is one
nobody holds, and requiring it to carry our `action_id` would forbid the retry the row exists for.
The ownership decision on that path belongs to `plan_reservation`, which the re-issue routes
through. A review found the first wording of this row describing a check the renewal path does not
perform, which is the prevention-versus-attribution confusion in one table cell.

Reaching any of them means a store write's outcome was unobservable, which is worth a line in an
operator's log whether or not it resolved cleanly. That is the smaller reason.

The larger one is that **without it, §8's T155 cannot fail.** The outcome T155 asserts — a
reservation that is ours, and a blind retry refused — is also exactly what a store that never
re-read at all produces, because on that path the write *did* land. A review replaced
`_resolve_lost_insert` with `return` and every assertion in T155 still passed. §8 had asked for
this ("the events name which branch of §4.3.2 ran"); nothing implemented it, and the test that
depended on it was green for a year of nobody noticing. A test that asserts an outcome rather
than which guard produced it is this repository's first and most common mutation pattern, and the
most important test in this milestone was an instance of it.

This is not a new event type and does not touch §9's frozen names: `ctrlrun.receipt.Event` is
unchanged, and nothing here reaches a sink. It is a log line, which is what this project already
uses for everything a receipt does not carry.

### 4.4 Connections, encoding and collation

- **One connection per thread**, as `SQLiteStateStore` does. `psycopg` connections are not
  thread-safe, and the existing thread-local shape is the one `close()` is already specified
  against (§2.7).
- **A broken connection is replaced only between transactions, and for the re-read.** The store
  never silently reconnects mid-transaction; a reconnect is a new transaction and pretending
  otherwise is how a partial write becomes invisible.
- **No pool ships.** An operator may put pgbouncer in front in **transaction** mode, and it works
  because the store holds nothing session-scoped — no advisory lock, no temp table, no prepared
  statement it depends on surviving, no `SET`. That is the second reason §4.2.1 rejected advisory
  locks, and it is a property worth keeping deliberately rather than by luck.
- **The store refuses a database whose `server_encoding` is not `UTF8`**, naming it, at open.
  `v0.1 §2.3` hashes the exact code points it is given and applies no Unicode normalization; an
  effect key that survives a round trip through `SQL_ASCII` as different bytes is a **different
  identity**, and two attempts at one logical effect would then reserve two keys and both execute.
  That is a double execution reached through the storage layer's character set, so it is refused
  at open rather than discovered.
- **`effect_key`, `approval_id`, `action_id`, `delegation_id` and `continuation` are declared
  `COLLATE "C"`.** Byte comparison, no locale. A non-deterministic collation would merge two
  distinct keys into one — a refusal, which is the safe direction — but the store should not
  depend on which direction a deployment's `lc_collate` happens to fail in.
- `continuation` keeps its uniqueness constraint and `take_continuation` keeps
  `hmac.compare_digest` for the comparison it makes in Python.

### 4.5 Cross-host concurrency and failure injection (item 4)

The `v0.1 §7` T3 standard, unchanged, against a real Postgres, on **two hosts**.

**Two hosts, not two processes.** `docker compose` with two application containers and one
Postgres is the cheap honest version, and the PR **says which was run**. The reason is worth
stating rather than assuming: two containers give separate connections, no shared memory and no
shared file locks — which is exactly what `BEGIN IMMEDIATE` was silently relying on and what a
second host removes. What it does **not** exercise is a network partition between hosts, which is
why partition injection is listed separately below.

Then the half that matters, each as a **deterministic** test that opens its window on purpose:

- `kill -9` an application container mid-transaction;
- **kill the connection during `COMMIT`** — the effect must end `AMBIGUOUS`, never `FAILED`, and a
  blind retry against that key must be refused;
- partition the app from Postgres, and restore it;
- restart Postgres under load.

The connection-killed-during-`COMMIT` test is **the single most important test in this
milestone**. It is the case where a naive port reports `FAILED` for an effect that committed, and
an agent acting on `FAILED` retries — the exact failure this library exists to prevent, arriving
through its own storage layer. Per §4.3.2 it is also *recoverable*: the test asserts the re-read
resolves it (T155), and a **second** test asserts that a failed re-read refuses to proceed and
writes nothing (T156).

A probabilistic test that "usually" reproduces an interleaving is not evidence. Every wait is
bounded, and a timeout is not a test failure.

---

## 5. Recovery on restart

### 5.1 What a process may conclude, and what it may not

A process that restarts and finds an effect record in `RESERVED` or `EXECUTING` may conclude
exactly two things: **what state the record is in**, and **whether its lease is still live**.

It may **not** conclude that the holder is gone. *"The holder is dead"* is **not knowable from the
store**, and no amount of schema makes it knowable:

- The record carries an `action_id`, which identifies an **attempt** and not a process. Two
  attempts by one process have two ids; one attempt survives a process restart with the same id
  if the caller rebuilt the same `Action`.
- **No process identity is added, and this is a decision rather than an omission.** A
  `holder_host` or `holder_pid` column would be the first thing somebody wrote a reclaimer
  against — *if the holder is not alive, free the key* — and that reclaimer is wrong on the day it
  matters, because a container that stopped answering a health check may still have an open socket
  to a payment API. Worse, the column would look authoritative: it names a host, and a host can be
  pinged. A field that invites a false inference is worse than no field.

So the only thing that ages a reservation out is its **lease**, and the only thing an expired
lease produces is `AMBIGUOUS` — which is a refusal, not a reclaim.

### 5.2 Nothing sweeps

There is no background thread, no timer, no cron, no `ctrlrun reap`. An expired lease becomes
`AMBIGUOUS` **lazily**, at the moment the next contender plans a reservation, which is what
`plan_reservation` already does and what `v0.1 §5.3 E3` already says.

Two consequences, both stated:

- **An expired lease that is never contended stays `EXECUTING` in the table.** `ctrlrun effects`
  shows it, and — this is the change — shows it as `executing (lease expired)`, because
  `EffectRecord.lease_is_live(now)` already answers the question and printing the state alone
  hides it. That is a display change and not a transition: **`get_effect` and `list_effects` are
  reads and MUST NOT transition anything** (§2.3's `outcome` suite drives it).
- **A restart does nothing on its own.** Opening a store migrates it (§3) and reads nothing else.
  A process that came back does not scan, does not repair, and does not report — it waits to be
  asked for an effect key, exactly as it did before it died.

### 5.3 What reclaims an expired lease, and on whose authority

**Nothing reclaims it automatically.** Expired → `AMBIGUOUS`. Out of `AMBIGUOUS` there are exactly
two authorities, both of which already exist:

| Authority | Route | Recorded as |
|---|---|---|
| A human | `ctrlrun resolve <effect_key> --committed \| --failed` | `resolved_by = "cli:local"` |
| A reconcile hook | `v0.2 §2`, and **only where its answer points** — `"unknown"` changes nothing | `resolved_by = "reconcile:<action name>"` |

**`cli:local`, not `cli:<user>`, and the difference is a promise this milestone cannot keep.**
An earlier draft of this table wrote `"cli:<user>"`. CTRLRun does not authenticate the person at
the terminal — §11 puts *authenticating the approver* out of scope by name, and it is the same
sentence for the same reason here — so a `<user>` in that column would be whatever the shell says
`$USER` is, which is a claim about a person made from a value that person controls. `cli:local`
says exactly what is known: **somebody with the operator's terminal**. The constant is the one
`ctrlrun approve` has always written into `approver`, and one string for "a human at this
installation" is better than two that mean the same thing. A review found the draft's wording;
the code was right and the document was not.

**The hook's half of the column is the code's fault, and the code changed.** It wrote the bare
`reconcile`, which made two hooks reconciling two different actions one indistinguishable string
— the finer distinction this section promises and §8.1's soak is told it has.

Two names, each meaning one thing on both paths, because they did not:

| Where | Human | Reconcile hook |
|---|---|---|
| `EffectRecord.resolved_by` | `cli:local` | `reconcile:<action name>` |
| `EFFECT_RESOLVED` event, `resolver` | `cli:local` | `reconcile:<action name>` |
| `EFFECT_RESOLVED` event, `resolved_by` | `human` | `reconcile` |

The record's field and the event's `resolver` are **the same string**, which is what lets a
reader — §8.1's soak first — join the two. They were not: the CLI stamped `cli:local` on the
record and put `human` in the event's `resolved_by`, while the hook put its record value in that
same key. The two coincided for the hook, and that is precisely what hid that they did not
coincide for the human.

**`EffectRecord.resolved_by` is a new field and `effects.resolved_by` a new column**, migrated by
`0003_resolved_by` (§3.7). Today the resolver's identity is written into the record's free-text
`error` string, which means the one field that says *a human overrode the kernel* is not
queryable and is indistinguishable from an executor's error text. §5's whole argument is that
those two authorities are different, and item 8's soak has to count them separately to say
anything at all about unexplained ambiguity. §9.2 states the bar this cleared and why it is a
different bar from the one a new **method** faces.

The `error` string keeps what it already keeps — `v0.1`'s *"resolved committed by X (was: …)"* —
because a receipt already written must not change meaning. `resolved_by` is additive.

**No receipt carries `resolved_by`, and that is structural rather than an omission.** A receipt is
written when an action reaches a terminal state (`v0.1 §6.1`); a resolution happens **afterwards**,
to the effect record, often days later and by somebody who was not the actor. Putting it on a
receipt would mean either mutating a receipt that has already been written and hashed — which
§6.5 would correctly report as `content_altered`, the chain detecting its own kernel — or minting
a second receipt for an action that did not run again. `resolved_by` lives on `EffectRecord`, and
`ctrlrun effects` and `ctrlrun inspect` are where a reader finds it. §9.5's `ctrlrun.receipt/v3`
row does not list it.

### 5.4 A continuation held by a dead process

`v0.2 §6.9`'s elicitation holds a reservation open across a round trip the kernel does not
control: `hold_continuation` extends the lease to `until` and stores the continuation token
alongside the whole `Action`.

When the process holding it dies, **the continuation outlives its process but not its lease**:

- The row survives, and `take_continuation` still admits **exactly one** resumption, because the
  token is consumed in the transaction that admits it (`v0.2 §6.9.2`, and §2.3's `continuation`
  suite).
- The `Action` rehydrates from `action_json`, which is why it travels with the token: a resumption
  is *the same action*.
- **With a shared store, a different process may finish it.** That is new in v0.6 and only
  because of Postgres: two gateways on two hosts now share continuations, where before they shared
  nothing unless they shared a file. It is a consequence of the backend, not a feature, and it
  needs no new surface.
- If the **lease** expired first, `take_continuation` is refused — the record is no longer
  `EXECUTING` with a live lease — and the next reservation attempt makes the effect `AMBIGUOUS`
  by the ordinary path. A human or a reconcile hook then answers, as for any other unknown.

T163 asserts all four, and distinguishes the two refusals **by exception type**: a lapsed lease
raises `AmbiguousEffect`, an already-taken token raises `InvalidArgument`.

**Not by message, and that is deliberate.** `take_continuation`'s message is identical whether the
value was forged, already consumed, or belongs to a suspension that has since moved on — *telling
an agent which of those it hit is telling it how to search*. The two facts an operator needs are
carried by the type and by the effect record's own state, and a more helpful string here would
reopen a disclosure the store closed on purpose.

---

## 6. Receipt integrity

### 6.1 What it is

Each receipt carries the hash of the one before it. Altering, deleting or reordering a receipt
breaks the chain and the break is detected and **named**.

### 6.2 The chain

**One chain per store.** Not one per effect key, and not one per process: a per-key chain would
not detect the deletion of every receipt for one key, and a per-process chain is not a chain.

Each receipt gains two fields **inside** the document:

- **`seq`** — a monotonic integer, starting at 1, assigned in the transaction that writes the
  receipt.
- **`prev_hash`** — the `hash` of receipt `seq - 1`, or the constant `"sha256:" + "00" * 32` for
  `seq == 1`.

And one field **outside** it:

- **`hash`** — `"sha256:" + hex(SHA-256(canonical_form(receipt document)))`, stored as a column
  and in the chain head. It is not in the document, because a document cannot contain its own
  hash. It is **derived**: any reader can recompute it.

**`seq` is inside the hashed content**, and that is what makes deletion and reordering detectable
rather than only edits. Two adjacent rows swapped *with* their `seq` values change both documents
and therefore both hashes; swapped *without* them break the links. There is no swap that is
invisible.

**The canonical form is `v0.1 §2.3`'s, unchanged.** Sorted keys recursively, `separators=(",",
":")`, `ensure_ascii=False`, UTF-8, floats rejected. A receipt's `arguments` are already
float-free because `Action` rejects floats at construction, and its timestamps are ISO-8601
strings. A **second canonicalizer is the drift this codebase must not have**: `v0.1 §2.3` is a
versioned security primitive, and the whole approval binding rests on there being one of it.

**Which requires one public name that does not exist yet, and §9.1 lists it.** `canonicalize`
today takes an `Action` and builds a fixed six-key payload; there is no way to ask for the
canonical form of an arbitrary mapping. Hashing a receipt document (§6.2) or a parsed policy
(§7.1) therefore needs either a new public function or a private second implementation — and the
second is what this paragraph forbids by name. So the encoding half is promoted:

```python
def canonical_bytes(payload: Mapping[str, Any]) -> bytes: ...   # §9.1
```

and **`canonicalize(action)` is redefined in terms of it**, so there is provably one
implementation rather than two that agree today. That redefinition changes no byte of any existing
canonical form — `ctrlrun.action/v1` is unchanged (§9.5) and T164b asserts that every hash written
before v0.6 still verifies.

### 6.3 The head

```sql
CREATE TABLE IF NOT EXISTS receipt_chain(
  id   INTEGER PRIMARY KEY CHECK (id = 1),
  seq  INTEGER NOT NULL,
  hash TEXT NOT NULL
);
```

One row. `put_receipt` inserts the receipt and advances the head **in one transaction**, and it
**takes the head row's lock first**:

1. `UPDATE receipt_chain SET seq = seq + 1 WHERE id = 1 RETURNING seq, hash` — unconditional, so a
   concurrent writer **blocks** on the row rather than losing a race.
2. Build the receipt with that `seq` and the returned `hash` as its `prev_hash`; compute its own
   `hash`.
3. `INSERT` the receipt, and write the new `hash` back to the head row.
4. `COMMIT`.

**This is a lock, not a compare-and-set, and the difference is dropped receipts.** An earlier
draft used `UPDATE … WHERE id = 1 AND seq = ?` retried **once** on zero. That bound was imported
from §4.2, where its justification is that a second zero would mean a record vanished, *"which
nothing in this protocol can do"*. The argument does not transfer to a row **every** writer
updates: there, a second zero means a third concurrent writer, which is ordinary. Combined with
the rule that a failed receipt write is not raised, three concurrent actions would have silently
dropped a receipt — in the one place §6.3 itself identifies as the first in the kernel where two
unrelated actions contend. Taking the lock first removes the race instead of bounding it.

The head exists to detect **truncation at the end**. Deleting the last N receipts leaves a chain
that is internally consistent; only a head that still names a `seq` and a `hash` no row carries
catches it.

Two costs, both stated:

- **Every receipt write now serializes on one row.** This is the first place in the kernel where
  two unrelated actions contend, and it is a throughput ceiling — a real one, since the lock is
  held for the receipt insert as well. Item 8's soak measures it, and `docs/postgres.md` says so.
- **A failed receipt write is a lost receipt, and the caller is told.** See §6.3.1, which is a
  change to shipped behaviour and is recorded as one.

### 6.3.1 What happens when the receipt cannot be written

An earlier draft of this section said *"a receipt whose write fails is logged, not raised —
`v0.1 §6.1`'s rule, unchanged"*. **That was wrong on every clause**, and the correction matters
because the subject of §6 is evidence integrity.

`v0.1 §6.1`'s logged-not-raised rule is about the **JSONL file**, and the same paragraph says the
opposite about the store: *"SQLite is authoritative and the JSONL file is a convenience export of
what it already holds, so the store is written first … a store that refuses the write has not
recorded the action, and there is nothing to export."* The swallowing that does exist in the code
is `Control._fan_out`'s, and it is for **sinks**. `Control._record` calls `put_receipt`
unguarded, and always has.

So the shipped behaviour is: **a store that cannot write the receipt raises, and the exception
reaches the caller.** v0.6 keeps it, and the draft's version would have been a change for the
worse — an action that committed at the remote returning normally with no evidence, no signal, and
a chain that is blind to the loss by construction (§6.4). Silent evidence loss is not a smaller
problem than a surprising exception; it is the problem this section exists to prevent.

What v0.6 adds is the honest statement of the residual, because the exception is not free:

- The effect **has already committed at the remote** by the time `put_receipt` runs, and the
  effect record already says `COMMITTED` — that write happened first and separately. So the
  guarantee `v0.1 §5.4` rests on is intact: a retry of the same effect key is refused as a
  duplicate whether or not the receipt exists.
- An agent that reads the raised exception as a failure and retries is therefore **refused**, not
  executed twice. That is why raising here is survivable where raising from a sink is not, and it
  is the distinction the draft collapsed.
- The action's evidence is not wholly lost: `EXECUTION_COMMITTED` was appended to the **events**
  log before the receipt was built, on a different write. §6.4's fourth bullet is what an operator
  reconciles with.

T170 asserts all three, and asserts that nothing swallows the exception.

### 6.4 What the chain does not cover

Stated before what it does, and repeated in the README, the changelog and `THREAT_MODEL.md`:

- **Not authorship.** The chain says the log was not altered. It says nothing about who wrote it.
  If a sentence anywhere could be read as "signed", it is rewritten.
- **Not an adversary who can rewrite every row including the head.** A database administrator with
  full write access recomputes the chain and it verifies. `THREAT_MODEL.md` already lists a
  malicious administrator as out of scope, and v0.6 does not change that — it narrows it. What
  the chain closes is the *partial* tamper: an `UPDATE` on one row, a `DELETE` from the middle, a
  reordering, a truncation. What it does not close is a rewrite of the whole log by somebody who
  can do that.
- **Not a signature.** §11 has the argument.
- **Not that every action wrote a receipt.** The chain proves that the receipts which were
  written were not altered. A receipt whose write failed (§6.3.1) leaves no gap in `seq` — the
  head is advanced in the same transaction, so nothing was numbered — and is therefore invisible
  to the chain by construction. It is **not** invisible to everything: the write raises to the
  caller, and `EXECUTION_COMMITTED` is already in the **events** log, which is the other evidence
  stream and is written on a different path. An operator reconciling *"did everything get a
  receipt?"* reads events against receipts, and no chain answers it.
- **Not the JSONL export's tail.** The export is fully verifiable by recomputation — every
  document carries its `seq` and `prev_hash` — **except** for a truncation at the end, which only
  the stored head detects. A reader with the file alone can prove the file was not altered
  internally and cannot prove it is complete.

### 6.5 Detection, and the five names

A break is reported with a name, because a chain that only catches the easy case is worse than
none: it gets quoted as if it caught all of them.

| Name | Condition |
|---|---|
| `content_altered` | the recomputed hash of receipt `n` ≠ the stored `hash` for `n` |
| `link_broken` | receipt `n`'s `prev_hash` ≠ receipt `n-1`'s `hash` |
| `missing` | a gap in `seq` |
| `head_mismatch` | the stored head's `seq`/`hash` ≠ the last receipt's |
| `unchained` | `seq` is `NULL` — a receipt written before the chain existed (§3.7) |

**The chain reader orders by `seq`, and by nothing else.** No store provides a usable order
otherwise: `SQLiteStateStore.receipts()` orders by `rowid`, `InMemoryStateStore.receipts()` by
append order, and Postgres promises neither. `missing` and `head_mismatch` are statements about
positions in a sequence, so a reader that took physical row order would report them, or fail to,
according to how the rows happen to sit on disk.

Rows with `seq IS NULL` have **no position**. They are read separately, reported as `unchained`
with their count, and never interleaved with the chain — a `NULL` sorted to either end would
manufacture a gap at one end or a head mismatch at the other.

`unchained` is **never** a pass. A pre-chain receipt is reported as unchained with its count, and
the summary line says how many of how many were verified. Folding them into a green count is
`v0.4 §3.8`'s false green in a new costume.

The six tamper cases item 6 must detect, and which name each produces:

| Tamper | Detected as |
|---|---|
| alter a committed receipt's `arguments.amount` | `content_altered` |
| alter its `decision` | `content_altered` |
| alter its `approver` | `content_altered` |
| alter its `finished_at` | `content_altered` |
| delete a receipt from the middle | `missing`, then `link_broken` at the next one |
| reorder two adjacent receipts | `link_broken`; and `content_altered` if their `seq` values moved with them |

**The tamper test is the deliverable and it is written first.**

### 6.6 Where it is checked

Two things, and they are kept apart because the item-6 brief's single sentence turns out to be
two:

- **`ctrlrun verify` gains `G11`**, `ctrlrun.guarantees/v2`. It runs against the **scratch store**
  like every other guarantee (`v0.4 §3.5`): it writes a short chain, alters one receipt, and
  asserts the alteration is detected and named. Its **positive control** (`v0.4 §1.3`, which is
  required and not optional) is that the unaltered chain verifies — without it, a kernel whose
  detector returned "broken" unconditionally would pass. G11 is applicable to every v0.6
  configuration, because the store it uses is one verify created.
- **`ctrlrun receipts --verify-chain`** reads the **operator's** store — which `ctrlrun receipts`
  already opens — and reports the first break by `seq` and by name, plus the count of `unchained`
  rows. It is a **flag on an existing command** and not a new one (§9.4).

Verify does not open the operator's store and this does not change that. A guarantee that read the
operator's database would be a verification tool with a side effect on the thing it verifies, and
`v0.4 §3.5` refuses it.

---

## 7. Policy versioning, the control registry, and data scope

Two things in one item because both answer *"what decided this, and can I still tell?"*.

### 7.1 The policy hash, and the declared version

**Both, with the content hash authoritative.**

- **`policy_hash`** — `"sha256:" + hex(SHA-256(canonical_form(…)))` over `v0.1 §2.3`'s
  canonicalizer, computed over the **parsed** decision inputs rather than the file's bytes:
  the schema string, the actions and their rules in document order, `mode`, `environment`, and
  the authority grants. Two files differing only in comments, key order or whitespace hash the
  same, which is correct — the decision is a function of the rules, not of the formatting — and a
  reformat that changed every receipt's provenance would make the field noise.
- **Authority is included**, and where it was loaded from a separate `--authority` document both
  are folded into the one canonical structure before hashing. A receipt's job here is to say what
  decided the action, and `v0.3 §4.6` makes authority half of that.
- **`version:`** — a new, optional, top-level policy key: a free string the operator chooses, for
  humans. It is recorded and **never authoritative**. Two documents with the same `version:` and
  different hashes are two different policies, and the hash is what says so.

This needs `schema: ctrlrun.policy/v4` (§9.5). The top-level key set grows by one — `schema`,
`actions`, `mode`, `authority`, `environment`, `version` — and stays closed. `v1`, `v2` and `v3`
documents load unchanged and get a `policy_hash` like any other; only `version:` needs `v4`.

Receipts record `policy_hash` and `policy_version` (`ctrlrun.receipt/v3`, §9.5), and — the second
half — **`policy_hash_at_approval`**, which is the hash that was in force when the approval was
*granted*, carried on the approval record. Where the two differ, the policy changed between the
grant and its consumption, and §7.2 says what happens.

### 7.2 The sharp case: the policy changed between grant and consumption

`v0.1 §4.2` binds an approval to an `action_hash` and not to a policy, and **that is right**: a
human approved *an action*, not a rule. `v0.2 §3.3` already records the case where a policy edit
changes a `resource:` template and therefore voids every approval by changing the hash. What this
section decides is the case where the hash is untouched and the *decision* moved.

The policy is **already re-evaluated at consumption** — `Control.execute` evaluates on every pass,
including the one presenting an approval — so this is mostly a statement of existing behaviour
with one change:

| The new decision | What happens |
|---|---|
| `APPROVE` | unchanged. The approval is consumed with the reservation (`v0.1 §4.2 A4`) and the receipt records both policy hashes |
| `DENY` | the action is **refused**, and the approval is left `granted` (§7.2.1) |
| `ALLOW` | the approval is **invalidated** after the reservation succeeds, and the receipt notes it (§7.2.2) |

**The `ALLOW` row is a change to shipped behaviour and is the reason this section exists.** Today
a re-evaluation that returns `ALLOW` leaves `approval_id` unset, so the presented approval is
never consumed: it stays `granted` for its full TTL, for a hash that a later policy edit could
make `APPROVE`-requiring again — a live bearer token for an action a human already answered.
`v0.1 §4.1` calls a request id a bearer token in as many words.

#### 7.2.1 Why the `DENY` row leaves the approval granted

The two rows reason in opposite directions and the document owes the argument, because the `ALLOW`
row's whole case is that a live granted approval is a bearer token — and the `DENY` row leaves one
live for an action the policy **currently forbids**, which is the worse instance of the same
hazard.

It is still right, for three reasons that bound the exposure:

- **A human's answer is not spent on an action that did not run.** Consuming it would mean the
  operator who corrects a mistaken policy edit must also go and ask the human again, for an action
  they already approved and which was refused by a rule rather than by them.
- **The token authorizes nothing on its own.** `v0.1 §4.2 A1` binds it to one `action_hash`, and
  the policy is re-evaluated on every presentation (this table). While the policy says `DENY` the
  approval opens nothing; if the policy is corrected, it opens exactly the action it was granted
  for.
- **The exposure is bounded by `expires_at`**, which is checked at consumption and not only at
  grant (`v0.1 §4.2 A3`), and the refusal is recorded against the approval so the history shows a
  grant that met a denial.

The asymmetry, stated in one line: the `ALLOW` row closes a token that would otherwise outlive
**an action that ran**; the `DENY` row keeps one for **an action that did not**.

#### 7.2.2 Why the `ALLOW` row invalidates rather than consumes

An earlier draft said *consumed anyway, in the same transaction*, and that would have added a
refusal path to the permissive decision.

`consume_approval_and_reserve` checks the approval **first** — `v0.1 §4.2 A4`, and the store's
documented ordering for T4, so that a replayed approval is what gets raised when a duplicate
effect would also apply. Route an `ALLOW` action through it and an approval that is expired,
already consumed, or denied raises `ApprovalMismatch` — and **an action the policy allows is
refused because of an approval it did not need.** The reachable case is ordinary: an agent retries
inside `with_approval(id)` after the operator relaxed the rule, the grant having been spent on the
first attempt.

So the order is inverted and the coupling removed:

1. The action is decided `ALLOW` and reserves its effect key exactly as any allowed action does.
   **The approval is not an input to that**, and no failure of it can refuse the action.
2. **After** the reservation succeeds, the presented approval is moved `granted → consumed` in a
   write of its own. If that write fails or the approval was never grantable, it is logged and the
   action proceeds — there is nothing to protect, because the policy permits this action outright.
3. The receipt records `approval_id` and `approver` with `decision: "allow"`, so the evidence says
   a human answered and the policy did not require it.

That closes the bearer-token hazard, which is the whole point of the row, without letting the
approval gate an action that does not have one.

#### 7.2.3 Where this table binds, and where it does not

- **`Control.execute`'s presenting pass only** — the pass that carries a `with_approval` context.
  That is the only place the case arises.
- **`Control.resume` is unchanged.** It re-evaluates the policy *for the receipt, not to
  re-decide*, and applying the `DENY` row there would strand a reservation held open across a
  round trip the remote may already be acting on — which `v0.2 §6.9.2` forbids. A resumption that
  the policy would now deny is recorded and completes; the operator's lever is the next action,
  not this one.
- **Observe mode is unchanged and asks no human.** Its own gate reads the presented approval only
  on `APPROVE`, exactly as enforce mode does, and `v0.3 §6.2` already says observe mode never
  requests one. A counterfactual is not a place to spend a real grant.

Fail-closed in the only direction that matters: the `DENY` row refuses the action, and the `ALLOW`
row never lets an approval refuse one.

### 7.3 The control registry

The kernel-side objects a sector pack configures, so that a pack is **configuration rather than
code**. v0.6 ships the primitives and **no pack** (§11).

```yaml
schema: ctrlrun.policy/v4

controls:
  maker-checker-refunds:
    title: "A refund over the desk limit is approved by a second person"
    source: "House policy FIN-4.2"          # free text; cited, never interpreted
  card-data-handling:
    title: "Cardholder data is not written to evidence"
    source: "PCI DSS v4.0 §3.3.1"

actions:
  stripe.refund:
    controls: [maker-checker-refunds]
    rules:
      - when: {amount_gt: 50000}
        decision: approve
        controls: [maker-checker-refunds]    # a rule may narrow or add
```

`when:` is `v0.1 §3.2`'s mapping of `<argument>_<op>` keys, unchanged and not extended here. An
earlier draft of this example wrote `when: amount > 50000`, which is not the policy language — it
is an expression, and there is no expression parser. The distinction matters more than a typo:
§7.4 turned on the same mistake and the mistake there was load-bearing.

Three rules, and they are what keeps this from becoming a compliance feature:

- **CTRLRun does not interpret a control.** `source:` is a string the operator wrote. The kernel
  does not know what PCI DSS is, does not check the clause exists, and makes **no compliance,
  conformance or alignment claim** on the strength of one. A control is an identifier and a
  citation.
- **A control is attribution, not prevention**, and the distinction is this project's sharpest
  rule. Citing `maker-checker-refunds` on a rule does not cause an approval; the rule's
  `decision: approve` does. The control says *which written expectation this rule exists to
  serve*, so a receipt can answer "under what" and an operator can go from a control to its
  evidence. Any sentence that implies otherwise is a false green in prose.
- **An unknown control id is a load error**, naming it. A registry whose citations can dangle is a
  registry that quietly stops meaning anything.

The receipt records `controls`: the ids the **matched rule** cited, unioned with the action's.
`ctrlrun receipts --control <id>` filters on it — a flag, not a command (§9.4).

### 7.4 Data scope

Two primitives, and the second is on probation.

**Labels, and a condition that can see them.** An action declares which of its arguments carry
which class of data:

```yaml
actions:
  patient.record.update:
    data:
      diagnosis: phi
      patient_id: phi
      note: internal
    rules:
      - when: {data_scope_in: [phi]}
        decision: approve
```

`data_scope` is the **set of labels present in this action's arguments**, derived at evaluation
from the `data:` map and the arguments actually supplied.

**The condition is written in `v0.1 §3.2`'s grammar and adds no operator.** `when:` is a mapping
of `<subject>_<op>` keys; `data_scope_in: [phi]` means *the derived set intersects this list*,
which is the membership `_in` already expresses. `_eq` and `_neq` compare the whole set. **No
`contains` and no `not_in` are added**, and the reason is not economy: `_OPERATORS` is shared with
authority `constraints:` (`v0.3 §4.5` — *one shared implementation, not two*), so an operator
added here would silently become available to grants, and §11 puts *"matching a grant on a data
label"* out of scope. An operator that reaches a surface this milestone has excluded is not a
small addition.

An earlier draft wrote `when: data_scope contains phi` and named three operators including two new
ones. That was three mistakes in one line — an expression where the language has a mapping, a
parser this project does not have and §7.4 claimed to be avoiding, and an unnoticed extension of
the authority surface.

**`data_scope` is reserved as an *argument* name and permitted as a *condition subject*, and those
are two different checks.** The distinction is what makes the feature implementable at all:

| Check | `data_scope` | Where it lives |
|---|---|---|
| May an **argument** be called this? | **No** — `PolicyError` at load | `RESERVED_ARGUMENTS`, as `claims`, `issuer` and `expires_at` are |
| May a **condition key** split to this subject? | **Yes** | the condition splitter, which today refuses every reserved name |

Today those are one check: the splitter refuses a condition whose subject is in
`RESERVED_ARGUMENTS`, which is exactly how `claims_eq:` becomes a load error. Adding `data_scope`
to that set unchanged would make `data_scope_in:` a load error too — **the very condition this
section asks operators to write**. So the splitter gains a small, explicit allow-list of
*derived subjects*: names that are refused as arguments and resolved, at evaluation, from
something other than `action.canonical_arguments`. `data_scope` is its only member in v0.6, and a
name in it is still refused as an argument.

The reservation is **not gated on the schema version**, for `v0.3 §12.1`'s reason and at
`v0.3 §12.1`'s stated cost: a policy whose protected function takes an argument called
`data_scope` stops loading under v0.6, and the load error says so. Gating it on `v4` would leave
one name meaning two things in two files, which is the ambiguity `v0.1 §3.2` refuses.

**Authority is untouched.** `constraints:` see the same operators they saw in v0.3 and cannot
address `data_scope`: the derived-subject allow-list is the policy evaluator's, and a grant that
named one is refused as it always was (§11).

**Redaction, and where it does not apply.** A label may be declared `redact: true`:

```yaml
data:
  diagnosis: {label: phi, redact: true}
```

A redacted argument's value is replaced **in the evidence** — the receipt, the events, the JSONL
export — by `"sha256:<hex>"` of its canonical form. The value is gone; two different values are
still distinguishable; the `action_hash` is **unchanged**, because it is computed over the real
arguments as it always was, so a reader can still check the binding.

**Redaction never applies to the approval payload.** `ApprovalRequest.action`, `PendingApproval`
(`v0.5 §2.2`) and `ctrlrun approve`'s display carry the real values. A human must see what they
approve — that is the whole of `v0.1 §4.2` — and a redacted approval screen is an approval of
something nobody read.

**`redact:` is on probation and item 7 must earn it.** It is the primitive here that exists most
plausibly because an imagined sector might want it, and `v0.6`'s scope rule is that a field added
for a pack nobody is writing is a guess that gets frozen. Item 7 writes one throwaway sector
configuration (§7.5). If that configuration does not need `redact:`, **item 7 cuts it** and
records the cut in §12. The registry and the labels are not on probation: a receipt that cannot
say which control governed an action, and a rule that cannot see that an argument is PHI, are the
two things a pack cannot be written without.

### 7.5 The throwaway configuration

The test that a primitive is right is that a pack can be written as YAML against it. Item 7 writes
**one** sector's configuration, asserts it loads and drives a decision, and **does not ship it**:
it lives in the test suite and in no `packs/` directory, no `examples/`, and no distribution.

The artefact is disposable; what it finds is the deliverable. This is `v0.5 §8`'s device — the
third adapter written against the contract alone — and its output is the same shape: a list of
what the primitives could not express, each of which becomes an edit to this section or an
explicit "not in v0.6".

---

## 8. Acceptance tests

Each MUST exist as a pytest test with the given ID in its name. All MUST pass for v0.6, and every
test of `v0.1 §7`, `v0.2 §10`, `v0.3 §10`, `v0.4 §8` and `v0.5 §8` MUST still pass.

### Item 1 — The store conformance suite (§2)

#### T140 — Every fixture fails its named suite, and every suite has a fixture
Each of §2.6's fourteen broken stores is run through `ctrlrun.conformance.store.run`. Each MUST
fail the suite named in its row, and the assertion is on the **case** that failed and its
**reason**, not only on the suite's status. Both directions of the coverage rule are asserted:
every suite in §2.3 is named by at least one fixture, and every fixture names a suite that exists.
A fixture that fails nothing is a failure; a fixture whose named suite passed is a failure.

#### T141 — Both shipped backends pass every suite
`SQLiteStateStore` and `InMemoryStateStore` report `pass` for every case, with the single
exception of `reservation`'s cross-process case for `InMemoryStateStore`, which is
`not_applicable` with §2.4's reason. No other N/A is accepted, from either backend.

#### T142 — The report refuses a degenerate run
Every case `not_applicable` → `report.ok` is `False`. `run(backend, only=…)` naming a case that is
not in the registry **raises**, rather than silently running everything or nothing.
`0/0` is not a pass (`v0.4 §3.8`).

`only` is a `run()` keyword and **not** a CLI flag: §9.4 adds no command, so there is nothing for
an unknown name to exit from. An earlier draft asserted `--only … exits non-zero`, which described
a surface this milestone does not build.

#### T143 — E1 twice, and each catches what the other cannot
Both of §2.4's cases against a real SQLite file. **In-process**: N threads released by a barrier
after every one has read and before any has written — the window opened on purpose, not raced for
— exactly one granted, N−1 refused with `v0.1 §5.4`'s errors. **Cross-process**: `processes`
subprocesses, one winner, N−1 refused, the fake remote called exactly once, which is `v0.1 §7`
T3's standard reached through the suite rather than a second implementation of it. T3 itself is
unchanged.

The pair is asserted to be non-redundant, deterministically and in both directions: a store whose
atomicity is a Python lock passes in-process and fails cross-process; `two-winners` fails
in-process and cannot be seen by the cross-process case at all, because a subprocess opening the
backend's `url()` gets the real store and not the fixture. That asymmetry is why there are two
cases (§2.4).

#### T144 — `outcome` catches both shapes
`guesses-failed` fails `outcome` on the `FAILED` check and `raises-not-executed` on the
`NotExecuted` check, each by reason. Removing either check leaves the other fixture's suite still
failing and the removed one's passing. Two independent defences hide each other's mutations: a
test that only asserts the *outcome* stays green with either one deleted, so each check needs a
fixture no other check reaches.

#### T145 — `close()` is not a fence
Against every backend: `close()`, then a read and a write, both of which succeed; and the record
written after `close()` is visible from a `reopen()`.

#### T146 — Every divergence item 1 found is now specified and asserted
One test per case §2.7 grew during item 1, each naming the section paragraph that decides it. If
item 1 added no paragraph, this test does not exist and the PR says why — which is a finding
about the suite (§2.7).

#### T140f — The suite is not imported at package import
A subprocess asserts `import ctrlrun` leaves `ctrlrun.conformance` and
`ctrlrun.conformance.store` out of `sys.modules`, beside T30, T92, T125b and T134.

### Item 2 — Schema version and migrations (§3)

#### T147 — A v0.5 database migrates, and every row survives
The fixture database is built **by v0.5's own code** — a pinned `ctrlrun==0.5.0` in a subprocess
writing effects, approvals, receipts, events and delegations — never by a hand-written schema. A
v0.6 store opens it, migrates it, and every row is present **by content**: the same effect states,
the same `action_hash` values, the same `grant_json` with its UTC offset intact. A count alone
does not pass this test.

#### T148 — An older binary refuses a newer database, and names both versions
A database at `head` is opened by a store whose known set stops short of it. `SchemaMismatch` is
raised at open, before any other table is read; the message names the unknown migration id, the
opening binary's version, and the `ctrlrun_version` recorded for that migration. The test asserts
that **no row from any other table was read**, by injecting a store whose other reads raise.

#### T149 — A migration that fails half way leaves the old version
A deliberately broken migration ships in the test suite: its second statement raises. The store
refuses to open; the database is still at its previous version; every row is intact; and
re-opening with a correct binary migrates cleanly. Proved by execution, never by argument (§3.4).

#### T150 — Re-opening does not re-run a migration
Two opens; the `schema_version` rows and their `applied_at` values are identical after the second.

#### T151 — An unknown version is refused, not guessed at
A `schema_version` carrying an id the binary does not know, **and** missing one it does, is the
divergent case: refused, naming both sides, and nothing is applied.

#### T152 — Adoption, every classification, from every release that has one
Empty database → migrated to head. A database with foreign tables and no `effects` → refused,
naming what it found. A database at head → opened, nothing applied.

And the baseline path **from v0.1, v0.2, v0.3 and v0.5 databases, each built by that release's own
code** (§3.5 item 2, plural). The v0.1 and v0.2 fixtures are the ones that matter: a v0.1 database
has no `continuations` and no `delegations` table, and a v0.2 database has no `delegations`. After
adoption each MUST have every table at head, and the test drives a **continuation and a
delegation** against the migrated v0.1 database rather than only listing tables — the defect this
replaces would have left both raising `no such table`, and a schema-name assertion is one an
implementer can satisfy without the store working.

#### T152b — There is no flag that opens a database without migrating it
The CLI, the constructor and the environment are searched for one; the test asserts no accepted
argument, keyword or `CTRLRUN_*` variable changes the migration behaviour (§3.6, `v0.4 §3.9`).

### Item 3 — The Postgres backend (§4)

#### T153 — The extra says so when it is missing, and core does not import it
`import ctrlrun` pulls in no `psycopg` module (subprocess, `sys.modules`). Constructing a
`PostgresStateStore` without the driver raises `MissingDependency` carrying the install line.

#### T154 — Postgres passes item 1's suite
Every case in §2.3, against a real Postgres. **Not a suite written for it — that one.** Every
case `pass`; no N/A except §2.5's re-read cases, which item 4 supplies.

#### T154b — `--store-url` accepts its second value, and refuses a third
A `postgresql://` URL opens a Postgres store; a URL naming neither backend exits **2** with a
message that no longer says "v0.6" as a future tense. `v0.4 §3.1`'s test is amended in this
commit and the amendment is recorded in §9.6.

#### T154c — The store refuses a non-UTF8 database
A database created with `ENCODING SQL_ASCII` is refused at open, naming the encoding (§4.4).

#### T154e — Verify's Postgres scratch store touches nothing it did not create
`ctrlrun verify --store-url postgresql://…` creates a `ctrlrun_verify_<hex>` schema per guarantee
and drops it, including when the run ends by exception. A CTRLRun database in `public` is
**byte-identical before and after** — `v0.4`'s T103 carried across to the backend that made it
hard — and it is not migrated. A URL the store cannot `CREATE SCHEMA` on, or one whose
`search_path` already names a CTRLRun schema, exits **2** naming the reason and never falls back
to `public` (§4.1).

#### T154f — DDL rights, collation and connection discipline
Three assertions §10 makes rows about and §8 did not reach: a database user without DDL rights
makes the store refuse to open **naming the missing privilege** rather than reporting the SQL that
failed (§3.6); the identity columns are declared `COLLATE "C"`, asserted by reading the catalogue
rather than by reading the DDL string; and two threads receive two different connections while one
thread receives the same one twice (§4.4).

#### T154d — `Control` never maps a store exception through `v0.1 §5.5`
A store whose `commit_effect` raises an arbitrary exception: the effect record is not `FAILED`,
the caller sees the store's exception, and nothing in the path calls `NotExecuted`'s branch.

### Item 4 — Cross-host concurrency and failure injection (§4.5)

#### T155 — The connection dies during `COMMIT`, and the re-read resolves it
Deterministic: the window is opened on purpose by killing the connection at `COMMIT` rather than
by racing. The effect ends `AMBIGUOUS` or reserved-by-us **as the re-read determines**, never
`FAILED`; a blind retry against that key is refused; the events name which branch of §4.3.2 ran.
**The single most important test in this milestone.**

#### T155b — A lost `COMMIT` on `commit_effect` and on `fail_effect`
§4.3.2's Table A2, the row a single table gets wrong. The connection is killed during `COMMIT` on
`commit_effect`: the re-read finds the record **unchanged and still ours**, the store **re-issues**
the `UPDATE`, and the effect ends `COMMITTED` — not refused, and not aged out to a human. The same
against `fail_effect`: the effect ends `FAILED` and `v0.1 §5.4`'s automatic retry is still
available, rather than the proven non-execution becoming `AMBIGUOUS`. Both assert the record and
the exception, and a mutation that routes A2 through A1's table fails both.

#### T155c — The re-read's identity check is not a match on `action_id`
§4.3.3, and the double-execution this milestone's review found. Two attempts share one
`action_id`; the first holds a live reservation; the second loses its `COMMIT` on the `INSERT` and
re-reads. It MUST refuse — `DuplicateEffect(state=in_progress)`, the answer `plan_reservation`
gives — and MUST NOT conclude it holds the key. The executor call count is **1**. The mutation
that reduces §4.3.3's comparison to `action_id` alone makes this test fail with two executions,
which is the only assertion here that matters.

#### T156 — A failed re-read refuses to proceed
The `COMMIT` is lost **and** the re-read connection is refused. No effect state is written, the
executor is never reached, and the caller sees a store exception rather than any outcome. The
record is left exactly as it was.

#### T157 — T3's standard, on two hosts
Two application containers, one Postgres. N contenders, one winner, the fake remote called exactly
once, one committed receipt and N−1 blocked. **The PR says which was run** — two hosts, or two
processes — and the reason two containers is the honest version is in the PR body (§4.5).

#### T158 — `kill -9` mid-transaction changes nothing
An application container is killed mid-transaction. The database is consistent, no effect is
`FAILED` that was not proven so, and the key it held ages out by its lease and by nothing else.

#### T158b — Partition and restart
The app is partitioned from Postgres and restored; Postgres is restarted under load. Every wait is
bounded and a timeout fails red. No effect ends `FAILED` on either path.

### Item 5 — Recovery on restart (§5)

#### T159 — `AMBIGUOUS` survives a restart and still refuses a blind retry
Against every backend, through `reopen()`.

#### T160 — An expired lease frees nothing
The lease lapses; no process, timer or open reclaims the key. The next contender's reservation is
refused with `AmbiguousEffect` and the record is now `AMBIGUOUS`. The test asserts that **no**
other call — `get_effect`, `list_effects`, opening the store, `ctrlrun effects` — transitioned it.

#### T161 — What reclaims it, and on whose authority
`ctrlrun resolve` and a reconcile hook each move the record, and `resolved_by` records which. A
reconcile hook answering `"unknown"` changes nothing. The test asserts the **reason** and not only
the status.

#### T162 — No process identity is inferable from a record
The record exposes no host, pid or holder field, and the test asserts the absence by name so that
adding one is a deliberate act with a failing test in front of it (§5.1).

#### T163 — A continuation held by a dead process
Four assertions: the row survives the process; a second process sharing the store may take it
exactly once; a second take is refused; and where the lease lapsed first the take is refused with
the lease's reason and the effect becomes `AMBIGUOUS` by the ordinary path.

### Item 6 — Receipt integrity (§6)

#### T164 — The six tamper cases, each detected and named
§6.5's table, one assertion per row, on the **name** and the `seq` and not only on "invalid".
Written first.

#### T164b — Promoting `canonical_bytes` changes no existing hash
Canonicalization is security-critical, and this project's standing rule for touching it is a test
proving old hashes still verify, or an explicit schema version bump. This is the first. A corpus of `Action`s and their `action_hash`
values recorded **before** the promotion — including the `v0.1 §7` T7 vectors, the NFC/NFD pair,
tuples-normalized-to-lists, and nested mappings in adversarial key order — hashes identically
after `canonicalize` is redefined to call `canonical_bytes`. An approval granted against a
pre-v0.6 hash still consumes. `ctrlrun.action/v1` is asserted unchanged.

Its control: `canonical_bytes` refuses a `float` at any depth, so the promoted function is
demonstrably the same primitive and not a permissive lookalike.

#### T165 — An untampered chain verifies
The positive control (`v0.4 §1.3`). Without it every row of T164 passes against a detector that
returns "broken" unconditionally.

#### T166 — `seq` inside the content is what catches reordering
Two adjacent receipts swapped **with** their `seq` values are `content_altered`; swapped
**without** them are `link_broken`. Removing `seq` from the hashed content makes the first case
pass silently — the mutation that justifies the design decision.

#### T167 — Truncation at the end is caught by the head, and only by it
The last receipt is deleted. `head_mismatch`. With the head row also deleted, the report says the
head is missing and **does not** report a valid chain.

#### T168 — A pre-chain receipt is `unchained` and never a pass
A store migrated from v0.5 with receipts carrying `seq IS NULL`: the report names them, counts
them, and `ok` is `False` for a run that verified nothing else.

#### T169 — G11, with its control
`ctrlrun verify` reports G11 against a scratch store, `pass` with the control satisfied; a kernel
with the detector disabled reports `fail` with a counterexample; `ctrlrun.guarantees/v2` is the
catalogue string.

#### T170 — A failed receipt write raises, and leaves no gap
The receipt insert fails. Four assertions, together, because any one alone would be misread:
the exception **reaches the caller** and nothing swallows it; the effect record is already
`COMMITTED`, so a retry of that key is refused as a duplicate rather than executed twice; the head
is unadvanced and `seq` has no gap; and `EXECUTION_COMMITTED` is in the events log. §6.3.1's
correction is what this test pins — the draft it replaces asserted that nothing was raised, which
would have made an action that committed at the remote return normally with no evidence.

### Item 7 — Policy versioning and the control registry (§7)

#### T171 — The hash is over the rules, not the bytes
Two documents differing only in comments, key order and whitespace produce the **same**
`policy_hash`; changing any rule, `mode`, `environment` or grant changes it. `version:` alone does
not.

#### T172 — Receipts carry both, and the hash is authoritative
`policy_hash` and `policy_version` on every receipt; two policies sharing a `version:` string and
differing in content are distinguished by hash in the evidence.

#### T173b — §7.2 binds `execute`'s presenting pass, and nothing else
`Control.resume` re-evaluating to `DENY` **completes** the resumption rather than stranding the
held reservation (`v0.2 §6.9.2`); observe mode asks no human and spends no grant, and a presented
approval survives an observed action untouched (§7.2.3). Both are asserted by observation of the
approval's stored status.

#### T173c — An `ALLOW` action is never refused by the approval it did not need
The policy is relaxed to `ALLOW` between a grant and its presentation, and the approval is
**expired**, then **already consumed**, then **denied**. In all three the action runs, the effect
commits, and the receipt records `decision: "allow"`. This is the failure §7.2.2's inversion
exists to prevent, and without it a draft that consumed in one transaction would pass T173.

#### T173 — The policy changed between grant and consumption, all three rows
§7.2's table by observation: `APPROVE` unchanged; `DENY` refuses and leaves the approval
`granted`; `ALLOW` **consumes** it and the receipt says so. The third row is a behaviour change and
its old behaviour is asserted to be gone.

#### T174 — `policy_hash_at_approval` is recorded and differs when it should
Granted under one policy, consumed under another: both hashes are on the receipt and they differ.

#### T175 — The registry loads, cites, and refuses a dangling id
A control cited by no rule loads; a rule citing an id the registry does not define is a
`PolicyError` naming it; the receipt carries the union of the action's and the matched rule's ids.

#### T176 — `data_scope` is derived, reserved, and drives a decision
The derived label set matches the declared `data:` map; `contains`, `in` and `not_in` behave as
`policy.py`'s evaluator behaves elsewhere; a condition or an argument named `data_scope` in a
document of **any** schema version is refused at load, with the message naming the reservation.

#### T177 — Redaction covers evidence and never the approval payload
A redacted argument is `sha256:…` in the receipt, the events and the JSONL export; it is the real
value in `ApprovalRequest.action`, `PendingApproval` and `ctrlrun approve`'s output; and
`action_hash` is unchanged by redaction. **If item 7 cuts `redact:` (§7.4), this test is deleted
and §12 records the cut.**

#### T177c — The CLI surfaces exist and no command was added
`ctrlrun receipts --verify-chain` reports a break by `seq` and by name against a tampered store,
and reports the `unchained` count against a migrated v0.5 one; `ctrlrun receipts --control ID`
filters. The command list is asserted against §9.4's: `receipts`, `verify`, `effects`, `approve`,
`deny`, `resolve`, `inspect`, `stats`, `delegate`, `revoke`, `init`, `demo`, `gateway` — and
**nothing new** (§9.4).

#### T177d — An expired lease is displayed as expired, and displaying it changes nothing
`ctrlrun effects` shows a record whose lease has lapsed as `executing (lease expired)`, and the
record is **still** `EXECUTING` afterwards: reading is not a transition (§5.2). The test asserts
the store's record before and after the command.

#### T177b — The throwaway configuration loads and drives a decision, and ships nowhere
It lives in the test suite; a packaging test asserts no `packs/` path and no new `examples/` path
is in the wheel or the sdist.

### Item 8 — The soak (§8.1)

#### T178 — The harness detects an injected `AMBIGUOUS`
The soak's **positive control**: a failure is injected on purpose partway through and MUST appear
in the results table, attributed to its injection. A soak whose harness could not have detected an
unattributed `AMBIGUOUS` is not a result.

### Item 9 — Release (§9)

#### T179 — `docs/CLAIMS.md` resolves every line it cites
`v0.5`'s line-number test, unchanged: every `file.py:NNN` in the table is checked against the line
it names. It exists because the table had rotted through three hand-regenerations.

#### T180 — The README and the changelog do not blur alteration and authorship
`README.md`, `CHANGELOG.md`, `docs/postgres.md` and `THREAT_MODEL.md` are scanned for
`sign`, `signed`, `signing`, `signature`, `authorship`, `non-repudiation` and `tamper-proof`, on
**word boundaries** — `design` and `assign` are not hits — and every occurrence must be in the
test's **allow-list of exact lines**, each of which is a sentence that *disclaims* one of them.

The allow-list is the whole design, and the reason is worth stating: a plain forbidden-word list
would flag §6.4's own *"Not authorship"* and the corrected `THREAT_MODEL.md` line, which are the
sentences this rule exists to produce. It would then be removed as a false positive and the check
would be gone. An allow-list fails on a **new** occurrence — which is exactly the event worth
failing on — and forces whoever adds one to say, in the test, that they meant it.

The third rule of §1.2, made a test rather than an intention.

#### T181 — Core still installs nothing new
`pip install ctrlrun` installs `pyyaml` and `click` and nothing else; the wheel contains no
`adapters/` path, no `research/` path and no `packs/` path.

### 8.1 The soak

`research/soak/`, outside `src/`, never packaged, on the precedent of
`research/framework-probe/` (`v0.4 §7`).

**"Unexplained" is defined before the run starts**, or the exit criterion is unfalsifiable: an
`AMBIGUOUS` caused by an injected failure is **explained**; one with no corresponding injection is
**unexplained**. Every `AMBIGUOUS` is recorded with its cause, and `resolved_by` (§5.3) is what
makes "resolved by a hook" and "resolved by a human" countable separately.

- It runs for **at least a week** against a real Postgres. This is calendar time and **it does not
  compress.**
- It starts the hour item 4 goes green, and runs while items 5, 6 and 7 are written. Starting it
  late is how a one-week criterion becomes a one-week delay.
- The harness ships separately from the numbers, and **the maintainer reads the table before it
  goes in a PR** (`v0.4 §7.3`).
- **The exit criterion is `ROADMAP.md`'s and is unchanged: a week with *no* unexplained
  `AMBIGUOUS`.** An earlier draft of this section required only that the count be *published*,
  which is a different and weaker criterion — and quietly relaxing a release gate in the document
  that defines the release is precisely what §1.4 corrected two other sentences for in this same
  commit. A non-zero unattributed count does not ship; it is a finding, and it is investigated
  before v0.6 is tagged.
- **The count is published either way** — including if it is zero, and including if it is not.
  Publication is what makes the criterion checkable; it is not the criterion.
- **Item 9 does not tag until item 8 reports.** The exit criterion says a week, the changelog
  would say a week, and a tag before it finishes makes both untrue. This is the one claim in the
  project that would be exactly as false as it looks.

---

## 9. Public API additions (frozen for v0.6)

`StateStore` is one of v1.0's six frozen contracts. Everything below is promised for a long time,
which is why there is so little of it.

### 9.1 The names

```python
# ctrlrun/__init__.py — added
from .action import canonical_bytes
from .errors import SchemaMismatch

# ctrlrun.postgres — ctrlrun[postgres], lazy, NOT re-exported at package import
#   ctrlrun.postgres.PostgresStateStore

# ctrlrun.conformance.store — core, stdlib, NOT re-exported at package import
#   ctrlrun.conformance.store.run
#   ctrlrun.conformance.store.StoreBackend
#   ctrlrun.conformance.store.StoreReport
#   ctrlrun.conformance.store.SuiteResult
#   ctrlrun.conformance.store.CaseResult
```

```python
class PostgresStateStore:                    # §4 — satisfies StateStore, adds nothing
    def __init__(self, url: str, *, clock=..., schema: str = "public") -> None: ...

def run(backend: StoreBackend, *, only: Sequence[str] = (),
        processes: int = 8) -> StoreReport: ...          # §2.2
```

`ctrlrun.conformance.store` also exports **`SUITES`**, **`all_case_ids`**, **`SQLiteBackend`** and
**`InMemoryBackend`**. The first two are what a backend author passes to `only=` and what T140
checks its fixture table against; the last two are the reference implementations of
`StoreBackend`, and a backend author with no worked example would be writing against prose. An
earlier draft's *"and no other public name"* did not survive contact with the package's `__all__`,
and the honest resolution is to list them rather than to have a sentence nobody could obey.

```python
def canonical_bytes(payload: Mapping[str, Any]) -> bytes: ...
```

`canonical_bytes` is the **encoding half of `v0.1 §2.3`, promoted rather than written** (§6.2):
sorted keys recursively, `separators=(",", ":")`, `ensure_ascii=False`, UTF-8, `float` rejected at
any depth. `canonicalize(action)` is redefined to call it, so the chain (§6.2) and the policy hash
(§7.1) provably share one implementation with the action hash instead of having a second that
agrees today. It clears §9.2's bar in the only way a new public name can here: without it, the
alternative is a private second canonicalizer, and §6.2 forbids that by name.

`ctrlrun.postgres` also exports the six **branch names** of §4.3.4 — `A1_OURS`, `A1_REINSERT`,
`A1_REFUSE`, `A2_LANDED`, `A2_REISSUE`, `A2_REFUSE` — beside the `STATED_ABORTS` and
`AmbiguousWrite` it already had. They are constants a test asserts against, and spelling the
strings out at each call site is how a closed set stops being one. Listing them is the same
correction the conformance package's paragraph above records: a draft that says *"and no other
public name"* while the module has an `__all__` is a sentence nobody can obey.

**And no other public name.** No new `Control` method, no new store method, no new event type, no
new CLI command, no new approval provider, no new sink.

### 9.2 The two bars, stated separately

A **new `StateStore` method** faces the bar §1.1 sets: *a second backend could not be written
without it.* The expected number was **zero**. It is **two**, and both were found by item 1
rather than by item 3 — which is the ordering working, not failing.

#### `events()` and `receipts()`

```python
class StateStore(ApprovalStore, Protocol):
    def events(self) -> tuple[Event, ...]: ...      # every event, oldest first
    def receipts(self) -> tuple[Receipt, ...]: ...  # every receipt, oldest first
```

**These are not new methods. They are methods the protocol never declared and every store has
always had.** `SQLiteStateStore` and `InMemoryStateStore` have both implemented them since v0.1,
and they are called by `ctrlrun receipts`, `ctrlrun inspect`, `ctrlrun stats`,
`ctrlrun.verify.scenarios` — for the counterexample of `v0.4 §4.5` and for the assertion that the
operator's store was not touched — and by the v0.5 adapter conformance kit.

The bar is cleared in the plainest way it can be: **a second backend written against the declared
protocol alone would omit both**, satisfy every declared method, and break four callers. That is
not a hypothetical — it is what the store suite's `evidence` cases surfaced the moment they were
type-checked against `StateStore` rather than against a concrete store.

v0.6 also *adds* a caller with a stronger need than any of those: §6.5's chain reader enumerates
receipts ordered by `seq` to verify the chain, and `ctrlrun receipts --verify-chain` (§9.4) runs
it against whichever backend the operator has. A chain the protocol gives no way to read would be
an integrity guarantee that only the SQLite backend could check.

**Nothing changes behaviourally.** No store gains a method, no caller changes, no test changes;
the declaration is written down and the type checker can see it. It is recorded here rather than
made silently because §1.1 says a backend needing more of the protocol is a specification
amendment first, and the rule reads the same in the direction where the protocol was short.

#### The bar Postgres still faces

Unchanged: **zero**. Item 1's amendment is about what the *existing* stores already do; if item 3
finds Postgres needs something neither SQLite nor the in-memory store provides, **it stops** and
the disagreement becomes an amendment before any code.

A **new field on an existing record** faces a lower and different bar: additive, migrated, named,
and answering a question the existing fields cannot. Conflating the two would let a field in
through a method's argument, or keep a field out on a method's argument. So they are written
separately, and the fields v0.6 adds are listed with what each answers:

| Field | Answers | Why an existing field does not |
|---|---|---|
| `EffectRecord.resolved_by` | which authority moved this record out of `AMBIGUOUS` | it is currently inside the free-text `error` string, unqueryable and indistinguishable from an executor's message (§5.3) |
| `Receipt.seq`, `Receipt.prev_hash` | this receipt's place in the chain | nothing orders receipts durably today; `receipt_id` is random (§6.2) |
| `Receipt.policy_hash`, `Receipt.policy_version` | which policy decided this | `decision_reason` names a rule index, which is meaningless against a document that changed (§7.1) |
| `Receipt.controls` | which written expectation this rule serves | there is no field for it, and putting it in `decision_reason` would make one string carry two meanings (§7.3) |
| `ApprovalRecord.policy_hash_at_approval` | which policy was in force when a human said yes | the approval binds to `action_hash`, which is silent about the policy (§7.1) |

### 9.3 One new error type

**`SchemaMismatch(CTRLRunError)`** — raised at open when a store meets a database it does not
recognise, in either direction (§3.3). It clears the bar because an operator's process refusing to
start needs a distinguishable exception: mapping it to `InvalidArgument` would put *"your database
is from the future"* in the same bucket as *"your lease is negative"*, and the two have entirely
different remedies. It carries `applied`, `known` and the recorded `ctrlrun_version`.

### 9.4 The CLI

```
ctrlrun receipts [--last N] [--json] [--verify-chain] [--control ID] [--store-url …]
ctrlrun verify   [… unchanged …] [--store-url postgresql://…]
ctrlrun effects  [--state ambiguous] [--store-url …]   # an expired lease shows as expired (§5.2)
ctrlrun resolve  [--committed | --failed] [--store-url …]
ctrlrun inspect  [--json] [--store-url …]              # now carries `resolved_by` (§5.3)
ctrlrun approve  [--store-url …]
ctrlrun deny     [--store-url …]
ctrlrun stats    [--since …] [--json] [--store-url …]
```

**`--store-url` is on every command that opens the operator's store, and an earlier draft of this
block put it only on `verify`.** That draft did not survive being read against §5.3. `_store()`
returned `SQLiteStateStore(state_path())` unconditionally, so on the backend this entire milestone
exists for: `ctrlrun resolve` — §5.3's *human authority*, one of exactly two ways an `AMBIGUOUS`
record ever moves — could not reach the record; `ctrlrun effects` could not show §5.2's lapsed
lease; `resolved_by` had no read surface outside a Python session; and `ctrlrun approve` could not
answer a request. A milestone that adds a backend and leaves the operator's commands unable to
speak to it has not added the backend. A review found it.

It reads `CTRLRUN_STORE_URL` as well, because the alternative is every command in a runbook
carrying the same flag.

**It creates nothing.** `PostgresStateStore` creates no schema and neither does this: a command an
operator runs to *read* evidence must not have a side effect on the database it reads. A schema
that is not there is an error. That is `v0.4 §3.5`'s argument for verify's scratch store, pointed
the other way — verify may create because it owns what it creates, and these commands own nothing.

**Still no new command.** `--verify-chain`, `--control` and `--store-url` are flags on commands that already open the
store and already reads receipts. A `ctrlrun chain` command would be a second entry point into
the evidence and would need `v0.3 §4.3.1`'s treatment for no benefit; a flag on the reader is the
same code path with a different report.

### 9.5 Schemas

| Schema | Change |
|---|---|
| `ctrlrun.action/v1` | **unchanged.** The canonical form of an Action is untouched by this milestone, and every approval granted before it still verifies |
| `ctrlrun.policy/v4` | new: `version:` (§7.1), `controls:` (§7.3), `data:` (§7.4). `v1`, `v2`, `v3` still load |
| `ctrlrun.receipt/v3` | new fields: `seq`, `prev_hash`, `policy_hash`, `policy_version`, `controls`. Every `v2` field keeps its meaning |
| `ctrlrun.guarantees/v2` | G11 added (§6.6) |
| `ctrlrun.store-conformance/v1` | new: the store suite's report document (§2.2) |
| `ctrlrun.verify/v1`, `ctrlrun.framework-probe/v1` | unchanged |
| `ctrlrun.inspection/v2` | **version unchanged, one additive field**: `effect.resolved_by` (§5.3). A draft of this row said "unchanged" outright, which was wrong the moment §5.3 named `ctrlrun inspect` as a place a reader finds the resolver — the command had to carry it, and a document that gains a key has changed. The version does not move because `v2` readers are unaffected: nothing is removed, nothing is renamed, no value changes meaning, and a reader that does not know the key ignores it. That is the same additive rule `ctrlrun.receipt/v3`'s row states, applied to a document whose readers are scripts rather than stores |

**A `ctrlrun.receipt/v3` writer and a ≤ 0.5 reader do not mix**, for `v0.3 §12.2`'s reason and
with the same instruction: `Receipt.from_dict` parses into closed sets, so **upgrade every reader
before upgrading any writer.** The chain fields are additive and a `v2` reader tolerating unknown
keys survives; one that does not, does not.

### 9.6 What v0.6 amends in v0.1–v0.5

Six amendments, each in the item that makes it true.

1. **`v0.1 §5.3`'s SQLite implementation note** — *"SQLite implementation: `PRAGMA
   journal_mode=WAL; … BEGIN IMMEDIATE`"* — becomes one of two named implementations of E1, with
   §4.2's the other. **E1 itself is unchanged**; only the sentence that named one mechanism as if
   it were the guarantee changes (item 3).
2. **`v0.4 §3.1`'s `--store-url` reservation is discharged** (item 3). The flag accepts a
   `postgresql://` URL; the exit-2 message no longer names v0.6 in the future tense, and its test
   changes in the same commit.
3. **`v0.3 §5.2`, `v0.4 §9.4` and `v0.5 §9`'s "there is still no migration story — that is v0.6"
   is discharged** (item 2).
4. **`v0.1 §6.1`'s receipt is amended additively** (item 6): `seq` and `prev_hash` join the
   document, `hash` is stored beside it, and the *"no signatures in v0.1 (v0.6)"* parenthesis
   becomes *"no signatures; see SPEC-v0.6 §11"*, because v0.6 does not add them.
5. **`docs/ROADMAP.md`'s v0.6 bullet and `docs/THREAT_MODEL.md`'s signing limitation are
   corrected in the same commit as this document** (§1.4), on the rule `v0.4 §9.4` set.
6. **An approval presented against a re-evaluated `ALLOW` is invalidated** (item 7, §7.2, §7.2.2),
   where today it is left `granted` for its full TTL. **`v0.1 §4.2 A4` is untouched**, and that is
   the point of the design: the invalidation happens in a write of its own *after* the reservation
   succeeds, precisely so the approval is never an input to an allowed action and can never refuse
   one. A4 continues to describe the `APPROVE` path, atomically, unchanged.

### 9.6.1 What the independent review of this document changed

A review in a session that did not write this document found **twenty-one defects**, four of which
would each have produced an insecure or unimplementable v0.6. They are recorded because this
project requires a declined finding's reasoning to live in the document it declines a change to —
and an *accepted* finding that silently rewrote a section leaves the next reader unable to tell
which sentences were argued and which were repaired.

| Found | Where it is now |
|---|---|
| §4.3.2's re-read concluded *"a record carrying **our** `action_id` → we hold it, proceed"*. `action_id` is caller-supplyable and §5.1 blesses two attempts sharing one, so another process's **live reservation** satisfied it: one logical effect executed twice, through the storage layer. It was also weaker than `plan_reservation`, which refuses a live lease without consulting `action_id` at all | §4.3.2 Table A1 row 1 and §4.3.3 — an identity check on every column written, anything else back through the planner; T155c |
| §6.3 said a failed receipt write is *"logged, not raised — `v0.1 §6.1`'s rule, unchanged"*. That rule is about the **JSONL file**; the same paragraph says the store is authoritative, and `Control._record` calls `put_receipt` unguarded. The draft was a silent-evidence-loss change, in the section about evidence integrity, presented as the status quo | §6.3.1, which keeps the shipped behaviour, states the residual, and is recorded in §9.6; T170 rewritten |
| §3.2 recorded `0001_baseline` *"without running its DDL"*. "Baseline" matches a v0.1 **or** v0.2 database too, and `continuations` and `delegations` arrived after v0.1 — so every continuation and delegation call on an adopted v0.1 database would have died on `no such table` | §3.2, which runs the DDL; T152, which builds fixtures from four releases instead of one |
| §7.4 required `data_scope` to be both a reserved argument name and a condition subject. Reserving a name is what makes `claims_eq:` a load error, so `data_scope_…:` would have been one too — the very condition the section asks operators to write. It also invented an expression grammar the language does not have, and added two operators to a set **shared with authority constraints** | §7.4 — the mapping grammar, no new operator, and an explicit derived-subject allow-list distinct from `RESERVED_ARGUMENTS` |
| §2.5's *"no store method writes `FAILED` except `fail_effect`"* is false of both shipped stores: `resolve_effect` writes it, and is `ctrlrun resolve --failed`. The suite would have failed a correct backend, or invited an implementer to remove the only route out of `AMBIGUOUS` | §2.5 and §10 — *"on a refusal path"* |
| §4.3.2's single table was written for the reservation `INSERT` and gave the wrong answer for every compare-and-set: a lost `COMMIT` leaves the record in its **pre-state**, which the draft read as *"somebody moved it, refuse"* — turning a committed effect into a human's problem and a proven non-execution into `AMBIGUOUS` | §4.3.2 Table A2, whose row 2 re-issues; T155b |
| §6.3's head compare-and-set retried **once**, importing a bound from §4.2 whose justification does not transfer to a row every writer updates. Three concurrent writers would have dropped a receipt silently | §6.3 — the head row's lock is taken first, so contention blocks instead of dropping |
| `durability` could not pass `InMemoryStateStore`, whose storage *is* the object, while T141 allowed exactly one N/A | §2.4's second honest N/A, pinned to `reopen()` returning `None`, with `falsely-declares-no-url` keeping the declaration honest |
| No fixture could fail `reservation`: a broken store is an in-process wrapper, and the cross-process case's subprocess opens the real backend. T140 — the only check that the suite can fail — could not pass | §2.4's in-process E1 case; T143, which asserts the two cases are non-redundant in both directions |
| `--store-url` is on **`ctrlrun verify` alone**. A `postgresql://` value would have had verify open, auto-migrate and write scratch tables into an operator's database — contradicting §3.6 and §6.6 in the same document | §4.1's scratch-schema rules; T154e; and `StoreBackend.url()` no longer calls itself a `--store-url` value |
| §4.2's *"nothing has been written; the transaction rolls back"* is false on two paths that already commit before raising — the lapsed lease made `AMBIGUOUS`, and the expired approval | §4.2.2, which names both and says why they are evidence rather than permission |
| §7.2's `ALLOW` row consumed the approval *"in the same transaction"*. `consume_approval_and_reserve` checks the approval **first**, so an expired or already-spent grant would have **refused an action the policy allows** | §7.2.2 — invalidate after the reservation, never as an input to it; §7.2.3 for `resume` and observe mode; T173b, T173c |
| §3.3 left a reachable classification undefined — every applied id known but **not contiguous** — and the natural guess applies a migration out of order | §3.3's five exhaustive shapes, with the gapped one refused |
| §9.5 listed `resolved_by` on `ctrlrun.receipt/v3`; §9.2, §5.3 and §3.7 all added it to `effects` only. A resolution happens after the receipt was written and hashed | §9.5's row, and §5.3's paragraph on why no receipt carries it |
| §7.2's two rows reasoned in opposite directions about the same hazard, and only one carried an argument | §7.2.1 |
| §6.2 and §7.1 both invoked *"`v0.1 §2.3`'s canonicalizer"* over a document. No such function exists — `canonicalize` takes an `Action` — so the draft required either a new public name it forbade or the second canonicalizer §6.2 forbids by name | `canonical_bytes` promoted in §9.1, `canonicalize` redefined in terms of it, T164b proving no hash changed |
| §6.5's `missing` and `head_mismatch` needed a receipt order no store provides | §6.5 — the reader orders by `seq`; `NULL`-`seq` rows have no position |
| §8.1 required only that the unattributed `AMBIGUOUS` count be **published**, where `ROADMAP.md`'s exit criterion is that it be **zero** — a release gate relaxed in the document defining the release | §8.1, restated as ROADMAP has it |
| Five §10 rows and two §9.4 surfaces had no test; T142 asserted a `--only` **flag** that §9.4 forbids | T154e, T154f, T177c, T177d, T173b; T142 corrected to the `run()` keyword |
| `0002_receipt_chain` did not say what the head row starts at, so `head_mismatch` could fire on a database whose receipts are all `unchained` | §3.7 — `seq = 0`, genesis hash |
| §5.4 asked for a distinguishable "already taken" refusal that `take_continuation` deliberately does not give, its message being identical by design | §5.4 — distinguished by exception **type**, with the disclosure argument stated |

Three of the four blockers were invisible from the diff and visible only from the shipped code —
`control.py`'s unguarded `put_receipt`, `policy.py`'s condition splitter, and `state.py`'s
`_SCHEMA` running on every open. That is the rule's whole justification, and it is the third
consecutive milestone in which it has held.

### 9.7 The module map

`ARCHITECTURE.md` §6 gains two rows, and the dependency direction is unchanged — downward only,
with `Control` the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `postgres.py` | `PostgresStateStore`: the same protocol, a different mechanism | policy, decorator, sinks — `state.py`'s row, unchanged |
| `conformance/store/` | the store suites, the broken-store fixtures, the report, the worker | the gateway, `otel`, `jwt_identity`; anything from an extra but the backend it was handed |

`postgres.py` sits **beside** `state.py`, at the same level, and imports from it the pure planning
functions and the record types. It does **not** subclass `SQLiteStateStore`: a shared base class
would make one backend's behaviour the other's default, which is the drift §2's suite exists to
catch and would be the one thing it could not see.

`conformance/store/` sits above `control.py` beside `verify/` and `cli/`, as `v0.5` put
`conformance/`. Nothing in the kernel imports it. It is a **package** rather than a module
because §2.4's cross-process case is reached as `python -m ctrlrun.conformance.store.worker`, and
a module cannot carry a submodule.

**It reuses `conformance/report.py`'s `CaseResult`, `SuiteResult` and `SuiteStatus` rather than
declaring its own.** A second set of result types that agreed with the first today is the drift
this document refuses elsewhere for canonicalization (§6.2), and the N/A accounting — a
denominator of applicable cases, `ok` false for `0/0` — is the rule `v0.4 §3.8` fixes once. Only
`StoreReport` is new, and it differs from `ConformanceReport` in one field: the subject is a
backend rather than a framework.

The migration runner lives **in `state.py`**, not in a module of its own: it is the thing that
decides whether a store may open, and a store whose admission check lived somewhere else would be
a store with two front doors.

---

## 10. Fail-closed table for v0.6

`v0.1 §3.4`, `v0.2 §6.11`, `v0.3 §9`, `v0.4 §10` and `v0.5 §10` hold in full and unchanged. These
rows are v0.6's own, and none of them is configurable.

| Condition | Result |
|---|---|
| A database records a migration this binary does not know | `SchemaMismatch` at open. No table is read (§3.3) |
| A database is missing a known migration **and** carries an unknown one | `SchemaMismatch` at open, naming both. Nothing is applied |
| A database has neither `schema_version` nor `effects`, but has other tables | Refused at open, naming what was found (§3.2) |
| A migration raises part way | The transaction rolls back; the database stays at its old version; the store refuses to open (§3.4) |
| The database user cannot run DDL | The store refuses to open, naming the missing privilege (§3.6) |
| `server_encoding` is not `UTF8` | Refused at open (§4.4) |
| An exception before `COMMIT` | Store write is `FAILED`. No effect state written. Retryable (§4.3 Table A) |
| SQLSTATE `40001` / `40P01`, anywhere | Store write is `FAILED`. A stated abort, per `v0.2 §6.8`'s principle (§4.3.1) |
| An exception during or after `COMMIT` | **Ambiguous store write.** Re-read (§4.3.2). Never `FAILED` |
| The re-read fails | No effect state written; execution refused; the store exception propagates (§4.3.2) |
| A store method would write `FAILED` other than `fail_effect` | Forbidden. `outcome` fails the backend (§2.5) |
| A store method raises `NotExecuted` | Forbidden. `outcome` fails the backend (§2.5) |
| A `rowcount == 0` on a compare-and-set | Re-read and re-plan **once**; a second zero raises rather than loops (§4.2) |
| A `RESERVED` record whose holder is gone | Nothing is concluded. The lease governs, and only a human or a reconcile hook moves it on (§5.1, §5.3) |
| An expired lease | `AMBIGUOUS` at the next reservation attempt. Never released, never reclaimed, never swept (§5.2) |
| A continuation whose lease lapsed | Refused; the effect becomes `AMBIGUOUS` by the ordinary path (§5.4) |
| A receipt whose chain write fails | **Raises to the caller.** No numbered receipt, no advanced head, no gap; the effect record is already `COMMITTED` so a retry is refused, and `EXECUTION_COMMITTED` is already in the events log (§6.3.1) |
| A receipt with `seq IS NULL` | `unchained`. Counted, named, **never a pass** (§6.5) |
| The chain head is missing | The report says so and does **not** report a valid chain (§6.5, T167) |
| A rule cites an undefined control id | `PolicyError` at load, naming it (§7.3) |
| An argument or condition named `data_scope` | Refused at load, in a document of any schema version (§7.4) |
| The policy re-evaluates to `DENY` at consumption | The action is refused; the approval is left `granted` (§7.2) |
| A redacted argument in an approval payload | Not redacted. A human sees what they approve (§7.4) |
| Every case in a store conformance run is `not_applicable` | `report.ok` is `False`. `0/0` is not a pass (§2.4) |

---

## 11. Explicitly out of scope for v0.6

Everything in `v0.1 §9`, `v0.2 §12`, `v0.3 §13`, `v0.4 §11` and `v0.5 §11` that v0.6 does not
deliver, and specifically:

- **Signing receipts.** It is **issuing** — key generation, rotation, revocation, and a story for
  what a receipt means after a key is compromised — and this project verifies what it is handed
  (`v0.3 §1.1`). A chain detects alteration; a signature proves origin, and proving origin brings
  a key management problem that is larger than everything else in this milestone put together.
  Noted rather than left as a gap: **a chain plus an external timestamp or anchor gets most of the
  benefit without keys** — a periodic publication of the chain head to somewhere the operator does
  not control bounds when history could have been rewritten. That is a **v0.8+** option and it is
  not in v0.6.
- **A downgrade path.** Forward-only (§3.6).
- **Sector packs.** They are their own version line, they depend on this milestone's registry and
  on nothing after it, and v0.7 follows v0.6 whether or not a single pack exists. Item 7 ships the
  primitives a pack configures and **no pack** (§7.5).
- **`docs/CONTROL-MAPPING.md`.** `ROADMAP.md` says it is written only when a design partner asks.
  A mapping table written speculatively is a compliance claim with no customer behind it.
- **Any compliance, conformance, certification or alignment claim**, in this document, the README,
  docstrings, CLI output or a control's `source:` string. "Store conformance suite" names this
  repository's own acceptance tests and §2.1 says so on its first line.
- **A second store backend beyond Postgres.** No MySQL, no DynamoDB, no Redis, no
  "pluggable backend registry". Two backends is what proves the protocol is a protocol.
- **A connection pool, a leader election, an advisory lock, or a background sweeper.** §4.2.1,
  §4.4 and §5.2 each say why.
- **A process-identity field on any record**, and any reclaimer built on one (§5.1).
- **Data scope beyond §7.4's labels and redaction.** No row-level filtering, no purpose limitation,
  no field-level authorization, no query rewriting. CTRLRun is not a database proxy and it is not
  DLP.
- **Matching a grant on a data label.** Authority addresses `agent` and `user` (`v0.3 §4.2`) and
  that is unchanged; `data_scope` is a policy condition and nothing more.
- **A consequence taxonomy, separation of duties, multi-approver workflows, M-of-N, break-glass,
  authenticating the approver.** Unchanged from every release before this one.
- **Authority propagation across agent hops / A2A.** v0.7.
- **Anything in `VISION.md`**, and no dashboard, no web UI, no management plane.

---

## 12. What building v0.6 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md §12` and `SPEC-v0.5.md §12` are the format.*

**This section is empty on purpose and the later items fill it.** The discipline is not
decoration: `v0.5`'s item 6 could tell which parts of that document had been stress-tested by
somebody other than their author **by looking for a §12 entry behind them**, and all four of its
most serious findings sat in sections that had none. The arguments are written down as they are
decided, not afterwards.

Four questions are already known to be open, and each names the item that closes it:

- **Whether `redact:` survives item 7.** §7.4 puts it on probation and requires the throwaway
  configuration to justify it or item 7 to cut it.
- **What item 1's suite finds between `SQLiteStateStore` and `InMemoryStateStore`.** §2.7 says a
  suite that finds nothing is too weak, and requires each divergence to become a paragraph there
  and a case in T146.
- **Whether item 3 needs anything from the `StateStore` protocol.** §9.2 expects nothing, and says
  what happens if that expectation is wrong: the item stops.
- **What the soak's unattributed count is.** §8.1 requires it published either way.

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
# ctrlrun/conformance/store.py — core, stdlib

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
        """A `--store-url` value a *subprocess* can open, or `None`.

        `None` means the storage cannot be reached from another OS process, which is a
        property of the backend and not of the harness — `InMemoryStateStore` says so in its
        own docstring. The cross-process cases are then `not_applicable` with that reason.
        """

    def reset(self) -> None:
        """Discard everything. Called between cases; the suite never reuses state."""
```

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
| `reservation` | `v0.1 §7` T1, T3, T8, T9 | E1 across processes · the §5.4 retry table, every row · a `FAILED` record renews with `attempt + 1` · an expired lease becomes `AMBIGUOUS` and is never released |
| `approval` | `v0.1 §7` T2, T4, T5, T12 | `consume_approval` refuses a different `action_hash`, a consumed one, an expired one · a refused reservation leaves the approval `granted` · the approval is checked first when both would refuse |
| `resolution` | `v0.1 §7` T10 | only an `AMBIGUOUS` record resolves, and only to `COMMITTED` or `FAILED` |
| `outcome` | `v0.1 §7` T1, and §5.5 | **no store method writes `FAILED` except `fail_effect`, and no store method raises `NotExecuted`** (§2.5) |
| `durability` | new; the statement `v0.1 §5.2` makes and no test drives | every terminal record — `AMBIGUOUS` above all — survives a `reopen()`, and a blind retry is still refused after one |
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

### 2.4 The cross-process case, and the one honest N/A

`reservation`'s E1 case is `v0.1 §7` T3 unchanged in its standard: **N contenders, one winner**,
the fake remote called exactly once, one committed record and N−1 refused. It runs in `processes`
OS processes started as `python -m ctrlrun.conformance.store.worker` with the backend's `url()`
and the payload on stdin — subprocesses rather than `multiprocessing`, for `v0.4 §12.5`'s reason:
`spawn` re-imports the caller's `__main__`, and requiring an `if __name__ == "__main__"` guard in
a backend author's test file is not a trade a conformance suite gets to make.

Where `url()` is `None` the case is `not_applicable` with the reason **`this backend's storage
cannot be opened from another process`**. That is a legitimate N/A — a property of the backend,
which `InMemoryStateStore` states in its own docstring — and it is the **only** N/A any suite in
§2.3 may report on the two backends that already exist. Every other case is applicable to every
backend, and a backend reporting one N/A has failed it. `v0.4 §3.8`'s rule holds here in full:
**not applicable is not a pass**, the denominator counts applicable cases only, and there is no
flag that folds one into the count.

### 2.5 `outcome`, and why it needs no fault injection

The `outcome` suite is the store-layer expression of `v0.1 §5.5`'s asymmetry, and it is the one
suite whose subject is a *negative*: what a store must never do.

- **No `StateStore` method raises `ctrlrun.NotExecuted`.** `NotExecuted` is the executor's opt-in
  to `FAILED` and the one exception an agent may read as permission to retry. A store that raised
  it would be asserting something about a remote it has never spoken to.
- **No `StateStore` method writes `FAILED` to an effect record except `fail_effect`.** Every
  other method's refusal path is driven — a duplicate reservation, a consumed approval, a
  transition from the wrong state, a lease that lapsed, a broken continuation, an unknown
  delegation id — and after each the record is read back and MUST NOT be `FAILED`.
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
| `two-winners` | Drops the uniqueness check: both contenders reserve one key | `reservation` |
| `releases-an-expired-lease` | Treats a lease-expired `RESERVED` record as free and re-reserves it instead of moving it to `AMBIGUOUS` | `reservation` |
| `grants-twice` | `consume_approval` returns the `Approval` and leaves the record `granted` | `approval` |
| `consumes-before-reserving` | Sequences `consume_approval` then `reserve_effect` in two transactions, so a refused reservation has already spent the approval | `approval` — T12's case |
| `resolves-anything` | `resolve_effect` moves a `COMMITTED` record | `resolution` |
| `guesses-failed` | Writes `FAILED` when a write refuses, rather than leaving the record alone | `outcome` |
| `raises-not-executed` | Raises `NotExecuted` from `commit_effect` when the write refuses | `outcome` |
| `forgets-the-unknown` | Holds `AMBIGUOUS` in memory and loses it across a `reopen()` | `durability` |
| `coerces-an-argument` | Round-trips `arguments` through a lossy encoding — an `int` returns as a `float`, a long string is truncated — so the stored `Action` no longer hashes to what was approved | `evidence` |
| `renumbers-events` | `append_event` returns an event carrying an id other than the one it stored | `evidence` |
| `admits-two-resumptions` | `take_continuation` does not consume the token in the transaction that admits it | `continuation` |
| `upserts-a-delegation` | `put_delegation` upserts on a duplicate id, clearing `revoked_at` — unrevoking by another door | `delegation` |

**Every suite is named by at least one fixture, and every fixture names a suite that exists.**
Both directions are asserted (T140), on `v0.5 §5.4`'s finding: a fixture pointed at a renamed
suite passes its own test by never being checked against anything, and a suite no fixture fails
would report `pass` for every backend ever written.

`guesses-failed` and `raises-not-executed` are two fixtures for one suite because `outcome` has
two checks and a single fixture would leave one subsumed — `v0.5 §5.4`'s `denial` finding, one
layer down. `releases-an-expired-lease` exists because `two-winners` fails `reservation`
incidentally on the lease case too, and a check whose only fixture reaches it by accident is a
check nothing is aimed at.

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

Where item 1 finds others, each is added to this section with the same shape: the divergence, the
decision, and the case number that pins it.

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
| No `schema_version`, and an `effects` table | **baseline** — a v0.1–v0.5 database | `0001_baseline` is recorded **without running its DDL**, because its tables already exist; every later migration is then applied in order |
| `schema_version` present | **versioned** | §3.3 |

`0001_baseline` is exactly the `_SCHEMA` script v0.5 shipped, and its DDL is `CREATE TABLE IF NOT
EXISTS` throughout, so the distinction above is about *recording* rather than about the writes.
It is drawn anyway, because "run it, it is idempotent" is an argument that stops being true the
first time a migration is not.

**A database with neither `schema_version` nor `effects` but with other tables present is
refused**, naming what it found. It is somebody else's database, and creating CTRLRun's tables in
it is not a recovery.

### 3.3 Refusal, in both directions

Let `known` be the binary's ordered migration ids and `applied` be the set read from
`schema_version`.

- **Forward — a newer binary, an older database.** `applied ⊂ known` and `applied` is a prefix of
  `known`: apply the missing ones in order, recording each. This is the ordinary case and it
  happens **automatically, at open**, with no flag to suppress it (§3.6).
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
| `0002_receipt_chain` | `receipts.seq`, `receipts.prev_hash`, `receipts.hash`; the `receipt_chain` head row | §6 |
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
3. If the plan refuses, raise. Nothing has been written; the transaction rolls back.
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
be usable:

| What the re-read finds | Conclusion |
|---|---|
| A record carrying **our** `action_id` in the state we were writing | the commit landed; we hold it; proceed |
| **No** record | the commit did not land; retry the insert, **once**, then §4.2 step 5 |
| A record carrying **another** `action_id` | somebody else holds it; refuse per `v0.1 §5.4` |
| A record carrying our `action_id` in a **different** state | somebody moved it — a lapsed lease made `AMBIGUOUS` by a contender is the reachable case; refuse per `v0.1 §5.4` |

**Only if the re-read itself fails** does the store refuse and write nothing. The cost of that is
stated plainly rather than hedged, because it is the operational consequence of the whole design:
if the lost `COMMIT` did land, an effect key is now `RESERVED` by an attempt that will never
execute, its lease will expire, and the next contender will make it `AMBIGUOUS` and need a human
(§5). **That costs availability. It never costs a double execution**, and that is the trade this
library exists to make.

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
| A human | `ctrlrun resolve <effect_key> --committed \| --failed` | `resolved_by = "cli:<user>"` |
| A reconcile hook | `v0.2 §2`, and **only where its answer points** — `"unknown"` changes nothing | `resolved_by = "reconcile:<action name>"` |

**`EffectRecord.resolved_by` is a new field and `effects.resolved_by` a new column**, migrated by
`0003_resolved_by` (§3.7). Today the resolver's identity is written into the record's free-text
`error` string, which means the one field that says *a human overrode the kernel* is not
queryable and is indistinguishable from an executor's error text. §5's whole argument is that
those two authorities are different, and item 8's soak has to count them separately to say
anything at all about unexplained ambiguity. §9.2 states the bar this cleared and why it is a
different bar from the one a new **method** faces.

The `error` string keeps what it already keeps — `v0.1`'s *"resolved committed by X (was: …)"* —
because a receipt already written must not change meaning. `resolved_by` is additive.

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

T163 asserts all four, and asserts the **reason** and not only the status: a resumption refused
because the lease lapsed and one refused because the token was already taken are different facts
and an operator reading the events needs to know which.

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

### 6.3 The head

```sql
CREATE TABLE IF NOT EXISTS receipt_chain(
  id   INTEGER PRIMARY KEY CHECK (id = 1),
  seq  INTEGER NOT NULL,
  hash TEXT NOT NULL
);
```

One row. `put_receipt` inserts the receipt and advances the head **in one transaction**, as a
compare-and-set: `UPDATE receipt_chain SET seq = ?, hash = ? WHERE id = 1 AND seq = ?`, row count
checked, retried **once** on zero (a concurrent writer got there first; re-read the head and
rebuild the receipt's `seq` and `prev_hash`), then refused.

The head exists to detect **truncation at the end**. Deleting the last N receipts leaves a chain
that is internally consistent; only a head that still names a `seq` and a `hash` no row carries
catches it.

Two costs, both stated:

- **Every receipt write now contends on one row.** This is the first place in the kernel where
  two unrelated actions contend, and it is a throughput ceiling. Item 8's soak measures it, and
  `docs/postgres.md` says so.
- **A receipt whose write fails is logged, not raised** — `v0.1 §6.1`'s rule, unchanged and for
  its original reason: by the time `put_receipt` runs, the effect has committed at the remote, and
  raising there reaches the caller as an exception on a successful action, which an agent reads as
  a failure and retries. Because the insert and the head advance are one transaction, a failed
  write leaves **no** numbered receipt and an unadvanced head, so it produces a *missing* receipt
  and never a broken chain. §6.4 says what that means and does not pretend it means nothing.

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
- **Not that every action wrote a receipt.** The chain proves that the receipts which were written
  were not altered. A receipt that failed to write (§6.3) leaves no gap in `seq` and is invisible
  to the chain by construction; it is visible in the **events** log, which is the other evidence
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
| `DENY` | the action is **refused**, and the approval is left `granted`. The human's answer is not spent on an action that did not run, and the request stays presentable if the policy is corrected |
| `ALLOW` | the approval is **consumed anyway**, in the same transaction, and the receipt notes that it was consumed against an `allow` decision |

**The `ALLOW` row is a change to shipped behaviour and is the reason this section exists.** Today
a re-evaluation that returns `ALLOW` leaves `approval_id` unset, so the presented approval is
never consumed: it stays `granted` for its full TTL, for a hash that a later policy edit could
make `APPROVE`-requiring again — a live bearer token for an action a human already answered.
`v0.1 §4.1` calls a request id a bearer token in as many words. Consuming it closes that, costs
nothing (it is the same one-transaction call), and keeps the invariant an operator would expect:
**an approval a human granted is spent exactly once, by the action it was granted for.**

Fail-closed in the only direction that matters: the `DENY` row refuses, and the `ALLOW` row spends.

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
      - when: amount > 50000
        decision: approve
        controls: [maker-checker-refunds]    # a rule may narrow or add
```

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
      - when: data_scope contains phi
        decision: approve
```

`data_scope` is the **set of labels present in this action's arguments**, derived at evaluation.
Three operators address it — `contains`, `in`, `not_in` — reusing the condition evaluator
`policy.py` already owns and `authority.py` already shares (`v0.3 §4.5`), so there is no second
place for `True` to start comparing equal to `1`.

`data_scope` joins `claims`, `issuer` and `expires_at` as a **reserved condition name**, refused
at load in a document of **any** schema version, for `v0.3 §12.1`'s reason and at `v0.3 §12.1`'s
stated cost: a policy whose protected function takes an argument called `data_scope` stops
loading, and the load error says so. Gating the reservation on `v4` would leave one name meaning
two things in two files, which is the ambiguity `v0.1 §3.2` refuses.

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
Each of §2.6's twelve broken stores is run through `ctrlrun.conformance.store.run`. Each MUST
fail the suite named in its row, and the assertion is on the **case** that failed and its
**reason**, not only on the suite's status. Both directions of the coverage rule are asserted:
every suite in §2.3 is named by at least one fixture, and every fixture names a suite that exists.
A fixture that fails nothing is a failure; a fixture whose named suite passed is a failure.

#### T141 — Both shipped backends pass every suite
`SQLiteStateStore` and `InMemoryStateStore` report `pass` for every case, with the single
exception of `reservation`'s cross-process case for `InMemoryStateStore`, which is
`not_applicable` with §2.4's reason. No other N/A is accepted, from either backend.

#### T142 — The report refuses a degenerate run
Every case `not_applicable` → `report.ok` is `False`. `--only` naming an unknown case exits
non-zero. `0/0` is not a pass (`v0.4 §3.8`).

#### T143 — E1 across OS processes, driven by the suite
The suite's own cross-process case against a real SQLite file: `processes` subprocesses, one
winner, N−1 refused, the fake remote called exactly once. It is `v0.1 §7` T3's standard reached
through the suite rather than a second implementation of it, and T3 itself is unchanged.

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

#### T152 — Adoption, all four classifications
Empty database → migrated to head. A v0.5 database → adopted at `0001_baseline` then migrated. A
database with foreign tables and no `effects` → refused, naming what it found. A database at head
→ opened, nothing applied.

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

#### T154d — `Control` never maps a store exception through `v0.1 §5.5`
A store whose `commit_effect` raises an arbitrary exception: the effect record is not `FAILED`,
the caller sees the store's exception, and nothing in the path calls `NotExecuted`'s branch.

### Item 4 — Cross-host concurrency and failure injection (§4.5)

#### T155 — The connection dies during `COMMIT`, and the re-read resolves it
Deterministic: the window is opened on purpose by killing the connection at `COMMIT` rather than
by racing. The effect ends `AMBIGUOUS` or reserved-by-us **as the re-read determines**, never
`FAILED`; a blind retry against that key is refused; the events name which branch of §4.3.2 ran.
**The single most important test in this milestone.**

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

#### T170 — A failed receipt write leaves no gap and is not raised
The receipt insert fails. Nothing is raised to the caller, the head is unadvanced, `seq` has no
gap, and the events log still carries the action — §6.3 and §6.4's fourth bullet, asserted
together because either alone would be misread.

### Item 7 — Policy versioning and the control registry (§7)

#### T171 — The hash is over the rules, not the bytes
Two documents differing only in comments, key order and whitespace produce the **same**
`policy_hash`; changing any rule, `mode`, `environment` or grant changes it. `version:` alone does
not.

#### T172 — Receipts carry both, and the hash is authoritative
`policy_hash` and `policy_version` on every receipt; two policies sharing a `version:` string and
differing in content are distinguished by hash in the evidence.

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
- **The unattributed count is published including if it is zero**, and including if it is not.
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

**And no other public name.** No new `Control` method, no new store method, no new event type, no
new CLI command, no new approval provider, no new sink.

### 9.2 The two bars, stated separately

A **new `StateStore` method** faces the bar §1.1 sets: *a second backend could not be written
without it.* The expected number in v0.6 is **zero**, and if item 3 disagrees it stops and amends
this section before writing code.

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
ctrlrun receipts [--last N] [--json] [--verify-chain] [--control ID]
ctrlrun verify   [… unchanged …] [--store-url postgresql://…]
ctrlrun effects  [--state ambiguous]        # now shows an expired lease as expired (§5.2)
```

**No new command.** `--verify-chain` and `--control` are flags on a command that already opens the
store and already reads receipts. A `ctrlrun chain` command would be a second entry point into
the evidence and would need `v0.3 §4.3.1`'s treatment for no benefit; a flag on the reader is the
same code path with a different report.

### 9.5 Schemas

| Schema | Change |
|---|---|
| `ctrlrun.action/v1` | **unchanged.** The canonical form of an Action is untouched by this milestone, and every approval granted before it still verifies |
| `ctrlrun.policy/v4` | new: `version:` (§7.1), `controls:` (§7.3), `data:` (§7.4). `v1`, `v2`, `v3` still load |
| `ctrlrun.receipt/v3` | new fields: `seq`, `prev_hash`, `policy_hash`, `policy_version`, `controls`, `resolved_by`. Every `v2` field keeps its meaning |
| `ctrlrun.guarantees/v2` | G11 added (§6.6) |
| `ctrlrun.store-conformance/v1` | new: the store suite's report document (§2.2) |
| `ctrlrun.verify/v1`, `ctrlrun.inspection/v2`, `ctrlrun.framework-probe/v1` | unchanged |

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
6. **`v0.1 §4.2 A4`'s consumption rule gains one case** (item 7): an approval presented against a
   re-evaluated `ALLOW` is consumed, in the same transaction, rather than left granted (§7.2).
   A4's atomicity is unchanged; what changes is when consumption happens.

### 9.7 The module map

`ARCHITECTURE.md` §6 gains two rows, and the dependency direction is unchanged — downward only,
with `Control` the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `postgres.py` | `PostgresStateStore`: the same protocol, a different mechanism | policy, decorator, sinks — `state.py`'s row, unchanged |
| `conformance/store.py` | the store suites, the broken-store fixtures, the report | the gateway, `otel`, `jwt_identity`; anything from an extra but the backend it was handed |

`postgres.py` sits **beside** `state.py`, at the same level, and imports from it the pure planning
functions and the record types. It does **not** subclass `SQLiteStateStore`: a shared base class
would make one backend's behaviour the other's default, which is the drift §2's suite exists to
catch and would be the one thing it could not see.

`conformance/store.py` sits above `control.py` beside `verify/` and `cli/`, as `v0.5` put
`conformance/`. Nothing in the kernel imports it.

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
| A receipt whose chain write fails | Logged, not raised. No numbered receipt, no advanced head, no gap (§6.3) |
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

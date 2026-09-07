---
title: "Running on Postgres"
description: "Connection strings, what to grant, migrations at open, what happens on failover, and what the store does not do for you."
---

`PostgresStateStore` puts the state store on a database instead of a local file. It implements
the same `StateStore` protocol as `SQLiteStateStore`, frozen in `SPEC-v0.1.md` §5.3, and extends
it by nothing. The guarantees are the ones `SPEC-v0.1.md` §7 already states; what changes is the
mechanism that earns them, because `BEGIN IMMEDIATE` is a write lock on a local file and there is
no file any more.

This page is for whoever runs the database. It says what to grant, what happens when the
connection or the server goes away, and — the part that is easy to skip and expensive to assume —
what the store does **not** do for you.

## Install

```bash
pip install 'ctrlrun[postgres]'
```

`psycopg` version 3. `import ctrlrun` imports no `psycopg` module, and neither does anything in
the core; the driver is loaded when a `PostgresStateStore` is first constructed, and its absence
raises `MissingDependency` naming this install line.

## Connect

```python
from ctrlrun.postgres import PostgresStateStore

store = PostgresStateStore("postgresql://ctrlrun@db.internal:5432/ctrlrun")
```

The URL is passed to `psycopg.connect` unchanged, so everything libpq accepts works: a
`postgres://` scheme, `?sslmode=require`, `?connect_timeout=5`, a `service=` name, or the
standard `PG*` environment variables with an otherwise-bare URL. CTRLRun parses none of it.

A second schema is a keyword:

```python
store = PostgresStateStore(url, schema="ctrlrun")
```

**Every statement names its schema**, so nothing in the store depends on `search_path` being set
for it, and two stores against two schemas in one database do not see each other. The schema must
be a plain identifier — letters, digits and underscores — which is what makes it safe to
interpolate; it is the only name in the module that reaches SQL, and it never comes from an
action, an argument or a request header.

On the command line the schema travels in the URL, as CTRLRun's own query parameter, peeled off
before anything reaches the driver:

```bash
ctrlrun effects --store-url 'postgresql://db.internal/ctrlrun?ctrlrun_schema=ctrlrun'
```

`ctrlrun_schema` defaults to `public`. It is the same spelling the store conformance suite uses,
so an operator who has seen one does not have to learn the other.

**The store does not create the schema.** `PostgresStateStore.create_schema(url, name)` exists and
is used by the conformance backend and by `ctrlrun verify`'s scratch store, but constructing a
store against a schema that is not there is refused, naming the schema. Creating databases for
operators is not this library's job and doing it silently is how a typo becomes a second, empty,
authoritative-looking store.

### Passwords

Put the password in `~/.pgpass`, in `PGPASSWORD`, or in a secret your process manager injects —
not in a URL you commit or pass on a command line. A URL in a config file is a URL in a backup,
and `--store-url` also reads `CTRLRUN_STORE_URL`, which is the better place for it.

## What to grant

The database user needs, on the schema the store lives in:

```sql
CREATE SCHEMA ctrlrun;
GRANT USAGE, CREATE ON SCHEMA ctrlrun TO ctrlrun_app;
```

`CREATE` is not optional and it is not only a first-run convenience. Migrations run **at open**,
automatically, and there is no flag that opens a database without migrating it (`SPEC-v0.6.md`
§3.6): a store that could run un-migrated would be a second configuration nobody tested. A user
without `CREATE` is refused at construction with the exact `GRANT` it is missing, rather than at
the first write with the SQL that failed.

If your policy is that application users hold no DDL rights, the supported shape is to run one
start with a migrating role during the deployment and let the application role connect afterwards
— but understand what you have bought: the next upgrade fails closed at open, on every host, until
somebody runs the migrating role again. That is louder than a partial migration, which is the
direction this project chooses; it is still an outage if nobody expected it.

Nothing else is needed. The store creates no extension, no function, no trigger, no role, and
touches no other schema.

### The database's encoding

**The store refuses a database whose `server_encoding` is not `UTF8`**, at open, naming it.

This looks fussy and is not. CTRLRun hashes the exact code points it is given and applies no
Unicode normalization (`SPEC-v0.1.md` §2.3). An effect key that survives a round trip through
`SQL_ASCII` as different bytes is a **different identity**, so two attempts at one logical effect
would reserve two different keys and both would execute. That is a double execution reached
through the storage layer's character set — the exact failure the library exists to prevent,
arriving somewhere nobody looks. It is refused at open rather than discovered in an incident.

For the same reason `effect_key`, `approval_id`, `action_id`, `delegation_id` and `continuation`
are declared `COLLATE "C"`: byte comparison, no locale. A non-deterministic collation would merge
two distinct keys into one, which is the safe direction — but the store should not depend on which
direction a deployment's `lc_collate` happens to fail in.

## Connections and pooling

- **One connection per thread.** `psycopg` connections are not thread-safe and the store does not
  share one. `close()` releases every connection the store opened; it is a release of resources,
  not a fence, and a caller that uses the store afterwards gets a fresh connection.
- **No pool ships.** Sizing a pool is a deployment decision and a library that guessed would be
  wrong on most of them.
- **pgbouncer in `transaction` mode works**, and it works because the store deliberately holds
  nothing session-scoped: no advisory lock, no temporary table, no prepared statement it needs to
  survive, no `SET` it depends on. Reads run outside a transaction; writes re-assert their schema
  with `SET LOCAL`, which reverts at the end of the transaction and cannot leak into a pooled
  connection's next tenant. `session` mode also works and buys nothing here.
- **A host running agents on an unbounded number of threads will open an unbounded number of
  connections.** Bound your worker pool, or put pgbouncer in front. The store does not bound it
  for you, and `max_connections` is where you will find out.

## What happens when things go away

This is the section worth reading twice, because it is where a plausible-looking implementation
would be wrong and the wrongness would only show up in an incident.

### The rule

**`FAILED` means it definitely did not happen.** Anything else unknown is `AMBIGUOUS`. A
connection that dies during `COMMIT` tells you nothing about whether Postgres committed, and
Postgres very often did — so that is `AMBIGUOUS`, never `FAILED`, and an agent that read it as
`FAILED` and retried is the failure mode this library exists to prevent.

### A store write whose outcome is unknown is re-read

There are two kinds of ambiguity and they have different remedies, which is why `SPEC-v0.6.md` §4
carries two tables rather than one.

A Postgres transaction is atomic, so an ambiguous **store write** has exactly one truth and the
store can go and look at it. On a lost `COMMIT` the store re-reads the row:

- it matches, **in every column the store was about to write**, the row we attempted → the
  commit landed, and we hold the reservation. Not a match on `action_id`: that names an attempt
  and is caller-supplyable, so a match on it alone is satisfied by another process's live
  reservation, and that is a double execution;
- the row is absent → the commit did not land, and the insert is re-issued, once;
- anything else → it goes back through `plan_reservation` and the store obeys what that says.

**Only if the re-read itself fails** does the store refuse to let execution proceed, writing no
effect state at all. Fail closed: the remote is not called, so there is nothing to be ambiguous
about.

An ambiguous **remote effect** has no such move. Nothing you can read settles whether the refund
landed, which is why `AMBIGUOUS` is a terminal state there and why a blind retry against that key
is refused until a human or a reconciliation hook answers.

Which branch ran is logged on the `ctrlrun.postgres` logger at `WARNING`, with a `branch`
attribute drawn from a closed set — `a1.row1.ours`, `a1.row2.reinsert`, `a1.row3.refuse`,
`a2.row1.landed`, `a2.row2.reissue`, `a2.row3.refuse`. Those are the values in the log; the
Python constants that carry them are spelled differently, so grep for the value.

### Failover

On a managed failover, a promotion, or a `pg_ctl restart`, in-flight connections break. What
follows depends on where each one was:

- **Before `COMMIT` was issued** — nothing committed. The store write is `FAILED` and may be
  retried, which is the one place `FAILED` is correct: the server stated, in band, that it never
  got there.
- **During or after `COMMIT`** — the re-read above, against a new connection. If the new primary
  is up, it answers and the ambiguity is resolved. If it is not up yet, the re-read fails and the
  store refuses to proceed and writes nothing.
- **A broken connection is replaced only between transactions**, and for the re-read. The store
  never silently reconnects mid-transaction; a reconnect is a new transaction, and pretending
  otherwise is how a partial write becomes invisible.

**Point the store at whatever your deployment uses to name the current primary** — a virtual IP,
a proxy, a `target_session_attrs=read-write` multi-host URL. The store does not discover a new
primary, does not retry a connection in a loop, and does not fail over between URLs. It reports
what it saw and fails closed.

**Do not point it at a read replica.** Every guarantee here rests on a unique index on
`effect_key` and on compare-and-set updates whose row counts are checked, and a replica can do
neither. Reads would appear to work, which is the problem.

### Losing the primary, honestly

An asynchronous replica that is promoted after losing transactions loses effect records with
them, and CTRLRun cannot tell that this happened — a key that was reserved and executed comes
back absent, and the next attempt reserves it again and executes again. If you need the store's
guarantee to survive a failover, you need the *database's* durability to survive it: synchronous
commit to at least one standby. This is a property of your Postgres configuration and not
something a client library can add.

## Concurrent starts, and restarts

**Concurrent starts are safe and are not clever.** The migration transaction takes the backend's
own write lock, so a second process either finds the work done or waits for it. There is no
advisory lock, no leader election and no retry loop.

**A restart does nothing on its own.** Opening a store migrates it and reads nothing else. A
process that came back does not scan, does not repair and does not report; it waits to be asked
about an effect key, exactly as it did before it died.

**Nothing sweeps.** There is no background thread, no timer and no reaper. An expired lease
becomes `AMBIGUOUS` lazily, when the next contender plans a reservation against that key. An
expired lease nobody contends stays `EXECUTING` in the table, and `ctrlrun effects` shows it as
`executing (lease expired)` so that the display does not hide it. `get_effect` and `list_effects`
are reads and transition nothing.

## Throughput, and the one row everything queues on

Every receipt write serializes on a single row.

`SPEC-v0.6.md` §6.3's chain advances a one-row head table, and `put_receipt` takes that row's lock
**first** and holds it across the receipt insert. That is deliberate — a compare-and-set with a
bounded retry silently dropped receipts under three concurrent writers, and a dropped receipt is
evidence loss — but it is a real ceiling and this is the first place in the kernel where two
unrelated actions contend with each other at all.

What that means in practice:

- Receipt writes are serialized per database. Effect reservations are not; they contend only per
  effect key, which is the whole point of the key.
- The lock is held for one `INSERT`, so the ceiling is set by your round-trip time to the
  database. Putting the store on another continent is the way to find it.
- Two schemas in one database have two head rows and do not contend. Sharding by schema works and
  gives you two chains to verify rather than one.

The soak in `research/soak/` is not a measurement of this. It publishes a duration and an action
count, on one host with four threads against a database on the same machine, and it names no
throughput figure on purpose — [what it does not establish](/docs/production/soak) says why. Size this
against your own hardware.

## Verifying the chain

```bash
ctrlrun receipts --store-url 'postgresql://db.internal/ctrlrun?ctrlrun_schema=ctrlrun' --verify-chain
```

Every break is reported by `seq` and by name — `content_altered`, `hash_missing`, `link_broken`,
`missing`, `head_mismatch`, `unchained` — rather than as one boolean, because *"receipt
41 was edited"* and *"the last nine were deleted"* are different incidents.

`ctrlrun receipts --control <id>` filters the same listing down to the receipts citing one
control, which is how you get from a written expectation to the actions taken under it. It is a
filter and not a lookup: an id no receipt cites returns nothing rather than an error.

`--store-url` is on every command that reads or resolves the operator's own store — `receipts`,
`effects`, `inspect`, `resolve`, `approve`, `deny` — and it reads `CTRLRUN_STORE_URL`. **It opens
the database you name and creates nothing**: a schema that is empty, or behind, or ahead is
refused with an instruction rather than migrated. A command an operator runs to read evidence must
not have a side effect on the database it reads.

`ctrlrun verify --store-url postgresql://…` is different and is worth understanding before you run
it against production: it creates a `ctrlrun_verify_<hex>` schema of its own for each guarantee,
works inside it, and drops it when the run ends, including when the run ends by exception. It does
not migrate, read or write your schema. It needs `CREATE` on the database to do that and refuses,
naming the reason, when it cannot.

## What the store does not do for you

Stated plainly, because each of these is something an operator could reasonably assume and none of
them is true:

- **It does not back anything up.** The receipts and effect records are your data in your
  database, and they are exactly as durable as your backup policy.
- **It does not rehash a receipt written by an older build.** The chain hashes the whole receipt
  document, so a release that *adds* a receipt field reports every receipt written before it as
  `content_altered`. Migrations are forward-only and there is no rehash. An operator meeting this
  accepts the break window — `--verify-chain` names the `seq` range, so it is legible — or
  truncates. Receipts written before the chain existed at all report `unchained`, which is a
  different and documented case.
- **It does not prune, roll or retain.** `effects`, `events`, `receipts`, `approvals`,
  `delegations` and `continuations` grow without bound. Deciding what may be deleted is a
  retention policy and this library does not have one; note that deleting receipts from the middle
  or the end of the chain is detected as a break by design, so a retention job needs to be written
  with that in mind, and `ctrlrun receipts --verify-chain` will report the boundary as a break
  because it cannot know the deletion was deliberate.
- **It does not create the schema, the database, the user or the grants.**
- **It does not pool, discover a primary, retry a failed connection, or fail over.**
- **It does not sweep expired leases**, and nothing runs in the background at all.
- **It does not prove who wrote a receipt.** The chain in `SPEC-v0.6.md` §6 detects **alteration**
  — an edited row, a deletion from the middle, a reordering — and that is all it detects.
  Receipts are not signed, alteration is not authorship, and the chain is not tamper-proof
  against an administrator with write access to every row including the chain head.
  `docs/docs/THREAT_MODEL.md` states what remains open.
- **It does not make a read replica safe to use.** See above.
- **It is not a queue, a scheduler or a workflow engine.** It records decisions and outcomes for
  actions somebody else is executing.

## Running the store's own tests against your database

The store conformance suite is this repository's acceptance tests, runnable against any
`StateStore` — including one pointed at a database configured the way yours is:

```python
from ctrlrun.conformance.store import run
from ctrlrun.conformance.store.backends import PostgresBackend

report = run(PostgresBackend("postgresql://…"))
print(report.to_text())
raise SystemExit(0 if report.ok else 1)
```

The suite is core and stdlib — no extra beyond `ctrlrun[postgres]` itself — and it takes a private
schema per case, which it drops. Two of its cases contend from eight OS processes, so point it at
a database you are willing to have hammered briefly.

Running it against a staging database that shares your production's encoding, collation, pooling
and Postgres version is the cheapest way to find out that one of them is not what you thought.

# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""One chain, five receipt schema versions, walked end to end. SPEC-v0.11 §6; T521-T525.

A store kept since v0.6 holds **five** receipt schema versions: `v3` (0.6), `v4` (0.7), `v5`
(0.8), `v6` (0.9), `v7` (0.10). The count comes from `receipt.py`'s constants, which is the only
place it cannot be stale: `ROADMAP.md` has said "three shapes", then "four", and was wrong at the
next release both times.

**No new field.** `schema` has existed since `SPEC-v0.3.md` §12.2 and the rule since then is that
every reader upgrades before any writer switches, so an older receipt on disk still parses. What
was never proved is that `verify_chain` walks such a chain **hash by hash, each row hashed by the
rule its own version wrote**. v0.10's release pass proved the `v6`/`v7` boundary against the
released 0.9.0 and stopped there.

**The premise is proved against the released wheels, not against fixtures**, by
`scripts/five_schema_chain.py`, which needs a network and five virtual environments and is
therefore a script rather than a test. Its transcript on 2026-09-14, against this build::

    0.6.1   wrote 2 receipts under ctrlrun.receipt/v3; store now holds 2
    0.7.0   wrote 2 receipts under ctrlrun.receipt/v4; store now holds 4
    0.8.0   wrote 2 receipts under ctrlrun.receipt/v5; store now holds 6
    0.9.0   wrote 2 receipts under ctrlrun.receipt/v6; store now holds 8
    0.10.0  wrote 2 receipts under ctrlrun.receipt/v7; store now holds 10

      receipts:              10
      schemas in ONE chain:  5
        ctrlrun.receipt/v3     at seq [1, 2]
        ctrlrun.receipt/v4     at seq [3, 4]
        ctrlrun.receipt/v5     at seq [5, 6]
        ctrlrun.receipt/v6     at seq [7, 8]
        ctrlrun.receipt/v7     at seq [9, 10]
      verify_chain:          ok=True verified=10 chained=10 unchained=0
      breaks:                none

**That run was wrong the first time and the way it was wrong is worth keeping.** Run as
`PYTHONPATH=src python scripts/five_schema_chain.py`, the variable is inherited by every child,
so each "released wheel" imported this build's `src/ctrlrun` instead of the wheel installed
beside it. It printed a chain of ten receipts that verified perfectly and reported **one** schema
version. The script now strips the variable and asserts, per release, that the interpreter it
just ran came from that release's own environment.

What is kept here is the invariant, so that a later change breaks the ordinary suite rather than
only a release rehearsal.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.receipt import (
    GENESIS_HASH,
    KNOWN_RECEIPT_SCHEMAS,
    RECEIPT_SCHEMA,
    Receipt,
    _document_hash,
    new_receipt_id,
    verify_chain,
)
from ctrlrun.verify.report import Status

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

#: The five a store kept since v0.6 can hold, oldest first. Written out **here** deliberately,
#: where `scenarios.py` derives its list from `KNOWN_RECEIPT_SCHEMAS`: a derived list and a
#: derived assertion about it agree with each other no matter what either says, and T522 is what
#: makes the derivation answerable to something.
CHAINED_SCHEMAS = (
    "ctrlrun.receipt/v3",
    "ctrlrun.receipt/v4",
    "ctrlrun.receipt/v5",
    "ctrlrun.receipt/v6",
    "ctrlrun.receipt/v7",
)


#: `scripts/five_schema_chain.py` belongs to the **repository** and not to the package: it builds
#: five virtual environments and needs a network, and `MANIFEST.in` prunes `scripts/` for the same
#: reason it prunes `.github`, because a downstream packager builds a library. So the two tests
#: about it skip where the file is absent, exactly as `test_verify_action.py` does for `action.yml`
#: and the CI workflow, and run with everything asserted in a checkout and in CI's `check` job,
#: which is where a change to the script is actually made.
#:
#: Found by the `package` job: the first version read the path unconditionally and both tests
#: failed with `FileNotFoundError` inside the sdist.
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "five_schema_chain.py"


def _repository_script() -> str:
    if not SCRIPT.exists():  # pragma: no cover - only outside a checkout
        pytest.skip("scripts/five_schema_chain.py is not in the sdist; this asserts a repo file")
    return SCRIPT.read_text(encoding="utf-8")


def an_action(payment_id: str) -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": 2000},
        principal=Principal(agent="chain-agent"),
    )


def a_chain_of_every_schema(seed: Receipt) -> tuple[tuple[Receipt, ...], str]:
    """One chained receipt per schema version, linked the way `put_receipt` links them.

    `seq` and `prev_hash` go **into** the document before it is hashed, which is what makes them
    tamper-evident (`SPEC-v0.6.md` §6.2); `hash` is a column and never a key, so it is attached
    afterwards. The document renders under each row's own schema's key set, which is
    `SPEC-v0.7.md` §6.11 and the whole reason one chain can hold five shapes.
    """
    rows: list[Receipt] = []
    previous = GENESIS_HASH
    for position, label in enumerate(CHAINED_SCHEMAS, start=1):
        row = replace(
            seed,
            receipt_id=new_receipt_id(),
            schema=label,
            seq=position,
            prev_hash=previous,
            hash=None,
        )
        previous = _document_hash(row.to_dict())
        rows.append(replace(row, hash=previous))
    return tuple(rows), previous


class _Chain:
    """A `ChainSource` over receipts this test built (`SPEC-v0.6.md` §6.5's protocol)."""

    def __init__(self, rows: tuple[Receipt, ...], head: tuple[int, str] | None) -> None:
        self._rows = rows
        self._head = head

    def receipts(self) -> tuple[Receipt, ...]:
        return self._rows

    def chain_head(self) -> tuple[int, str] | None:
        return self._head


@pytest.fixture
def seed(tmp_path) -> Receipt:
    """A real receipt, written by this binary through the ordinary path."""
    store = SQLiteStateStore(tmp_path / "seed.db", clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    control.execute(an_action("p0"), lambda: {"ok": True}, "refund:p0", lease=LEASE)
    written = store.receipts()[0]
    store.close()
    assert isinstance(written, Receipt)
    return written


# --- T521: the walk ----------------------------------------------------------------------------


def test_T521_a_chain_of_five_receipt_schema_versions_verifies_end_to_end(seed) -> None:
    """SPEC-v0.11 §6. **The deliverable.**

    The positive control comes first and is not decoration: a chain of five rows that all carry
    the *current* schema verifies perfectly and proves nothing, so the distinct-label count is
    asserted before the walk is. That is `SPEC-v0.4.md` §2.2's guarantee that could not have
    failed, in the place it would be easiest to write by accident.
    """
    rows, head = a_chain_of_every_schema(seed)
    labels = sorted({row.schema for row in rows})

    assert len(labels) == 5, f"the chain does not span five schemas: {labels}"
    assert labels == sorted(CHAINED_SCHEMAS)

    report = verify_chain(_Chain(rows, (len(rows), head)))

    assert report.ok, [(item.name, item.seq) for item in report.breaks]
    assert report.verified == 5
    assert report.chained == 5
    assert report.unchained == 0
    assert report.breaks == []


@pytest.mark.parametrize("position", range(5), ids=[s.rsplit("/", 1)[-1] for s in CHAINED_SCHEMAS])
def test_T521b_altering_the_row_of_any_one_version_is_named_at_its_seq(seed, position) -> None:
    """Every row is load-bearing, not just the newest.

    Without this, a walk that skipped rows whose label it did not recognise would pass `T521`
    and "verified" would be a count of the rows it bothered to read. Parametrized over all five,
    because a walk could plausibly read the two it knows best and skip the rest.
    """
    rows, head = a_chain_of_every_schema(seed)
    target = rows[position]
    altered = replace(target, decision_reason=f"{target.decision_reason}-altered")
    damaged = tuple(altered if row.seq == target.seq else row for row in rows)

    report = verify_chain(_Chain(damaged, (len(rows), head)))

    assert not report.ok, f"altering the {target.schema} row was not detected"
    named = [(item.name, item.seq) for item in report.breaks]
    assert ("content_altered", target.seq) in named, (
        f"altering a {target.schema} row was reported as {named}"
    )


# --- T522: the count comes from the constants, not from a document -----------------------------


def test_T522_the_schemas_a_chain_can_hold_are_the_ones_receipt_py_declares() -> None:
    """SPEC-v0.11 §6. `ROADMAP.md` said "three shapes", then "four", and was wrong at the next
    release both times, which is the argument for reading `receipt.py`'s constants instead.

    This is what makes `scenarios.py`'s **derived** list answerable to something. A derived list
    and a derived assertion about it agree no matter what either says; this states the five
    independently, so adding `v8` without adding it here goes red and somebody decides whether
    the new version belongs in the chain G31 grades.
    """
    from ctrlrun.verify.scenarios import _OLDER_RECEIPT_SCHEMAS

    assert RECEIPT_SCHEMA == "ctrlrun.receipt/v7", (
        "the current receipt schema moved; CHAINED_SCHEMAS and SPEC-v0.11 §6's count of five "
        "both need a decision, and G31 grades whatever this list says"
    )
    assert set(CHAINED_SCHEMAS) <= KNOWN_RECEIPT_SCHEMAS
    assert CHAINED_SCHEMAS[-1] == RECEIPT_SCHEMA
    assert tuple(_OLDER_RECEIPT_SCHEMAS) == CHAINED_SCHEMAS[:-1], (
        f"verify grades {_OLDER_RECEIPT_SCHEMAS} and this file says {CHAINED_SCHEMAS[:-1]}"
    )
    # `v1` and `v2` are known and are NOT in the chain's set: the chain arrived in `v3`
    # (SPEC-v0.6 §6.2), so those rows carry no `seq` and are `unchained`, which G11 covers and
    # which is never a pass.
    assert {"ctrlrun.receipt/v1", "ctrlrun.receipt/v2"} <= KNOWN_RECEIPT_SCHEMAS
    assert "ctrlrun.receipt/v1" not in CHAINED_SCHEMAS
    assert "ctrlrun.receipt/v2" not in CHAINED_SCHEMAS


# --- T523: a version this binary does not know -------------------------------------------------


def test_T523_a_receipt_from_a_future_version_is_named_and_is_not_a_break(seed) -> None:
    """SPEC-v0.11 §6.2. `SPEC-v0.6.md` §3.2 draws the same line for a `schema_version` row the
    binary does not know, and the difference matters to the only person who reads the output:
    *this evidence is from a future version* and *this evidence is tampered with* are different
    sentences that call for different actions.

    **Constructed honestly**, which is the whole difficulty: relabelling a stored row is a
    *tamper*, and `content_altered` is the right answer to that. A receipt a future writer
    actually wrote carries a hash computed over its own document, so that is what this builds.
    """
    future = "ctrlrun.receipt/v9"
    assert future not in KNOWN_RECEIPT_SCHEMAS, "pick a version this binary really does not know"

    rows, _ = a_chain_of_every_schema(seed)
    tail = replace(
        rows[-1],
        receipt_id=new_receipt_id(),
        schema=future,
        seq=len(rows) + 1,
        prev_hash=rows[-1].hash,
        hash=None,
    )
    digest = _document_hash(tail.to_dict())
    rows = (*rows, replace(tail, hash=digest))

    report = verify_chain(_Chain(rows, (len(rows), digest)))

    assert report.ok, (
        "a receipt written by a version this binary does not know was reported as a break: "
        f"{[(item.name, item.seq) for item in report.breaks]}"
    )
    assert report.verified == 6
    assert report.breaks == []
    # And it is **named**: a reader can tell which row it could not fully interpret, rather than
    # the row passing silently as though this binary had read every field in it.
    unknown = [row.seq for row in rows if row.schema not in KNOWN_RECEIPT_SCHEMAS]
    assert unknown == [6], unknown


def test_T523b_a_relabelled_row_is_a_tamper_and_is_reported_as_one(seed) -> None:
    """The other side of §6.2, and the reason it needs stating.

    Taking a stored `v7` row and writing `v9` on it **without** rehashing is somebody editing
    evidence, not a future version. It must be `content_altered`, and a reader that treated any
    unknown label as "from the future, nothing to see" would have made relabelling a way to
    launder a tamper.
    """
    rows, head = a_chain_of_every_schema(seed)
    target = rows[-1]
    relabelled = replace(target, schema="ctrlrun.receipt/v9")
    damaged = tuple(relabelled if row.seq == target.seq else row for row in rows)

    report = verify_chain(_Chain(damaged, (len(rows), head)))

    assert not report.ok, "relabelling a stored row to an unknown version was not detected"
    assert ("content_altered", target.seq) in [(item.name, item.seq) for item in report.breaks]


# --- T524: G31 grades it, and agrees with itself -----------------------------------------------


def test_T524_G31_is_in_the_catalogue_and_the_catalogue_moved_once() -> None:
    """SPEC-v0.11 §8. The catalogue moves to `v7` once, with whichever item lands first."""
    from ctrlrun.verify import guarantees as reg

    assert reg.CATALOGUE == "ctrlrun.guarantees/v7"
    assert reg.BY_ID["G31"].title == "five receipt schemas verify"
    assert reg.BY_ID["G31"].descends_from, "G31 names no acceptance test"
    # §8 assigns ids in **item** order rather than landing order, so the catalogue had holes
    # between items: `G31` landed with item 4 while `G28` to `G30` and `G32` did not exist. Every
    # one of §8's ids is present now, and this asserts the set rather than the absence, because
    # the milestone is the thing that closes the holes.
    assert {"G28", "G29", "G30", "G31", "G32"} <= {g.id for g in reg.GUARANTEES}
    numbers = [int(g.id[1:]) for g in reg.GUARANTEES]
    assert numbers == sorted(set(numbers)), "the catalogue is out of order or repeats"


# --- T525: the script that proves the premise --------------------------------------------------


def test_T525_the_released_wheel_script_strips_what_would_make_it_lie() -> None:
    """The methodology, asserted rather than trusted.

    `scripts/five_schema_chain.py` is the half of §6.1 that uses the **released** wheels, and its
    first run was wrong in a way that looked exactly like a pass: `PYTHONPATH` is inherited by
    every child, so every "released wheel" imported this build's source and the script reported
    one schema version across a chain of ten receipts.

    The script is not run here: it needs a network and builds five virtual environments. What is
    checked is that the two guards which make its answer mean anything are still in it, because
    a script whose methodology quietly regressed would go on printing a convincing transcript.
    """
    source = _repository_script()

    assert "PYTHONPATH" in source, "the script no longer strips PYTHONPATH from its children"
    assert "_clean_environment" in source
    assert source.count("env=_clean_environment()") >= 3, (
        "a subprocess in the script runs with this process's environment, so it may import this "
        "build instead of the wheel it just installed"
    )
    assert "_require_wrote_as" in source, (
        "the script no longer checks that each release ran from its own environment, which is "
        "the positive control for the stripping above"
    )
    for version, _schema in (
        ("0.6.1", ""),
        ("0.7.0", ""),
        ("0.8.0", ""),
        ("0.9.0", ""),
        ("0.10.0", ""),
    ):
        assert version in source, f"the script no longer builds the chain with {version}"
    for schema in CHAINED_SCHEMAS:
        assert schema in source, f"the script no longer expects {schema}"


# --- T521c: the same chain, read back off a real store ----------------------------------------


def test_T521c_five_schemas_on_disk_rehash_to_their_stored_hashes(tmp_path, seed) -> None:
    """The mechanism, not the arithmetic: a receipt read from a store is hashed as **the document
    it was read from** (`SPEC-v0.7.md` §6.11), and that is the only reason one chain can hold
    five shapes at all.

    **`T521` does not cover this and a mutation proved it.** `T521` builds its receipts with
    `replace()`, which drops the stored document, so `chain_hash()` there falls back to rendering
    with `to_dict()` and the stored-document branch is never taken. Deleting that branch left
    `T521` green. This test puts the five documents **on disk** and reads them back through the
    store, which is the path an operator's chain actually takes.

    The rows go in with SQL because `put_receipt` does `replace(receipt, schema=RECEIPT_SCHEMA)`:
    a writer writes under its own schema, so an older receipt cannot be forged through the public
    API. That is correct, and it is why this test writes the table the way 0.6 left it.
    """
    database = tmp_path / "five.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.close()

    rows, head = a_chain_of_every_schema(seed)
    connection = sqlite3.connect(database)
    for row in rows:
        connection.execute(
            "INSERT INTO receipts (receipt_id, action_id, ts, json, seq, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row.receipt_id,
                row.action_id,
                row.finished_at.isoformat(),
                json.dumps(row.to_dict(), ensure_ascii=False, separators=(",", ":")),
                row.seq,
                row.prev_hash,
                row.hash,
            ),
        )
    connection.execute(
        "INSERT INTO receipt_chain (id, seq, hash) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET seq = excluded.seq, hash = excluded.hash",
        (len(rows), head),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    read_back = reopened.receipts()
    report = verify_chain(reopened)
    reopened.close()

    assert len(read_back) == 5
    assert sorted({row.schema for row in read_back}) == sorted(CHAINED_SCHEMAS)
    # The stored document really is what came back, so the fallback is not what is being tested.
    for row in read_back:
        assert isinstance(row, Receipt)
        assert row.chain_hash() == row.hash, (
            f"the {row.schema} row at seq {row.seq} does not rehash to its stored hash, so it is "
            "being rendered by this binary rather than read as it was written"
        )
    assert report.ok, [(item.name, item.seq) for item in report.breaks]
    assert report.verified == 5


def test_T521d_a_stored_document_this_binary_would_not_render_still_rehashes(tmp_path, seed):
    """The stored-document branch, on the only input that can distinguish it.

    **`T521c` does not reach it either, and a mutation proved that too.** `T521c` writes
    `json.dumps(row.to_dict())` to disk, so re-rendering with `to_dict()` produces the same bytes
    and `chain_hash()` gives the same answer whichever branch it takes. The branch only matters
    when the document on disk is something this binary would **not** produce: a key it has never
    heard of, written by a version that came later.

    That is the case `SPEC-v0.7.md` §6.11 exists for, and the one an operator hits when a newer
    writer has touched their store. Deleting the stored-document branch makes this row's hash
    unreproducible and the chain reports `content_altered` about evidence nobody altered.
    """
    database = tmp_path / "future.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.close()

    document = dict(seed.to_dict())
    document["schema"] = "ctrlrun.receipt/v9"
    document["seq"] = 1
    document["prev_hash"] = GENESIS_HASH
    # The field that makes the document unrenderable by this binary: `to_dict` projects a fixed
    # key set per schema, so nothing here can put this key back.
    document["settled_at"] = "2026-09-14T00:00:00Z"
    digest = _document_hash(document)

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO receipts (receipt_id, action_id, ts, json, seq, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(document["receipt_id"]),
            str(document["action_id"]),
            seed.finished_at.isoformat(),
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            1,
            GENESIS_HASH,
            digest,
        ),
    )
    connection.execute(
        "INSERT INTO receipt_chain (id, seq, hash) VALUES (1, 1, ?) "
        "ON CONFLICT(id) DO UPDATE SET seq = excluded.seq, hash = excluded.hash",
        (digest,),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    report = verify_chain(reopened)
    reopened.close()

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, Receipt)
    assert row.schema == "ctrlrun.receipt/v9"
    assert "settled_at" not in row.to_dict(), (
        "this binary rendered a key it does not know, so the document is NOT one it would "
        "refuse to reproduce and this test proves nothing"
    )
    assert row.chain_hash() == digest, (
        "a receipt carrying a key this binary has never heard of did not rehash to its stored "
        "hash, so it is being re-rendered rather than read as it was written (SPEC-v0.7 §6.11)"
    )
    assert report.ok, (
        "a receipt a later version wrote was reported as a break: "
        f"{[(item.name, item.seq) for item in report.breaks]}"
    )
    assert report.verified == 1


# --- T522b: the version is a number, and two digits is where a string comparison breaks --------


def test_T522b_schema_versions_are_ordered_as_numbers_and_not_as_strings() -> None:
    """`"ctrlrun.receipt/v10"` sorts **below** `"ctrlrun.receipt/v3"` lexically.

    A string comparison in `_OLDER_RECEIPT_SCHEMAS` gives the right answer for every version that
    exists today and the wrong one from v0.13 on: it would drop `v10` and later out of the set
    G31 grades, and G31 would go on passing over a shorter chain with nothing to say about it.
    A mutation replacing the parse with `label >= "ctrlrun.receipt/v3"` survived the rest of this
    file, because nothing here reaches two digits yet. This is what makes the guard load-bearing
    now rather than in three milestones.
    """
    from ctrlrun.verify.scenarios import _schema_number

    assert _schema_number("ctrlrun.receipt/v3") == 3
    assert _schema_number("ctrlrun.receipt/v10") == 10
    assert "ctrlrun.receipt/v10" < "ctrlrun.receipt/v3", (
        "this test is about a lexical ordering that no longer holds; if that changed, the parse "
        "may no longer be needed"
    )
    ordered = sorted(
        ["ctrlrun.receipt/v10", "ctrlrun.receipt/v3", "ctrlrun.receipt/v9"], key=_schema_number
    )
    assert ordered == [
        "ctrlrun.receipt/v3",
        "ctrlrun.receipt/v9",
        "ctrlrun.receipt/v10",
    ], ordered


# --- T525b: the script's guard, run rather than grepped ---------------------------------------


def test_T525b_the_scripts_environment_guard_actually_strips_the_variables() -> None:
    """**`T525` greps and a mutation walked through it.** Replacing the body of
    `_clean_environment` with `pass` left every string `T525` looks for in place, so it passed
    over a script that hands its own `sys.path` to the wheels it is testing.

    That is the project's own rule about auditing by grep, applied to a script: searching for the
    identifier finds the identifier. This imports the function and runs it.
    """
    import importlib.util

    _repository_script()  # skips outside a checkout, for SCRIPT's reason
    spec = importlib.util.spec_from_file_location("five_schema_chain", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    leaked = dict(os.environ)
    leaked["PYTHONPATH"] = "/somewhere/that/would/shadow/the/wheel/src"
    leaked["PYTHONHOME"] = "/somewhere/else"
    original = os.environ.copy()
    try:
        os.environ.update(leaked)
        cleaned = module._clean_environment()
    finally:
        os.environ.clear()
        os.environ.update(original)

    assert "PYTHONPATH" not in cleaned, (
        "the script would hand its own sys.path to each released wheel, so every wheel would "
        "import THIS build and the run would report one schema version while looking like a pass"
    )
    assert "PYTHONHOME" not in cleaned
    assert "PATH" in cleaned, "the child needs an environment it can still run in"
    assert module.RELEASES[0][0] == "0.6.1"
    assert tuple(schema for _version, schema in module.RELEASES) == CHAINED_SCHEMAS


# --- T524b: G31's own control, forced ----------------------------------------------------------


def test_T524b_G31_fails_its_control_when_the_chain_spans_one_schema(tmp_path, monkeypatch):
    """**The guarantee that could not have failed, checked by making it fail.**

    `G31`'s first control requires the chain it built to hold five distinct labels, because a
    chain of five `v7` rows verifies perfectly and proves nothing. A mutation replacing that
    control with `True` survived the whole suite: nothing ever drove `G31` with a one-version
    chain, so the control was green and not load-bearing.

    Forcing it is the only way to know the control works, and `control failed` is the right
    status: it means the kernel, not the operator's document, is wrong
    (`SPEC-v0.4.md` §1.3).
    """
    from ctrlrun.verify import scenarios

    # Duplicates of the CURRENT schema, which is exactly what the failure looked like when it
    # really happened: the first version of this scenario built its rows with `put_receipt`,
    # which does `replace(receipt, schema=RECEIPT_SCHEMA, ...)`, so five rows came back carrying
    # one label. Emptying the tuple instead would make the control trivially *satisfied* -- one
    # expected label, one present -- which is a test of nothing.
    monkeypatch.setattr(
        scenarios, "_OLDER_RECEIPT_SCHEMAS", (RECEIPT_SCHEMA, RECEIPT_SCHEMA, RECEIPT_SCHEMA)
    )

    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text(
        "schema: ctrlrun.policy/v2\n"
        "actions:\n"
        "  stripe.refund:\n"
        "    decision: allow\n"
        '    effect: "refund:{payment_id}"\n',
        encoding="utf-8",
    )
    from ctrlrun.verify import run

    report = run(policy, only=("G31",))
    result = next(item for item in report.guarantees if item.id == "G31")

    assert result.status is not Status.PASS, (
        "G31 graded PASS over a chain holding one schema version, which is exactly the claim it "
        "is supposed to refuse to make"
    )
    assert result.reason == "control failed", result.reason


def test_T522c_the_derivation_orders_a_two_digit_version_correctly() -> None:
    """The ordering, on a set that contains the case a string comparison gets wrong.

    `_OLDER_RECEIPT_SCHEMAS` is computed once at import, so patching `KNOWN_RECEIPT_SCHEMAS`
    cannot reach it, and a mutation replacing the numeric comparison with `label >=
    "ctrlrun.receipt/v3"` survived every other test in this file: every version that exists
    today is one digit and the two comparisons agree. `_chainable_schemas` takes its inputs so
    that this can hand it `v10` and `v11`, which is where they stop agreeing.
    """
    from ctrlrun.verify.scenarios import _chainable_schemas

    known = {
        "ctrlrun.receipt/v1",
        "ctrlrun.receipt/v2",
        "ctrlrun.receipt/v3",
        "ctrlrun.receipt/v9",
        "ctrlrun.receipt/v10",
        "ctrlrun.receipt/v11",
    }

    assert _chainable_schemas(known, "ctrlrun.receipt/v11") == (
        "ctrlrun.receipt/v3",
        "ctrlrun.receipt/v9",
        "ctrlrun.receipt/v10",
    ), (
        "a two-digit receipt schema was dropped or misordered, which is what a lexical "
        "comparison does: 'ctrlrun.receipt/v10' sorts below 'ctrlrun.receipt/v3'"
    )
    # And the real inputs still give the real answer, so the function is the constant's producer
    # and not a second implementation beside it.
    from ctrlrun.verify.scenarios import _OLDER_RECEIPT_SCHEMAS

    assert _chainable_schemas(KNOWN_RECEIPT_SCHEMAS, RECEIPT_SCHEMA) == _OLDER_RECEIPT_SCHEMAS

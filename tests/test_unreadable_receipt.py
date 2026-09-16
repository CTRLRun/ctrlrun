# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""A reader that names a bad row and blinds nothing else. SPEC-v0.11 §5; T510-T519.

**The blinding is the deliverable and it is written first.** SPEC-v0.7 §12.5 recorded this
defect and deferred it twice: one malformed *value* of a declared key raised out of
`Receipt.from_dict`, and because both stores build every row before any caller sees one, a
single `UPDATE` stopped `ctrlrun receipts`, `receipts --verify-chain`, `ctrlrun inspect`,
`ctrlrun stats` and the operator server's `_receipts` and `_stats` tools together.

`inspect` on an action the tamper never touched is the case that says what the blast radius
really was: not "this receipt is unreadable" but "this store is unreadable".

Two halves, and the negative control is worth as much as the positive:

* one tampered row costs **one** row, and every other reader keeps working (T510, T512, T515);
* a store with **no** bad row reads exactly as it read at 0.10.0 (T511).

T513 is the defect §5.2 found underneath this one: a receipt's position came from the document
rather than from the `seq` column, which is the half a tamperer controls.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.cli.main import main
from ctrlrun.receipt import Receipt, UnreadableReceipt, verify_chain

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)


def an_action(payment_id: str) -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": 2000},
        principal=Principal(agent="chain-agent"),
    )


def a_chain(store, count: int = 4) -> list:
    """`count` committed actions through `Control`, so the receipts are real ones."""
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    for index in range(count):
        control.execute(
            an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
        )
    return list(store.receipts())


#: The tamper, in SQL, underneath the store: an `UPDATE` by somebody with write access who has
#: no interest in going through ctrlrun. A **declared** key set to a value of the wrong type,
#: which is the case SPEC-v0.7 §12.5 named and neither of the two it says already worked (an
#: unknown schema label, and an added key, are each already reported at their `seq`).
def _tamper_one_value(database: Path, at: int = 2) -> None:
    connection = sqlite3.connect(database)
    row = connection.execute("SELECT json FROM receipts WHERE seq = ?", (at,)).fetchone()
    document = json.loads(row[0])
    assert isinstance(document["controls"], list), (
        "this tamper rewrites `controls`, a declared key; the row does not have one, so the "
        "test would prove nothing"
    )
    document["controls"] = [1.5]
    connection.execute(
        "UPDATE receipts SET json = ? WHERE seq = ?",
        (json.dumps(document, separators=(",", ":")), at),
    )
    connection.commit()
    connection.close()


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "ctrlrun.yaml").write_text(ALLOW)
    return tmp_path


def _cli(workspace, database, *args):
    """One CLI invocation against this store, from a directory with a policy in it."""
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        return runner.invoke(main, [*args, "--store-url", f"sqlite:///{database}"])
    finally:
        os.chdir(cwd)


# --- T510: one tampered row costs one row, on every reader that used to blind -----------------


def test_T510_one_malformed_value_no_longer_blinds_the_five_readers(workspace) -> None:
    """SPEC-v0.11 §5, rule 3. **The deliverable, written first.**

    Measured at `main` before this item, on this exact store and this exact tamper::

        receipts                   exit=1  Error: a control id must be a string, got 1.5
        receipts --verify-chain    exit=1  Error: a control id must be a string, got 1.5
        inspect <untouched action> exit=1  Error: a control id must be a string, got 1.5
        stats                      exit=1  Error: a control id must be a string, got 1.5

    Each row here asserts the reader **answered**, and that what it answered is about the rows
    it could read. A test that only asserted "no longer raises" would pass against a reader
    that returned nothing at all, which is the same blindness with a zero exit code.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    written = a_chain(store, 4)
    untouched = written[0].action_id
    ids = [receipt.receipt_id for receipt in written]
    store.close()
    _tamper_one_value(database, at=2)

    # (1) `receipts`: the other three print, and the refused row is named where it sits.
    listed = _cli(workspace, database, "receipts")
    assert listed.exit_code == 0, listed.output
    for index, receipt_id in enumerate(ids, start=1):
        assert receipt_id in listed.output, (
            f"the receipt at seq {index} is missing from the listing; one bad row cost more "
            f"than one row:\n{listed.output}"
        )
    assert "UNREADABLE" in listed.output
    assert listed.output.count("UNREADABLE") == 1, (
        f"one row was tampered with and {listed.output.count('UNREADABLE')} were named:\n"
        f"{listed.output}"
    )
    assert "seq 2" in listed.output, "the refused row was not named at its position"

    # (2) `receipts --verify-chain`: a report, naming the break at the tampered seq. **Exit 1**,
    #     because a chain with a forgery in it is not ok -- recovering the reader must not
    #     turn a broken chain into a zero exit.
    checked = _cli(workspace, database, "receipts", "--verify-chain")
    assert checked.exit_code == 1, checked.output
    assert "content_altered at seq 2" in checked.output, checked.output

    # (3) `inspect` on an action the tamper never touched. §2.3's sharp case.
    inspected = _cli(workspace, database, "inspect", untouched)
    assert inspected.exit_code == 0, inspected.output
    assert untouched in inspected.output

    # (4) `stats` answers, counts the rows it could read, and says so.
    counted = _cli(workspace, database, "stats")
    assert counted.exit_code == 0, counted.output
    assert "actions                            3" in counted.output, counted.output
    assert "unreadable receipts                1" in counted.output, (
        "stats counted 3 of 4 rows and said nothing about the fourth, so the number reads as a "
        f"verdict about a store nobody could fully read:\n{counted.output}"
    )

    # (5) `effects`, which §2.3 measured and found does **not** blind. Asserted so that a later
    #     change cannot make it start.
    effects = _cli(workspace, database, "effects")
    assert effects.exit_code == 0, effects.output
    assert effects.output.count("committed") == 4, effects.output


# --- T511: the negative control ---------------------------------------------------------------


def test_T511_a_store_with_no_bad_row_reads_exactly_as_it_did(workspace) -> None:
    """SPEC-v0.11 §5.3. **Without this every row of T510 passes against a reader that reports
    every row as unreadable**, which is this project's oldest false green.

    The claim is "a clean store keeps working for everybody whose store is intact", so what is
    asserted is that nothing about this output is new: no refusal marker anywhere, no
    `unreadable_receipts` key in the document, and every position exactly where it was.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    written = a_chain(store, 4)
    untouched = written[0].action_id
    store.close()

    listed = _cli(workspace, database, "receipts")
    assert listed.exit_code == 0
    assert "UNREADABLE" not in listed.output, (
        f"a clean store reported a row as unreadable:\n{listed.output}"
    )
    assert listed.output.strip().count("\n") == 3, listed.output

    checked = _cli(workspace, database, "receipts", "--verify-chain")
    assert checked.exit_code == 0, checked.output
    assert "4 of 4 receipts verified" in checked.output, checked.output

    inspected = _cli(workspace, database, "inspect", untouched)
    assert inspected.exit_code == 0

    counted = _cli(workspace, database, "stats", "--json")
    assert counted.exit_code == 0
    document = json.loads(counted.output)
    assert document["actions"] == 4
    assert "unreadable_receipts" not in document, (
        "a clean store's stats document gained a key, so this item changed shipped output for "
        f"a deployment with nothing wrong with it: {document}"
    )

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    reopened.close()
    assert all(isinstance(row, Receipt) for row in rows)
    assert [row.seq for row in rows] == [1, 2, 3, 4]


# --- T512: the chain reports it, under a name that already exists ------------------------------


def test_T512_a_refused_row_is_content_altered_and_not_a_new_break_kind(workspace) -> None:
    """SPEC-v0.11 §5.1. `CHAIN_BREAKS` is a closed set on a SPEC-v0.6 §6.5 frozen surface, and
    §5.1 declines SPEC-v0.7 §12.5's first candidate with a reason: `content_altered` already
    names a document that cannot be canonicalized, and a second name for one fact would be two
    names for one break.

    So this asserts the set did **not** grow, as much as it asserts where the break lands.
    """
    from ctrlrun.receipt import CHAIN_BREAKS

    assert CHAIN_BREAKS == (
        "content_altered",
        "hash_missing",
        "link_broken",
        "missing",
        "head_mismatch",
        "unchained",
    ), "item 1 amended a frozen closed set; §5.1 says it does not"

    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()
    _tamper_one_value(database, at=2)

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok
    named = [(item.name, item.seq) for item in report.breaks]
    assert ("content_altered", 2) in named, named
    # One tamper, one accusation at the tampered row. `link_broken` at 3 travels with any
    # content alteration and is the pre-existing behaviour of T164, not a second accusation.
    assert [item for item in named if item[0] == "content_altered"] == [("content_altered", 2)], (
        f"one row was tampered with and the chain accused more than one of alteration: {named}"
    )
    assert report.verified == 2, f"expected 2 of 4 verified, got {report.verified}: {named}"


# --- T513: position comes from the column, which it did not ------------------------------------


def test_T513_a_receipts_position_comes_from_the_seq_column(workspace) -> None:
    """SPEC-v0.11 §5.2. `verify_chain`'s docstring has claimed since v0.6 that *"position comes
    from the store's `seq` column"*, and it was false as shipped: both stores selected
    `json, hash` and ordered by a column they never read, so every `Receipt.seq` came out of
    `document.get("seq")` -- the one field a tamperer controls.

    Measured at `main`, rewriting one document's `seq` from 2 to 99 and leaving the column::

        Receipt.seq values the store returns: [1, 99, 3]
        breaks: [('missing', 2), ('content_altered', 99), ('missing', 100), ('link_broken', 3)]

    Four breaks at three positions, two of which name rows that do not exist. One tamper must
    read as one tamper, and an operator paged at three in the morning must not be sent looking
    for `seq 100`.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)
    store.close()

    connection = sqlite3.connect(database)
    changed = connection.execute(
        "UPDATE receipts SET json = replace(json, '\"seq\":2', '\"seq\":99') WHERE seq = 2"
    ).rowcount
    connection.commit()
    columns = [row[0] for row in connection.execute("SELECT seq FROM receipts ORDER BY seq")]
    connection.close()
    assert changed == 1 and columns == [1, 2, 3], (
        "the tamper did not do what this test is about: the document's seq was to move and the "
        f"column was to stay, and the columns are {columns}"
    )

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    positions = [row.seq for row in reopened.receipts()]
    report = verify_chain(reopened)
    reopened.close()

    assert positions == [1, 2, 3], (
        f"a receipt's position came from the document after all: {positions}"
    )
    named = [(item.name, item.seq) for item in report.breaks]
    assert ("content_altered", 2) in named, named
    assert not any(item[1] in (99, 100) for item in named), (
        f"the chain named a position that does not exist in this store: {named}"
    )


# --- T514: what a refusal carries, and what it deliberately does not ---------------------------


def test_T514_a_refusal_carries_its_place_and_the_type_that_refused_it(workspace) -> None:
    """SPEC-v0.11 §5.2, and SPEC-v0.7 §6.11's rule: **by type and never by message.**

    The canonicalizer quotes what it refused. A message carried into a report is a report that
    can contain a lone surrogate, which is a report that cannot be printed -- so the refusal is
    named by the type of what raised and the offending value never travels.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    written = a_chain(store, 3)
    tampered_id = written[1].receipt_id
    store.close()
    _tamper_one_value(database, at=2)

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    reopened.close()

    assert len(rows) == 3, "one row was tampered with and the read lost more than one"
    refused = rows[1]
    assert isinstance(refused, UnreadableReceipt), rows
    assert refused.seq == 2, "the refusal does not know where it is"
    assert refused.receipt_id == tampered_id, (
        "`receipt_id` was readable on its own and the refusal dropped it"
    )
    assert refused.refusal == "InvalidArgument"
    rendered = json.dumps(refused.to_dict())
    assert "1.5" not in rendered, (
        f"the value that refused the row travelled into the report: {rendered}"
    )
    assert isinstance(rows[0], Receipt) and isinstance(rows[2], Receipt)


def test_T514b_a_row_that_is_not_a_receipt_at_all_is_refused_the_same_way(workspace) -> None:
    """A row truncated to `{}` carries no `receipt_id` to report, and must still cost one row.

    `Receipt.from_dict` raises `KeyError` here rather than `InvalidArgument` -- a different
    exception on a different line -- and a reader that recovered from one and not the other
    would still be blindable by one `UPDATE`.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET json = '{}' WHERE seq = 2")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    report = verify_chain(reopened)
    reopened.close()

    assert len(rows) == 3
    assert isinstance(rows[1], UnreadableReceipt)
    assert rows[1].seq == 2
    assert rows[1].receipt_id is None, "a receipt_id was invented for a row that carries none"
    assert rows[1].refusal == "KeyError"
    assert ("content_altered", 2) in [(item.name, item.seq) for item in report.breaks]


# --- T515: the network surface -----------------------------------------------------------------


def test_T515_the_operator_servers_read_tools_no_longer_blind(workspace) -> None:
    """SPEC-v0.11 §2.3. `gateway/operator.py`'s `_receipts` and `_stats` call `store.receipts()`
    directly, so the same one `UPDATE` took out the **remote console** as well as the terminal.

    Item 1's scope is the shared read and not the CLI, and this is the test that says so: a fix
    landing in `cli/main.py` alone would leave this red.
    """
    from ctrlrun.gateway.operator import OperatorConfig, OperatorServer

    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()
    _tamper_one_value(database, at=2)

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), reopened, clock=lambda: T0)
    server = OperatorServer(
        OperatorConfig(principal_header="x-approver", user_header="x-approver-user"),
        control,
        _NoIdentity(),
    )

    listed = _tool(server, "receipts", {"limit": 10})
    assert len(listed["receipts"]) == 4, f"one bad row cost the console more than one row: {listed}"
    refused = [row for row in listed["receipts"] if row.get("refusal")]
    assert len(refused) == 1 and refused[0]["seq"] == 2, listed

    counted = _tool(server, "stats", {})
    assert counted["actions"] == 3
    assert counted["unreadable_receipts"] == 1, counted

    # And under `control_id`, which is a **filter and not a lookup** (SPEC-v0.6 §7.3). A row
    # whose `controls` could not be read cannot be shown not to cite this id, so dropping it
    # would let one `UPDATE` hide a row from exactly the query an operator runs to find a
    # control's evidence. This half exists because a mutation survived without it.
    filtered = _tool(server, "receipts", {"limit": 10, "control": "no-such-control"})
    kept = [row for row in filtered["receipts"] if row.get("refusal")]
    assert len(kept) == 1 and kept[0]["seq"] == 2, (
        f"the refused row was filtered out of a control query that could not have excluded "
        f"it: {filtered}"
    )
    assert len(filtered["receipts"]) == 1, (
        f"the filter kept rows it could read and should have excluded: {filtered}"
    )
    reopened.close()


class _NoIdentity:
    """A provider that resolves nobody. The read tools never consult it (SPEC-mcp-operator §2)."""

    def resolve(self, context):
        return None


def _tool(server, name: str, arguments: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = server.handle(
        json.dumps(body).encode(),
        {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "tools/call", "Mcp-Name": name},
    )
    document = json.loads(response.body)
    assert "error" not in document, document
    return json.loads(document["result"]["content"][0]["text"])


# --- T516: both backends, because §5.2 amends the protocol and not one store -------------------


@postgres
def test_T516_postgres_names_a_bad_row_the_same_way(workspace) -> None:
    """SPEC-v0.11 §9: the amendment is to `StateStore`, so a second backend that raised on one
    bad row would still blind every reader in a deployment that uses it.

    The tamper is the same `UPDATE`, against the server rather than the file.
    """
    import psycopg

    from ctrlrun.postgres import PostgresStateStore

    schema = f"unreadable_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        written = a_chain(store, 4)
        tampered_id = written[1].receipt_id
        store.close()

        with psycopg.connect(POSTGRES_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT json FROM "{schema}".receipts WHERE seq = 2',
                )
                document = json.loads(str(cursor.fetchone()[0]))
                document["controls"] = [1.5]
                cursor.execute(
                    f'UPDATE "{schema}".receipts SET json = %s WHERE seq = 2',
                    (json.dumps(document),),
                )
            connection.commit()

        reopened = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        rows = reopened.receipts()
        report = verify_chain(reopened)
        reopened.close()

        assert len(rows) == 4, "one bad row cost the Postgres reader more than one row"
        assert isinstance(rows[1], UnreadableReceipt)
        assert rows[1].seq == 2 and rows[1].receipt_id == tampered_id
        assert [row.seq for row in rows] == [1, 2, 3, 4]
        assert ("content_altered", 2) in [(item.name, item.seq) for item in report.breaks]
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


# --- T517: replay names the row rather than dropping it ----------------------------------------


def test_T517_a_policy_replay_names_the_row_it_could_not_read(workspace) -> None:
    """`Control.replay` answers "what would this policy have decided differently?". A replay
    that silently skipped the one row somebody tampered with would report "no decision changes"
    about a store it could not read, which is `SPEC-v0.4 §3.8`'s false green.

    It is skipped and **named**, in the shape the loop already uses for a receipt it cannot
    rebuild an action from.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()
    _tamper_one_value(database, at=2)

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), reopened, clock=lambda: T0)
    deny = Policy.from_yaml(
        "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: deny\n"
    )
    rows = control._replay_policy(deny, limit=10)
    reopened.close()

    skipped = [row for row in rows if "skipped" in row]
    assert len(skipped) == 1, f"the unreadable row was not named in the replay: {rows}"
    assert "could not be read back" in skipped[0]["skipped"], skipped
    changed = [row for row in rows if "skipped" not in row]
    assert len(changed) == 3, (
        f"one bad row cost the replay more than one row: {len(changed)} of 3 rows replayed"
    )


# --- T518: where filtering a refused row would be a FALSE GREEN --------------------------------


def test_T518_a_scenario_store_fails_the_control_rather_than_filtering(workspace) -> None:
    """SPEC-v0.4 §3.8, on the surface where it costs most.

    `verify`'s scenarios run against a scratch store `verify` creates in this process and fills
    through this library, so a row that cannot be read back **there** is not evidence of a
    tamper: it is this library failing to read what it just wrote. `_readable` would drop it and
    the guarantee would grade clean over a store the grader could not read, which is the false
    green in its most expensive costume, because the clean result is the product.

    **This test exists because a mutation survived.** `_written`'s control was replaced with an
    unconditional pass and the whole suite stayed green, which is a finding about the tests and
    not a row to skip.
    """
    from ctrlrun.receipt import _readable
    from ctrlrun.verify.scenarios import _ControlFailed, _written

    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)

    class _OneRowRefused:
        """A store whose second row will not read back. Nothing else about it differs."""

        def __init__(self, real):
            self._real = real

        def receipts(self):
            rows = list(self._real.receipts())
            rows[1] = UnreadableReceipt(
                seq=2, receipt_id="ctr_" + "9" * 12, refusal="InvalidArgument"
            )
            return tuple(rows)

        def __getattr__(self, name):
            return getattr(self._real, name)

    damaged = _OneRowRefused(store)

    # The filtering helper would hand back a clean-looking two rows...
    assert len(_readable(damaged.receipts())) == 2

    # ...and `_written` refuses to, naming what it could not read.
    with pytest.raises(_ControlFailed) as failed:
        _written(damaged)
    assert "could not be read back" in failed.value.observed, failed.value.observed
    assert "2" in failed.value.observed, failed.value.observed

    # And on an undamaged store it is simply the receipts, so the guard is not a blanket refusal.
    assert len(_written(store)) == 3
    store.close()


# --- T519: a backend that cannot read back its own write -------------------------------------


def test_T519_the_store_conformance_kit_names_a_backend_that_cannot_read_its_own_write(
    tmp_path,
) -> None:
    """SPEC-v0.11 §5.2 lets a store hand back a refused row instead of raising. **A row the
    store just wrote is not that case**, and a candidate backend that cannot read back its own
    write must fail `receipt-round-trip` by name.

    Without this the kit falls into the field-by-field diff below the check and reports a
    missing attribute, which tells a backend author nothing about what they got wrong.

    **This test exists because a mutation survived**: the guard was replaced with `if False`
    and the conformance suite stayed green.
    """
    from ctrlrun.conformance.report import SuiteStatus
    from ctrlrun.conformance.store.backends import SQLiteBackend
    from ctrlrun.conformance.store.suites import evidence_receipt

    class _CannotReadItsOwnWrite:
        """A backend whose store writes correctly and reads every row back as a refusal."""

        name = "cannot-read-its-own-write"

        def __init__(self, root):
            self._real = SQLiteBackend(root)

        def open(self):
            real = self._real.open()

            class _Store:
                def receipts(self):
                    return tuple(
                        UnreadableReceipt(
                            seq=row.seq, receipt_id=row.receipt_id, refusal="InvalidArgument"
                        )
                        for row in real.receipts()
                    )

                def __getattr__(self, name):
                    return getattr(real, name)

            return _Store()

        def reopen(self):
            return None

    result = evidence_receipt.body(_CannotReadItsOwnWrite(tmp_path), 1)

    assert result.status is SuiteStatus.FAIL, result
    assert result.id == "receipt-round-trip", result
    assert "unreadable" in (result.reason or "").lower(), result.reason
    assert "InvalidArgument" in (result.reason or ""), result.reason

    # The positive control: the real backend still passes the same case, so this is not a kit
    # that fails everything.
    clean = evidence_receipt.body(SQLiteBackend(tmp_path), 1)
    assert clean.status is SuiteStatus.PASS, clean


# --- T520: a row that does not parse at all ----------------------------------------------------


#: Every tamper that stops a row parsing **before** `Receipt.from_dict` is reached. `T510` to
#: `T519` all tampered with a row's *content*, and `{}` and a float among the controls are both
#: valid JSON, so the parse was never on trial. An independent review found the gap: `json.loads`
#: ran in the generator expression that fed `_read_receipt`, outside its guard, so one `UPDATE`
#: setting `json` to anything unparseable raised through every reader exactly as before v0.11.
#: Worse than before, because `JSONDecodeError` is not a `CTRLRunError`, so `cli/main.py`'s
#: handler did not catch it either and `ctrlrun receipts` printed a **traceback**.
UNPARSEABLE = (
    ("not JSON at all", "not json at all", "JSONDecodeError"),
    ("empty", "", "JSONDecodeError"),
    ("truncated mid-object", '{"receipt_id": "ctr_1', "JSONDecodeError"),
    ("a JSON array, not an object", "[1, 2, 3]", "TypeError"),
    ("a bare JSON number", "3", "TypeError"),
    ("a bare JSON string", '"a receipt"', "TypeError"),
    ("JSON null", "null", "TypeError"),
)


@pytest.mark.parametrize(
    ("label", "stored", "refusal"), UNPARSEABLE, ids=[t[0] for t in UNPARSEABLE]
)
def test_T520_a_row_that_does_not_parse_still_costs_one_row(workspace, label, stored, refusal):
    """SPEC-v0.11 §5.2 and rule 3, through the door the first implementation left open.

    Rule 3 is *a malformed row names itself and blinds nothing else*, and it says **row**, not
    "row whose content is wrong". A tamperer writing `not json` is doing less work than one
    writing a well-formed document with a float in it, so a reader that survives the second and
    not the first has not paid the debt.

    `TypeError` for the four that parse to something that is not an object: `json.loads("3")` is
    an `int`, and the refusal names what is wrong with the row rather than what the next line
    tripped over.
    """
    database = workspace / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    written = a_chain(store, 4)
    ids = [receipt.receipt_id for receipt in written]
    untouched = written[0].action_id
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET json = ? WHERE seq = 2", (stored,))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    report = verify_chain(reopened)
    reopened.close()

    assert len(rows) == 4, f"{label}: one bad row cost {4 - len(rows)} extra rows"
    assert isinstance(rows[1], UnreadableReceipt), rows
    assert rows[1].seq == 2, "the refusal does not know where it is"
    assert rows[1].refusal == refusal, f"{label}: refused as {rows[1].refusal}"
    assert rows[1].receipt_id is None, "a receipt_id was invented for a row that has none"
    assert isinstance(rows[0], Receipt) and isinstance(rows[2], Receipt)
    assert ("content_altered", 2) in [(item.name, item.seq) for item in report.breaks]

    # And through the CLI, which is where this failed worst: `JSONDecodeError` is not a
    # `CTRLRunError`, so the handler did not catch it and the command printed a traceback.
    listed = _cli(workspace, database, "receipts")
    assert listed.exit_code == 0, listed.output
    assert "Traceback" not in listed.output, (
        f"{label}: ctrlrun receipts printed a traceback:\n{listed.output}"
    )
    for receipt_id in [ids[0], ids[2], ids[3]]:
        assert receipt_id in listed.output, f"{label}: an intact row is missing from the listing"

    inspected = _cli(workspace, database, "inspect", untouched)
    assert inspected.exit_code == 0, inspected.output
    assert "Traceback" not in inspected.output, inspected.output

    counted = _cli(workspace, database, "stats")
    assert counted.exit_code == 0, counted.output
    assert "Traceback" not in counted.output, counted.output
    assert "unreadable receipts                1" in counted.output, counted.output


@postgres
def test_T520b_postgres_refuses_an_unparseable_row_the_same_way(workspace) -> None:
    """The amendment is to `StateStore`, so a backend that raised here would blind every reader
    in a deployment that uses it. `json` is a `text` column on both sides, so both can hold this.
    """
    import psycopg

    from ctrlrun.postgres import PostgresStateStore

    schema = f"unparseable_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        a_chain(store, 4)
        store.close()

        with psycopg.connect(POSTGRES_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE "{schema}".receipts SET json = %s WHERE seq = 2', ("not json at all",)
                )
            connection.commit()

        reopened = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        rows = reopened.receipts()
        report = verify_chain(reopened)
        reopened.close()

        assert len(rows) == 4, "one bad row cost the Postgres reader more than one row"
        assert isinstance(rows[1], UnreadableReceipt)
        assert rows[1].seq == 2 and rows[1].refusal == "JSONDecodeError"
        assert ("content_altered", 2) in [(item.name, item.seq) for item in report.breaks]
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)

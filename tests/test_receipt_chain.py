"""Receipt integrity. Build-list item 6; SPEC-v0.6 §6, §8 T164-T170.

**The tamper test is the deliverable and it is written first** (§6.5). Everything else in this
file exists to stop that test lying: the positive control that an untampered chain verifies, the
`seq`-inside-the-content case that justifies the design, and the corpus proving the promotion of
`canonical_bytes` moved no hash that already exists.

What the chain does **not** cover is in §6.4 and is repeated wherever it is described. It is not
authorship, not a signature, not a defence against somebody who can rewrite every row including
the head, and not evidence that every action wrote a receipt.
"""

from __future__ import annotations

import itertools
import os
import unicodedata
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApprovalRequest
from ctrlrun.errors import InvalidArgument
from ctrlrun.policy import Decision
from ctrlrun.receipt import GENESIS_HASH, ReceiptResult

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)


@pytest.fixture
def pg_schema():
    """A private schema per test, created and dropped."""
    from ctrlrun.postgres import PostgresStateStore

    name = f"chain_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, name)
    try:
        yield name
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, name)


T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

#: Every `action_hash` in this table was recorded **before** `canonicalize` was redefined to call
#: `canonical_bytes` (§6.2, §9.1). That ordering is the whole point: a corpus generated after the
#: change would agree with itself no matter what the change did.
#:
#: `v0.1 §2.3` is a versioned security primitive and the approval binding rests on it, so this
#: project's standing rule for touching canonicalization is a test proving old hashes still
#: verify, or an explicit schema version bump. This is the first time that rule has been invoked.
FROZEN_HASHES = {
    "bool_vs_int": "sha256:78f9f98d97544da7221c1abbfaca41377c803113b6da662885467c61053bfe53",
    "empty_args": "sha256:b156e80d807f827558ffe6eb55f980fa7c7ca55d86a54126cad23f3b473b1c83",
    "nested_adversarial": "sha256:24734bb4c6a36d7fd5271d81dbc9f21c7fb035d96b53ca1ec22faa57d8a57819",
    "nfc": "sha256:6dfe3b5c7a62d475b34100e6c392a0e7e3839e454ec9df64904313787ea63cce",
    "nfd": "sha256:29ed73a58511555192d110886d44f4dc83bef2e94ba4a79ad97edbc7a93a010f",
    "plain": "sha256:1337055f45f3ab3272b02024b6fee99ddd39c9a84247a9ced3854ca0006d5038",
    "resource_env": "sha256:1aec6db2329a1aa1fc8e8c24c1938b9d9ed862fd2d4d31905c3cf4478fec1f29",
    "tuple_to_list": "sha256:4aaaf57e788681943723d1afe1ba0114987a66db2f388b734a09b96944194cd9",
    "unicode_raw": "sha256:a4f7febed12add0d0b05a84d5a23d0a05409d055f0df6c8b129ffafa62526e73",
    "with_user": "sha256:b9aace0329b8d3e8380f27735d550515d90c69a9018e87d14e356674cdebc9fe",
}


def corpus() -> dict[str, Action]:
    """The `Action`s `FROZEN_HASHES` was recorded from, rebuilt identically."""
    return {
        "plain": Action(
            name="stripe.refund",
            arguments={"payment_id": "p1", "amount": 2000},
            principal=Principal(agent="a"),
        ),
        "with_user": Action(
            name="stripe.refund",
            arguments={"payment_id": "p1", "amount": 2000},
            principal=Principal(agent="a", user="u@example.com"),
        ),
        "resource_env": Action(
            name="k8s.delete_namespace",
            arguments={"ns": "prod"},
            principal=Principal(agent="a"),
            resource="cluster-1",
            environment="production",
        ),
        "nfc": Action(
            name="x",
            arguments={"who": unicodedata.normalize("NFC", "café")},
            principal=Principal(agent="a"),
        ),
        "nfd": Action(
            name="x",
            arguments={"who": unicodedata.normalize("NFD", "café")},
            principal=Principal(agent="a"),
        ),
        "nested_adversarial": Action(
            name="x",
            arguments={"z": 1, "a": {"zz": [3, 2, 1], "aa": {"m": True, "b": None}}, "m": "s"},
            principal=Principal(agent="a"),
        ),
        "tuple_to_list": Action(
            name="x", arguments={"items": (1, 2, 3)}, principal=Principal(agent="a")
        ),
        "unicode_raw": Action(
            name="x", arguments={"note": "→ ünïcode ✓"}, principal=Principal(agent="a")
        ),
        "bool_vs_int": Action(
            name="x", arguments={"flag": True, "n": 1}, principal=Principal(agent="a")
        ),
        "empty_args": Action(name="x", arguments={}, principal=Principal(agent="a")),
    }


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


# --- T164b: promoting `canonical_bytes` changes no existing hash ------------------------------


@pytest.mark.parametrize("name", sorted(FROZEN_HASHES))
def test_T164b_every_recorded_hash_still_verifies(name: str) -> None:
    """SPEC-v0.6 §6.2, §9.1, and `v0.1 §2.3`'s rule about touching canonicalization.

    `canonicalize(action)` is now `canonical_bytes(payload)`. If that moved a single byte of a
    single canonical form, every approval granted before this release would stop consuming and
    every receipt hash on disk would stop verifying -- silently, because nothing else would
    change.
    """
    action = corpus()[name]
    assert action.action_hash == FROZEN_HASHES[name], (
        f"the canonical form of {name!r} moved. `ctrlrun.action/v1` is unchanged by this "
        "milestone (§9.5), so this is a break, not a version bump"
    )


def test_T164b_the_action_schema_string_is_unchanged() -> None:
    from ctrlrun.action import ACTION_SCHEMA

    assert ACTION_SCHEMA == "ctrlrun.action/v1"


def test_T164b_an_approval_granted_against_a_pre_v0_6_hash_still_consumes(tmp_path) -> None:
    """The end-to-end half. A hash that verifies in isolation but no longer binds an approval
    would be the same break wearing a different costume."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = corpus()["plain"]
    recorded = FROZEN_HASHES["plain"]
    assert action.action_hash == recorded

    request = ApprovalRequest(
        request_id="req_frozen",
        action_hash=recorded,  # the hash as it was written down before the promotion
        action=action,
        created_at=T0,
        expires_at=T0 + timedelta(hours=1),
    )
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:ada")
    consumed = store.consume_approval(request.request_id, action.action_hash)
    assert consumed.approval_id == request.request_id
    store.close()


def test_T164b_canonical_bytes_refuses_a_float_at_any_depth() -> None:
    """The control (§8). Without it the promoted function could be a permissive lookalike that
    agrees with the old one on every value the old one accepted."""
    from ctrlrun.action import canonical_bytes

    for payload in (
        {"amount": 1.5},
        {"outer": {"amount": 1.5}},
        {"items": [1, 2, 1.5]},
        {"items": [{"deep": [{"deeper": 1.5}]}]},
        {"amount": float("nan")},
        {"amount": float("inf")},
    ):
        try:
            canonical_bytes(payload)
        except InvalidArgument:
            pass
        else:
            raise AssertionError(f"canonical_bytes accepted a float in {payload!r}")

    # And the control for the control: it accepts what `v0.1 §2.3` allows.
    assert canonical_bytes({"a": 1, "b": "x", "c": True, "d": None, "e": [1, {"f": 2}]})


def test_T164b_canonicalize_is_canonical_bytes(monkeypatch) -> None:
    """§6.2 forbids a second canonicalizer **by name**, so one implementation is the assertion.

    Two functions that agree today are not one implementation; they are two that will drift. This
    replaces `canonical_bytes` and asserts `canonicalize` changed with it -- which it can only do
    if it calls through.
    """
    from ctrlrun import action as action_module

    action = corpus()["plain"]
    before = action_module.canonicalize(action)
    monkeypatch.setattr(action_module, "canonical_bytes", lambda payload: b"{}")
    after = action_module.canonicalize(action)
    assert before != after, (
        "replacing `canonical_bytes` did not change `canonicalize`, so there are two "
        "implementations of §2.3 and §6.2 forbids exactly that"
    )


# --- T164: the six tamper cases, each detected and NAMED ---------------------------------------


TAMPERS = (
    ("alter the amount", 'amount":2000', 'amount":1', "content_altered", 2),
    ("alter the decision", '"decision":"allow"', '"decision":"deny"', "content_altered", 2),
    ("alter the approver", '"approver":null', '"approver":"cli:eve"', "content_altered", 2),
    ("alter finished_at", "T12:00:00", "T09:00:00", "content_altered", 2),
)


@pytest.mark.parametrize(("label", "find", "replace", "name", "at"), TAMPERS, ids=lambda v: str(v))
def test_T164_an_altered_receipt_is_content_altered_at_its_seq(
    tmp_path, label, find, replace, name, at
) -> None:
    """SPEC-v0.6 §6.5, the first four rows. **The deliverable, written first.**

    A chain that only catches the easy case is worse than none, because it gets quoted as though
    it caught all of them -- so each row asserts *which* break was reported and *where*, not
    merely that something was invalid.

    The tampering is done in SQL, underneath the store, because that is the threat model: an
    `UPDATE` by somebody with write access who has no interest in going through CTRLRun.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()

    connection = sqlite3.connect(database)
    changed = connection.execute(
        "UPDATE receipts SET json = replace(json, ?, ?) WHERE seq = ?", (find, replace, at)
    ).rowcount
    connection.commit()
    altered = connection.execute("SELECT json FROM receipts WHERE seq = ?", (at,)).fetchone()[0]
    connection.close()
    assert changed == 1
    assert replace in altered, (
        f"the tamper for {label!r} did not change the document, so this row proves nothing: "
        f"{find!r} was not in it"
    )

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok
    reported = [(break_.name, break_.seq) for break_ in report.breaks]
    assert reported[0] == (name, at), (
        f"tampering with {label} was reported as {reported}, expected ({name!r}, {at}) first"
    )
    # And the link the **next** receipt asserts is broken too, because receipt `at` no longer
    # hashes to what receipt `at + 1` says it does. Both statements are true and an operator
    # wants both: one says the document was edited, the other says the chain no longer joins.
    #
    # A first version asserted exactly one break here. That was written against a reader which
    # carried the *stored* hash forward -- which hid this, and which also let one corrupted hash
    # column accuse the innocent next receipt. Both went with the same one-line change.
    assert ("link_broken", at + 1) in reported, (
        f"altering receipt {at} left receipt {at + 1}'s link unquestioned: {reported}"
    )


@pytest.mark.parametrize("scope", ["every row", "one row"])
def test_T164_a_missing_stored_hash_is_a_break_and_not_a_skip(tmp_path, scope) -> None:
    """The seventh tamper, and the one the first version of this file did not have.

    `content_altered` was the only check against the stored hash, and it was **skipped** rather
    than failed when the column was `NULL`:

        sqlite3 state.db "UPDATE receipts SET hash=NULL"
        ctrlrun receipts --verify-chain
        chain: 3 of 3 receipts verified / the chain is intact / exit 0

    One statement, no `WHERE`, and the chain reports itself sound while the only independent copy
    of every receipt's hash is gone. That is squarely inside what §6.4 says is covered -- *"an
    `UPDATE` on one row"* -- and it is `v0.4 §3.8`'s false green exactly: a check that cannot be
    evaluated resolved as a pass. A review found it.

    `unchained` does not cover this. That name is about a receipt with no `seq`, written before
    the chain existed; a row that has a `seq` and no `hash` was chained and has been stripped.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)
    store.close()

    connection = sqlite3.connect(database)
    where = "" if scope == "every row" else " WHERE seq = 2"
    connection.execute(f"UPDATE receipts SET hash = NULL{where}")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok, (
        f"stripping the stored hash from {scope} left the chain reported intact; the check that "
        "could not be made was resolved as a pass"
    )
    stripped = [break_ for break_ in report.breaks if break_.name == "hash_missing"]
    assert stripped, f"the break was not named `hash_missing`: {report.breaks}"
    assert stripped[0].seq == (1 if scope == "every row" else 2)


def test_T164_a_corrupt_stored_hash_does_not_accuse_the_next_receipt(tmp_path) -> None:
    """The link is checked against what the **document** hashes to, not against what the column
    says it hashed to.

    Trusting the column propagated a corrupt value into the next receipt's expected `prev_hash`,
    so altering one row's hash reported `content_altered` at that row **and** `link_broken` at
    its innocent successor. An operator reading that would go looking for two tampers.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET hash = ? WHERE seq = 2", ("sha256:" + "ab" * 32,))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok
    assert [(break_.name, break_.seq) for break_ in report.breaks] == [("content_altered", 2)], (
        f"one corrupted hash column was reported as {[(b.name, b.seq) for b in report.breaks]}; "
        "receipt 3 is untouched and its link is sound"
    )


def test_T164_a_deleted_receipt_is_missing_then_link_broken(tmp_path) -> None:
    """§6.5's fifth row. Deleting from the middle leaves a gap **and** an unlinked successor,
    and both are reported: a reader told only "missing" would not know the chain no longer
    joins up either side of the hole."""
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq = 2")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok
    reported = [(break_.name, break_.seq) for break_ in report.breaks]
    assert ("missing", 2) in reported, f"a gap in seq was not reported as missing: {reported}"
    assert ("link_broken", 3) in reported, (
        f"the receipt after the hole still linked to something: {reported}"
    )


def test_T164_reordering_two_receipts_is_detected_either_way(tmp_path) -> None:
    """§6.5's sixth row, and T166's justification for putting `seq` **inside** the hashed content.

    Swap two adjacent receipts *with* their `seq` values and both documents changed, so both
    hashes are wrong: `content_altered`. Swap them *without* their `seq` values and the documents
    are untouched but no longer join up: `link_broken`. There is no swap that is invisible, and
    that is the property `seq`-in-the-content buys.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    def build() -> object:
        database = tmp_path / f"state{next(counter)}.db"
        store = SQLiteStateStore(database, clock=lambda: T0)
        a_chain(store, 4)
        store.close()
        return database

    counter = iter(range(10))

    # (a) The rows move and their `seq` values move with them: the documents differ from what
    #     was hashed, because `seq` is part of what was hashed.
    with_seq = build()
    connection = sqlite3.connect(with_seq)
    connection.execute("UPDATE receipts SET seq = 99 WHERE seq = 2")
    connection.execute("UPDATE receipts SET seq = 2 WHERE seq = 3")
    connection.execute("UPDATE receipts SET seq = 3 WHERE seq = 99")
    connection.execute(
        "UPDATE receipts SET json = replace(json, '\"seq\":2', '\"seq\":3') WHERE seq = 3"
    )
    connection.execute(
        "UPDATE receipts SET json = replace(json, '\"seq\":3', '\"seq\":2') WHERE seq = 2"
    )
    connection.commit()
    connection.close()
    store = SQLiteStateStore(with_seq, clock=lambda: T0)
    moved = verify_chain(store)
    store.close()
    assert not moved.ok
    assert any(break_.name == "content_altered" for break_ in moved.breaks), (
        f"two receipts swapped with their seq values were reported as {moved.breaks}; `seq` is "
        "inside the hashed content precisely so that this is not invisible"
    )

    # (b) Only the `seq` column moves; the documents are untouched. The links no longer join.
    without_seq = build()
    connection = sqlite3.connect(without_seq)
    connection.execute("UPDATE receipts SET seq = 99 WHERE seq = 2")
    connection.execute("UPDATE receipts SET seq = 2 WHERE seq = 3")
    connection.execute("UPDATE receipts SET seq = 3 WHERE seq = 99")
    connection.commit()
    connection.close()
    store = SQLiteStateStore(without_seq, clock=lambda: T0)
    swapped = verify_chain(store)
    store.close()
    assert not swapped.ok
    assert swapped.breaks, "two receipts were reordered and nothing was reported"


# --- T165: the positive control ---------------------------------------------------------------


def test_T165_an_untampered_chain_verifies(tmp_path) -> None:
    """`v0.4 §1.3`'s required positive control. **Without it every row of T164 passes against a
    detector that returns "broken" unconditionally**, which is this project's oldest false green
    and the reason the control is required rather than encouraged."""
    from ctrlrun.receipt import verify_chain

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    receipts = a_chain(store, 5)
    report = verify_chain(store)
    store.close()

    assert report.ok, f"an untampered chain was reported broken: {report.breaks}"
    assert report.breaks == []
    assert report.verified == 5
    assert report.unchained == 0
    assert [receipt.seq for receipt in receipts] == [1, 2, 3, 4, 5]
    assert receipts[0].prev_hash == "sha256:" + "00" * 32, "the genesis link is not the genesis"
    for earlier, later in itertools.pairwise(receipts):
        assert later.prev_hash is not None
        assert later.prev_hash != earlier.prev_hash


def test_T165_an_empty_store_is_a_chain_of_length_zero(tmp_path) -> None:
    """§3.7: a head at `seq = 0` and no chained receipt is consistent, not truncated. Without
    this, every fresh database would report `head_mismatch` on its first read."""
    from ctrlrun.receipt import verify_chain

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    report = verify_chain(store)
    store.close()
    assert report.ok, f"an empty store was reported broken: {report.breaks}"
    assert report.verified == 0


def _unchain(database, only_seq: int | None = None) -> None:
    """Make receipts look as v0.5 wrote them: no chain columns, and no chain keys in the JSON.

    A pre-chain receipt is not a chained one with its column blanked. `0002_receipt_chain` adds
    nullable columns and backfills nothing, so a row written before it has `NULL` in all three
    **and** a document that never had `seq` or `prev_hash` in it -- which is why §6.5 reads the
    two apart rather than sorting a `NULL` to one end.
    """
    import json as _json
    import sqlite3

    connection = sqlite3.connect(database)
    where = "" if only_seq is None else f" WHERE seq = {int(only_seq)}"
    rows = connection.execute(f"SELECT rowid, json FROM receipts{where}").fetchall()
    for rowid, document in rows:
        parsed = _json.loads(document)
        parsed.pop("seq", None)
        parsed.pop("prev_hash", None)
        connection.execute(
            "UPDATE receipts SET json = ?, seq = NULL, prev_hash = NULL, hash = NULL "
            "WHERE rowid = ?",
            (_json.dumps(parsed, ensure_ascii=False, separators=(",", ":")), rowid),
        )
    connection.commit()
    connection.close()


# --- T167: truncation at the end, caught by the head and only by it ----------------------------


def test_T167_deleting_the_last_receipt_is_caught_by_the_head(tmp_path) -> None:
    """SPEC-v0.6 §6.3, §6.5.

    **This is the case the head row exists for.** Deleting the last N receipts leaves a chain
    that is internally consistent -- every link joins, every hash checks, no `seq` is skipped --
    so nothing inside the chain can see it. Only a head that still names a `seq` and a `hash` no
    row carries catches it.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 4)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq = 4")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok, "a truncated chain verified; the head is not being consulted"
    assert [break_.name for break_ in report.breaks] == ["head_mismatch"], (
        f"truncation reported {[b.name for b in report.breaks]}; the chain that remains is "
        "internally consistent, so head_mismatch is the only thing that can catch it"
    )
    assert report.head_seq == 4
    assert report.verified == 3


def test_T167_a_missing_head_row_is_not_a_valid_chain(tmp_path) -> None:
    """The obvious next move for somebody truncating: delete the head too.

    A store with no head row **must not** report a valid chain. Saying "ok" because there was
    nothing to compare against is the failure mode this whole section exists to prevent, and it
    is `v0.4 §3.8`'s false green with the evidence removed rather than faked.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq = 3")
    connection.execute("DELETE FROM receipt_chain")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert not report.ok, "a chain with no head reported valid; there was nothing to check it"
    assert any(break_.name == "head_mismatch" for break_ in report.breaks)


# --- T168: a pre-chain receipt is `unchained`, and never a pass --------------------------------


def test_T168_pre_chain_receipts_are_unchained_and_never_a_pass(tmp_path) -> None:
    """SPEC-v0.6 §3.7, §6.5.

    `0002_receipt_chain` does **not** backfill: a chain computed over rows written before the
    chain existed would assert an integrity property nobody can have. So they are reported, with
    their count, and `ok` is `False` for a run that verified nothing else -- folding them into a
    green count is `v0.4 §3.8`'s false green in a new costume.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 2)
    store.close()

    # A database migrated from v0.5: the rows are there, the columns are NULL, and -- the part
    # that matters -- the *documents* carry no `seq` either, because the code that wrote them
    # had never heard of one. Nulling only the column would be a tamper, not a migration.
    _unchain(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipt_chain SET seq = 0, hash = ?", (GENESIS_HASH,))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    assert report.unchained == 2
    assert report.verified == 0
    assert not report.ok, (
        "a store whose every receipt predates the chain reported ok; the summary would say "
        "'verified' about rows nothing verified"
    )
    assert [break_.name for break_ in report.breaks] == ["unchained"]
    assert "2 receipt" in report.breaks[0].detail, (
        f"the count is what an operator acts on: {report.breaks[0].detail}"
    )


def test_T168_an_unchained_row_does_not_manufacture_a_gap(tmp_path) -> None:
    """§6.5: rows with `seq IS NULL` have **no position** and are never interleaved.

    A `NULL` sorted to either end would produce a gap at one end or a head mismatch at the other,
    and an operator would chase a break that is not there.

    The mixed state is built the way it actually arises, not by blanking a row in the middle:
    receipts written before `0002_receipt_chain`, then the migration, then receipts written after
    it -- which chain from 1 because §3.7 starts the head at `seq = 0` whether the database was
    empty or already held a thousand unchained rows. A first draft of this test unchained row 1
    of an existing chain and left rows 2 and 3 pointing at it, which is a genuine break and not
    this one.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    before = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(before, 2)
    before.close()

    # v0.5 wrote those two. Now `0002` runs: nullable columns, nothing backfilled, head at 0.
    _unchain(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipt_chain SET seq = 0, hash = ?", (GENESIS_HASH,))
    connection.commit()
    connection.close()

    after = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), after, clock=lambda: T0)
    for index in (7, 8):
        control.execute(
            an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
        )
    report = verify_chain(after)
    after.close()

    assert report.unchained == 2
    assert report.verified == 2, "the receipts written after the migration are a chain of two"
    names = [break_.name for break_ in report.breaks]
    assert names == ["unchained"], (
        f"a NULL-seq row was given a position and manufactured a break: {report.breaks}"
    )
    assert not report.ok, "unchained is never a pass (§6.5)"


# --- T170: a failed receipt write raises, and leaves no gap ------------------------------------


def test_T170_a_failed_receipt_write_raises_and_leaves_no_gap(tmp_path) -> None:
    """SPEC-v0.6 §6.3.1, and the correction it records.

    Four assertions together, because any one alone would be misread:

    1. The exception **reaches the caller** and nothing swallows it.
    2. The effect record already says `COMMITTED` -- that write happened first and separately --
       so a retry of the key is refused as a duplicate rather than executed twice. This is why
       raising here is survivable where raising from a *sink* is not.
    3. The head is unadvanced and `seq` has no gap.
    4. `EXECUTION_COMMITTED` is in the events log, which is the other evidence stream and is
       written on a different path.

    A draft of §6.3.1 said a receipt whose write fails is *"logged, not raised -- `v0.1 §6.1`'s
    rule, unchanged"*. That was wrong on every clause: §6.1's logged-not-raised rule is about the
    **JSONL file**, and the same paragraph says the opposite about the store. The draft's version
    would have let an action that committed at the remote return normally with no evidence, no
    signal, and a chain that is blind to the loss by construction (§6.4).
    """
    from ctrlrun.errors import DuplicateEffect
    from ctrlrun.receipt import EventType, verify_chain

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    a_chain(store, 2)

    class RefusesTheReceipt:
        """A store whose receipt write fails, and whose every other write is the real one."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def put_receipt(self, receipt):
            raise RuntimeError("the store could not write that receipt")

    control = Control(Policy.from_yaml(ALLOW), RefusesTheReceipt(store), clock=lambda: T0)
    action = an_action("p_lost")
    executed: list[str] = []

    try:
        control.execute(action, lambda: executed.append("ran") or {"ok": True}, "refund:p_lost")
    except RuntimeError as raised:
        assert "could not write that receipt" in str(raised)
    else:
        raise AssertionError(
            "the receipt write failed and execute() returned normally; the action committed at "
            "the remote and nothing said so"
        )

    assert executed == ["ran"], "the executor did not run, so this tests the wrong failure"

    # 2. The effect is COMMITTED, so a retry is refused rather than executed twice.
    record = store.get_effect("refund:p_lost")
    assert record is not None
    assert record.state.value == "committed"
    try:
        store.reserve_effect("refund:p_lost", "act_again", LEASE)
    except DuplicateEffect:
        pass
    else:
        raise AssertionError("a retry after a lost receipt was allowed to reserve the key")

    # 3. No gap: nothing was numbered, so the chain is exactly the two receipts before it.
    report = verify_chain(store)
    assert report.ok, f"the failed write left the chain broken: {report.breaks}"
    assert report.verified == 2
    assert store.chain_head() is not None and store.chain_head()[0] == 2, (
        "the head advanced for a receipt that was never written, which is a gap `missing` would "
        "report forever"
    )

    # 4. The events log has it, which is what an operator reconciles with (§6.4).
    committed = [event for event in store.events() if event.type is EventType.EXECUTION_COMMITTED]
    assert any(event.action_id == action.action_id for event in committed), (
        "the action committed and no evidence stream says so"
    )
    store.close()


def test_T170_a_receipt_insert_that_fails_rolls_the_head_back(tmp_path) -> None:
    """The half the test above cannot reach, and the one the implementation is about.

    That store refuses *before* `put_receipt` runs, so the head was never touched -- which proves
    the caller sees the exception and proves nothing about the transaction. Here the head
    `UPDATE` lands and then the `INSERT` fails, which is the only ordering in which the head can
    be left naming a receipt that does not exist. §6.3 puts both in one transaction precisely so
    that the rollback takes the head with it, and a gap `missing` would report forever is the
    cost of getting it wrong.
    """
    import sqlite3

    from ctrlrun.receipt import verify_chain

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    receipts = a_chain(store, 2)
    head_before = store.chain_head()
    assert head_before == (2, receipts[-1].hash)

    # A receipt whose id is already taken: the head advances, then the INSERT hits the primary
    # key and the whole transaction goes.
    clash = replace(receipts[0], seq=None, prev_hash=None, hash=None)
    try:
        store.put_receipt(clash)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("a duplicate receipt_id was accepted, so nothing was rolled back")

    assert store.chain_head() == head_before, (
        f"the head is {store.chain_head()} and was {head_before}; it advanced for a receipt that "
        "was never inserted, and every later receipt would sit behind a permanent gap"
    )
    report = verify_chain(store)
    assert report.ok, f"the rolled-back write left the chain broken: {report.breaks}"
    assert report.verified == 2

    # And the next real receipt takes the seq the failed one would have.
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    control.execute(an_action("p_after"), lambda: {"ok": True}, "refund:p_after", lease=LEASE)
    assert verify_chain(store).ok
    assert store.chain_head() is not None and store.chain_head()[0] == 3
    store.close()


# --- §6.6: `ctrlrun receipts --verify-chain` ----------------------------------------------------


def test_the_verify_chain_flag_reports_a_break_by_seq_and_by_name(tmp_path, monkeypatch) -> None:
    """SPEC-v0.6 §6.6, and §9.4's "no new command".

    A **flag on the reader**, not a `ctrlrun chain` command: the same code path that already
    opens the store and already reads receipts, with a different report. A second entry point
    into the evidence would need `v0.3 §4.3.1`'s treatment for no benefit.

    Unlike `ctrlrun verify`'s G11, this reads the **operator's** store, which is the difference
    §6.6 keeps apart: verify never opens it, and this command already did.
    """
    import sqlite3

    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 3)
    store.close()
    monkeypatch.setenv("CTRLRUN_STATE", str(database))

    intact = CliRunner().invoke(cli.main, ["receipts", "--verify-chain"])
    assert intact.exit_code == 0, intact.output
    assert "3 of 3" in intact.output, intact.output

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE receipts SET json = replace(json, '\"amount\":2000', '\"amount\":1') WHERE seq = 2"
    )
    connection.commit()
    connection.close()

    broken = CliRunner().invoke(cli.main, ["receipts", "--verify-chain"])
    assert broken.exit_code != 0, (
        f"a tampered chain exited 0; an operator scripting this would not notice:\n{broken.output}"
    )
    assert "content_altered" in broken.output, broken.output
    assert "seq 2" in broken.output, broken.output


def test_the_verify_chain_flag_never_reports_unchained_as_a_pass(tmp_path, monkeypatch) -> None:
    """§6.5: `unchained` is never a pass, and the summary says how many of how many."""
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    a_chain(store, 2)
    store.close()
    _unchain(database)

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    result = CliRunner().invoke(cli.main, ["receipts", "--verify-chain"])
    assert result.exit_code != 0, (
        f"a store whose every receipt predates the chain exited 0:\n{result.output}"
    )
    assert "unchained" in result.output
    assert "0 of 2" in result.output, (
        f"the summary must say how many of how many were verified:\n{result.output}"
    )


# --- §6.2: one canonicalizer, and the chain hash uses it ---------------------------------------


def test_the_chain_hash_is_the_canonical_form_and_not_the_stored_json(tmp_path) -> None:
    """SPEC-v0.6 §6.2, and the one MUST in §6 that had nothing behind it.

    A review replaced `canonical_bytes(self.to_dict())` with `self.to_json().encode()` -- a
    second, non-canonical encoder, which is what §6.2 forbids **by name** -- and the whole suite
    passed. It is not an equivalent mutant: `to_json()` has no `sort_keys`, so two receipts whose
    `arguments` differ only in insertion order hash differently.

    That would also make the two backends disagree about the same receipt, because SQLite stores
    `to_json()` (insertion order) and Postgres stores `json.dumps(..., sort_keys=True)`. A chain
    written on one and read on the other would report every row `content_altered`.
    """
    from ctrlrun.receipt import Receipt

    def receipt_with(arguments: dict) -> Receipt:
        return Receipt(
            receipt_id="ctr_" + "1" * 12,
            action_id="act_1",
            action="stripe.refund",
            action_hash="sha256:" + "0" * 64,
            principal=Principal(agent="a"),
            resource=None,
            arguments=arguments,
            environment="production",
            decision=Decision.ALLOW,
            decision_reason="decision",
            result=ReceiptResult.COMMITTED,
            started_at=T0,
            finished_at=T0,
            seq=1,
            prev_hash=GENESIS_HASH,
        )

    one = receipt_with({"b": 1, "a": 2, "nested": {"z": 1, "a": 2}})
    two = receipt_with({"a": 2, "nested": {"a": 2, "z": 1}, "b": 1})
    assert one.to_json() != two.to_json(), (
        "the control is void: these two receipts already serialise identically, so equal hashes "
        "would prove nothing about sorting"
    )
    assert one.chain_hash() == two.chain_hash(), (
        "two receipts differing only in key insertion order hash differently, so the chain hash "
        "is over the stored JSON rather than the canonical form (§6.2)"
    )


@postgres
def test_a_chain_written_on_one_backend_verifies_when_read_on_the_other(
    tmp_path, pg_schema
) -> None:
    """The consequence of the above, end to end.

    The two backends store the receipt document as different byte strings on purpose -- SQLite
    `to_json()`, Postgres `json.dumps(..., sort_keys=True)`. The chain must not care, because it
    recomputes the canonical form from the parsed document rather than hashing whatever was
    stored. If it ever did care, an operator migrating a database would find every receipt
    reported as altered.
    """
    from ctrlrun.postgres import PostgresStateStore
    from ctrlrun.receipt import verify_chain

    written = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    receipts = a_chain(written, 3)
    written.close()

    on_postgres = PostgresStateStore(POSTGRES_URL, schema=pg_schema, clock=lambda: T0)
    for receipt in receipts:
        # The same documents, re-chained by the other backend.
        on_postgres.put_receipt(replace(receipt, seq=None, prev_hash=None, hash=None))
    report = verify_chain(on_postgres)
    assert report.ok, f"a chain rewritten on Postgres does not verify: {report.breaks}"
    assert report.verified == 3
    for original, moved in zip(receipts, on_postgres.receipts(), strict=True):
        assert moved.chain_hash() == original.chain_hash(), (
            "the same document hashes differently on the two backends, so the chain hash is "
            "over the stored bytes and not the canonical form"
        )
    on_postgres.close()


# --- §6.3: the head row is a lock, and what that is worth on each backend ----------------------


def test_concurrent_receipt_writes_leave_no_gap_and_lose_nothing(tmp_path) -> None:
    """SPEC-v0.6 §6.3, on SQLite, across threads.

    Eight writers, thirty receipts each: the `seq` values must be exactly 1..240 with no gap, no
    duplicate, and nothing lost. §6.3's whole argument is that a compare-and-set here would drop
    receipts under contention, and until now nothing drove `put_receipt` concurrently on any
    backend at all -- the design decision the item argues hardest for was untested.

    **On SQLite the unconditional `UPDATE` is a subsumed guard, and this test says so rather than
    implying otherwise.** `BEGIN IMMEDIATE` already holds the database write lock, so replacing
    the head update with a compare-and-set changes nothing here; a review measured 240/240 either
    way. It is load-bearing on Postgres, which is the case below. Reporting this row as evidence
    for the lock would be a subsumed guard reported as a defence -- a check that can only fire
    where a later one already would, with the same observable result.
    """
    import threading

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    writers, each = 8, 30
    barrier = threading.Barrier(writers)
    errors: list[BaseException] = []

    def write(index: int) -> None:
        barrier.wait(timeout=30)
        for number in range(each):
            try:
                store.put_receipt(_a_receipt(f"ctr_{index:02d}{number:04d}"))
            except BaseException as broke:
                errors.append(broke)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()

    assert errors == [], f"{len(errors)} writers raised: {errors[:3]}"
    seqs = sorted(receipt.seq for receipt in store.receipts())
    assert seqs == list(range(1, writers * each + 1)), (
        f"{len(seqs)} receipts with seqs {seqs[:5]}...{seqs[-3:]}; a gap or a duplicate means a "
        "receipt was dropped or numbered twice under contention"
    )
    from ctrlrun.receipt import verify_chain

    assert verify_chain(store).ok
    store.close()


@postgres
def test_concurrent_receipt_writes_across_processes_lose_nothing(tmp_path, pg_schema) -> None:
    """§6.3 on Postgres, across **processes**, which is where the lock is load-bearing.

    Replacing the unconditional head `UPDATE` with a compare-and-set here is not equivalent and
    not subtle: a review measured **82 of 120 receipts lost** to `UniqueViolation`. Postgres has
    no `BEGIN IMMEDIATE`, so the row lock is the only thing serialising two unrelated actions --
    which §6.3 names as the first place in the kernel where that happens.
    """
    import json
    import subprocess
    import sys
    import tempfile
    import textwrap
    from pathlib import Path

    writers, each = 6, 20
    child = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "src")!r})
        from datetime import UTC, datetime
        from ctrlrun.postgres import PostgresStateStore
        from ctrlrun.receipt import Receipt, ReceiptResult
        from ctrlrun.action import Principal
        from ctrlrun.policy import Decision

        job = json.loads(sys.stdin.read())
        T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        store = PostgresStateStore(job["url"], schema=job["schema"], clock=lambda: T0)
        wrote, failed = [], []
        for number in range({each}):
            try:
                written = store.put_receipt(Receipt(
                    receipt_id="ctr_%02d%04d" % (job["index"], number),
                    action_id="act_1", action="stripe.refund",
                    action_hash="sha256:" + "0" * 64, principal=Principal(agent="a"),
                    resource=None, arguments={{}}, environment="production",
                    decision=Decision.ALLOW, decision_reason="decision",
                    result=ReceiptResult.COMMITTED, started_at=T0, finished_at=T0,
                ))
                wrote.append(written.seq)
            except BaseException as broke:
                failed.append(type(broke).__name__)
        store.close()
        sys.stdout.write(json.dumps({{"wrote": wrote, "failed": failed}}))
    """)

    with tempfile.TemporaryDirectory() as home:
        script = Path(home) / "writer.py"
        script.write_text(child, encoding="utf-8")
        payloads = [
            json.dumps({"url": POSTGRES_URL, "schema": pg_schema, "index": index})
            for index in range(writers)
        ]
        children = [
            subprocess.Popen(
                [sys.executable, str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in payloads
        ]
        try:
            for process, payload in zip(children, payloads, strict=True):
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
            results = []
            for process in children:
                process.wait(timeout=120)
                assert process.stdout is not None and process.stderr is not None
                raw = process.stdout.read()
                assert raw.strip(), f"a writer said nothing: {process.stderr.read()[-400:]}"
                results.append(json.loads(raw))
        finally:
            for process in children:
                if process.poll() is None:
                    process.kill()

    assert all(result["failed"] == [] for result in results), (
        f"writers raised: {[r['failed'] for r in results if r['failed']]}"
    )
    from ctrlrun.postgres import PostgresStateStore
    from ctrlrun.receipt import verify_chain

    store = PostgresStateStore(POSTGRES_URL, schema=pg_schema, clock=lambda: T0)
    seqs = sorted(receipt.seq for receipt in store.receipts())
    assert seqs == list(range(1, writers * each + 1)), (
        f"{len(seqs)} of {writers * each} receipts survived; a compare-and-set here loses them "
        "to UniqueViolation, which is what §6.3 takes the row lock to prevent"
    )
    assert verify_chain(store).ok
    store.close()


def _a_receipt(receipt_id: str):
    from ctrlrun.receipt import Receipt

    return Receipt(
        receipt_id=receipt_id,
        action_id="act_1",
        action="stripe.refund",
        action_hash="sha256:" + "0" * 64,
        principal=Principal(agent="a"),
        resource=None,
        arguments={},
        environment="production",
        decision=Decision.ALLOW,
        decision_reason="decision",
        result=ReceiptResult.COMMITTED,
        started_at=T0,
        finished_at=T0,
    )


@postgres
def test_verify_chain_reads_a_postgres_store_through_store_url(tmp_path, pg_schema) -> None:
    """§6.6 and §9.4 together, which the two items landed separately.

    Item 5 put `--store-url` on `ctrlrun receipts` and item 6 put `--verify-chain` on it. Neither
    test covered the pair, and the pair is the case that matters: an operator on the backend this
    milestone exists for, checking the chain in their own database.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli
    from ctrlrun.postgres import PostgresStateStore

    store = PostgresStateStore(POSTGRES_URL, schema=pg_schema, clock=lambda: T0)
    a_chain(store, 3)
    store.close()

    url = f"{POSTGRES_URL}?ctrlrun_schema={pg_schema}"
    intact = CliRunner().invoke(cli.main, ["receipts", "--verify-chain", "--store-url", url])
    assert intact.exit_code == 0, intact.output
    assert "3 of 3" in intact.output, intact.output

    import psycopg

    admin = psycopg.connect(POSTGRES_URL, autocommit=True)
    admin.execute(f'UPDATE "{pg_schema}".receipts SET hash = NULL WHERE seq = 2')
    admin.close()

    broken = CliRunner().invoke(cli.main, ["receipts", "--verify-chain", "--store-url", url])
    assert broken.exit_code != 0, broken.output
    assert "hash_missing" in broken.output, broken.output
    assert "2 of 3" in broken.output, (
        f"the summary counts a stripped receipt as verified:\n{broken.output}"
    )

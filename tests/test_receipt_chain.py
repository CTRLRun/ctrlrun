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
import unicodedata
from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApprovalRequest
from ctrlrun.errors import InvalidArgument

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
    assert [(break_.name, break_.seq) for break_ in report.breaks] == [(name, at)], (
        f"tampering with {label} was reported as {[(b.name, b.seq) for b in report.breaks]}, "
        f"expected exactly [({name!r}, {at})]"
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

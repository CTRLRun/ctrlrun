"""`ctrlrun inspect <action_id>`. Build-list item 5; SPEC-v0.2 §5, acceptance test T18.

One action's whole history in one place: what was proposed, what the policy said, which
approval was involved, what happened to the effect, and what the receipt records. The
evidence already holds all of it, in four places; this is the command that joins them.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ctrlrun import (
    AmbiguousEffect,
    ApprovalRequired,
    Control,
    Policy,
    SQLiteStateStore,
    context,
    protect,
    with_approval,
)
from ctrlrun.cli.main import main
from ctrlrun.receipt import JSONLEventSink

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""

# SPEC-v0.3 §12.2 — v2 where the header block gained the principal's issuer, expiry and claim
# names. Spelled out here rather than imported, so a bump has to be made deliberately in two
# places and cannot ride along with an unrelated edit.
INSPECTION_SCHEMA = "ctrlrun.inspection/v2"


class Remote:
    def __init__(self) -> None:
        self.calls = 0
        self.lose = False

    def refund(self, payment_id: str) -> str:
        self.calls += 1
        if self.lose:
            raise TimeoutError("no response from api.stripe.com after 30s")
        return f"re_{payment_id}"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "ctrlrun.yaml").write_text(POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)
    return tmp_path


@pytest.fixture
def store(workspace):
    store = SQLiteStateStore(workspace / ".ctrlrun" / "state.db")
    yield store
    store.close()


@pytest.fixture
def control(workspace, store):
    return Control(
        Policy.from_file(workspace / "ctrlrun.yaml"),
        store,
        sinks=[JSONLEventSink(workspace / ".ctrlrun")],
    )


@pytest.fixture
def remote():
    return Remote()


@pytest.fixture
def refund(control, remote):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id)

    return refund


def _cli(*args):
    return CliRunner().invoke(main, list(args))


def _ok(result):
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    return result


def _lost_response(refund, remote, store):
    """T1's scenario: the remote commits and the response goes missing."""
    remote.lose = True
    with context(agent="refund-agent", user="alice"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=200)
    return store.receipts()[-1].action_id


# --- T18: the JSON document ------------------------------------------------------------


def test_T18_inspect_json_emits_the_inspection_schema(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    assert document["schema"] == INSPECTION_SCHEMA
    assert document["action_id"] == action_id


def test_T18_inspect_json_carries_the_receipt(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    assert document["receipt"]["receipt_id"] == store.receipts()[-1].receipt_id
    assert document["receipt"]["result"] == "ambiguous"


def test_T18_inspect_json_carries_the_effect_record(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    assert document["effect"]["effect_key"] == "refund:txn_1"
    assert document["effect"]["state"] == "ambiguous"
    assert document["effect"]["attempt"] == 1


def test_T18_inspect_json_carries_the_events_in_event_id_order(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    ids = [event["event_id"] for event in document["events"]]
    assert ids == sorted(ids)
    assert [event["type"] for event in document["events"]] == [
        "ACTION_PROPOSED",
        "POLICY_EVALUATED",
        "EFFECT_RESERVED",
        "EXECUTION_STARTED",
        "EXECUTION_AMBIGUOUS",
    ]


def test_T18_inspect_json_carries_every_approval_for_the_action_hash(control, store):
    """§5 — every approval carrying this hash, not only the consumed one: an invalidated or
    expired request is part of the history."""

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as first:
        refund(payment_id="txn_2", amount=2000)
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as second:
        refund(payment_id="txn_2", amount=2000)

    _ok(_cli("approve", second.value.request_id))
    with context(agent="refund-agent"), with_approval(second.value.request_id):
        refund(payment_id="txn_2", amount=2000)

    action_id = store.receipts()[-1].action_id
    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    ids = {approval["approval_id"] for approval in document["approvals"]}
    assert ids == {first.value.request_id, second.value.request_id}
    statuses = {approval["approval_id"]: approval["status"] for approval in document["approvals"]}
    assert statuses[second.value.request_id] == "consumed"
    assert statuses[first.value.request_id] == "pending"


def test_T18_every_enum_renders_by_value(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    raw = _ok(_cli("inspect", action_id, "--json")).stdout

    assert "EffectState." not in raw
    assert "ReceiptResult." not in raw
    assert "Decision." not in raw
    assert "ApprovalStatus." not in raw
    assert "EventType." not in raw


def test_T18_an_unknown_action_id_exits_non_zero_with_empty_stdout(store, workspace):
    result = _cli("inspect", "act_nothing", "--json")

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "act_nothing" in result.stderr


def test_T18_an_unknown_action_id_is_non_zero_in_human_form_too(store, workspace):
    result = _cli("inspect", "act_nothing")

    assert result.exit_code != 0
    assert result.stdout == ""


# --- §5: the matching rule, and the two nullable fields --------------------------------


def test_the_action_id_is_matched_exactly_never_as_a_prefix(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    result = _cli("inspect", action_id[:12], "--json")

    assert result.exit_code != 0
    assert result.stdout == ""


def test_the_receipt_is_null_for_an_action_still_awaiting_a_human(control, store):
    """§5, and v0.1 §6.1: a suspended action is not terminal, so it has no receipt yet.

    Null because *this* action has none. Another action commits first and writes one, so a
    lookup that returned any receipt rather than this action's would be caught here.
    """

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_committed", amount=200)
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
        refund(payment_id="txn_2", amount=2000)

    assert store.receipts(), "another action must hold a receipt for this to prove anything"
    action_id = store.events()[-1].action_id
    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    assert document["receipt"] is None
    assert document["approvals"][0]["status"] == "pending"
    assert [event["type"] for event in document["events"]] == [
        "ACTION_PROPOSED",
        "POLICY_EVALUATED",
        "APPROVAL_REQUESTED",
    ]


def test_the_effect_is_null_for_an_action_with_no_effect_key(control, store):
    """Null because *this* action has none — not because the store happens to be empty.

    A keyed action runs first, so a lookup that fell back to any record rather than to this
    action's would find one and be caught here.
    """

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def keyed(payment_id: str, amount: int) -> str:
        return "re_1"

    @protect("stripe.refund", control=control)
    def keyless(payment_id: str, amount: int) -> str:
        return "re_2"

    with context(agent="refund-agent"):
        keyed(payment_id="txn_1", amount=200)
        keyless(payment_id="txn_2", amount=200)

    assert store.list_effects(), "the store must hold an effect record for this to prove anything"
    action_id = store.receipts()[-1].action_id
    document = json.loads(_ok(_cli("inspect", action_id, "--json")).stdout)

    assert document["effect"] is None
    assert document["receipt"]["effect_key"] is None


def test_the_json_is_one_object_on_stdout(refund, remote, store):
    """A script reads this; one object, parseable whole, and nothing else printed."""
    action_id = _lost_response(refund, remote, store)

    stdout = _ok(_cli("inspect", action_id, "--json")).stdout

    assert isinstance(json.loads(stdout), dict)


# --- §5: the human rendering -----------------------------------------------------------


def test_the_human_output_leads_with_the_action_id_and_name(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    output = _ok(_cli("inspect", action_id)).stdout

    assert output.splitlines()[0].startswith(action_id)
    assert "stripe.refund" in output.splitlines()[0]


def test_the_human_output_shows_the_header_fields_the_spec_names(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    output = _ok(_cli("inspect", action_id)).stdout

    assert "principal   refund-agent (user: alice)" in output
    assert "environment production" in output
    assert "decision    allow" in output
    assert "effect      refund:txn_1  ambiguous  attempt 1" in output
    assert "receipt     ctr_" in output
    assert '"payment_id": "txn_1"' in output


def test_the_human_output_lists_every_event_with_its_id_and_timestamp(refund, remote, store):
    action_id = _lost_response(refund, remote, store)

    output = _ok(_cli("inspect", action_id)).stdout

    for event in store.events():
        assert f"{event.event_id}" in output
        assert str(event.type) in output
    assert "EXECUTION_AMBIGUOUS" in output


def test_the_human_output_shows_who_resolved_an_ambiguous_effect(refund, remote, store):
    """SPEC-v0.2 §2.2 — `ctrlrun inspect` MUST show `resolved_by`."""
    action_id = _lost_response(refund, remote, store)
    _ok(_cli("resolve", "refund:txn_1", "--failed"))

    output = _ok(_cli("inspect", action_id)).stdout

    assert "EFFECT_RESOLVED" in output
    assert "resolved_by=human" in output


def test_the_human_output_of_a_suspended_action_comes_from_its_approval_request(control, store):
    """An action awaiting a human has no receipt, and an Event carries neither its name nor
    its arguments. The approval request holds the whole Action (v0.1 §4.1), and that is the
    only reason this header can be rendered at all."""

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent", user="alice"), pytest.raises(ApprovalRequired):
        refund(payment_id="txn_2", amount=2000)

    action_id = store.events()[0].action_id
    output = _ok(_cli("inspect", action_id)).stdout

    assert "stripe.refund" in output.splitlines()[0]
    assert "principal   refund-agent (user: alice)" in output
    assert '"payment_id": "txn_2"' in output
    assert "receipt     -  (awaiting a human)" in output


def test_the_human_output_shows_the_approval_and_who_answered(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as pending:
        refund(payment_id="txn_2", amount=2000)
    _ok(_cli("approve", pending.value.request_id))
    with context(agent="refund-agent"), with_approval(pending.value.request_id):
        refund(payment_id="txn_2", amount=2000)

    action_id = store.receipts()[-1].action_id
    output = _ok(_cli("inspect", action_id)).stdout

    assert pending.value.request_id in output
    assert "cli:local" in output
    assert "consumed" in output


def test_a_blocked_retry_inspects_as_its_own_action(refund, remote, store):
    """Each attempt is a distinct action (v0.1 §5.3), so each has its own history."""
    _lost_response(refund, remote, store)
    remote.lose = False
    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund(payment_id="txn_1", amount=200)

    first, second = (receipt.action_id for receipt in store.receipts())
    assert first != second

    document = json.loads(_ok(_cli("inspect", second, "--json")).stdout)
    assert document["receipt"]["result"] == "blocked"
    assert document["effect"]["state"] == "ambiguous"
    assert [event["type"] for event in document["events"]] == [
        "ACTION_PROPOSED",
        "POLICY_EVALUATED",
        "EFFECT_RESERVATION_REFUSED",
    ]


def test_inspect_reads_the_store_the_env_var_names(refund, remote, store, tmp_path, monkeypatch):
    """Every CLI command works on the store an agent is using (v0.1 §8)."""
    action_id = _lost_response(refund, remote, store)
    monkeypatch.setenv("CTRLRUN_STATE", str(tmp_path / "elsewhere.db"))

    result = _cli("inspect", action_id, "--json")

    assert result.exit_code != 0
    assert result.stdout == ""

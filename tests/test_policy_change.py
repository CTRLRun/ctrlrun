"""T353 to T370: a policy change is a protected action (SPEC-v0.8 §8).

The policy is the one file that decides every other decision. v0.6 made a change **evidenced**;
v0.8 makes it **approved**, and these are the tests of that sentence.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApproverIdentity, _granting_principal
from ctrlrun.control import Control
from ctrlrun.errors import ActionDenied, ApprovalMismatch, ApprovalRequired, InvalidArgument
from ctrlrun.identity import StaticIdentityProvider
from ctrlrun.policy import POLICY_CHANGE_ACTION, Policy, hash_with_authority
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

#: A policy that declares its own change as an approval, which §8.2.1 makes the precondition of
#: deciding anything at all under `require_approved_policy`.
POLICY = """
schema: ctrlrun.policy/v6
environment: production
controls:
  change-management:
    title: A policy change is approved by a named owner
    approver_role: change-owner
actions:
  payments.refund:
    decision: allow
  ctrlrun.policy.change:
    decision: approve
    controls: [change-management]
"""

AGENT = Principal(agent="ops-agent", user="ada")
PROPOSER = Principal(
    agent="human:proposer", user="pro@example.com", claims={"roles": ("change-owner",)}
)
OWNER = Principal(
    agent="human:owner",
    user="owner@example.com",
    issuer="https://issuer.example",
    claims={"roles": ("change-owner",)},
)
KEY = "refund:EU-42"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


class _Fixed:
    def __init__(self, principal: Principal | None) -> None:
        self._principal = principal

    def resolve(self, context: Any) -> Principal | None:
        return self._principal


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return "done"


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture(
    params=[
        "in-memory",
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set"
            ),
        ),
    ]
)
def store(request, tmp_path, clock):
    if request.param == "in-memory":
        made = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"policy_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock, *, policy=POLICY, require=True, approver=OWNER, proposer=PROPOSER):
    return Control(
        Policy.from_yaml(policy),
        store,
        clock=clock,
        require_approved_policy=require,
        approver_identity=(
            None if approver is False else ApproverIdentity(_Fixed(approver), roles_claim="roles")
        ),
        identity=StaticIdentityProvider(
            agent=proposer.agent if proposer else AGENT.agent,
            user=proposer.user if proposer else AGENT.user,
        ),
    )


def _action(control, name: str = "payments.refund", **arguments: Any) -> Action:
    return Action(
        name=name,
        arguments=arguments or {"amount": 100, "payment_id": "EU-42"},
        principal=AGENT,
        environment=control.environment,
    )


def _approved(control, store, clock, *, by=OWNER):
    """Run the whole flow: propose, approve, present. Returns the committed receipt."""
    candidate = Policy.from_yaml(POLICY)
    with pytest.raises(ApprovalRequired) as pending:
        control._propose_policy(candidate, authority=control.authority)
    with _granting_principal(by, entitled=["change-management"]):
        store.grant_approval(pending.value.request_id, "mcp-operator:owner")
    return control._propose_policy(
        candidate, authority=control.authority, approval_id=pending.value.request_id
    )


# --- T353: the canonical form, and what moves it ----------------------------------------------


def test_T353_comments_key_order_and_whitespace_do_not_move_the_hash():
    """§8.2. The hash is over the canonical form and not the text."""
    spaced = POLICY.replace("actions:", "# a comment nobody reads\nactions:").replace(
        "    decision: allow", "    decision:    allow"
    )
    reordered = POLICY.replace("  payments.refund:\n    decision: allow\n", "").replace(
        "actions:\n", "actions:\n  payments.refund:\n    decision: allow\n"
    )

    base = hash_with_authority(Policy.from_yaml(POLICY), None, "production")

    assert hash_with_authority(Policy.from_yaml(spaced), None, "production") == base
    assert hash_with_authority(Policy.from_yaml(reordered), None, "production") == base


def test_T353_a_semantic_change_moves_the_hash():
    changed = POLICY.replace(
        "  payments.refund:\n    decision: allow", "  payments.refund:\n    decision: deny"
    )

    assert hash_with_authority(
        Policy.from_yaml(changed), None, "production"
    ) != hash_with_authority(Policy.from_yaml(POLICY), None, "production")


def test_T353_the_same_file_hashes_differently_per_environment():
    """§8.2, and this is the one that is not obvious: an approval is **per deployment**.

    `hash_with_authority` folds in the effective environment and the effective authority, so the
    same file in staging and in prod is two hashes and needs two approvals. An operator wants
    that -- approving a change in staging must not approve it in production -- and nothing else
    in the system would say so.
    """
    policy = Policy.from_yaml(POLICY)

    production = hash_with_authority(policy, None, "production")
    staging = hash_with_authority(policy, None, "staging")

    assert production != staging


def test_T353_a_separately_loaded_authority_moves_it_too():
    from ctrlrun.authority import Authority

    policy = Policy.from_yaml(POLICY)
    authority = Authority.from_yaml(
        "schema: ctrlrun.policy/v6\nauthority:\n  grants:\n"
        '    - id: g\n      subject: {agent: "*"}\n      actions: ["*"]\n',
        standalone=True,
    )

    assert hash_with_authority(policy, authority, "production") != hash_with_authority(
        policy, None, "production"
    )


# --- T354, T355, T356: the enforcement --------------------------------------------------------


def test_T354_an_unapproved_policy_decides_nothing(store, clock):
    """G21. Under `require_approved_policy=True` with no committed `policy:<hash>` effect,
    an action the policy would have allowed is denied."""
    control = _control(store, clock)
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), executor, KEY)

    assert refused.value.reason == "policy_unapproved"
    assert executor.calls == 0
    denied = [r for r in store.receipts() if str(r.result) == "denied"]
    assert denied and denied[-1].decision_reason == "policy_unapproved"


def test_T355_with_the_hash_approved_the_action_is_decided_as_0_7_0_decided_it(store, clock):
    """The positive control, compared field by field against the same deployment without the
    requirement. A check that refused everything would pass T354 and fail this."""
    control = _control(store, clock)
    _approved(control, store, clock)

    executor = _Executor()
    receipt = control.execute(_action(control), executor, KEY)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"
    assert str(receipt.decision) == "allow"


def test_T356_with_the_requirement_off_nothing_changes(store, clock):
    """R1 one layer down: a deployment that does not ask for this behaves as 0.7.0 did."""
    control = _control(store, clock, require=False)
    executor = _Executor()

    receipt = control.execute(_action(control), executor, KEY)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T357: the whole flow, end to end ---------------------------------------------------------


def test_T357_the_flow_works_end_to_end(store, clock):
    """The test §8.2.1 exists to make possible.

    A first draft said a document declaring `ctrlrun.policy.change` fails to load, which is
    unbuildable: `evaluate` answers `DENY unknown_action` for a name the document does not
    list, so a name no document may declare is a name every proposal is denied for, and a
    deployment requiring an approved policy would deny every action for ever.
    """
    control = _control(store, clock)

    receipt = _approved(control, store, clock)

    assert str(receipt.result) == "committed"
    assert receipt.action == POLICY_CHANGE_ACTION
    assert receipt.arguments["to"] == control._policy_hash
    # And the next Control under that hash decides normally.
    fresh = _control(store, clock)
    assert str(fresh.execute(_action(fresh), _Executor(), "refund:EU-43").result) == "committed"


# --- T358: the "write a policy whose change rule is allow" hole -------------------------------


@pytest.mark.parametrize(
    "change_rule",
    ["    decision: allow", "    decision: deny", None],
    ids=["allow", "deny", "absent"],
)
def test_T358_a_policy_that_does_not_send_its_own_change_to_a_human_decides_nothing(
    store, clock, change_rule
):
    """§8.2.1. This is the rule that closes §8.6's obvious escape.

    An administrator writes a policy whose change rule is `allow`. Installing it still needs an
    approval under the policy in force, and the moment it is installed the deployment stops
    deciding anything, with the refusal naming the key.
    """
    if change_rule is None:
        policy = POLICY.replace(
            "  ctrlrun.policy.change:\n    decision: approve\n    controls: [change-management]\n",
            "",
        )
    else:
        policy = POLICY.replace(
            "  ctrlrun.policy.change:\n    decision: approve",
            f"  ctrlrun.policy.change:\n{change_rule}",
        )
    control = _control(store, clock, policy=policy)

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), _Executor(), KEY)

    assert refused.value.reason == "policy_unapproved"
    assert POLICY_CHANGE_ACTION in str(refused.value)
    assert "decision: approve" in str(refused.value)


# --- T359, T360: the reserved name ------------------------------------------------------------


def test_T359_the_reserved_action_cannot_be_proposed_without_the_flows_marker(store, clock):
    """§8.2.1. The honest claim is not "nothing else can propose it": an application inside the
    process can call a private function, which §2.5.1 already concedes. It is that **nothing
    outside the flow proposes one by accident**, and the shipped surfaces are its only setters.
    """
    control = _control(store, clock, require=False)
    forged = Action(
        name=POLICY_CHANGE_ACTION,
        arguments={"from": None, "to": "sha256:whatever"},
        principal=AGENT,
        resource="policy",
        environment=control.environment,
    )

    with pytest.raises(InvalidArgument) as refused:
        control.execute(forged, _Executor(), "policy:sha256:whatever")

    assert "reserved for the policy-change flow" in str(refused.value)
    assert store.get_effect("policy:sha256:whatever") is None


def test_T359_the_marker_is_named_and_the_shipped_surfaces_are_its_only_setters():
    """What the marker is, asserted rather than described."""
    import pathlib

    from ctrlrun import control as control_module

    assert hasattr(control_module, "_POLICY_CHANGE_IN_FLIGHT")
    root = pathlib.Path(control_module.__file__).parent
    setters = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "_policy_change_in_flight()" in path.read_text(encoding="utf-8")
    )

    assert setters == ["control.py"], f"something outside the flow sets the marker: {setters}"


@pytest.mark.parametrize(
    "name", ["ctrlrun.policy.changes", "ctrlrun.policy.change.extra", "ctrlrun.policy"]
)
def test_T360_the_exemption_is_exactly_one_action_matched_by_name(store, clock, name):
    """§8.4. A prefix match would exempt every action somebody chose to name that way."""
    policy = POLICY.replace("  payments.refund:", f"  {name}:")
    control = _control(store, clock, policy=policy)

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control, name=name), _Executor(), KEY)

    assert refused.value.reason == "policy_unapproved"


# --- T361: the bootstrap ----------------------------------------------------------------------


def test_T361_the_first_proposal_carries_from_and_an_empty_store_is_never_an_approval(store, clock):
    """§8.4. An empty store is not an approval, and the first proposal is distinguishable."""
    control = _control(store, clock)
    assert store.get_effect(f"policy:{control._policy_hash}") is None

    with pytest.raises(ApprovalRequired) as pending:
        control._propose_policy(Policy.from_yaml(POLICY), authority=control.authority)

    record = store.get_approval(pending.value.request_id)
    assert record is not None
    assert record.request.action.arguments["from"] == control._policy_hash
    assert record.request.action.arguments["to"] == control._policy_hash


# --- T362: the keyed read, and both cache transitions ------------------------------------------


class _CountingStore(InMemoryStateStore):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.effect_reads = 0
        self.receipt_scans = 0

    def get_effect(self, key: str):
        self.effect_reads += 1
        return super().get_effect(key)

    def receipts(self):
        self.receipt_scans += 1
        return super().receipts()


def test_T362_the_enforcement_asks_get_effect_and_never_scans_receipts(clock):
    """§8.4. `receipts()` could only answer this by returning every receipt in the store,
    parsed, on the first decision of every process."""
    store = _CountingStore(clock=clock)
    control = _control(store, clock)
    store.receipt_scans = 0

    with pytest.raises(ActionDenied):
        control.execute(_action(control), _Executor(), KEY)

    assert store.effect_reads >= 1
    assert store.receipt_scans == 0, "the enforcement scanned every receipt in the store"


def test_T362_a_negative_answer_is_re_asked_and_a_positive_one_is_cached(clock):
    """**Both transitions, on one long-lived Control**, which is the point.

    A negative answer is not cached, so a process that started before the approval landed
    begins working the moment it lands, with no restart. A positive answer **is**, so deleting
    the effect row underneath a running process does not stop it -- which is §8.6's residual,
    asserted rather than assumed. A test driving only the first would leave §8.6's sentence
    unchecked in the direction that overclaims.
    """
    store = _CountingStore(clock=clock)
    control = _control(store, clock)

    with pytest.raises(ActionDenied):
        control.execute(_action(control), _Executor(), KEY)

    _approved(control, store, clock)
    # The same Control, never restarted, now decides.
    assert str(control.execute(_action(control), _Executor(), "refund:EU-43").result) == "committed"

    # And the row is deleted underneath it. §8.6: the refusal is not retroactive.
    store._effects.clear()
    assert str(control.execute(_action(control), _Executor(), "refund:EU-44").result) == "committed"
    # A Control built afterwards refuses, which is the other half of that sentence.
    fresh = _control(store, clock)
    with pytest.raises(ActionDenied):
        fresh.execute(_action(fresh), _Executor(), "refund:EU-45")


# --- T363, T364: the approval path applies ------------------------------------------------------


def test_T363_an_unentitled_approver_cannot_approve_a_policy_change(store, clock):
    """§8.3: §2, §3 and §4 apply, with no second path to keep correct."""
    bystander = Principal(agent="human:bob", user="bob@example.com", claims={"roles": ("viewer",)})
    control = _control(store, clock, approver=bystander)
    candidate = Policy.from_yaml(POLICY)
    with pytest.raises(ApprovalRequired) as pending:
        control._propose_policy(candidate, authority=control.authority)
    with _granting_principal(bystander, entitled=()):
        store.grant_approval(pending.value.request_id, "mcp-operator:bob")

    with pytest.raises(ApprovalMismatch) as refused:
        control._propose_policy(
            candidate, authority=control.authority, approval_id=pending.value.request_id
        )

    assert refused.value.reason == "approver_unentitled"


def test_T363_the_proposer_cannot_approve_their_own_change(store, clock):
    """§4.1 on the resolved principal, reached through the policy-change path."""
    control = _control(store, clock, approver=PROPOSER)
    candidate = Policy.from_yaml(POLICY)
    with pytest.raises(ApprovalRequired) as pending:
        control._propose_policy(candidate, authority=control.authority)
    with _granting_principal(PROPOSER, entitled=["change-management"]):
        store.grant_approval(pending.value.request_id, "cli:self")

    with pytest.raises(ApprovalMismatch) as refused:
        control._propose_policy(
            candidate, authority=control.authority, approval_id=pending.value.request_id
        )

    assert refused.value.reason == "approver_is_requester"


def test_T364_m_of_n_applies_to_a_policy_change(store, clock):
    """§8.3. The threshold is the policy's, and the policy-change action is an ordinary one."""
    policy = POLICY.replace(
        "  ctrlrun.policy.change:\n    decision: approve",
        "  ctrlrun.policy.change:\n    decision: approve\n    approvals_required: 2",
    )
    control = _control(store, clock, policy=policy)
    candidate = Policy.from_yaml(policy)
    with pytest.raises(ApprovalRequired) as pending:
        control._propose_policy(candidate, authority=control.authority)

    with _granting_principal(OWNER, entitled=["change-management"]):
        first = store.grant_approval(pending.value.request_id, "mcp-operator:owner")
    assert first is None, "one yes is not two"

    second = Principal(
        agent="human:second", user="two@example.com", claims={"roles": ("change-owner",)}
    )
    with _granting_principal(second, entitled=["change-management"]):
        assert store.grant_approval(pending.value.request_id, "mcp-operator:two") is not None

    receipt = control._propose_policy(
        candidate, authority=control.authority, approval_id=pending.value.request_id
    )
    assert str(receipt.result) == "committed"


# --- T365 to T368: the replay -------------------------------------------------------------------


def _with_history(store, clock):
    control = _control(store, clock, require=False)
    for payment in ("EU-1", "EU-2"):
        control.execute(
            Action(
                name="payments.refund",
                arguments={"amount": 100, "payment_id": payment},
                principal=AGENT,
                environment=control.environment,
            ),
            _Executor(),
            f"refund:{payment}",
        )
    return control


def test_T365_the_replay_reports_which_decisions_change(store, clock):
    control = _with_history(store, clock)
    stricter = Policy.from_yaml(
        POLICY.replace(
            "  payments.refund:\n    decision: allow", "  payments.refund:\n    decision: deny"
        )
    )

    rows = control._replay_policy(stricter, limit=100)

    assert rows, "two allowed actions became denied and the replay reported nothing"
    assert all(row["from"]["decision"] == "allow" for row in rows)
    assert all(row["to"]["decision"] == "deny" for row in rows)


def test_T365_a_policy_that_changes_nothing_reports_nothing(store, clock):
    """The control: a replay that reported every action would pass the test above."""
    control = _with_history(store, clock)

    assert control._replay_policy(Policy.from_yaml(POLICY), limit=100) == []


def test_T366_the_replay_writes_nothing(store, clock):
    """§8.5. Byte identical afterwards: receipts, events, approvals and effects."""
    control = _with_history(store, clock)
    before = (
        [r.receipt_id for r in store.receipts()],
        [e.event_id for e in store.events()],
        [e.effect_key for e in store.list_effects()],
    )

    control._replay_policy(
        Policy.from_yaml(POLICY.replace("decision: allow", "decision: deny")), limit=100
    )

    after = (
        [r.receipt_id for r in store.receipts()],
        [e.event_id for e in store.events()],
        [e.effect_key for e in store.list_effects()],
    )
    assert before == after


#: §8.5, asserted **by word**: this kernel does not grade an operator's document, and a replay
#: that scored one would be the same claim in a new costume.
VERDICT_WORDS = (
    "safe",
    "unsafe",
    "risky",
    "permissive",
    "secure",
    "insecure",
    "score",
    "grade",
    "percentage",
    "pass",
    "fail",
)


def test_T367_the_replays_output_carries_no_verdict_vocabulary(store, clock):
    control = _with_history(store, clock)

    rows = control._replay_policy(
        Policy.from_yaml(POLICY.replace("decision: allow", "decision: deny")), limit=100
    )
    rendered = repr(rows).lower()

    found = [word for word in VERDICT_WORDS if word in rendered]
    assert not found, f"the replay graded the operator's document: {found}"


def test_T368_a_receipt_the_replay_cannot_rebuild_is_named_and_skipped(store, clock):
    """§8.5, on `v0.6 §3.2`'s distinction: skipped is not the same as unchanged."""
    control = _with_history(store, clock)

    rows = control._replay_policy(Policy.from_yaml(POLICY), limit=0)

    assert rows == [], "limit=0 reads nothing and must report nothing"


# --- T369: a load failure is not an unapproved policy -------------------------------------------


def test_T369_a_policy_that_fails_to_load_is_a_load_failure(tmp_path):
    """The two reasons are distinct, in the exception and on the receipt."""
    from ctrlrun.errors import PolicyError

    path = tmp_path / "broken.yaml"
    path.write_text("schema: ctrlrun.policy/v6\nactions: [not, a, mapping]\n", encoding="utf-8")

    with pytest.raises(PolicyError) as refused:
        Policy.from_file(path)

    assert "policy_unapproved" not in str(refused.value)

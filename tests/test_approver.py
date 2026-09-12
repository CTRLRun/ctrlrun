"""T281 to T296: the approver is a principal (SPEC-v0.8 §2, §4.1).

Opt in, then fail closed. A `Control` with no `ApproverIdentity` is 0.7.0 exactly; one that
names an identity refuses an approval whose row carries no verified approver, wherever the
approval came from and whatever the store did with the column.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import (
    ApproverIdentity,
    _granting_principal,
)
from ctrlrun.control import Control, with_approval
from ctrlrun.errors import ActionDenied, ApprovalMismatch, ApprovalRequired, IdentityError
from ctrlrun.identity import IdentityContext, StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.receipt import RECEIPT_SCHEMA, EventType
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

POLICY = """
schema: ctrlrun.policy/v1
actions:
  payments.refund:
    decision: approve
  payments.read:
    decision: allow
"""

KEY = "refund:EU-42"

UNVERIFIED = "approver_unverified"
IS_REQUESTER = "approver_is_requester"

AGENT = Principal(agent="ops-agent", user="ada")


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _Recording:
    """An `IdentityProvider` that answers a fixed principal and keeps what it was asked."""

    def __init__(self, principal: Principal | None = None, raises: Exception | None = None) -> None:
        self.principal = principal
        self.raises = raises
        self.contexts: list[IdentityContext] = []

    def resolve(self, context: IdentityContext) -> Principal | None:
        self.contexts.append(context)
        if self.raises is not None:
            raise self.raises
        return self.principal


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return "done"


@pytest.fixture
def clock():
    return _Clock()


POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")


@pytest.fixture(
    params=[
        "in-memory",
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not POSTGRES_URL,
                reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against",
            ),
        ),
    ]
)
def store(request, clock, tmp_path):
    """Every shipped store, because the `approvers` column is written by each of them.

    The first draft of this file used the in-memory store alone, and the mutation table caught
    it: blanking the verified approver in the SQLite write path left every test green, because
    nothing here had ever executed that path. A store's column is not covered by a test of a
    different store (SPEC-v0.6 §2's argument for the conformance suite, applied to a test file).
    """
    if request.param == "in-memory":
        made = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"approver_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock, *, approver_identity=None, principal=AGENT):
    return Control(
        Policy.from_yaml(POLICY),
        store,
        clock=clock,
        approver_identity=approver_identity,
        identity=StaticIdentityProvider(agent=principal.agent, user=principal.user),
    )


def _action(control, name: str = "payments.refund", **arguments: Any) -> Action:
    return Action(
        name=name,
        arguments=arguments or {"amount": 100, "payment_id": "EU-42"},
        principal=AGENT,
        environment=control.environment,
    )


def _requested(control, action, key: str | None = KEY) -> str:
    with pytest.raises(ApprovalRequired) as pending:
        control.execute(action, _Executor(), key)
    return pending.value.request_id


def _present(control, action, request_id, executor=None, key: str | None = KEY):
    with with_approval(request_id):
        return control.execute(action, executor or _Executor(), key)


#: A verified approver, recorded the way a resolving surface records one (§2.5).
APPROVER = Principal(agent="human:bob", user="bob@example.com", issuer="https://issuer.example")


def _grant_verified(store, request_id, principal=APPROVER, approver="mcp-operator:bob"):
    with _granting_principal(principal):
        return store.grant_approval(request_id, approver)


# --- T281: an approval with no verified approver is refused ------------------------------------


def test_T281_an_approval_with_no_verified_approver_is_refused(store, clock):
    """THE test. The row was granted by a surface that cannot resolve, and it is not consumable."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    store.grant_approval(request_id, "cli:local")
    executor = _Executor()

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id, executor)

    assert refused.value.reason == UNVERIFIED
    assert executor.calls == 0
    record = store.get_approval(request_id)
    assert record is not None
    assert str(record.status) == "granted", "the human's yes is not spent on a refusal"
    assert record.approvers == ()
    assert store.get_effect(KEY) is None, "nothing was reserved"


# --- T282: the positive control, with no ApproverIdentity at all -------------------------------


def test_T282_with_no_approver_identity_the_whole_path_is_0_7_0(store, clock):
    """R1: a deployment that names no approver identity is unchanged, field for field."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    store.grant_approval(request_id, "cli:local")
    executor = _Executor()

    receipt = _present(control, action, request_id, executor)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"
    assert receipt.approver == "cli:local"
    assert receipt.approval_id == request_id
    assert receipt.approvers == ()
    assert store.get_approval(request_id).status == "consumed"


# --- T283: what a resolving surface records ----------------------------------------------------


def test_T283_a_resolving_surface_records_the_principal_and_no_claim_value(store, clock):
    """§2.5: agent, user and issuer reach the row; a claim value reaches nothing."""
    sentinel = "SENTINEL-CLAIM-VALUE"
    principal = Principal(
        agent="human:bob",
        user="bob@example.com",
        issuer="https://issuer.example",
        claims={"roles": sentinel},
    )
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(principal)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id, principal)

    receipt = _present(control, action, request_id)

    record = store.get_approval(request_id)
    assert [(who.agent, who.user, who.issuer) for who in record.approvers] == [
        ("human:bob", "bob@example.com", "https://issuer.example")
    ]
    written = json.dumps(
        [receipt.to_dict(), *[event.to_dict() for event in store.events()]], default=str
    )
    assert sentinel not in written
    assert sentinel not in json.dumps(
        [approver.__dict__ for approver in record.approvers], default=str
    )


# --- T284: the receipt ------------------------------------------------------------------------


def test_T284_the_receipt_carries_the_approvers_and_keeps_the_string(store, clock):
    """§2.5, §11.3: `ctrlrun.receipt/v5`, and `approver` still says what 0.7.0 said."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)

    receipt = _present(control, action, request_id)

    assert receipt.schema == "ctrlrun.receipt/v5"
    assert RECEIPT_SCHEMA == "ctrlrun.receipt/v5"
    assert receipt.approver == "mcp-operator:bob"
    assert [who.agent for who in receipt.approvers] == ["human:bob"]
    document = receipt.to_dict()
    assert document["approvers"] == [
        {
            "agent": "human:bob",
            "user": "bob@example.com",
            "issuer": "https://issuer.example",
            "entitled": [],
            "granted_at": document["approvers"][0]["granted_at"],
        }
    ]
    assert "authority_grant_id" in document, "the v5 shape is frozen before item 5 fills it"


# --- T285, T286: G18 ---------------------------------------------------------------------------


def test_T285_self_approval_is_refused_on_the_principal_not_the_string(store, clock):
    """G18: the strings differ and the principals are the same, which is the whole point."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(AGENT)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id, Principal(agent=AGENT.agent, user=AGENT.user))
    executor = _Executor()

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id, executor)

    assert refused.value.reason == IS_REQUESTER
    assert executor.calls == 0
    assert store.get_approval(request_id).status == "granted"


def test_T286_a_different_principal_approves_and_the_action_runs(store, clock):
    """G18's positive control: a guarantee that refused everything would pass T285 alone."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    executor = _Executor()

    receipt = _present(control, action, request_id, executor)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T287: a provider that raises, and one that declines ---------------------------------------


def test_T287_a_provider_that_raises_is_never_backfilled(store, clock):
    """`v0.3 §3.2` at this door: a refused credential propagates rather than falling back.

    The raise is the point. There is no `context()` on the approval door to fall back to, so a
    provider that rejects a credential must reach the surface that called it, which then grants
    nothing, which the consume-side check then refuses for having no verified approver.
    """
    identity = ApproverIdentity(_Recording(raises=IdentityError("rejected")))

    with pytest.raises(IdentityError):
        identity.resolve(IdentityContext(action="payments.refund", environment="production"))


def test_T287_a_declining_provider_produces_no_verified_approver(store, clock):
    """A decline has nothing to fall back to here: there is no `context()` on this door."""
    identity = ApproverIdentity(_Recording(None))

    assert identity.resolve(IdentityContext(action="payments.refund", environment="x")) is None


# --- T289: a static provider warns once --------------------------------------------------------


def test_T289_a_static_approver_identity_warns_once_and_does_not_refuse(caplog):
    """§2.3: a static provider answers with one name for every request.

    That is the deployment's choice to make and the record it produces is true, so this warns
    rather than refuses; what is not true is that such a record distinguishes anybody.
    """
    with caplog.at_level("WARNING"):
        identity = ApproverIdentity(StaticIdentityProvider(agent="human:one"))

    assert identity.provider is not None
    assert any("StaticIdentityProvider" in record.message for record in caplog.records)


# --- T290: the IdentityContext an approval resolution gets -------------------------------------


def test_T290_the_context_names_the_stored_request_and_asserts_nothing(store, clock):
    """§2.8: the action and environment of the action being approved, and no caller assertion."""
    provider = _Recording(APPROVER)
    identity = ApproverIdentity(provider)
    control = _control(store, clock, approver_identity=identity)
    action = _action(control)
    request_id = _requested(control, action)
    record = store.get_approval(request_id)

    identity.resolve(
        IdentityContext(
            action=record.request.action.name,
            environment=record.request.action.environment,
            headers={"authorization": "Bearer x"},
        )
    )

    context = provider.contexts[-1]
    assert context.action == "payments.refund"
    assert context.environment == control.environment
    assert context.agent is None and context.user is None


# --- T291: the early return is gone ------------------------------------------------------------


def test_T291_the_check_runs_with_no_precondition_provider_anywhere(store, clock):
    """§2.4: the 0.6-shaped path is where every deployment lives, and where the check must run.

    `_recheck` returned immediately when no provider was named and the record carried no
    fingerprint. A check added after that return is dead here, green, and invisible to a
    mutation table.
    """
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    store.grant_approval(request_id, "cli:local")

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id)

    assert refused.value.reason == UNVERIFIED


# --- T291b: the gate, all four rows ------------------------------------------------------------


def test_T291b_a_denied_approval_still_denies(store, clock):
    """§2.4.1 row 1: a human's no must not be reported as an approver problem."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    store.deny_approval(request_id, "cli:local")

    with pytest.raises(ActionDenied) as denied:
        _present(control, action, request_id)

    assert denied.value.reason == "approval_denied"
    types = [event.type for event in store.events() if event.approval_id == request_id]
    assert EventType.APPROVAL_DENIED in types
    receipts = [receipt for receipt in store.receipts() if receipt.action_id == action.action_id]
    assert str(receipts[-1].result) == "denied"


def test_T291b_a_consumed_approval_still_reports_consumed(store, clock):
    """§2.4.1 row 2: G2's replayed approval keeps its reason."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    _present(control, action, request_id)

    with pytest.raises(ApprovalMismatch) as replayed:
        _present(control, action, request_id, key="refund:EU-43")

    assert replayed.value.reason == "consumed"


def test_T291b_a_moved_action_hash_still_reports_mismatch(store, clock):
    """§2.4.1 row 3: G1 keeps its reason, which is `mismatch`."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    moved = _action(control, amount=999, payment_id="EU-42")

    with pytest.raises(ApprovalMismatch) as mismatched:
        _present(control, moved, request_id)

    assert mismatched.value.reason == "mismatch"


def test_T291b_a_lapsed_grant_still_expires_with_its_event_and_its_write(store, clock):
    """§2.4.1 row 4: the one a status-only gate fails: the lapse keeps its event and its write."""
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    clock.advance(timedelta(hours=48))

    with pytest.raises(ApprovalMismatch) as lapsed:
        _present(control, action, request_id)

    assert lapsed.value.reason == "expired"
    expired = [event for event in store.events() if event.type is EventType.APPROVAL_EXPIRED]
    assert len(expired) == 1
    assert str(store.get_approval(request_id).status) == "expired"


# --- T295: the upgrade case --------------------------------------------------------------------


def test_T295_an_approval_granted_before_the_provider_was_configured_is_refused(store, clock):
    """§2.9: R1 working, and the sentence the changelog owes an operator."""
    before = _control(store, clock)
    action = _action(before)
    request_id = _requested(before, action)
    before.store.grant_approval(request_id, "cli:local")

    after = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))

    with pytest.raises(ApprovalMismatch) as refused:
        _present(after, action, request_id)

    assert refused.value.reason == UNVERIFIED


# --- T296: observe mode ------------------------------------------------------------------------


OBSERVE_POLICY = POLICY.replace("ctrlrun.policy/v1", "ctrlrun.policy/v3") + "mode: observe\n"


def test_T296_observe_mode_records_the_approver_reason_and_runs(store, clock):
    """§4.1: and the deliberate consequence: a mismatch records its own reason, not a constant."""
    control = Control(
        Policy.from_yaml(OBSERVE_POLICY),
        store,
        clock=clock,
        approver_identity=ApproverIdentity(_Recording(AGENT)),
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )
    action = _action(control)
    executor = _Executor()

    receipt = control.execute(action, executor, KEY)

    assert executor.calls == 1, "observe mode runs the action"
    assert str(receipt.result) == "observed"


# --- T292, T293, T294 live beside the machinery they exercise ----------------------------------


def test_T292_the_migration_keeps_every_row_and_adds_the_column(tmp_path, clock):
    """§11.1's `0006_verified_approver`, forward-only, on a database with rows in it."""
    path = tmp_path / "state.db"
    first = SQLiteStateStore(path, clock=clock)
    control = _control(first, clock)
    action = _action(control)
    request_id = _requested(control, action)
    first.store_approver = None
    first.grant_approval(request_id, "cli:local")
    first.close()

    reopened = SQLiteStateStore(path, clock=clock)
    try:
        record = reopened.get_approval(request_id)
        assert record is not None
        assert record.approver == "cli:local"
        assert record.approvers == ()
    finally:
        reopened.close()


def test_T293_a_v4_receipt_still_reads_and_a_v5_chain_verifies(store, clock):
    """`v0.7 §6.11`: a receipt renders under its own schema, and the chain spans both."""
    from ctrlrun.receipt import Receipt

    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    _present(control, action, request_id)

    written = [receipt for receipt in store.receipts() if receipt.action_id == action.action_id]
    document = written[-1].to_dict()
    assert document["schema"] == "ctrlrun.receipt/v5"

    older = dict(document, schema="ctrlrun.receipt/v4")
    older.pop("approvers")
    older.pop("authority_grant_id")
    parsed = Receipt.from_dict(older)
    assert parsed.schema == "ctrlrun.receipt/v4"
    assert parsed.approvers == ()
    assert "approvers" not in parsed.to_dict()


def test_T294_the_catalogue_is_v4_and_carries_G18():
    """§11.4: the version moves once, here, and G18 lands with it."""
    from ctrlrun.verify.guarantees import CATALOGUE, GUARANTEES

    assert CATALOGUE == "ctrlrun.guarantees/v4"
    identifiers = [guarantee.id for guarantee in GUARANTEES]
    assert "G18" in identifiers
    assert identifiers == sorted(identifiers, key=lambda name: int(name[1:]))

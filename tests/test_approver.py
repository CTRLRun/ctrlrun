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


def test_T290_the_approver_identity_is_a_switch_and_a_delegator_today(store, clock):
    """§2.8.1: what item 2 actually wires, asserted instead of a context the test wrote itself.

    The first version of this built an `IdentityContext`, handed it to `identity.resolve`, and
    asserted the fields it had just written: a tautology an independent review caught. Nothing
    in the shipped tree calls `ApproverIdentity.resolve` yet. What item 2 wires is the switch,
    and what §2.8's contract binds is a surface that resolves through it, which item 3 adds.
    """
    provider = _Recording(APPROVER)
    identity = ApproverIdentity(provider)
    control = _control(store, clock, approver_identity=identity)

    assert control.approver_identity is identity, "the switch is readable, per §11.1"
    assert provider.contexts == [], (
        "nothing in item 2 resolves through ApproverIdentity; §2.8.1 says so, and item 3 is "
        "where that changes"
    )

    # And the delegation itself carries a provider's answer through without touching it.
    given = IdentityContext(action="payments.refund", environment=control.environment)
    assert identity.resolve(given) is APPROVER
    assert provider.contexts == [given], "the context reaches the provider unaltered"


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


def test_T291b_a_lapsed_grant_whose_approver_is_fine_still_expires(store, clock):
    """§2.4.1 row 4: the lapse keeps its reason, its event and the store's own write.

    This is the row that makes the gate more than a status test, and it holds because the
    approver check **passes** here: a lapsed grant with a good approver falls through to
    `_take`, which is where `v0.1 §4.2 A3` says the expiry decision and its write belong. The
    row where the approver check would refuse is the test below.
    """
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


def test_T291b_a_lapsed_grant_with_a_bad_approver_reports_the_approver(store, clock):
    """§2.4.1's fifth row, which is what checking the lapsed row costs.

    A grant that is **both** lapsed and refused on approver grounds reports the approver reason,
    so that row keeps no `APPROVAL_EXPIRED` event and no lapse write. The grant is unusable
    either way and `check_consumable` refuses it at every later presentation; what is bought is
    that the lapsed row cannot be a way past §2.7 on a host whose clock runs ahead of the
    store's, which is T291c.
    """
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    store.grant_approval(request_id, "cli:local")
    clock.advance(timedelta(hours=48))

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id)

    assert refused.value.reason == UNVERIFIED
    assert [event.type for event in store.events()].count(EventType.APPROVAL_EXPIRED) == 0
    assert str(store.get_approval(request_id).status) == "granted"


def test_T291b_a_pending_or_unknown_approval_keeps_its_own_reason(store, clock):
    """§2.4.1's fourth row, which had no test until an independent review said so.

    `pending` is the reason §2.7's fifth row and all of item 4's M-of-N depend on, so a gate
    narrowed to exclude it would go unnoticed and take M-of-N's refusal with it.
    """
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)

    with pytest.raises(ApprovalMismatch) as pending:
        _present(control, action, request_id)
    assert pending.value.reason == "pending"

    with pytest.raises(ApprovalMismatch) as unknown:
        _present(control, action, "apr_" + "0" * 32)
    assert unknown.value.reason == "unknown"


# --- T291c: the skew that turned the gate into a skip ------------------------------------------


def test_T291c_a_clock_ahead_of_the_stores_does_not_skip_the_approver_check(store, clock):
    """§2.4.1: the lapsed row is a deferral, not a skip, and this is why.

    An independent review broke the first version of the gate here. It stood aside whenever
    *this* clock called the grant lapsed, because refusing on approver grounds would cost the
    lapse its `APPROVAL_EXPIRED` event and the store's own lapse write. But a skip is permanent
    and the store keeps its own clock: with this host running ahead, the gate stood aside, the
    store consumed the grant happily, and the action ran **with no approver check at all**. The
    reproduction was a self-approval committing under a twenty-minute skew.

    Here the store's clock stays still and the `Control`'s runs ahead of the expiry, which is
    the same disagreement from the other side, and the approval is the requester's own.
    """
    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(AGENT)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id, Principal(agent=AGENT.agent, user=AGENT.user))

    ahead = _Clock()
    ahead.now = clock.now + timedelta(hours=48)
    skewed = Control(
        Policy.from_yaml(POLICY),
        store,
        clock=ahead,
        approver_identity=ApproverIdentity(_Recording(AGENT)),
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )
    executor = _Executor()

    with pytest.raises(ApprovalMismatch) as refused, with_approval(request_id):
        skewed.execute(action, executor, KEY)

    assert refused.value.reason == IS_REQUESTER, (
        "the gate skipped the approver check because this clock called the grant lapsed, and "
        "the store then consumed it: a self-approval ran"
    )
    assert executor.calls == 0
    # **And the refusal costs nothing**, which the first fix for this could not say: it deferred
    # the check past `_take`, so the grant was consumed and the effect key left RESERVED with a
    # live lease and nothing to release it, which lapses into an `AMBIGUOUS` record a human must
    # resolve for an action the kernel itself refused. The review found that too.
    record = store.get_approval(request_id)
    assert str(record.status) == "granted", "the human's yes was spent on a refusal"
    assert store.get_effect(KEY) is None, "a refused action left a reservation behind"


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


def _observing(store, clock, identity):
    return Control(
        Policy.from_yaml(OBSERVE_POLICY),
        store,
        clock=clock,
        approver_identity=identity,
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )


def test_T296_observe_mode_records_the_approver_reason_and_runs(store, clock):
    """§4.1: what enforce mode would have done, recorded, with the action still running.

    **An approval is presented here, which the first version of this test did not do.** Without
    one, `_observe_take` never reaches an approval at all, so the test asserted only that
    observe mode runs the action, which is true of every observe-mode run and of a deployment
    where this row is unimplemented. An independent review found it: a negative test against
    behaviour the setup already prevented, in a window that was never opened.
    """
    identity = ApproverIdentity(_Recording(AGENT))
    enforcing = _control(store, clock, approver_identity=identity)
    action = _action(enforcing)
    request_id = _requested(enforcing, action)
    _grant_verified(store, request_id, Principal(agent=AGENT.agent, user=AGENT.user))
    executor = _Executor()

    with with_approval(request_id):
        receipt = _observing(store, clock, identity).execute(action, executor, KEY)

    assert executor.calls == 1, "observe mode runs the action whatever it found"
    assert str(receipt.result) == "observed"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == IS_REQUESTER
    assert str(store.get_approval(request_id).status) == "granted", "no grant is spent"


def test_T296_every_other_mismatch_now_records_its_own_reason(store, clock):
    """The deliberate consequence §4.1 names, asserted rather than left to a reader.

    Observe mode recorded the constant `approval_mismatch` for every `ApprovalMismatch`, so a
    moved precondition and an approver who may not answer were one word in a report. Recording
    the specific reason for the approver refusals alone would leave a vocabulary nobody can
    explain, so every mismatch records its own reason now. Here: a hash that moved, which is
    `G1`'s case and has nothing to do with v0.8.
    """
    enforcing = _control(store, clock)
    action = _action(enforcing)
    request_id = _requested(enforcing, action)
    store.grant_approval(request_id, "cli:local")
    moved = _action(enforcing, amount=999, payment_id="EU-42")
    executor = _Executor()

    with with_approval(request_id):
        receipt = _observing(store, clock, None).execute(moved, executor, "refund:EU-99")

    assert executor.calls == 1
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "mismatch", (
        "the constant `approval_mismatch` is what this recorded before, for every mismatch"
    )


# --- T296b: the report still adds up -----------------------------------------------------------


def test_T296b_an_observed_approval_refusal_is_still_counted_by_stats(store, clock):
    """§4.1's vocabulary change, against the bucket `ctrlrun stats` counts on (`v0.3 §6.4`).

    An independent review measured what this change did before the bucket was widened: an
    observe-mode approval refusal landed in no bucket at all, so `would_have_been_blocked` went
    from 1 to 0 and the command whose whole job is "what changes if you turn enforcement on"
    silently under-reported it. `receipt.py` already said why that must not happen: a bucketed
    count over a string nobody constrained is a report that quietly stops adding up.

    Driven with **no `ApproverIdentity` anywhere**, because the receipt that stopped counting was
    a plain hash mismatch with nothing to do with v0.8.
    """
    from ctrlrun.reporting import stats_document

    enforcing = _control(store, clock)
    action = _action(enforcing)
    request_id = _requested(enforcing, action)
    store.grant_approval(request_id, "cli:local")
    moved = _action(enforcing, amount=999, payment_id="EU-42")

    with with_approval(request_id):
        _observing(store, clock, None).execute(moved, _Executor(), "refund:EU-99")

    document = stats_document(list(store.receipts()), mode="observe", boundary=None)

    assert document["would_have_been_blocked"] == 1
    assert document["blocked_by_reason"] == {"mismatch": 1}


def test_T296b_an_observed_denial_by_a_human_is_counted_too(store, clock):
    """The half of the bucket that was wrong before v0.8 touched it (§4.1, `v0.3 §6.4`).

    `check_consumable` catches a denied record one branch before the generic status branch and
    raises `ActionDenied(reason="approval_denied")`, so the set carried `"denied"`, which nothing
    on this path can produce, and missed `"approval_denied"`, which observe mode records. An
    observe-mode run where a **human said no** was counted nowhere. An independent review found
    it while checking the set this item introduced.
    """
    from ctrlrun.reporting import stats_document

    enforcing = _control(store, clock)
    action = _action(enforcing)
    request_id = _requested(enforcing, action)
    store.deny_approval(request_id, "cli:local")

    with with_approval(request_id):
        _observing(store, clock, None).execute(action, _Executor(), KEY)

    document = stats_document(list(store.receipts()), mode="observe", boundary=None)

    assert document["would_have_been_blocked"] == 1
    assert document["blocked_by_reason"] == {"approval_denied": 1}


def test_T283b_a_corrupted_entitled_column_is_refused_and_not_exploded(store, clock):
    """`str` is a `Sequence`, so a corrupted row became three control ids rather than a refusal.

    `tuple("abc")` is `("a", "b", "c")`. A column holding a bare string was therefore accepted,
    silently, as an approver entitled for three controls that do not exist, and
    `_approvers_from_json`'s promise to raise on a corrupted row was false for exactly the shape
    a corruption most easily takes. CodeRabbit found it; §3.4 states the same hazard for the
    roles claim, which is where it was expected and not where it landed.
    """
    from ctrlrun.approval import VerifiedApprover
    from ctrlrun.errors import CTRLRunError

    for bad in ("abc", 5, [1], [""]):
        with pytest.raises(CTRLRunError):
            VerifiedApprover(
                agent="human:bob",
                user=None,
                issuer=None,
                granted_at=datetime.now(UTC),
                entitled=bad,
            )

    fine = VerifiedApprover(
        agent="human:bob",
        user=None,
        issuer=None,
        granted_at=datetime.now(UTC),
        entitled=["card-data-handling"],
    )
    assert fine.entitled == ("card-data-handling",)


# --- T296c: the resumed leg is the only receipt some actions get ------------------------------


def test_T296c_a_resumed_leg_carries_the_approvers_onto_its_receipt(store, clock):
    """§2.5 and `SPEC-mcp-operator.md` §8.3, which is why this is not a nicety.

    A suspended action writes no receipt on the leg that suspended, so the resumed leg's is the
    **only** receipt an MCP multi round-trip or an ACS action ever gets. `_resumed_context`
    recovers the precondition fingerprints from the record and recovered nothing about who
    approved, so "carried onto the receipt" was false for exactly those actions. An independent
    review found it.
    """
    from ctrlrun.errors import Suspended

    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)

    def suspends():
        raise Suspended("continuation-EU-42")

    with pytest.raises(Suspended), with_approval(request_id):
        control.execute(action, suspends, KEY)

    receipt = control.resume("continuation-EU-42", lambda: "refunded")

    assert str(receipt.result) == "committed"
    assert receipt.approver == "mcp-operator:bob"
    assert [who.agent for who in receipt.approvers] == ["human:bob"], (
        "the resumed leg is the only receipt this action gets, and it says who approved"
    )


# --- T292, T293, T294 live beside the machinery they exercise ----------------------------------


def test_T292_the_migration_is_at_head_and_the_column_round_trips(tmp_path, clock):
    """`0006_verified_approver`, and what this test does **not** claim to cover.

    An independent review found the first version of this asserting nothing: it opened a store
    with *this* binary, which applies `0006` at creation, reopened it, and called the survival
    of a row evidence of a migration. No pre-`0006` database was ever built.

    The real upgrade coverage is `test_preconditions.py::test_T264`, which builds a database
    with 0.6.1's own code and migrates it to `HEAD` with every row intact, on both backends.
    What is left here is the half that belongs beside item 2: the column round-trips through a
    real file-backed store, and the migration ledger says HEAD.
    """
    from ctrlrun.migrations import HEAD

    path = tmp_path / "state.db"
    first = SQLiteStateStore(path, clock=clock)
    control = _control(first, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(first, request_id)
    first.close()

    reopened = SQLiteStateStore(path, clock=clock)
    try:
        record = reopened.get_approval(request_id)
        assert record is not None
        assert record.approver == "mcp-operator:bob"
        assert [who.agent for who in record.approvers] == ["human:bob"]
        applied = [
            row[0]
            for row in reopened._connection().execute(
                "SELECT migration_id FROM schema_version ORDER BY 1"
            )
        ]
        assert applied[-1] == HEAD
        assert "0006_verified_approver" in applied
    finally:
        reopened.close()


def test_T293_a_chain_spanning_two_receipt_schemas_verifies(store, clock):
    """`v0.7 §6.11`: a receipt renders under its own schema, and the chain spans both.

    The first version round-tripped one relabelled document and never called `verify_chain`,
    which an independent review called what it was. The cross-version chain over a *stored* v3
    and a continued v5 is `test_preconditions.py::test_T265`; this asserts the half item 2 adds,
    that a v5 receipt's two new keys are read only from a v5 document and that a chain carrying
    one verifies end to end.
    """
    from ctrlrun.receipt import Receipt, verify_chain

    control = _control(store, clock, approver_identity=ApproverIdentity(_Recording(APPROVER)))
    action = _action(control)
    request_id = _requested(control, action)
    _grant_verified(store, request_id)
    _present(control, action, request_id)

    report = verify_chain(store)
    assert report.ok, report.breaks
    assert report.verified == len(store.receipts())

    written = [receipt for receipt in store.receipts() if receipt.action_id == action.action_id]
    document = written[-1].to_dict()
    assert document["schema"] == "ctrlrun.receipt/v5"
    assert document["approvers"], "a v5 document carries what a v5 writer wrote"

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

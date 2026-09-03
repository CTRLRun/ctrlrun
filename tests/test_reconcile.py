"""The reconciliation hook. Build-list item 1; SPEC-v0.2 §2.

`AMBIGUOUS` is the state only a human could leave in v0.1. v0.2 admits one other authority: a
`reconcile` hook that asks the remote what happened, and moves the record only where its
answer points. Everything about it is arranged so that a hook which cannot answer changes
nothing — an exception, a nonsense return value and a hook that is never called all mean
`"unknown"`, and `"unknown"` is the value with no effect.
"""

from datetime import timedelta

import pytest

from ctrlrun import (
    AmbiguousEffect,
    Control,
    DuplicateEffect,
    InvalidArgument,
    NotExecuted,
    Policy,
    context,
    protect,
)
from ctrlrun.effect import COMMITTED_EFFECT, EffectState
from ctrlrun.receipt import EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  stripe.refund:
    decision: allow
"""


class Remote:
    """A fake remote that commits and then loses the response, as in T1."""

    def __init__(self) -> None:
        self.calls = 0

    def refund(self, payment_id: str, amount: int) -> str:
        self.calls += 1
        raise TimeoutError("response lost")

    def working_refund(self, payment_id: str, amount: int) -> str:
        self.calls += 1
        return f"re_{payment_id}"


@pytest.fixture
def control(state_store, fake_clock):
    return Control(Policy.from_yaml(POLICY), state_store, clock=fake_clock)


def _strand(control, remote):
    """Leave `refund:txn_1` AMBIGUOUS, the way a lost response does (SPEC-v0.1 §5.5)."""

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)


def _events(store, type_):
    return [event for event in store.events() if event.type is type_]


# --- T13: reconciliation unblocks a retry -----------------------------------------------


def test_T13_a_hook_answering_not_executed_moves_the_record_to_failed(control, state_store):
    remote = Remote()
    _strand(control, remote)
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: "not_executed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=2000) == "re_txn_1"

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.COMMITTED
    assert record.attempt == 2
    assert remote.calls == 2


def test_T13_the_reconciliation_is_recorded_as_events(control, state_store):
    remote = Remote()
    _strand(control, remote)

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: "not_executed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)

    started = _events(state_store, EventType.RECONCILIATION_STARTED)
    resolved = _events(state_store, EventType.RECONCILIATION_RESOLVED)
    assert len(started) == 1
    assert started[0].data["effect_key"] == "refund:txn_1"
    assert started[0].data["trigger"] == "blocking"
    assert len(resolved) == 1
    assert resolved[0].data["outcome"] == "not_executed"

    effect_resolved = _events(state_store, EventType.EFFECT_RESOLVED)
    assert len(effect_resolved) == 1
    assert effect_resolved[0].data["resolved_by"] == "reconcile"


def test_T13_the_hook_is_given_the_effect_key_and_nothing_else(control):
    remote = Remote()
    _strand(control, remote)
    seen: list[str] = []

    def reconcile(effect_key: str) -> str:
        seen.append(effect_key)
        return "not_executed"

    @protect(
        "stripe.refund", effect="refund:{payment_id}", control=control, reconcile=reconcile
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)

    assert seen == ["refund:txn_1"]


# --- T14: reconciliation answering committed refuses the retry --------------------------


def test_T14_a_hook_answering_committed_refuses_the_retry_as_a_duplicate(control, state_store):
    remote = Remote()
    _strand(control, remote)

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: "committed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(DuplicateEffect) as raised:
        refund(payment_id="txn_1", amount=2000)

    assert raised.value.state == COMMITTED_EFFECT
    assert state_store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert remote.calls == 1


def test_T14_the_refused_retry_writes_a_blocked_receipt(control, state_store):
    remote = Remote()
    _strand(control, remote)

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: "committed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(DuplicateEffect):
        refund(payment_id="txn_1", amount=2000)

    receipts = state_store.receipts()
    assert receipts[-1].result is ReceiptResult.BLOCKED


# --- T15: a hook that fails changes nothing, and runs once ------------------------------


@pytest.mark.parametrize(
    ("hook", "reason"),
    [
        (lambda effect_key: (_ for _ in ()).throw(RuntimeError("remote unreachable")), "raised"),
        (lambda effect_key: "maybe", "invalid_return"),
        (lambda effect_key: None, "invalid_return"),
        (lambda effect_key: "unknown", None),
    ],
)
def test_T15_a_hook_that_cannot_answer_leaves_the_record_ambiguous(
    control, state_store, hook, reason
):
    remote = Remote()
    _strand(control, remote)

    @protect("stripe.refund", effect="refund:{payment_id}", control=control, reconcile=hook)
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund(payment_id="txn_1", amount=2000)

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert remote.calls == 1

    resolved = _events(state_store, EventType.RECONCILIATION_RESOLVED)
    assert len(resolved) == 1
    assert resolved[0].data["outcome"] == "unknown"
    if reason is None:
        assert "reason" not in resolved[0].data
    else:
        assert resolved[0].data["reason"] == reason
    assert not _events(state_store, EventType.EFFECT_RESOLVED)


def test_T15_the_hook_is_called_at_most_once_per_attempt(control, state_store):
    """Both trigger points apply to one attempt; only the first may fire (SPEC-v0.2 §2.3).

    A blocking reconcile answers `not_executed`, which unblocks the reservation; the attempt
    then runs and ends AMBIGUOUS, which is where eager reconciliation would fire again.
    """
    remote = Remote()
    _strand(control, remote)
    calls: list[str] = []

    def reconcile(effect_key: str) -> str:
        calls.append(effect_key)
        return "not_executed"

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=reconcile,
        reconcile_eagerly=True,
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)

    assert len(calls) == 1
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T15_eager_reconciliation_resolves_an_ambiguous_outcome(control, state_store):
    remote = Remote()

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: "not_executed",
        reconcile_eagerly=True,
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)

    assert state_store.get_effect("refund:txn_1").state is EffectState.FAILED
    started = _events(state_store, EventType.RECONCILIATION_STARTED)
    assert [event.data["trigger"] for event in started] == ["eager"]
    # The receipt records what the attempt did, not what reconciliation later learned:
    # `ctrlrun resolve` does not rewrite a receipt either (SPEC-v0.2 §2.4).
    assert state_store.receipts()[-1].result is ReceiptResult.AMBIGUOUS


def test_T15_eager_reconciliation_is_off_by_default(control, state_store):
    remote = Remote()
    calls: list[str] = []

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "not_executed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T15_eager_reconciliation_never_runs_while_an_interrupt_unwinds(control, state_store):
    """A KeyboardInterrupt mid-request must not be delayed by a network call (§2.3).

    SPEC-v0.1 §5.5 already refuses to let tidying up swallow an interrupt. Calling arbitrary
    user code while one unwinds is the same mistake with a longer stack.
    """
    calls: list[str] = []

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "not_executed",
        reconcile_eagerly=True,
    )
    def refund(payment_id: str, amount: int) -> str:
        raise KeyboardInterrupt

    with context(agent="refund-agent"), pytest.raises(KeyboardInterrupt):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T15_eager_reconciliation_skips_a_record_this_attempt_could_not_mark(
    control, state_store
):
    """A record whose outcome this attempt could not write is not this attempt's to resolve.

    The window is opened deterministically: the executor moves the record to a terminal state
    mid-flight, the way a human running `ctrlrun resolve` during a long call would, and then
    raises. `mark_ambiguous` is refused, so there is nothing here to reconcile.
    """
    calls: list[str] = []

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "not_executed",
        reconcile_eagerly=True,
    )
    def refund(payment_id: str, amount: int) -> str:
        record = state_store.get_effect("refund:txn_1")
        state_store.mark_ambiguous("refund:txn_1", record.action_id, "lost")
        state_store.resolve_effect("refund:txn_1", EffectState.COMMITTED, "cli:local")
        raise TimeoutError("response lost")

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []
    assert state_store.get_effect("refund:txn_1").state is EffectState.COMMITTED


def test_T15_a_base_exception_from_the_hook_propagates(control, state_store):
    remote = Remote()
    _strand(control, remote)

    def reconcile(effect_key: str) -> str:
        raise KeyboardInterrupt

    @protect(
        "stripe.refund", effect="refund:{payment_id}", control=control, reconcile=reconcile
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(KeyboardInterrupt):
        refund(payment_id="txn_1", amount=2000)

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert remote.calls == 1


def test_T15_a_hook_is_never_called_for_a_record_that_is_not_ambiguous(control, state_store):
    """Only AMBIGUOUS is reconcilable. A committed key is refused without asking anyone."""
    remote = Remote()
    calls: list[str] = []

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "not_executed",
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)
    with context(agent="refund-agent"), pytest.raises(DuplicateEffect):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []
    assert remote.calls == 1


def test_T15_a_hook_on_an_action_with_no_effect_key_warns_and_is_never_called(
    control, caplog
):
    """Not a decoration-time error: the template may come from policy (SPEC-v0.2 §2.1)."""
    calls: list[str] = []

    @protect(
        "customer.read",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "unknown",
    )
    def read(customer_id: str) -> str:
        return "ok"

    with caplog.at_level("WARNING", logger="ctrlrun"), context(agent="support-agent"):
        read(customer_id="cus_1")
        read(customer_id="cus_2")

    assert calls == []
    warnings = [record for record in caplog.records if "reconcile" in record.getMessage()]
    assert len(warnings) == 1


def test_T15_reconcile_eagerly_without_a_hook_is_rejected_at_decoration_time(control):
    with pytest.raises(InvalidArgument):

        @protect(
            "stripe.refund",
            effect="refund:{payment_id}",
            control=control,
            reconcile_eagerly=True,
        )
        def refund(payment_id: str, amount: int) -> str:
            return "re_1"


def test_T15_a_failed_record_is_retried_without_asking_the_hook(control, state_store):
    """FAILED already permits a retry (SPEC-v0.1 §5.4); there is nothing to reconcile."""
    remote = Remote()
    calls: list[str] = []
    attempts = {"n": 0}

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        reconcile=lambda effect_key: calls.append(effect_key) or "unknown",
    )
    def refund(payment_id: str, amount: int) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NotExecuted("rejected before any side effect")
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(NotExecuted):
        refund(payment_id="txn_1", amount=2000)
    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []
    assert state_store.get_effect("refund:txn_1").attempt == 2


def test_T15_an_expired_lease_is_made_ambiguous_before_it_is_reconciled(
    control, state_store, fake_clock
):
    """Order matters: reconciliation asks about a record, in the state that describes it."""
    remote = Remote()
    seen: list[str] = []

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        lease=timedelta(minutes=5),
    )
    def slow_refund(payment_id: str, amount: int) -> str:
        return "never returns"

    control.store.reserve_effect("refund:txn_9", "act_stranded", timedelta(minutes=5))
    control.store.begin_execution("refund:txn_9", "act_stranded")
    fake_clock.advance(timedelta(minutes=6))

    def reconcile(effect_key: str) -> str:
        seen.append(str(state_store.get_effect(effect_key).state))
        return "unknown"

    @protect(
        "stripe.refund", effect="refund:{payment_id}", control=control, reconcile=reconcile
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote.working_refund(payment_id, amount)

    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund(payment_id="txn_9", amount=2000)

    assert seen == [str(EffectState.AMBIGUOUS)]

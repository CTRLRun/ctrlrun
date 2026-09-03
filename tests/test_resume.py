"""Suspension and resumption. Build-list item 6d; SPEC-v0.2 §6.9 via the kernel.

Suspension is modelled the way v0.1 models "nothing happened": an explicit opt-in signal an
executor raises, never a default and never inferred. `Suspended` says the remote asked for
something before it will finish — so there is no outcome to record, the reservation stays
exactly where it is, and the caller gets the signal back to relay.

`Control.resume` is the other half, and it runs the executor through the *same* outcome
mapping `execute` uses. There is one implementation of v0.1 §5.5 in this codebase and this
item does not add a second.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

import pytest

from ctrlrun import (
    AmbiguousEffect,
    Control,
    DuplicateEffect,
    EffectState,
    InvalidArgument,
    NotExecuted,
    Policy,
    Suspended,
    context,
    protect,
)
from ctrlrun.receipt import EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
  customer.read:
    decision: allow
"""

CONTINUATION = "opaque-state-from-the-server"


@pytest.fixture
def control(state_store, fake_clock):
    return Control(Policy.from_yaml(POLICY), state_store, clock=fake_clock)


def _suspending(control, continuation=CONTINUATION, effect="refund:{payment_id}"):
    @protect("stripe.refund", effect=effect, control=control)
    def refund(payment_id: str, amount: int) -> str:
        raise Suspended(continuation)

    return refund


def _types(store):
    return [event.type for event in store.events()]


def _suspend(control, payment_id="txn_1"):
    refund = _suspending(control)
    with context(agent="refund-agent"), pytest.raises(Suspended):
        refund(payment_id=payment_id, amount=200)


# --- suspension holds the record, and records nothing else -----------------------------


def test_a_suspension_leaves_the_record_executing(control, state_store):
    _suspend(control)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.EXECUTING


def test_a_suspension_writes_no_receipt(control, state_store):
    """The same rule as an action awaiting a human: it has not reached a terminal state."""
    _suspend(control)

    assert state_store.receipts() == ()


def test_a_suspension_appends_execution_suspended(control, state_store):
    _suspend(control)

    suspended = [e for e in state_store.events() if e.type is EventType.EXECUTION_SUSPENDED]
    assert len(suspended) == 1
    assert suspended[0].effect_key == "refund:txn_1"
    assert suspended[0].data["round"] == 1
    assert suspended[0].data["held"] is True


def test_the_lease_is_extended_by_the_suspend_timeout(state_store, fake_clock):
    control = Control(
        Policy.from_yaml(POLICY),
        state_store,
        clock=fake_clock,
        lease=timedelta(minutes=1),
        suspend_timeout=timedelta(minutes=30),
    )
    _suspend(control)

    record = state_store.get_effect("refund:txn_1")
    assert record.lease_expires_at == fake_clock.now + timedelta(minutes=30)


def test_the_signal_propagates_to_the_caller(control):
    """The gateway needs it back to relay the elicitation to the client."""
    refund = _suspending(control)

    with context(agent="refund-agent"), pytest.raises(Suspended) as raised:
        refund(payment_id="txn_1", amount=200)

    assert raised.value.continuation == CONTINUATION


def test_a_concurrent_duplicate_is_refused_while_suspended(control, state_store):
    """The guarantee that actually matters across a round trip: the key is still held."""
    _suspend(control)
    other = _suspending(control, continuation="another")

    with context(agent="refund-agent-b"), pytest.raises(DuplicateEffect) as refused:
        other(payment_id="txn_1", amount=200)

    assert refused.value.state == "in_progress"


# --- resumption runs the same outcome mapping ------------------------------------------


def test_resume_commits_and_writes_one_receipt(control, state_store):
    _suspend(control)

    receipt = control.resume(CONTINUATION, lambda: "re_1")

    assert receipt.result is ReceiptResult.COMMITTED
    assert state_store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert len(state_store.receipts()) == 1
    assert receipt.attempt == 1


def test_resume_keeps_the_original_action_id_and_hash(control, state_store):
    """§6.9.2 — the continuation is the same action: same id, same hash, one receipt."""
    _suspend(control)
    proposed = state_store.events()[0]

    receipt = control.resume(CONTINUATION, lambda: "re_1")

    assert receipt.action_id == proposed.action_id
    assert receipt.action_hash == proposed.data["action_hash"]


def test_resume_appends_execution_resumed_after_suspended(control, state_store):
    _suspend(control)
    control.resume(CONTINUATION, lambda: "re_1")

    types = _types(state_store)
    assert types.index(EventType.EXECUTION_SUSPENDED) < types.index(EventType.EXECUTION_RESUMED)


@pytest.mark.parametrize(
    ("executor", "state", "result"),
    [
        (lambda: "re_1", EffectState.COMMITTED, ReceiptResult.COMMITTED),
        (
            lambda: (_ for _ in ()).throw(NotExecuted("rejected before any side effect")),
            EffectState.FAILED,
            ReceiptResult.FAILED,
        ),
        (
            lambda: (_ for _ in ()).throw(TimeoutError("response lost")),
            EffectState.AMBIGUOUS,
            ReceiptResult.AMBIGUOUS,
        ),
    ],
)
def test_resume_maps_outcomes_exactly_as_execute_does(
    control, state_store, executor, state, result
):
    """The same cases `execute` is held to, run through `resume` (v0.1 §5.5).

    Parametrized against the same three outcomes deliberately: if these two paths ever
    disagree, the asymmetry this library exists for has two implementations and one of them
    is wrong.
    """
    _suspend(control)

    with contextlib.suppress(NotExecuted, TimeoutError):
        control.resume(CONTINUATION, executor)

    assert state_store.get_effect("refund:txn_1").state is state
    assert state_store.receipts()[-1].result is result


def test_a_failed_resume_permits_a_retry(control, state_store):
    """`NotExecuted` is still the only outcome that leaves the key retryable (v0.1 §5.4)."""
    _suspend(control)
    with pytest.raises(NotExecuted):
        control.resume(CONTINUATION, lambda: (_ for _ in ()).throw(NotExecuted("nothing ran")))

    refund = _suspending(control, continuation="round-2")
    with context(agent="refund-agent"), pytest.raises(Suspended):
        refund(payment_id="txn_1", amount=200)

    assert state_store.get_effect("refund:txn_1").attempt == 2


# --- what resumption refuses -----------------------------------------------------------


def test_a_continuation_cannot_be_consumed_twice(control):
    """One suspension admits one resumption. Two racing on one held key is the duplicate
    execution this product exists to prevent, wearing a continuation as a disguise."""
    _suspend(control)
    control.resume(CONTINUATION, lambda: "re_1")

    with pytest.raises(InvalidArgument):
        control.resume(CONTINUATION, lambda: "re_2")


def test_a_forged_continuation_is_refused(control, state_store):
    _suspend(control)

    with pytest.raises(InvalidArgument):
        control.resume("guessed", lambda: "re_1")

    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING


def test_a_continuation_nobody_holds_is_refused(control):
    with pytest.raises(InvalidArgument):
        control.resume(CONTINUATION, lambda: "re_1")


def test_resume_after_the_lease_expired_is_ambiguous(control, state_store, fake_clock):
    """The only way an elicitation ends ambiguous: a client that never returns."""
    _suspend(control)
    fake_clock.advance(timedelta(minutes=10))

    with pytest.raises(AmbiguousEffect):
        control.resume(CONTINUATION, lambda: "re_1")


def test_resume_after_another_attempt_ambiguated_the_record_is_refused(control, state_store):
    _suspend(control)
    record = state_store.get_effect("refund:txn_1")
    state_store.mark_ambiguous("refund:txn_1", record.action_id, "lost")

    with pytest.raises(AmbiguousEffect):
        control.resume(CONTINUATION, lambda: "re_1")


def test_the_executor_never_runs_when_the_continuation_is_refused(control):
    calls: list[int] = []

    with pytest.raises(InvalidArgument):
        control.resume("guessed", lambda: calls.append(1))

    assert calls == []


# --- multi-round, and the keyless case -------------------------------------------------


def test_a_resumed_executor_may_suspend_again(control, state_store):
    _suspend(control)

    def elicits_again() -> str:
        raise Suspended("round-2")

    with pytest.raises(Suspended):
        control.resume(CONTINUATION, elicits_again)

    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING
    rounds = [
        event.data["round"]
        for event in state_store.events()
        if event.type is EventType.EXECUTION_SUSPENDED
    ]
    assert rounds == [1, 2]
    assert control.resume("round-2", lambda: "re_1").result is ReceiptResult.COMMITTED


def test_the_first_continuation_is_dead_once_the_second_is_held(control):
    _suspend(control)
    with pytest.raises(Suspended):
        control.resume(CONTINUATION, lambda: (_ for _ in ()).throw(Suspended("round-2")))

    with pytest.raises(InvalidArgument):
        control.resume(CONTINUATION, lambda: "re_1")


def test_suspending_without_an_effect_key_holds_nothing_and_says_so(control, state_store):
    """Documented, not silent: suspension without an effect key gives no protection, exactly
    as retries without one give none (v0.1 §5.1)."""

    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        raise Suspended(CONTINUATION)

    with context(agent="support-agent"), pytest.raises(Suspended):
        read(customer_id="cus_1")

    suspended = [e for e in state_store.events() if e.type is EventType.EXECUTION_SUSPENDED]
    assert suspended[0].data["held"] is False
    assert state_store.list_effects() == ()
    with pytest.raises(InvalidArgument):
        control.resume(CONTINUATION, lambda: "ok")


def test_a_keyless_suspension_writes_no_receipt_either(control, state_store):
    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        raise Suspended(CONTINUATION)

    with context(agent="support-agent"), pytest.raises(Suspended):
        read(customer_id="cus_1")

    assert state_store.receipts() == ()


# --- the signal itself -----------------------------------------------------------------


def test_Suspended_accepts_bytes_and_str():
    assert Suspended(b"abc").continuation == "abc"
    assert Suspended("abc").continuation == "abc"


def test_Suspended_refuses_an_empty_continuation():
    """A continuation nobody can present again is a suspension nobody can end."""
    for empty in ("", b""):
        with pytest.raises(InvalidArgument):
            Suspended(empty)


def test_Suspended_is_a_ctrlrun_error():
    from ctrlrun import CTRLRunError

    assert issubclass(Suspended, CTRLRunError)

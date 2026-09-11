"""The attempt ceiling, `max_attempts`. Build-list item 4; SPEC-v0.7 §5, §8.4 (T240 to T252).

`v0.1 §5.4`'s `FAILED` row permits a renewal and bounds nothing. An executor that raises
`NotExecuted` on every call is therefore an unlimited number of provider dispatches, each one
individually correct and the sequence as a whole unbounded. §5's amendment bounds it where the
operator's policy entry says so, and leaves it exactly as it was where the entry says nothing.

**Two defences, two tests, because defence in depth hides mutations** (`CONTRIBUTING.md`). The
guarantee is the comparison against the attempt number **the store assigned**, taken after the
reservation and before the executor: two callers who both read `N-1` both pass a read taken
before reserving, and only the assigned number is atomic by construction. The read before the
approval gate is a fast path that saves a write, a spent approval and a human's attention, and
it is never the guarantee. T245 reaches the check with the fast path live, through the public
route §5.5 describes, and T245b reaches the fast path by the evidence only it leaves.

**Every refusal is asserted by its `reason`**, never by its type alone (mutation pattern 1): an
`ActionDenied` is also what a policy denial raises, and an `EFFECT_RESERVATION_REFUSED` is also
what a duplicate or an ambiguous record appends.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from ctrlrun import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    InMemoryStateStore,
    InvalidArgument,
    NotExecuted,
    Policy,
    PolicyError,
    SQLiteStateStore,
    Suspended,
    context,
    protect,
    with_approval,
)
from ctrlrun.effect import EffectState
from ctrlrun.receipt import BLOCKED_ATTEMPT_CEILING, EventType, ReceiptResult

#: The reason string §5.5 freezes, and §9.2 lists. Imported rather than spelled here so a
#: change to the constant is a red test and not a silent rename of the thing an operator greps.
CEILING = BLOCKED_ATTEMPT_CEILING

ALLOW_CEILING_3 = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 3
    decision: allow
"""

ALLOW_NO_CEILING = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    decision: allow
"""

ALLOW_CEILING_1 = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1
    decision: allow
"""

APPROVE_CEILING_1 = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1
    decision: approve
"""

APPROVE_CEILING_2 = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 2
    decision: approve
"""

OBSERVE_CEILING_1 = """
schema: ctrlrun.policy/v5
mode: observe
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1
    decision: allow
"""

ALLOW_CEILING_2 = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 2
    decision: allow
"""

OBSERVE_CEILING_2 = """
schema: ctrlrun.policy/v5
mode: observe
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 2
    decision: allow
"""

KEY = "refund:txn_1"


class Remote:
    """An executor that counts its calls and does whatever the script says (§8.4 T241)."""

    def __init__(self, *script: object) -> None:
        self.calls = 0
        self._script = list(script)

    def __call__(self) -> str:
        self.calls += 1
        step = self._script[min(self.calls - 1, len(self._script) - 1)]
        if isinstance(step, BaseException):
            raise step
        return f"re_txn_1-{self.calls}"


class _Hook:
    """A `reconcile` hook that counts its calls, so "before" and "after" are observed.

    `reconcile=lambda key: "not_executed"` answers the same whenever it runs, so a test that
    asserts only what the hook's answer produced cannot tell the order it ran in.
    """

    def __init__(self, answer: str = "not_executed") -> None:
        self.calls = 0
        self.keys: list[str] = []
        self._answer = answer

    def __call__(self, effect_key: str) -> str:
        self.calls += 1
        self.keys.append(effect_key)
        return self._answer


def _not_executed() -> NotExecuted:
    return NotExecuted("the remote rejected it before doing anything")


@pytest.fixture(
    params=[
        "in-memory",
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not os.environ.get("CTRLRUN_TEST_POSTGRES"),
                reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against",
            ),
        ),
    ]
)
def stores(request, tmp_path, fake_clock):
    """A factory for stores on whichever backend, so one test can build two (T247).

    `conftest.state_store` hands out one store, and the positive control needs a second: the
    same sequence under a document with no ceiling, compared record for record.

    **Postgres is one of the three**, which §8.4's T247 asks for in its first sentence: without
    it the ceiling's only Postgres coverage is T247's property assertion, which catches a deleted
    check about a quarter of the time and a deleted fast path never. Each store gets a scratch
    schema of its own, dropped afterwards (`v0.6 §4.1`), so nothing is left in `public`.
    """
    made: list[object] = []
    schemas: list[str] = []

    def make():
        if request.param == "in-memory":
            store = InMemoryStateStore(clock=fake_clock)
        elif request.param == "sqlite":
            store = SQLiteStateStore(tmp_path / f"state-{len(made)}.db", clock=fake_clock)
        else:
            from ctrlrun.postgres import PostgresStateStore

            url = os.environ["CTRLRUN_TEST_POSTGRES"]
            name = f"cap_{uuid.uuid4().hex[:12]}"
            PostgresStateStore.create_schema(url, name)
            schemas.append(name)
            store = PostgresStateStore(url, schema=name, clock=fake_clock)
        made.append(store)
        return store

    yield make
    for store in made:
        store.close()
    if schemas:
        from ctrlrun.postgres import PostgresStateStore

        for name in schemas:
            PostgresStateStore.drop_schema(os.environ["CTRLRUN_TEST_POSTGRES"], name)


def _control(document, store, fake_clock):
    return Control(Policy.from_yaml(document), store, clock=fake_clock)


def _refund(control, remote, **kwargs):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control, **kwargs)
    def refund(payment_id: str, amount: int) -> str:
        return remote()

    return refund


def _call(control, remote, *, payment_id="txn_1", approval=None, **kwargs):
    """One attempt, returning what it raised or the receipt it produced."""
    refund = _refund(control, remote, **kwargs)
    with context(agent="refund-agent"):
        if approval is None:
            return refund(payment_id=payment_id, amount=200)
        with with_approval(approval):
            return refund(payment_id=payment_id, amount=200)


def _events(store, type_=None):
    return [event for event in store.events() if type_ is None or event.type is type_]


def _refusals(store):
    return _events(store, EventType.EFFECT_RESERVATION_REFUSED)


def _receipts(store):
    return list(store.receipts())


def _grant(control, store, payment_id="txn_1"):
    """Verify's own approval, through the call `ctrlrun approve` makes."""
    from ctrlrun.action import Action
    from ctrlrun.identity import Principal

    action = Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": 200},
        principal=Principal(agent="refund-agent"),
        environment=control.environment,
    )
    request = control.approvals.request(action, timedelta(hours=1))
    store.grant_approval(request.request_id, "ops@example.com")
    return request.request_id


@contextmanager
def _window(store, method, interleave, *, after=True):
    """Open one window inside a `Control.execute`, on purpose, and only once.

    `interleave` runs immediately after (or before) the store's `method` returns, so what the
    caller does next happens in a world that has moved. That is what makes these tests
    reproductions rather than approximations (mutation pattern 4): two `Control`s calling in
    turn open nothing, because the second reads what the first left behind.

    Armed once, and re-entrant by construction: `interleave` runs its own `Control.execute`,
    whose own call to the same method must not fire the window again.
    """
    original = getattr(store, method)
    state = {"armed": True, "inside": False}

    def wrapped(*args, **kwargs):
        if not after:
            _fire(state, interleave)
        answer = original(*args, **kwargs)
        if after:
            _fire(state, interleave)
        return answer

    setattr(store, method, wrapped)
    try:
        yield state
    finally:
        setattr(store, method, original)


def _fire(state, interleave):
    if not state["armed"] or state["inside"]:
        return
    state["armed"] = False
    state["inside"] = True
    try:
        interleave()
    finally:
        state["inside"] = False


def _stale_read(store, interleave):
    """The window between the fast path's read and the reservation (§5.5).

    The fast path's `get_effect` returns what the record was, and `interleave` then moves it on
    before the caller reaches `reserve_effect`: two callers who both read attempt N-1 both pass
    a read taken before reserving, and only the check on the number the store **assigned** can
    stop the second.
    """
    return _window(store, "get_effect", interleave)


@contextmanager
def monkeypatched(owner, name, replacement):
    """One attribute swapped for the length of a `with`, restored on the way out."""
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _renew_and_fail(control, remote, payment_id="txn_1"):
    """One whole attempt by somebody else: renew the key, dispatch, and fail."""
    with pytest.raises(NotExecuted):
        _call(control, remote, payment_id=payment_id)


def _drive_to_failed(control, store, ceiling):
    """Run `ceiling` attempts that each raise `NotExecuted`, leaving the record FAILED at N."""
    remote = Remote(*[_not_executed() for _ in range(ceiling)])
    for _ in range(ceiling):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    record = store.get_effect(KEY)
    assert record is not None and record.state is EffectState.FAILED
    assert record.attempt == ceiling
    return remote


# --- T240: exactly N dispatches, then a named refusal -----------------------------------


def test_T240_exactly_N_dispatches_then_a_refusal_naming_the_ceiling(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = _drive_to_failed(control, store, 3)

    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)

    # The reason, not only the type: a policy denial is an `ActionDenied` too.
    assert refused.value.reason == CEILING
    assert remote.calls == 3

    blocked = _receipts(store)[-1]
    assert blocked.result is ReceiptResult.BLOCKED
    assert "max_attempts is 3" in (blocked.error or "")

    refusal = _refusals(store)[-1]
    assert refusal.data["reason"] == CEILING
    assert refusal.data["attempt"] == 4
    assert refusal.data["max_attempts"] == 3

    record = store.get_effect(KEY)
    assert record.state is EffectState.FAILED


# --- T241: a refused attempt never calls the executor -----------------------------------


def test_T241_the_fast_path_never_calls_the_executor(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = _drive_to_failed(control, store, 3)
    with pytest.raises(ActionDenied):
        _call(control, remote)
    assert remote.calls == 3


def test_T241_the_check_after_the_reservation_never_calls_the_executor(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = _reach_the_check(control, store)
    assert remote.calls == 3


# --- T242: the positive control -- under the ceiling, 0.6.1's behaviour -----------------


#: The three fields of `CLOCK_SKEW_DETECTED` that differ between two measurements of the same
#: thing: the microseconds measured, the instant, and the round trip's half (SPEC-v0.7 §3.4).
_VOLATILE_SKEW_FIELDS = ("skew_us", "measured_at", "bound_us")


def _projection(store):
    """What a run left behind, in the terms 0.6.1 and 0.7 must agree on.

    `CLOCK_SKEW_DETECTED` is **normalised, not dropped**: on Postgres every scratch store is
    opened with this suite's frozen clock, so each one measures a real skew of several days and
    reports it with its own microseconds (SPEC-v0.7 §1.4 item 6, §3.8). Replacing those three
    fields keeps the event in the sequence, so the comparison still counts them and still fixes
    where they fall: dropping the event would let a capped run that opened one more store than
    the uncapped one compare equal. Nothing reaches that today, and the stronger form is free.
    """
    events = [
        (
            str(event.type),
            {
                key: ("<volatile>" if key in _VOLATILE_SKEW_FIELDS else value)
                for key, value in event.data.items()
            }
            if str(event.type) == "CLOCK_SKEW_DETECTED"
            else dict(event.data),
            event.effect_key,
        )
        for event in store.events()
    ]
    receipts = [
        (receipt.result, receipt.attempt, receipt.effect_key) for receipt in _receipts(store)
    ]
    record = store.get_effect(KEY)
    return events, receipts, (record.state, record.attempt)


def test_T242_under_the_ceiling_nothing_changes(stores, fake_clock):
    capped_store = stores()
    capped = _control(ALLOW_CEILING_3, capped_store, fake_clock)
    _drive_to_failed(capped, capped_store, 3)

    free_store = stores()
    free = _control(ALLOW_NO_CEILING, free_store, fake_clock)
    _drive_to_failed(free, free_store, 3)

    assert _projection(capped_store) == _projection(free_store)


# --- T243: no ceiling, no change --------------------------------------------------------


def test_T243_a_document_with_no_ceiling_renews_without_bound(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_NO_CEILING, store, fake_clock)
    # More attempts than any ceiling in this suite, so a hardcoded default would show up here.
    remote = Remote(*[_not_executed() for _ in range(8)])
    for _ in range(8):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    assert remote.calls == 8
    assert store.get_effect(KEY).attempt == 8
    assert not [event for event in _refusals(store) if event.data.get("reason") == CEILING]


def test_T243_an_action_with_no_ceiling_beside_one_with_a_ceiling(stores, fake_clock):
    """The key is per action: a ceiling on one action bounds nothing on another."""
    document = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1
    decision: allow
  stripe.payout:
    effect: "payout:{payment_id}"
    decision: allow
"""
    store = stores()
    control = _control(document, store, fake_clock)
    remote = Remote(*[_not_executed() for _ in range(5)])

    @protect("stripe.payout", effect="payout:{payment_id}", control=control)
    def payout(payment_id: str, amount: int) -> str:
        return remote()

    for _ in range(5):
        with context(agent="refund-agent"), pytest.raises(NotExecuted):
            payout(payment_id="txn_1", amount=200)
    assert remote.calls == 5


# --- T244: the loader refuses a malformed ceiling ---------------------------------------


def _document_with(value: str) -> tuple[str, int]:
    """A v5 document whose `max_attempts` is `value`, and the line it is on."""
    lines = [
        "schema: ctrlrun.policy/v5",
        "actions:",
        "  stripe.refund:",
        '    effect: "refund:{payment_id}"',
        f"    max_attempts: {value}",
        "    decision: allow",
    ]
    return "\n".join(lines) + "\n", lines.index(f"    max_attempts: {value}") + 1


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"3"', "{a: 1}", "[1]", "null"])
def test_T244_a_malformed_ceiling_is_refused_at_load_naming_key_action_and_line(value):
    document, line = _document_with(value)
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document, source="ctrlrun.yaml")
    message = str(refused.value)
    assert "max_attempts" in message
    assert "stripe.refund" in message
    assert f"line {line}" in message


def test_T244_a_ceiling_needs_schema_v5():
    document = """
schema: ctrlrun.policy/v4
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 3
    decision: allow
"""
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document, source="ctrlrun.yaml")
    message = str(refused.value)
    assert "max_attempts" in message
    assert "ctrlrun.policy/v5" in message


def test_T244_v5_is_a_superset_of_every_earlier_version():
    """Three equality gates in the loader would refuse exactly this (§5.3)."""
    document = """
schema: ctrlrun.policy/v5
version: "2026-09-11"
environment: production
mode: enforce
controls:
  card-data-handling:
    title: Card data handling
authority:
  grants:
    - id: g1
      subject: { agent: "*" }
      actions: ["**"]
      resources: ["**"]
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    resource: "payment/{payment_id}"
    max_attempts: 3
    controls: [card-data-handling]
    data:
      payment_id: pii
    rules:
      - when: { amount_lte: 50000 }
        decision: allow
      - decision: deny
"""
    policy = Policy.from_yaml(document, source="ctrlrun.yaml")
    assert policy.max_attempts("stripe.refund") == 3
    assert policy.version == "2026-09-11"
    assert policy.environment == "production"


def test_T244_a_standalone_authority_document_labelled_v5_is_accepted():
    from ctrlrun.authority import Authority

    document = """
schema: ctrlrun.policy/v5
authority:
  grants:
    - id: g1
      subject: { agent: "*" }
      actions: ["**"]
      resources: ["**"]
"""
    authority = Authority.from_yaml(document, source="authority.yaml", standalone=True)
    assert authority is not None


def test_T244_two_documents_differing_only_in_a_ceiling_hash_differently():
    three = Policy.from_yaml(ALLOW_CEILING_3, source="ctrlrun.yaml")
    one = Policy.from_yaml(ALLOW_CEILING_1, source="ctrlrun.yaml")
    assert three.policy_hash != one.policy_hash


def test_T244_an_absent_ceiling_reads_as_None():
    assert (
        Policy.from_yaml(ALLOW_NO_CEILING, source="ctrlrun.yaml").max_attempts("stripe.refund")
        is None
    )
    assert Policy.from_yaml(ALLOW_CEILING_3, source="ctrlrun.yaml").max_attempts("nope") is None


# --- T245: the check on the assigned number, alone, through the public route ------------


def _reach_the_check(control, store):
    """§5.5's public route to the check, with the fast path live and no seam.

    Attempts 1 and 2 raise `NotExecuted`; attempt 3 raises `TimeoutError`, so the record is
    `AMBIGUOUS` at 3 -- which the fast path is normatively not allowed to refuse. Attempt 4
    carries a `reconcile` hook answering `not_executed`: the hook moves the record to `FAILED`
    at 3 and the second take renews it to 4, which only the check after the reservation can
    refuse.
    """
    remote = Remote(_not_executed(), _not_executed(), TimeoutError("the response was lost"))
    for _ in range(2):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    with pytest.raises(TimeoutError):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote, reconcile=lambda key: "not_executed")
    assert refused.value.reason == CEILING
    return remote


def test_T245_the_check_refuses_the_attempt_the_store_assigned(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = _reach_the_check(control, store)

    assert remote.calls == 3

    types = [str(event.type) for event in store.events()]
    reserved = len(types) - 1 - types[::-1].index("EFFECT_RESERVED")
    refused_at = len(types) - 1 - types[::-1].index("EFFECT_RESERVATION_REFUSED")
    assert reserved < refused_at, "the check runs after a reservation the store granted"

    refusal = _refusals(store)[-1]
    assert refusal.data["reason"] == CEILING
    assert refusal.data["attempt"] == 4
    assert refusal.data["max_attempts"] == 3

    record = store.get_effect(KEY)
    assert record.state is EffectState.FAILED
    assert record.attempt == 4, "the store assigned 4 and the check refused that number"

    assert _receipts(store)[-1].result is ReceiptResult.BLOCKED
    assert "EXECUTION_STARTED" not in types[reserved:]


def test_T245_the_fast_path_lets_an_ambiguous_record_through(stores, fake_clock):
    """The fast path refuses only a `FAILED` record, normatively (§5.5).

    T245's route and G15 both rest on it: a fast path that refused an `AMBIGUOUS` record at or
    above the ceiling would take the check's own test away from it.
    """
    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    remote = Remote(TimeoutError("the response was lost"), "ok")
    with pytest.raises(TimeoutError):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS

    with pytest.raises(AmbiguousEffect):
        _call(control, remote)
    assert _refusals(store)[-1].data["reason"] == "ambiguous"


def test_T245_two_callers_that_both_read_below_the_ceiling_do_not_both_get_through(
    stores, fake_clock
):
    """The concurrency case, deterministic, with the window opened on purpose (§5.5).

    A ceiling of 2 and a record `FAILED` at 1. This caller's fast path reads that record and
    passes it, because attempt 2 is within the ceiling. Between that read and the reservation
    somebody else renews to 2, dispatches and fails. The store then assigns **3**, and only the
    check on the assigned number can refuse it: a kernel whose ceiling is the read alone lets
    this attempt execute, which is the attribution-instead-of-prevention §5.5 rejects.
    """
    store = stores()
    control = _control(ALLOW_CEILING_2, store, fake_clock)
    mine = Remote(_not_executed(), "ok")
    with pytest.raises(NotExecuted):
        _call(control, mine)
    assert store.get_effect(KEY).attempt == 1

    other = Remote(_not_executed())
    with (
        _stale_read(store, lambda: _renew_and_fail(control, other)) as window,
        pytest.raises(ActionDenied) as refused,
    ):
        _call(control, mine)
    assert not window["armed"], "the window never opened, so this test proved nothing"
    assert refused.value.reason == CEILING
    assert mine.calls == 1 and other.calls == 1, "exactly two dispatches under a ceiling of 2"

    refusal = _refusals(store)[-1]
    assert refusal.data["attempt"] == 3
    assert refusal.data["max_attempts"] == 2
    record = store.get_effect(KEY)
    assert record.state is EffectState.FAILED
    assert record.attempt == 3


# --- T245b: the fast path, alone, by the evidence it leaves -----------------------------


def test_T245b_the_fast_path_creates_no_approval_request(stores, fake_clock):
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed())
    approval = _grant(control, store)
    with pytest.raises(NotExecuted):
        _call(control, remote, approval=approval)
    assert store.get_effect(KEY).attempt == 1
    requested_before = len(_events(store, EventType.APPROVAL_REQUESTED))

    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)
    assert refused.value.reason == CEILING
    assert len(_events(store, EventType.APPROVAL_REQUESTED)) == requested_before, (
        "the fast path runs before the approval gate, so no human is asked"
    )
    assert _refusals(store)[-1].data["reason"] == CEILING
    assert remote.calls == 1


def test_T245b_the_fast_path_leaves_a_presented_approval_granted(stores, fake_clock):
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed())
    with pytest.raises(NotExecuted):
        _call(control, remote, approval=_grant(control, store))

    second = _grant(control, store)
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote, approval=second)
    assert refused.value.reason == CEILING
    assert store.get_approval(second).status == "granted"
    assert _refusals(store)[-1].data["reason"] == CEILING

    # SPEC-v0.6 §7.2.1, as the `DENY` path applies it: the refusal is **recorded against** the
    # approval it met, so the history connects a live granted approval to what stopped it. A
    # review found `approval_id=None` here while an unspent approval sat in the store, which is
    # the same omission that review found on the `DENY` path.
    assert _refusals(store)[-1].approval_id == second
    blocked = _receipts(store)[-1]
    assert blocked.approval_id == second
    assert blocked.approver == "ops@example.com"


def test_T245b_the_fast_path_reserves_nothing(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed())
    with pytest.raises(NotExecuted):
        _call(control, remote)
    reserved_before = len(_events(store, EventType.EFFECT_RESERVED))

    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)
    assert refused.value.reason == CEILING
    assert len(_events(store, EventType.EFFECT_RESERVED)) == reserved_before
    assert store.get_effect(KEY).attempt == 1
    assert _refusals(store)[-1].data["reason"] == CEILING


def test_T245b_without_the_fast_path_the_three_do_not_fail_alike(stores, fake_clock):
    """What the fast path is worth, stated as the difference it makes (§8.4 T245b).

    With the fast path deleted, call 1 is not refused at all: the check runs only after an
    approval is presented, so an `APPROVE` action with nothing presented raises
    `ApprovalRequired` and creates a request. That asymmetry is what the three assertions
    above are for, and asserting it here keeps them from reading as one fact repeated.
    """
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed())
    with pytest.raises(NotExecuted):
        _call(control, remote, approval=_grant(control, store))

    # The check alone cannot see this call: it is refused before any approval exists.
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)
    assert refused.value.reason == CEILING
    assert not isinstance(refused.value, ApprovalRequired)


# --- T247: both backends, and the v0.6 multi-process standard on Postgres ---------------

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

#: The tree under test: a child that imported a different checkout grades a different kernel.
REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")

#: Generous, because a bound that fires is a red test and not a hang.
BOUND = 180.0

CHILD = textwrap.dedent("""
    import json, os, sys, time
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, job["src"])
    import ctrlrun
    _WHERE = os.path.realpath(ctrlrun.__file__)
    assert _WHERE.startswith(os.path.realpath(job["src"])), (
        "the child imported ctrlrun from %s, not the tree under test" % _WHERE)
    from ctrlrun import ActionDenied, Control, NotExecuted, Policy, context, protect
    from ctrlrun.errors import CTRLRunError
    from ctrlrun.postgres import PostgresStateStore

    store = PostgresStateStore(job["url"], schema=job["schema"])
    control = Control(Policy.from_yaml(job["policy"]), store)
    dispatches = []

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id, amount):
        dispatches.append(1)
        raise NotExecuted("the remote rejected it before doing anything")

    refused = None
    started = time.time()
    for _ in range(job["rounds"]):
        try:
            with context(agent="refund-agent"):
                refund(payment_id=job["payment_id"], amount=200)
        except NotExecuted:
            continue
        except ActionDenied as denied:
            refused = denied.reason
            break
        except CTRLRunError:
            continue
    finished = time.time()
    record = store.get_effect("refund:" + job["payment_id"])
    store.close()
    print(json.dumps({
        "dispatches": len(dispatches),
        "refused": refused,
        "attempt": None if record is None else record.attempt,
        "state": None if record is None else str(record.state),
        "started": started,
        "finished": finished,
    }))
""")


def _overlapping(results):
    """The largest number of children whose `[started, finished]` windows all overlap.

    The signal a serialised run cannot produce. Each child reports the wall clock it took either
    side of its own loop, so two children overlap when neither finished before the other began.
    """
    most = 0
    for probe in results:
        together = [
            other
            for other in results
            if other["started"] <= probe["finished"] and probe["started"] <= other["finished"]
        ]
        most = max(most, len(together))
    return most


@postgres
def test_T247_no_more_than_N_dispatches_across_separate_processes():
    """`v0.6`'s multi-process standard: separate OS processes, one Postgres, one key.

    It depends on item 3a and cannot see item 3a missing: racing processes do not reliably
    stall between a `SELECT` and an `UPDATE`, which is why item 3a's T246 opens that window
    deterministically and this test does not claim to. What it does claim is the property
    §5.5 states: whatever the concurrency, at most `max_attempts` attempts execute.
    """
    from ctrlrun.postgres import PostgresStateStore

    # A scratch schema of this test's own, dropped afterwards (SPEC-v0.6 §4.1): a store opened
    # on the default `public` would leave tables behind in the operator's own schema, and
    # T154f is the test that says so.
    schema = f"cap_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    payment_id = f"txn_{uuid.uuid4().hex[:12]}"
    job = {
        "src": REPO_SRC,
        "url": POSTGRES_URL,
        "schema": schema,
        "policy": ALLOW_CEILING_3,
        "payment_id": payment_id,
        "rounds": 8,
    }
    children = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results = []
    try:
        # **Every child is fed before any is waited on.** A `communicate()` per child in turn
        # feeds the second only once the first has exited, which serialises them: an earlier
        # version of this test did that and reported six processes racing where none did.
        for child in children:
            assert child.stdin is not None
            child.stdin.write(json.dumps(job))
            child.stdin.close()
        for child in children:
            # Bounded, so a child that wedges fails red rather than hanging CI: a timeout is
            # not a test failure (SPEC-v0.4 §3.6), it is this test failing to say anything.
            child.wait(timeout=BOUND)
            assert child.stdout is not None and child.stderr is not None
            out, err = child.stdout.read(), child.stderr.read()
            assert child.returncode == 0, err
            results.append(json.loads(out.strip().splitlines()[-1]))
    finally:
        for child in children:
            if child.poll() is None:  # pragma: no cover - only on a wedged child
                child.kill()
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)

    total = sum(result["dispatches"] for result in results)
    assert total <= 3, f"the ceiling of 3 admitted {total} dispatches: {results}"
    assert total >= 1, "no process reached the executor at all, so nothing was bounded"
    assert any(result["refused"] == CEILING for result in results), (
        "no process met the ceiling, so this run did not exercise it"
    )
    # The record itself may sit at 4: where the check refuses, the store assigned that number
    # and the record is released `FAILED` at it, having executed nothing (§5.5). What is bounded
    # is the number of **dispatches**, which is what the guarantee is about.
    assert all(result["refused"] in (None, CEILING) for result in results), (
        f"a process was refused for a reason this run cannot explain: {results}"
    )
    # **And the run has to have contended**, asserted on something only contention produces.
    # Every assertion above is satisfied by six processes running one after another, which is
    # what this test looked like before a review replaced the feed-all-then-wait loop with a
    # `wait()` per child and watched it pass 3 out of 3. A serialised run has no two windows
    # that overlap; a contended one has several.
    assert _overlapping(results) >= 2, (
        f"no two children ran at the same time, so nothing was contended: {results}"
    )


# --- T248: what happens to an approval on a refused attempt -----------------------------


def test_T248_the_reconcile_route_asks_a_human_for_an_attempt_that_can_never_run(
    stores, fake_clock
):
    """The cost §5.5 states rather than closes, pinned so the claim cannot drift back.

    The fast path refuses only a `FAILED` record, normatively, so an `AMBIGUOUS` one goes
    through to `_secure` -- and `_secure` reaches the approval gate **before** any reconcile.
    So on §5.5's own public route, which is the one T245 and G15 are built on, a human is asked
    for an attempt that the check will then refuse, and the yes they give is consumed by the
    reservation that gets refused.

    This is not the adapter's `Control.evaluate` exception: it is the kernel's primary route.
    §5.5 states the cost and does not close it, because re-reading the record between the
    reconcile and the second take would destroy the seamless route the check's own test needs.
    """
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    remote = Remote(TimeoutError("the response was lost"), "never reached")
    with pytest.raises(TimeoutError):
        _call(control, remote, approval=_grant(control, store))
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS

    requested_before = len(_events(store, EventType.APPROVAL_REQUESTED))

    # **Counted, not inferred** (mutation pattern 4). "The approval gate ran before the
    # reconcile" is the whole finding, and a request created *after* the hook had run would
    # satisfy the count above just as well. The hook counts its own calls, and the assertion is
    # that it had not been called when the human was asked.
    hook = _Hook("not_executed")

    # Attempt 2 presents nothing and carries the hook. A NEW request is created and the caller
    # is told to go and find a human, although attempt 2 can never run under a ceiling of 1.
    with pytest.raises(ApprovalRequired) as asked:
        _call(control, remote, reconcile=hook)
    assert hook.calls == 0, "the reconcile hook ran first, so this is not the route §5.5 names"
    assert len(_events(store, EventType.APPROVAL_REQUESTED)) == requested_before + 1, (
        "the approval gate ran before the reconcile, so a human was asked"
    )
    request_id = asked.value.request_id
    assert store.get_approval(request_id).status == "pending"

    # The human says yes, the agent retries, and the yes is spent on the refusal.
    store.grant_approval(request_id, "ops@example.com")
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote, approval=request_id, reconcile=hook)
    assert refused.value.reason == CEILING
    assert hook.calls == 1, "the retry is where the hook runs, once, after the approval gate"
    assert store.get_approval(request_id).status == "consumed", (
        "the reservation consumed it in the transaction the check then refused"
    )
    assert remote.calls == 1, "one dispatch under a ceiling of 1, and one wasted answer"
    assert _receipts(store)[-1].approval_id == request_id


def test_T248_the_check_leaves_the_approval_consumed_and_the_receipt_names_it(stores, fake_clock):
    store = stores()
    control = _control(APPROVE_CEILING_2, store, fake_clock)
    remote = Remote(_not_executed(), TimeoutError("the response was lost"))
    with pytest.raises(NotExecuted):
        _call(control, remote, approval=_grant(control, store))
    with pytest.raises(TimeoutError):
        _call(control, remote, approval=_grant(control, store))
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS

    third = _grant(control, store)
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote, approval=third, reconcile=lambda key: "not_executed")
    assert refused.value.reason == CEILING

    assert store.get_approval(third).status == "consumed"
    blocked = _receipts(store)[-1]
    assert blocked.result is ReceiptResult.BLOCKED
    assert blocked.approval_id == third
    assert remote.calls == 2


# --- T249: a crash between the reservation and the release ------------------------------


class _Killed(BaseException):
    """What a `kill -9` looks like from inside the process that is about to stop."""


def _killed(self, *args, **kwargs):
    raise _Killed("the process was killed between the reservation and the release")


def test_T249_a_crash_between_the_reservation_and_the_release_is_ambiguous(
    stores, fake_clock, monkeypatch
):
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)

    # §5.5's public route to the check, so no seam is needed to get past the fast path:
    # attempts 1 and 2 fail, attempt 3 times out and leaves the record AMBIGUOUS at 3.
    remote = Remote(_not_executed(), _not_executed(), TimeoutError("the response was lost"))
    for _ in range(2):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    with pytest.raises(TimeoutError):
        _call(control, remote)

    # The process stops exactly where §5.2 asks about: after the reservation the store
    # granted, and before `fail_effect` released it.
    monkeypatch.setattr(type(store), "fail_effect", _killed)
    with pytest.raises(_Killed):
        _call(control, remote, reconcile=lambda key: "not_executed")
    record = store.get_effect(KEY)
    assert record.state in (EffectState.RESERVED, EffectState.EXECUTING)
    assert record.attempt == 4
    assert remote.calls == 3, "the executor was never called for the refused attempt"

    monkeypatch.undo()
    fake_clock.advance(timedelta(minutes=10))
    with pytest.raises(AmbiguousEffect):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS


def test_T249_a_record_that_moved_while_the_ceiling_decided_propagates_the_stores_refusal(
    stores, fake_clock
):
    """§5.7: the refusal propagates after the `blocked` receipt, as `v0.1 §5.5` has it.

    The window is real and it is opened here rather than described: between the reservation the
    check refuses and the `begin_execution` that releases it, this attempt's lease lapses and
    another attempt declares the effect `AMBIGUOUS` (`v0.1 §5.3 E3`). The store then refuses the
    release for the reason it always would, and the caller is told the truer thing, which is
    that the key is not theirs any more.
    """
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = Remote(_not_executed(), _not_executed(), TimeoutError("the response was lost"))
    for _ in range(2):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    with pytest.raises(TimeoutError):
        _call(control, remote)

    # Another deployment of the same action, with no ceiling, so it is free to meet the lapsed
    # lease and move the record where E3 says it goes.
    other = _control(ALLOW_NO_CEILING, store, fake_clock)

    def lease_lapses_and_somebody_ambiguates():
        fake_clock.advance(timedelta(minutes=10))
        with pytest.raises(AmbiguousEffect):
            _call(other, Remote("never reached"))

    with (
        _window(store, "reserve_effect", lease_lapses_and_somebody_ambiguates) as window,
        pytest.raises(AmbiguousEffect),
    ):
        _call(control, remote, reconcile=lambda key: "not_executed")
    assert not window["armed"], "the window never opened, so this test proved nothing"

    assert remote.calls == 3, "the executor was never called for the refused attempt"
    blocked = _receipts(store)[-1]
    assert blocked.result is ReceiptResult.BLOCKED
    assert "max_attempts is 3" in (blocked.error or "")
    assert _refusals(store)[-1].data["reason"] == CEILING
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS


def test_T249_a_store_refusal_of_any_type_still_leaves_the_evidence(stores, fake_clock):
    """The refusal takes nothing with it (§5.5, `v0.1 §5.5`).

    `_checked` raises `InvalidArgument`, not `DuplicateEffect` or `AmbiguousEffect`, where the
    record moved under a different `action_id` with a dead lease. A review found that escaping
    before the event and the receipt were written, leaving the record `RESERVED` and the refusal
    with no evidence at all. The evidence is written first now, and every `CTRLRunError` the
    release raises still propagates.
    """
    store = stores()
    control = _control(ALLOW_CEILING_3, store, fake_clock)
    remote = Remote(_not_executed(), _not_executed(), TimeoutError("the response was lost"))
    for _ in range(2):
        with pytest.raises(NotExecuted):
            _call(control, remote)
    with pytest.raises(TimeoutError):
        _call(control, remote)

    def refusing(self, *args, **kwargs):
        raise InvalidArgument("the record moved under another action with a dead lease")

    with monkeypatched(type(store), "begin_execution", refusing), pytest.raises(InvalidArgument):
        _call(control, remote, reconcile=lambda key: "not_executed")

    assert _refusals(store)[-1].data["reason"] == CEILING
    blocked = _receipts(store)[-1]
    assert blocked.result is ReceiptResult.BLOCKED
    assert "max_attempts is 3" in (blocked.error or "")
    assert remote.calls == 3


# --- §6's provider in front of the ceiling, on the route §5.5 states ---------------------


def _with_provider(control, remote, provider, *, approval=None, reconcile=None):
    """One attempt whose `@protect` names a precondition provider (SPEC-v0.7 §6.2)."""
    extra = {"reconcile": reconcile} if reconcile is not None else {}

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        control=control,
        preconditions=provider,
        **extra,
    )
    def refund(payment_id: str, amount: int) -> str:
        return remote()

    with context(agent="refund-agent"):
        if approval is None:
            return refund(payment_id="txn_1", amount=200)
        with with_approval(approval):
            return refund(payment_id="txn_1", amount=200)


class _Provider:
    """A precondition provider that counts its calls and can be broken on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.broken = False

    def __call__(self, action):
        self.calls += 1
        if self.broken:
            raise RuntimeError("ctrlrun-test: the precondition provider is down")
        return {"balance": 0}


def _strand_at_one(control, store, provider, remote):
    """Attempt 1: ask, grant, dispatch, time out. Leaves the record AMBIGUOUS at 1."""
    with pytest.raises(ApprovalRequired) as asked:
        _with_provider(control, remote, provider)
    store.grant_approval(asked.value.request_id, "ops@example.com")
    with pytest.raises(TimeoutError):
        _with_provider(control, remote, provider, approval=asked.value.request_id)
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS


def test_the_provider_runs_in_front_of_the_ceiling_on_the_reconcile_route(stores, fake_clock):
    """§5.5's reconcile bullet, with the numbers it states (SPEC-v0.7 §5.5, §6.6).

    `_recheck` sits immediately before each `_take`, and this route takes twice, so a doomed
    attempt under `max_attempts: 1` spends the operator's provider three times before the check
    refuses: once on the request pass and twice on the retry. §6.6's principle, that a provider
    is spent only where its answer can matter, does not reach the ceiling, and the spec says so
    rather than closing it.
    """
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    provider = _Provider()
    remote = Remote(TimeoutError("the response was lost"), "never reached")
    _strand_at_one(control, store, provider, remote)

    before = provider.calls
    hook = _Hook("not_executed")
    with pytest.raises(ApprovalRequired) as asked:
        _with_provider(control, remote, provider, reconcile=hook)
    store.grant_approval(asked.value.request_id, "ops@example.com")
    with pytest.raises(ActionDenied) as refused:
        _with_provider(control, remote, provider, approval=asked.value.request_id, reconcile=hook)

    assert refused.value.reason == CEILING
    assert provider.calls - before == 3, (
        "one call on the request pass and two on the retry, because _recheck runs per _take"
    )
    assert remote.calls == 1, "and none of the three could have changed the answer"


def test_a_broken_provider_renames_the_refusal_of_an_attempt_that_could_never_run(
    stores, fake_clock
):
    """The sharper half of the same ordering: the operator is told the wrong reason.

    The provider runs first, so its failure is what refuses the call: `ApprovalMismatch` with
    `precondition_unavailable`, not `ActionDenied` with `attempt_ceiling`. No effect record is
    written, the record stays `AMBIGUOUS` at N, and the `reconcile` hook never runs.
    """
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    provider = _Provider()
    remote = Remote(TimeoutError("the response was lost"), "never reached")
    _strand_at_one(control, store, provider, remote)

    hook = _Hook("not_executed")
    with pytest.raises(ApprovalRequired) as asked:
        _with_provider(control, remote, provider, reconcile=hook)
    store.grant_approval(asked.value.request_id, "ops@example.com")

    provider.broken = True
    with pytest.raises(ApprovalMismatch) as refused:
        _with_provider(control, remote, provider, approval=asked.value.request_id, reconcile=hook)
    assert refused.value.reason == "precondition_unavailable"
    assert hook.calls == 0, "the provider refused before the reconcile could run"
    record = store.get_effect(KEY)
    assert record.state is EffectState.AMBIGUOUS
    assert record.attempt == 1, "nothing was reserved, so the ceiling never had an opinion"
    assert remote.calls == 1


def test_the_sequential_route_never_calls_the_provider(stores, fake_clock):
    """§6.6's principle where the fast path can answer: zero calls, and the right reason."""
    store = stores()
    control = _control(APPROVE_CEILING_1, store, fake_clock)
    provider = _Provider()
    remote = Remote(_not_executed(), "never reached")

    with pytest.raises(ApprovalRequired) as asked:
        _with_provider(control, remote, provider)
    store.grant_approval(asked.value.request_id, "ops@example.com")
    with pytest.raises(NotExecuted):
        _with_provider(control, remote, provider, approval=asked.value.request_id)
    assert store.get_effect(KEY).state is EffectState.FAILED

    before = provider.calls
    with pytest.raises(ActionDenied) as refused:
        _with_provider(control, remote, provider)
    assert refused.value.reason == CEILING
    assert provider.calls == before, "the fast path refuses before the approval gate"
    assert remote.calls == 1


# --- what the refused attempt number costs, and what the ceiling does not bound ----------


def test_a_refused_attempt_number_is_spent(stores, fake_clock):
    """§5.5: the reservation assigned it, so raising the ceiling buys less than the difference.

    §5.7 justifies counting an attempt a human resolved because *"it was dispatched"*. The number
    a check-path refusal burns was not dispatched, and it is spent all the same, because the
    store assigned it before the check could look. An operator raising `max_attempts` from 2 to 4
    buys one more dispatch, not two, and the spec says so rather than leaving it to be found.
    """
    store = stores()
    control = _control(ALLOW_CEILING_2, store, fake_clock)
    remote = Remote(_not_executed(), TimeoutError("the response was lost"), "ok", "ok")
    with pytest.raises(NotExecuted):
        _call(control, remote)
    with pytest.raises(TimeoutError):
        _call(control, remote)
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote, reconcile=lambda key: "not_executed")
    assert refused.value.reason == CEILING
    assert remote.calls == 2

    record = store.get_effect(KEY)
    assert record.state is EffectState.FAILED
    assert record.attempt == 3, "the refused number was assigned, and it is gone"

    raised = _control(
        ALLOW_CEILING_3.replace("max_attempts: 3", "max_attempts: 4"), store, fake_clock
    )
    assert _call(raised, remote) == "re_txn_1-3"
    assert remote.calls == 3, "raising 2 to 4 bought one more dispatch, not two"


def test_the_ceiling_bounds_attempts_and_not_executor_invocations(stores, fake_clock):
    """§5.7: `Control.resume` reserves nothing, and the consequence is stated.

    One attempt can invoke the executor many times: a `Suspended` executor holds attempt 1 and
    each `resume` runs it again on that same attempt. The gateway bounds this with
    `max_elicitation_rounds` (`v0.2 §6.9.2`); a direct `Control.resume` caller has no bound, and
    `max_attempts` is not one. It bounds attempts, which is what a provider dispatch costs.
    """
    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    rounds = {"n": 0}

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        rounds["n"] += 1
        raise Suspended(f"round-{rounds['n']}")

    with context(agent="refund-agent"), pytest.raises(Suspended):
        refund(payment_id="txn_1", amount=200)
    for expected in range(2, 6):
        with pytest.raises(Suspended):
            control.resume(f"round-{expected - 1}", lambda: refund.__wrapped__("txn_1", 200))
        assert rounds["n"] == expected

    record = store.get_effect(KEY)
    assert record.attempt == 1, "every round was the same attempt"
    assert rounds["n"] == 5, "five executor invocations under max_attempts: 1"


# --- T250: observe mode, resume, and resolved attempts ----------------------------------


def test_T250_observe_mode_records_the_ceiling_and_runs(stores, fake_clock):
    store = stores()
    control = _control(OBSERVE_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed(), "ok")
    with pytest.raises(NotExecuted):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.FAILED

    assert _call(control, remote) == "re_txn_1-2"
    receipt = _receipts(store)[-1]
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have.blocked_reason == CEILING
    assert remote.calls == 2, "observe mode suppresses the decision, not the execution"

    # §5.5: `attempt_ceiling` is a member of `BLOCKED_BY_STATE`, so `ctrlrun stats` counts it on
    # the "would have been blocked" line and not as a policy denial. A reason left out of that
    # set is a rollout report that quietly stops adding up (`v0.3 §6.4`).
    from ctrlrun.reporting import stats_document

    counted = stats_document(_receipts(store), mode="observe", boundary=None)
    assert counted["would_have_been_blocked"] == 1
    assert counted["blocked_by_reason"] == {CEILING: 1}
    assert counted["would_have_been_denied"] == 0


def test_T250_the_observed_fast_path_records_the_ceiling_before_the_approval_gate(
    stores, fake_clock
):
    """The fast path's own observe-mode half, told apart from the check's (§5.5, §5.7).

    Both defences write the same `blocked_reason`, so a test on an `ALLOW` action passes with
    either one deleted, which is the defence-in-depth problem `CONTRIBUTING.md` names. What tells
    them apart is the **order**: `_Observation.block` keeps the first reason, the fast path runs
    before the approval gate, and with nothing presented the gate's own reason is
    `approval_required`. So on an `APPROVE` action with no approval presented, `attempt_ceiling`
    can only have come from the fast path.
    """
    document = OBSERVE_CEILING_1.replace("decision: allow", "decision: approve")
    store = stores()
    control = _control(document, store, fake_clock)
    remote = Remote(_not_executed(), "ok")
    with pytest.raises(NotExecuted):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.FAILED

    assert _call(control, remote) == "re_txn_1-2"
    receipt = _receipts(store)[-1]
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have.blocked_reason == CEILING, (
        "the fast path decided first, as it does in enforce mode"
    )


def test_T250_the_observed_check_after_the_reservation_records_the_ceiling_too(stores, fake_clock):
    """The other observe-mode half, through the window the check exists for (§5.7).

    Observe mode never calls the `reconcile` hook (`v0.3 §6.2`), so T245's route cannot reach
    the check here. What reaches it is the race the check is *for*: a fast path that read a
    record below the ceiling, and a reservation that lands after somebody else renewed.
    """
    store = stores()
    control = _control(OBSERVE_CEILING_2, store, fake_clock)
    remote = Remote(_not_executed(), "ok")
    with pytest.raises(NotExecuted):
        _call(control, remote)

    other = Remote(_not_executed())
    with _stale_read(store, lambda: _renew_and_fail(control, other)):
        assert _call(control, remote) == "re_txn_1-2"
    receipt = _receipts(store)[-1]
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have.blocked_reason == CEILING
    assert store.get_effect(KEY).attempt == 3


def test_T250_a_resumed_leg_past_the_ceiling_is_not_refused(stores, fake_clock):
    """`Control.resume` reserves nothing, so there is no new number to compare (§5.7).

    Reached by lowering the ceiling while an attempt is suspended, which is the only way a
    resumed leg can be past one: `execute` refuses before it can suspend such an attempt.
    """
    store = stores()
    control = _control(ALLOW_CEILING_2, store, fake_clock)
    with pytest.raises(NotExecuted):
        _call(control, Remote(_not_executed()))

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def suspending(payment_id: str, amount: int) -> str:
        raise Suspended("opaque-state-from-the-server")

    with context(agent="refund-agent"), pytest.raises(Suspended):
        suspending(payment_id="txn_1", amount=200)
    assert store.get_effect(KEY).attempt == 2

    lowered = _control(ALLOW_CEILING_1, store, fake_clock)
    receipt = lowered.resume("opaque-state-from-the-server", lambda: "re_txn_1")
    assert receipt.result is ReceiptResult.COMMITTED
    assert store.get_effect(KEY).state is EffectState.COMMITTED


def test_T250_an_attempt_a_human_resolved_to_failed_still_counts(stores, fake_clock):
    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    remote = Remote(TimeoutError("the response was lost"), "ok")
    with pytest.raises(TimeoutError):
        _call(control, remote)
    assert store.get_effect(KEY).state is EffectState.AMBIGUOUS

    # What `ctrlrun resolve <key> failed` does.
    store.resolve_effect(KEY, EffectState.FAILED, "human:ops@example.com")
    assert store.get_effect(KEY).attempt == 1

    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)
    assert refused.value.reason == CEILING
    assert remote.calls == 1


# --- T251: the amendment is in SPEC-v0.1.md ---------------------------------------------


def test_T251_the_amendment_is_in_spec_v0_1():
    spec = (Path(__file__).resolve().parents[1] / "docs" / "SPEC-v0.1.md").read_text(
        encoding="utf-8"
    )
    section = spec.split("### 5.4 Retry rules", 1)[1].split("### 5.5", 1)[0]
    # The original table, unchanged, and the amendment beneath it.
    original = "| `FAILED` | **allowed** — new attempt, same key, `attempt += 1` | — |"
    assert original in section
    amendment = section.index("**Amendment (v0.7, `SPEC-v0.7.md` §5).**")
    assert amendment > section.index(original)
    assert "max_attempts" in section[amendment:]
    assert 'ActionDenied(reason="attempt_ceiling")' in section[amendment:]
    assert "ctrlrun.policy/v5" in section[amendment:]


# --- one warning where a ceiling can count nothing --------------------------------------


def test_a_ceiling_on_an_action_that_resolves_no_effect_key_warns_once(stores, fake_clock, caplog):
    """§5.3: the template may come from the decorator, so the policy cannot refuse this."""
    document = """
schema: ctrlrun.policy/v5
actions:
  stripe.refund:
    max_attempts: 3
    decision: allow
"""
    store = stores()
    control = _control(document, store, fake_clock)

    @protect("stripe.refund", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "ok"

    with caplog.at_level("WARNING", logger="ctrlrun"), context(agent="refund-agent"):
        for _ in range(3):
            refund(payment_id="txn_1", amount=200)
    named = [record for record in caplog.records if "max_attempts" in record.getMessage()]
    assert len(named) == 1
    assert "stripe.refund" in named[0].getMessage()


# --- the derivation of the number the ceiling is compared against -----------------------


def test_the_ceiling_counts_executions_and_not_retries(stores, fake_clock):
    """`max_attempts: 1` means no renewal at all, the first attempt included (§5.3)."""
    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    remote = Remote(_not_executed())
    with pytest.raises(NotExecuted):
        _call(control, remote)
    with pytest.raises(ActionDenied) as refused:
        _call(control, remote)
    assert refused.value.reason == CEILING
    assert remote.calls == 1


def test_a_committed_effect_is_still_a_duplicate_and_not_a_ceiling_refusal(stores, fake_clock):
    """The ceiling acts on a renewal and never widens what `v0.1 §5.4`'s other rows say."""
    from ctrlrun import DuplicateEffect

    store = stores()
    control = _control(ALLOW_CEILING_1, store, fake_clock)
    remote = Remote("ok", "ok")
    _call(control, remote)
    with pytest.raises(DuplicateEffect):
        _call(control, remote)
    assert _refusals(store)[-1].data["reason"] == "duplicate"


# --- T252: G15 in verify, and what the ceiling does to G5's selection --------------------

#: G15's `N/A` reasons and G5's new one, verbatim from §8.9.
G15_NOT_DECLARED = (
    "no action verify can drive to allow or approve declares both `effect:` and `max_attempts`"
)
G15_ABOVE_BOUND = (
    "every action verify can drive to allow or approve that declares both `effect:` and "
    "`max_attempts` declares one above verify's bound of 100 attempts"
)
CEILING_FORBIDS_RENEWAL = (
    "every action with an `effect:` template that verify can select (a decision of allow or "
    "approve under a grant that covers it) declares max_attempts: 1, so no renewal can happen"
)

V5_CEILING_3 = """
schema: ctrlrun.policy/v5
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    max_attempts: 3
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
"""

V5_CEILING_1_ONLY = """
schema: ctrlrun.policy/v5
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
"""

V5_NO_CEILING = """
schema: ctrlrun.policy/v5
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
"""

V5_CEILING_ABOVE_BOUND = """
schema: ctrlrun.policy/v5
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    max_attempts: 1000
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
"""

#: §8.9's precedence case: the ceiling is the only reason nothing is selectable, and the
#: deny-only action B must not make the sentence read as G5's old one.
V5_CAPPED_A_AND_DENIED_B = """
schema: ctrlrun.policy/v5
actions:
  acme.alpha:
    effect: "alpha:{ticket}"
    max_attempts: 1
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
  acme.beta:
    effect: "beta:{ticket}"
    decision: deny
"""


def _verify(tmp_path, document, *, only):
    from ctrlrun.verify import run as run_verify

    path = tmp_path / "ctrlrun.yaml"
    path.write_text(document, encoding="utf-8")
    report = run_verify(path, only=only)
    return {result.id: result for result in report.guarantees}


def test_T252_G15_is_in_the_catalogue():
    from ctrlrun.verify import guarantees as reg

    assert reg.CATALOGUE == "ctrlrun.guarantees/v3"
    assert "G15" in reg.BY_ID
    assert "v0.1 §5.4" in reg.BY_ID["G15"].descends_from


def test_T252_G15_is_graded_where_the_document_declares_a_ceiling(tmp_path):
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_CEILING_3, only=("G15",))["G15"]
    assert result.status is Status.PASS, (result.reason, result.counterexample)


def test_T252_G15_is_graded_at_a_ceiling_of_one(tmp_path):
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_CEILING_1_ONLY, only=("G15",))["G15"]
    assert result.status is Status.PASS, (result.reason, result.counterexample)


def test_T252_G15_is_not_applicable_where_no_action_declares_a_ceiling(tmp_path):
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_NO_CEILING, only=("G15",))["G15"]
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == G15_NOT_DECLARED


def test_T252_G15_is_not_applicable_above_verifys_bound(tmp_path):
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_CEILING_ABOVE_BOUND, only=("G15",))["G15"]
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == G15_ABOVE_BOUND


def test_T252_the_above_bound_reason_is_true_of_a_document_with_a_low_deny_only_ceiling(tmp_path):
    """§8.9's opening MUST: every N/A reason is a sentence true of the operator's document.

    The fallback selection re-applies the effect and ceiling filters and drops only the bound, so
    it can only ever describe the actions verify can **drive**. An earlier wording said "every
    declared max_attempts is above verify's bound" and was false here: `acme.beta` declares 3.
    That is the identical defect §8.9 already caught and fixed in the sibling sentence.
    """
    from ctrlrun.verify import Status

    document = """
schema: ctrlrun.policy/v5
actions:
  acme.alpha:
    effect: "alpha:{ticket}"
    max_attempts: 1000
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
  acme.beta:
    effect: "beta:{ticket}"
    max_attempts: 3
    decision: deny
"""
    result = _verify(tmp_path, document, only=("G15",))["G15"]
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == G15_ABOVE_BOUND
    assert "every declared max_attempts" not in result.reason, (
        "the sentence must not claim anything about a ceiling verify never looked at"
    )


def test_T252_G14_reuses_the_same_selection_and_the_same_sentence(tmp_path):
    """§8.9 amends G5 **and** G14 in the same way, because each one's control is a renewal.

    Item 3 landed G14 on a branch of its own while this was being built, so the wiring happened
    at the merge. One mechanism, not two: `select(needs_renewal=True)` and
    `_renewal_unselected(...)`, and the sentence a reader sees is the same one.
    """
    from ctrlrun.verify import Status

    capped = _verify(tmp_path, V5_CEILING_1_ONLY, only=("G5", "G14"))
    for gid in ("G5", "G14"):
        assert capped[gid].status is Status.NOT_APPLICABLE, (gid, capped[gid].reason)
        assert capped[gid].reason == CEILING_FORBIDS_RENEWAL, gid

    graded = _verify(tmp_path, V5_CEILING_3, only=("G5", "G14"))
    for gid in ("G5", "G14"):
        assert graded[gid].status is Status.PASS, (gid, graded[gid].reason)


def test_T252_the_catalogue_is_in_id_order(tmp_path):
    """`BY_ID`'s insertion order is the report's order (SPEC-v0.7 §9.4).

    Items 3, 4 and 5 each appended after G13 on their own branch, so a textual merge yields
    G13/G15/G14 as easily as a conflict, and the table then simply prints out of order while
    every count still adds up and every id is still present.

    **This is a second guard, not the only one, and the difference matters.** An earlier
    docstring here claimed nothing else would have noticed the swap; a review checked and it is
    untrue. `tests/test_verify.py`'s catalogue test has asserted the same ordering since before
    this branch existed, and it fails on the same mutation. What this one adds is locality: the
    merge that could produce the swap is item 4's, and a reader of item 4's tests should find
    the assertion that governs it here rather than in another file. `BY_ID`'s own order is the
    part `test_verify.py` does not assert.
    """
    from ctrlrun.verify import guarantees as reg

    numbers = [int(guarantee.id[1:]) for guarantee in reg.GUARANTEES]
    assert numbers == sorted(numbers), [g.id for g in reg.GUARANTEES]
    assert [int(gid[1:]) for gid in reg.BY_ID] == numbers


def test_T252_the_catalogue_titles_fit_the_report_table(tmp_path):
    """A title wider than `report._TITLE_WIDTH` breaks the CLI table's alignment."""
    from ctrlrun.verify import guarantees as reg
    from ctrlrun.verify.report import _TITLE_WIDTH

    too_wide = {g.id: len(g.title) for g in reg.GUARANTEES if len(g.title) > _TITLE_WIDTH}
    assert not too_wide, too_wide


def test_T252_a_kernel_with_the_check_deleted_fails_G15(tmp_path, monkeypatch):
    """The control §8.9 asks for: G15 must be able to fail, and on the check's own mechanism.

    An earlier draft drove N+1 sequential `NotExecuted` attempts, which the fast path alone
    refuses, so G15 passed with the guarantee's own mechanism deleted.
    """
    from ctrlrun.verify import Status

    monkeypatch.setattr(Control, "_over_the_ceiling", lambda self, ceiling, attempt: False)
    result = _verify(tmp_path, V5_CEILING_3, only=("G15",))["G15"]
    assert result.status is Status.FAIL
    assert result.reason != "control failed", "the observable fails, not the control"


def test_T252_a_kernel_that_refuses_every_renewal_fails_G15s_control(tmp_path, monkeypatch):
    from ctrlrun.verify import Status

    monkeypatch.setattr(Control, "_over_the_ceiling", lambda self, ceiling, attempt: True)
    result = _verify(tmp_path, V5_CEILING_3, only=("G15",))["G15"]
    assert result.status is Status.FAIL
    assert result.reason == "control failed"


def test_T252_G5_is_still_graded_where_the_ceiling_allows_a_renewal(tmp_path):
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_CEILING_3, only=("G5",))["G5"]
    assert result.status is Status.PASS, (result.reason, result.counterexample)


def test_T252_G5_is_not_applicable_where_every_ceiling_forbids_a_renewal(tmp_path):
    """`max_attempts: 1` would otherwise turn G5's control into a false `fail` (§8.9)."""
    from ctrlrun.verify import Status

    result = _verify(tmp_path, V5_CEILING_1_ONLY, only=("G5",))["G5"]
    assert result.status is Status.NOT_APPLICABLE, (result.reason, result.counterexample)
    assert result.reason == CEILING_FORBIDS_RENEWAL


def test_T252_G5s_ceiling_sentence_is_printed_only_where_the_ceiling_is_the_only_reason(tmp_path):
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    result = _verify(tmp_path, V5_CAPPED_A_AND_DENIED_B, only=("G5",))["G5"]
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == CEILING_FORBIDS_RENEWAL

    # And where nothing is selectable for the older reason, that reason still wins.
    nothing = """
schema: ctrlrun.policy/v5
actions:
  acme.refund:
    max_attempts: 1
    decision: allow
"""
    result = _verify(tmp_path, nothing, only=("G5",))["G5"]
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == reg.NO_EFFECT_TEMPLATE


def test_T252_G5_selects_the_uncapped_action_where_there_is_one(tmp_path):
    from ctrlrun.verify import Status

    both = """
schema: ctrlrun.policy/v5
actions:
  acme.alpha:
    effect: "alpha:{ticket}"
    max_attempts: 1
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
  acme.beta:
    effect: "beta:{ticket}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
"""
    result = _verify(tmp_path, both, only=("G5",))["G5"]
    assert result.status is Status.PASS, (result.reason, result.counterexample)
    assert result.action == "acme.beta", "G5 skips the action whose ceiling forbids a renewal"

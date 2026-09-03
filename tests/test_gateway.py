"""What the gateway needs from the StateStore. Build-list item 6a; SPEC-v0.2 §6.9.4, §6.10.

The gateway itself is items 6b-6d. This is the kernel half it stands on, built first and on
its own because it is the part that touches transactions: a lease extension and two
hash-keyed approval lookups. T26b is the acceptance test, and the row that matters in it is
the last one — an `EXECUTING` record whose lease has *already* expired is not extendable by
anything, because another attempt may already have declared it `AMBIGUOUS`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ctrlrun import (
    Action,
    AmbiguousEffect,
    ApprovalRequest,
    CTRLRunError,
    DuplicateEffect,
    EffectState,
    InvalidArgument,
    Principal,
    SQLiteStateStore,
)
from ctrlrun.approval import DEFAULT_APPROVAL_TTL, ApprovalStatus
from ctrlrun.effect import COMMITTED_EFFECT, IN_PROGRESS_EFFECT

KEY = "refund:txn_1"
MINE = "act_mine"
THEIRS = "act_theirs"
LEASE = timedelta(minutes=5)


@pytest.fixture
def sqlite_store(tmp_path, fake_clock):
    """T26b is explicit that this runs against a real SQLite store, not the double."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    yield store
    store.close()


def _executing(store, action_id: str = MINE) -> None:
    store.reserve_effect(KEY, action_id, LEASE)
    store.begin_execution(KEY, action_id)


def _until(fake_clock, minutes: int = 10) -> datetime:
    return fake_clock.now + timedelta(minutes=minutes)


# --- T26b: extend_lease refuses what it must -------------------------------------------


def test_T26b_extend_lease_succeeds_for_an_executing_record_this_action_holds(
    sqlite_store, fake_clock
):
    _executing(sqlite_store)
    until = _until(fake_clock)

    sqlite_store.extend_lease(KEY, MINE, until)

    record = sqlite_store.get_effect(KEY)
    assert record.lease_expires_at == until
    assert record.state is EffectState.EXECUTING
    assert record.lease_is_live(fake_clock.now + timedelta(minutes=9))


def test_T26b_an_extended_lease_keeps_the_record_alive_past_the_original(sqlite_store, fake_clock):
    """The point of the call: a round trip that outlasts the lease it started under."""
    _executing(sqlite_store)
    sqlite_store.extend_lease(KEY, MINE, _until(fake_clock, 10))
    fake_clock.advance(timedelta(minutes=7))

    assert sqlite_store.get_effect(KEY).lease_is_live(fake_clock.now)


def test_T26b_extend_lease_refuses_a_reserved_record(sqlite_store, fake_clock):
    sqlite_store.reserve_effect(KEY, MINE, LEASE)

    with pytest.raises(DuplicateEffect) as refused:
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert refused.value.state == IN_PROGRESS_EFFECT
    assert sqlite_store.get_effect(KEY).state is EffectState.RESERVED


def test_T26b_extend_lease_refuses_a_committed_record(sqlite_store, fake_clock):
    _executing(sqlite_store)
    sqlite_store.commit_effect(KEY, MINE, "re_1")

    with pytest.raises(DuplicateEffect) as refused:
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert refused.value.state == COMMITTED_EFFECT


def test_T26b_extend_lease_refuses_a_failed_record(sqlite_store, fake_clock):
    _executing(sqlite_store)
    sqlite_store.fail_effect(KEY, MINE, "rejected before any side effect")

    with pytest.raises(AmbiguousEffect) as refused:
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert "failed" in str(refused.value)
    # Refused without mutating: FAILED still permits a retry (v0.1 §5.4).
    assert sqlite_store.get_effect(KEY).state is EffectState.FAILED


def test_T26b_extend_lease_refuses_an_ambiguous_record(sqlite_store, fake_clock):
    _executing(sqlite_store)
    sqlite_store.mark_ambiguous(KEY, MINE, "response lost")

    with pytest.raises(AmbiguousEffect) as refused:
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert "ambiguous" in str(refused.value)
    assert sqlite_store.get_effect(KEY).state is EffectState.AMBIGUOUS


def test_T26b_extend_lease_refuses_a_different_action_id(sqlite_store, fake_clock):
    _executing(sqlite_store, THEIRS)

    with pytest.raises(DuplicateEffect) as refused:
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert refused.value.state == IN_PROGRESS_EFFECT
    assert sqlite_store.get_effect(KEY).action_id == THEIRS


def test_T26b_extend_lease_refuses_a_lease_that_already_expired(sqlite_store, fake_clock):
    """The row that carries the whole safety argument (SPEC-v0.2 §6.9.4).

    A record whose lease has lapsed may already have been declared `AMBIGUOUS` by another
    attempt, and extending it would resurrect a reservation someone else has moved on from —
    reintroducing exactly the double execution the lease exists to prevent. Deterministic:
    the clock is advanced, never slept on.
    """
    _executing(sqlite_store)
    fake_clock.advance(LEASE + timedelta(seconds=1))

    with pytest.raises(AmbiguousEffect):
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert sqlite_store.get_effect(KEY).state is EffectState.EXECUTING
    assert not sqlite_store.get_effect(KEY).lease_is_live(fake_clock.now)


def test_T26b_an_expired_lease_stays_unextendable_after_another_attempt_ambiguates_it(
    sqlite_store, fake_clock
):
    """The sequence §6.9.4 is defending against, run in order."""
    _executing(sqlite_store)
    fake_clock.advance(LEASE + timedelta(seconds=1))
    with pytest.raises(AmbiguousEffect):
        sqlite_store.reserve_effect(KEY, THEIRS, LEASE)  # declares it AMBIGUOUS (v0.1 §5.4)

    assert sqlite_store.get_effect(KEY).state is EffectState.AMBIGUOUS
    with pytest.raises(AmbiguousEffect):
        sqlite_store.extend_lease(KEY, MINE, _until(fake_clock))


def test_T26b_extend_lease_refuses_a_key_no_record_exists_for(sqlite_store, fake_clock):
    with pytest.raises(CTRLRunError):
        sqlite_store.extend_lease("refund:nothing", MINE, _until(fake_clock))


def test_T26b_extend_lease_refuses_an_until_in_the_past(sqlite_store, fake_clock):
    """A lease that has already expired reserves nothing (v0.1 §5.3 E3)."""
    _executing(sqlite_store)

    with pytest.raises(InvalidArgument):
        sqlite_store.extend_lease(KEY, MINE, fake_clock.now - timedelta(seconds=1))

    assert sqlite_store.get_effect(KEY).lease_is_live(fake_clock.now)


def test_T26b_a_past_until_is_refused_before_the_record_is_even_read(sqlite_store, fake_clock):
    """The two refusals are separated deliberately (SPEC-v0.2 §6.9.4).

    A live lease always expires after `now`, so a past `until` is also a shortening — behind
    the no-shortening rule this check could never fire, and two defences that can only fire
    together are one defence and a comment. Validating the argument first makes each
    independently reachable, and this is the window where only the first one can apply:
    there is no record, so there is no lease to shorten.
    """
    with pytest.raises(InvalidArgument):
        sqlite_store.extend_lease("refund:nothing", MINE, fake_clock.now - timedelta(seconds=1))

    # The same call with a future `until` gets the record refusal instead, not this one.
    with pytest.raises(AmbiguousEffect):
        sqlite_store.extend_lease("refund:nothing", MINE, _until(fake_clock))


def test_T26b_extend_lease_refuses_a_naive_datetime(sqlite_store):
    _executing(sqlite_store)

    with pytest.raises(InvalidArgument):
        sqlite_store.extend_lease(KEY, MINE, datetime(2099, 1, 1))


def test_T26b_extend_lease_never_shortens_a_lease(sqlite_store, fake_clock):
    """`extend_lease`, not `set_lease`: v0.1 §5.3's first clause is untouched, and moving
    expiry closer is a release of reservation by another name."""
    _executing(sqlite_store)
    original = sqlite_store.get_effect(KEY).lease_expires_at

    with pytest.raises(InvalidArgument):
        sqlite_store.extend_lease(KEY, MINE, original - timedelta(minutes=1))

    assert sqlite_store.get_effect(KEY).lease_expires_at == original


def test_extend_lease_holds_for_the_in_memory_store_too(state_store, fake_clock):
    """A double that permits what SQLite refuses invalidates every test that uses it."""
    state_store.reserve_effect(KEY, MINE, LEASE)
    state_store.begin_execution(KEY, MINE)
    state_store.extend_lease(KEY, MINE, _until(fake_clock))

    assert state_store.get_effect(KEY).lease_expires_at == _until(fake_clock)
    state_store.mark_ambiguous(KEY, MINE, "lost")
    with pytest.raises(AmbiguousEffect):
        state_store.extend_lease(KEY, MINE, _until(fake_clock, 20))


# --- §6.10: the two hash-keyed approval lookups ----------------------------------------


def _request(store, request_id: str, action: Action, clock, ttl=DEFAULT_APPROVAL_TTL):
    request = ApprovalRequest(
        request_id=request_id,
        action_hash=action.action_hash,
        action=action,
        created_at=clock.now,
        expires_at=clock.now + ttl,
    )
    store.put_approval_request(request)
    return request


def _action(amount: int = 2000) -> Action:
    return Action(
        name="mcp.acme.refund",
        arguments={"payment_id": "txn_1", "amount": amount},
        principal=Principal(agent="refund-agent"),
    )


def test_find_granted_approval_returns_the_newest_granted_one(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.grant_approval("apr_1", "human:ops")
    fake_clock.advance(timedelta(minutes=1))
    _request(state_store, "apr_2", action, fake_clock)
    state_store.grant_approval("apr_2", "human:ops")

    found = state_store.find_granted_approval(action.action_hash)

    assert found is not None
    assert found.approval_id == "apr_2"


def test_find_granted_approval_breaks_a_tie_toward_the_later_record(state_store, fake_clock):
    """Two grants in one clock tick tie on `granted_at`; the later-stored one is newer."""
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    _request(state_store, "apr_2", action, fake_clock)
    state_store.grant_approval("apr_1", "human:ops")
    state_store.grant_approval("apr_2", "human:ops")

    assert state_store.find_granted_approval(action.action_hash).approval_id == "apr_2"


def test_find_granted_approval_ignores_a_pending_one(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)

    assert state_store.find_granted_approval(action.action_hash) is None


def test_find_granted_approval_ignores_a_consumed_one(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.grant_approval("apr_1", "human:ops")
    state_store.consume_approval("apr_1", action.action_hash)

    assert state_store.find_granted_approval(action.action_hash) is None


def test_find_granted_approval_ignores_an_expired_one(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock, ttl=timedelta(minutes=5))
    state_store.grant_approval("apr_1", "human:ops")
    fake_clock.advance(timedelta(minutes=6))

    assert state_store.find_granted_approval(action.action_hash) is None


def test_find_granted_approval_ignores_another_actions_hash(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.grant_approval("apr_1", "human:ops")

    assert state_store.find_granted_approval(_action(amount=9999).action_hash) is None


def test_find_denied_request_returns_an_unexpired_denial(state_store, fake_clock):
    """§6.10 — "no" is an answer, and re-asking is not free."""
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.deny_approval("apr_1", "human:ops")

    found = state_store.find_denied_request(action.action_hash)

    assert found is not None
    assert found.request_id == "apr_1"


def test_find_denied_request_ignores_an_expired_denial(state_store, fake_clock):
    """A denial holds for the life of the request that carried it; after that, asking
    again is legitimate (§6.10)."""
    action = _action()
    _request(state_store, "apr_1", action, fake_clock, ttl=timedelta(minutes=5))
    state_store.deny_approval("apr_1", "human:ops")
    fake_clock.advance(timedelta(minutes=6))

    assert state_store.find_denied_request(action.action_hash) is None


def test_find_denied_request_ignores_a_granted_one(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.grant_approval("apr_1", "human:ops")

    assert state_store.find_denied_request(action.action_hash) is None


def test_the_two_lookups_agree_with_the_record_the_store_holds(state_store, fake_clock):
    action = _action()
    _request(state_store, "apr_1", action, fake_clock)
    state_store.deny_approval("apr_1", "human:ops")

    assert state_store.get_approval("apr_1").status is ApprovalStatus.DENIED
    assert state_store.find_granted_approval(action.action_hash) is None
    assert state_store.find_denied_request(action.action_hash).request_id == "apr_1"

# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""T388 to T398: scope providers (SPEC-v0.9 §5).

The bite on an identifier an attacker chose. A grant permits `records.read` on `customer:*`; a
scope provider answers whether *this* customer belongs to *this* principal, strictly before the
reservation, fail-closed when it cannot answer.

Every refusal test asserts the **reason**, never the type alone: three guards deny the same
action with the same exception, and a test that cannot tell which fired is the first of
CONTRIBUTING.md's four shapes of a false green.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.control import Control
from ctrlrun.errors import ActionDenied, InvalidArgument
from ctrlrun.policy import Policy
from ctrlrun.receipt import ReceiptResult
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
AGENT = Principal(agent="reader", user="ada")

POLICY = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  records.read:
    decision: allow
"""


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


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
        made: Any = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"scope_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock) -> Control:
    return Control(
        policy=Policy.from_yaml(POLICY, source="<test>"),
        store=store,
        clock=clock,
        environment="prod",
    )


def _action(resource: str | None = "customer:1") -> Action:
    return Action(
        name="records.read",
        arguments={"id": "1"},
        principal=AGENT,
        resource=resource,
        environment="prod",
    )


def _scope(*patterns: str):
    """A provider that answers, and records that it was asked."""

    calls: list[Action] = []

    def provider(action: Action) -> dict[str, Any]:
        calls.append(action)
        return {"resources": list(patterns)}

    provider.calls = calls  # type: ignore[attr-defined]
    return provider


def test_T388_a_scope_containing_the_resource_permits(store, clock) -> None:
    """G23's positive control. Without it a kernel refusing everything grades PASS."""
    control = _control(store, clock)
    receipt = control.execute(_action(), lambda: {"ok": True}, "read:1", scope=_scope("customer:1"))
    assert receipt.result is ReceiptResult.COMMITTED


def test_T389_a_scope_not_containing_the_resource_refuses(store, clock) -> None:
    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(
            _action("customer:90210"), lambda: {"ok": True}, "read:x", scope=_scope("customer:1")
        )
    assert caught.value.reason == "out_of_scope"
    assert store.get_effect("read:x") is None, "nothing may be reserved for an out-of-scope action"


def test_T390_a_provider_that_raises_reserves_nothing_and_executes_nothing(store, clock) -> None:
    """G23. The provider fails, and the kernel must not have reserved on the way to finding out."""
    calls: list[int] = []

    def boom(action: Action) -> dict[str, Any]:
        raise RuntimeError("the scope service is down")

    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(_action(), lambda: calls.append(1) or {"ok": True}, "read:1", scope=boom)
    assert caught.value.reason == "scope_unavailable"
    assert calls == [], "the executor must not run when the scope cannot be read"
    assert store.get_effect("read:1") is None, "nothing reserved"


def test_T391_a_provider_returning_a_non_mapping_refuses(store, clock) -> None:
    """And the **message** names what was wrong, which is what keeps the guard load-bearing.

    A mutation run found the shape guard removable with every test still green: a list reaches
    `dict()` inside the hash and raises there anyway, so the refusal happened for a reason that
    was not this check. CONTRIBUTING.md's first pattern allows keeping a subsumed branch for its
    message, on the condition that a test asserts which message it got.
    """
    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(_action(), lambda: {"ok": True}, "read:1", scope=lambda action: ["nope"])
    assert caught.value.reason == "scope_unavailable"
    denied = [e for e in store.events() if e.type.value == "ACTION_DENIED"][-1]
    assert "mapping" in str(denied.data.get("error")), (
        "the refusal must name the shape that was wrong, not merely fail somewhere downstream"
    )


def test_T394_observe_mode_runs_the_provider_and_refuses_nothing(store, clock) -> None:
    """SPEC-v0.9 §5.2.2's observe row, and `v0.3 §6.2`'s rule.

    Observe mode records what it would have done. A scope check that enforced under observation
    would refuse during the phase whose entire purpose is to refuse nothing, and
    observe-then-enforce is the documented adoption path. Found by a mutation run: collapsing the
    two refusal paths into one left every test green.
    """
    observing = Control(
        policy=Policy.from_yaml(
            POLICY.replace("environment: prod", "environment: prod\nmode: observe"), source="<obs>"
        ),
        store=store,
        clock=clock,
        environment="prod",
    )
    calls: list[int] = []
    receipt = observing.execute(
        _action("customer:90210"),
        lambda: calls.append(1) or {"ok": True},
        "read:x",
        scope=_scope("customer:1"),
    )
    # It ran: observe mode executes, and records the counterfactual.
    assert calls == [1]
    assert receipt.result is ReceiptResult.OBSERVED
    denied = [
        event
        for event in store.events()
        if event.type.value == "ACTION_DENIED" and event.data.get("reason") == "out_of_scope"
    ]
    assert denied, "observe mode must record the scope refusal it did not enforce"
    assert denied[0].data.get("observed") is True


def test_T392_a_provider_returning_what_the_canonicalizer_refuses(store, clock) -> None:
    """`v0.1 §2.3`'s float rejection, inherited: a scope hashed over a float would drift.

    **The float is deliberately NOT in `resources`.** A mutation run found the first version of
    this test green against a kernel with the canonicalizer bypassed entirely: `{"resources":
    [1.5]}` is refused by the *shape* guard, which wants a list of strings, so the hash never had
    to reject anything. That is CONTRIBUTING.md's first mutation pattern, a subsumed guard, and a
    test that cannot tell which one fired proves nothing about either.

    Here `resources` is well-formed and the float sits beside it, so the shape guard passes and
    only `canonical_bytes` can refuse.
    """
    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(
            _action(),
            lambda: {"ok": True},
            "read:1",
            scope=lambda a: {"resources": ["customer:1"], "quota": 1.5},
        )
    assert caught.value.reason == "scope_unavailable"


def test_T392a_a_non_string_key_in_the_scope_is_refused(store, clock) -> None:
    """The canonicalizer's other inherited refusal, for the same reason and by the same route."""
    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(
            _action(),
            lambda: {"ok": True},
            "read:1",
            scope=lambda a: {"resources": ["customer:1"], 7: "not-a-string-key"},
        )
    assert caught.value.reason == "scope_unavailable"


def test_T393_the_provider_is_called_before_the_store_call(store, clock) -> None:
    """§5.3's ordering IS the safety argument, so it is asserted rather than assumed."""
    seen: list[Any] = []

    def provider(action: Action) -> dict[str, Any]:
        seen.append(store.get_effect("read:1"))
        return {"resources": ["customer:1"]}

    control = _control(store, clock)
    control.execute(_action(), lambda: {"ok": True}, "read:1", scope=provider)
    assert seen == [None], "the provider ran after the reservation; §5.3 forbids it"


def test_T395_a_provider_that_hangs_leaves_nothing_reserved(store, clock) -> None:
    """A provider that never answers cannot strand a reservation, because it runs first.

    The real hazard is a *slow* provider; the test drives the same window with one that fails
    after an arbitrary delay's worth of work, because a test that really slept would be a
    timeout rather than an assertion.
    """

    def slow_then_fail(action: Action) -> dict[str, Any]:
        for _ in range(1000):
            pass
        raise TimeoutError("no answer")

    control = _control(store, clock)
    with pytest.raises(ActionDenied):
        control.execute(_action(), lambda: {"ok": True}, "read:1", scope=slow_then_fail)
    assert store.get_effect("read:1") is None


def test_T396_no_provider_is_0_8_0_exactly(store, clock) -> None:
    """R5. Absent means absent, proven by driving the whole path and reading the receipt."""
    control = _control(store, clock)
    receipt = control.execute(_action(), lambda: {"ok": True}, "read:1")
    assert receipt.result is ReceiptResult.COMMITTED
    assert receipt.to_dict()["scope_hash"] is None


def test_T397_the_hash_reaches_the_receipt_and_the_scope_never_does(store, clock) -> None:
    """`v0.7 §6.10`'s rule: evidence verifiable without being a copy of the operator's data."""
    # The other patterns this principal may touch. They are NOT the action's resource, which
    # legitimately appears on the receipt: a test whose "secret" was the resource would pass
    # against a kernel that wrote the whole scope out.
    secret = "customer:the-rest-of-adas-book-of-business"
    control = _control(store, clock)
    receipt = control.execute(
        _action(), lambda: {"ok": True}, "read:1", scope=_scope("customer:1", secret)
    )
    document = receipt.to_dict()
    assert document["scope_hash"] is not None
    assert document["scope_hash"].startswith("sha256:")
    import json

    assert secret not in json.dumps(document), "the scope's contents reached the evidence"


def test_T398a_a_scope_that_is_not_callable_is_refused(store, clock) -> None:
    """§5.6's third row, which had no test until the spec's third review round."""
    control = _control(store, clock)
    with pytest.raises(InvalidArgument) as caught:
        control.execute(_action(), lambda: {"ok": True}, "read:1", scope="not-callable")
    assert "scope" in str(caught.value)


def test_T398c_an_action_with_no_resource_is_out_of_scope(store, clock) -> None:
    """Fail closed, on `matches_shape`'s rule: an action carrying no resource does not match a
    scope that names resources. Treating it as in-scope would make the check optional for any
    caller who omitted the field."""
    control = _control(store, clock)
    with pytest.raises(ActionDenied) as caught:
        control.execute(
            _action(resource=None), lambda: {"ok": True}, "read:1", scope=_scope("customer:1")
        )
    assert caught.value.reason == "out_of_scope"


def test_T398d_a_scope_refusal_writes_no_approval_event_and_one_denial(store, clock) -> None:
    """SPEC-v0.9 §3.3.2's hazard, met by §5's refusal first.

    `_secure`'s `except ActionDenied` appends `APPROVAL_DENIED` unconditionally, so a scope
    refusal raised as an `ActionDenied` fabricates an approval denial for an action no human ever
    saw, and records `ACTION_DENIED` twice. Found by reading the events a refusal actually wrote,
    which is the only way it shows: the exception, the reason and the receipt were all correct.
    """
    control = _control(store, clock)
    with pytest.raises(ActionDenied):
        control.execute(
            _action("customer:90210"), lambda: {"ok": True}, "read:x", scope=_scope("customer:1")
        )
    kinds = [event.type.value for event in store.events()]
    assert kinds.count("ACTION_DENIED") == 1, kinds
    assert "APPROVAL_DENIED" not in kinds, (
        "a scope refusal must not fabricate an approval denial; no human was asked"
    )
    assert len(store.receipts()) == 1


def test_T394a_observe_mode_reports_which_refusal_it_would_have_made(store, clock) -> None:
    """An independent review found one hardcoded reason for both refusals.

    A deployment whose scope *source was down* read a counterfactual saying the record was not
    theirs. Observe mode exists to tell an operator what enforce mode would do, and reporting the
    wrong category is the one way it can be worse than useless.
    """
    observing = Control(
        policy=Policy.from_yaml(
            POLICY.replace("environment: prod", "environment: prod\nmode: observe"),
            source="<obs>",
        ),
        store=store,
        clock=clock,
        environment="prod",
    )

    def down(action: Action) -> dict[str, Any]:
        raise RuntimeError("the scope service is down")

    observing.execute(_action(), lambda: {"ok": True}, "read:1", scope=down)
    denied = [event for event in store.events() if event.type.value == "ACTION_DENIED"]
    assert denied and denied[-1].data["reason"] == "scope_unavailable", (
        "a provider that failed must not be reported as an out-of-scope record"
    )
    receipt = store.receipts()[-1]
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "scope_unavailable"

"""T310 to T325: M-of-N on distinct verified principals (SPEC-v0.8 §4.2).

N distinct resolved principals, counted once each, decided by the store's write and never by a
read followed by one. Written before the implementation: a red suite is the specification.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApproverIdentity, _granting_principal
from ctrlrun.control import Control, with_approval
from ctrlrun.errors import ActionDenied, ApprovalMismatch, ApprovalRequired, PolicyError
from ctrlrun.identity import IdentityContext, StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POLICY = """
schema: ctrlrun.policy/v6
actions:
  payments.refund:
    decision: approve
    approvals_required: 2
  payments.single:
    decision: approve
"""

KEY = "refund:EU-42"
AGENT = Principal(agent="ops-agent", user="ada")
ALICE = Principal(agent="human:alice", user="alice@example.com", issuer="https://issuer.example")
BOB = Principal(agent="human:bob", user="bob@example.com", issuer="https://issuer.example")

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _Fixed:
    def __init__(self, principal: Principal | None = None) -> None:
        self.principal = principal

    def resolve(self, context: IdentityContext) -> Principal | None:
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
    """Every shipped store: the count is decided by each one's own write (§4.3)."""
    if request.param == "in-memory":
        made = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"mofn_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock, *, verifying=True, policy=POLICY):
    return Control(
        Policy.from_yaml(policy),
        store,
        clock=clock,
        approver_identity=ApproverIdentity(_Fixed(ALICE)) if verifying else None,
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
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


def _grant(store, request_id, principal, *, approver=None, entitled=()):
    with _granting_principal(principal, entitled=entitled):
        return store.grant_approval(request_id, approver or f"mcp-operator:{principal.user}")


def _present(control, action, request_id, executor=None, key: str | None = KEY):
    with with_approval(request_id):
        return control.execute(action, executor or _Executor(), key)


# --- T310: N distinct principals, and not before ----------------------------------------------


def test_T310_the_approval_is_consumable_only_after_the_nth(store, clock):
    """§4.2. At N-1 the record is still `pending`, which is what the consume refuses on."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    first = _grant(store, request_id, ALICE)
    assert first is None, "a partial grant is not an Approval"
    assert str(store.get_approval(request_id).status) == "pending"

    executor = _Executor()
    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id, executor)
    assert refused.value.reason == "pending"
    assert executor.calls == 0
    assert store.get_effect(KEY) is None

    second = _grant(store, request_id, BOB)
    assert second is not None, "the Nth grant produces the Approval"
    assert str(store.get_approval(request_id).status) == "granted"

    receipt = _present(control, action, request_id, executor)
    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T311: G19, one principal counts once -----------------------------------------------------


def test_T311_a_second_grant_from_the_same_principal_counts_once(store, clock):
    """G19. The strings differ and the principal is the same, which is the whole point.

    Not an error and not a duplicate row: rejecting the second answer would make a human think
    their answer was lost, and counting it would be the defect.
    """
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    _grant(store, request_id, ALICE, approver="mcp-operator:alice")
    clock.advance(timedelta(minutes=1))
    again = _grant(store, request_id, ALICE, approver="cli:alice-from-a-different-door")

    assert again is None, "one principal cannot reach N alone"
    record = store.get_approval(request_id)
    assert str(record.status) == "pending"
    assert len(record.approvers) == 1
    assert record.approvers[0].granted_at == clock.now, "the entry moves, the count does not"


# --- T313 lives elsewhere -------------------------------------------------------------------
#
# SPEC-v0.8 §4.3's concurrency case needs the TCP proxy, the spawned child processes and the
# armed hold that open the window between the count's read and its write, and all three live in
# `tests/test_attempt_integrity.py`. It is
# `test_T313_two_processes_granting_in_the_window_produce_two_approvers`, and it fails against a
# compare-and-set on `status` alone, which is the shape this store had.
#
# Nothing in this file reproduces that window, and the fourth mutation shape in
# `CONTRIBUTING.md` is why
# that is written down rather than left to be noticed: every test here passes against a store
# with no compare-and-set whatever.


# --- T312: after N, a further grant is refused as it always was -------------------------------


def test_T312_a_grant_after_the_record_is_granted_is_refused(store, clock):
    """§4.2, by `check_answerable`, which `v0.1 §4.2` freezes."""
    control = _control(store, clock)
    request_id = _requested(control, _action(control))
    _grant(store, request_id, ALICE)
    _grant(store, request_id, BOB)

    with pytest.raises(ApprovalMismatch):
        _grant(store, request_id, Principal(agent="human:carol", user="carol@example.com"))


# --- T314, T315, T316: what does not count ----------------------------------------------------


def test_T314_the_requesters_own_yes_is_refused_at_consumption(store, clock):
    """§4.1 and §14.4: it is counted by the store and refused by `Control`, not uncounted.

    A first draft of §4.2 said this yes "does not count". Excluding it from the count looks
    stricter and is weaker: at N=1 the record would never reach `granted`, the consumption
    refusal would be `pending`, and **G18 would never fire** on the deployment shape it was
    written for. So the store counts every verified approver and the refusal is `Control`'s,
    which is one rule in one place.
    """
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    _grant(store, request_id, ALICE)
    _grant(store, request_id, Principal(agent=AGENT.agent, user=AGENT.user))

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id)

    assert refused.value.reason == "approver_is_requester"
    assert str(store.get_approval(request_id).status) == "granted", (
        "the store counted it; what refuses it is §4.1 at consumption"
    )


def test_T316_an_unverifiable_yes_does_not_count(store, clock):
    """§2.7: a grant a surface could not resolve records no approver, so it cannot be one of N."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    _grant(store, request_id, ALICE)
    store.grant_approval(request_id, "cli:local")

    record = store.get_approval(request_id)
    assert len(record.approvers) == 1, "an unverified answer is not a verified approver"
    assert str(record.status) == "pending"


# --- T317, T318: a denial, and expiry ---------------------------------------------------------


def test_T317_one_denial_denies_a_request_holding_n_minus_one(store, clock):
    """§4.2: a request that absorbs a no while it waits for yeses asked the wrong question."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, ALICE)

    store.deny_approval(request_id, "mcp-operator:bob")

    assert str(store.get_approval(request_id).status) == "denied"
    with pytest.raises(ActionDenied):
        _present(control, action, request_id)


def test_T318_grants_do_not_extend_the_requests_expiry(store, clock):
    """§4.2: expiry is the request's, and a partial grant does not renew it."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, ALICE)
    clock.advance(timedelta(hours=48))

    with pytest.raises(ApprovalMismatch) as refused:
        _grant(store, request_id, BOB)

    assert refused.value.reason == "expired"


# --- T319: N = 1 is 0.7.0 ---------------------------------------------------------------------


def test_T319_one_required_is_unchanged(store, clock):
    """The positive control for the whole item: absent means 1, and 1 is what 0.7.0 did."""
    control = _control(store, clock)
    action = _action(control, "payments.single", amount=5)
    request_id = _requested(control, action, "refund:EU-1")

    granted = _grant(store, request_id, ALICE)

    assert granted is not None, "at N=1 the first grant is the Approval, as it always was"
    executor = _Executor()
    receipt = _present(control, action, request_id, executor, key="refund:EU-1")
    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T320, T321: the key, and the deployment it needs -----------------------------------------


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.0", '"2"'])
def test_T320_a_malformed_threshold_is_refused_at_load(value):
    """§4.2, on `v0.7 §5.3`'s precedent: a malformed threshold fails the policy, not the action."""
    document = POLICY.replace("approvals_required: 2", f"approvals_required: {value}")

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "approvals_required" in str(refused.value)


def test_T320_the_key_needs_v6():
    document = POLICY.replace("ctrlrun.policy/v6", "ctrlrun.policy/v5")

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "approvals_required" in str(refused.value)


def test_T321_a_threshold_above_one_with_no_approver_identity_is_denied(store, clock):
    """§4.2: "distinct principals" has no referent in a deployment that verifies nobody.

    Denied at evaluation and not at load: the policy is loadable and correct, and what is missing
    is the `Control` it is deployed in, which the loader cannot see.
    """
    control = _control(store, clock, verifying=False)
    action = _action(control)
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        control.execute(action, executor, KEY)

    assert refused.value.reason == "approvals_unverifiable"
    assert "approvals_required" in str(refused.value)
    assert executor.calls == 0


# --- T322, T323: the widened return, and its callers ------------------------------------------


def test_T322_no_provider_wait_returns_none_for_a_partial_grant(store, clock):
    """`v0.1 §4.3`: `None` from `wait` means "answered, no", never "still waiting".

    Both shipped providers returned `grant_approval`'s result straight through, so a partial
    grant would have reached `Control` as a **denial** and `@protect(wait=True)` would have
    raised `ActionDenied` for a request a second human was still answering.
    """
    from ctrlrun.approval import ScriptedApprovalProvider, ScriptedOutcome
    from ctrlrun.errors import ApprovalTimeout

    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    provider = ScriptedApprovalProvider(store, [ScriptedOutcome.GRANT], clock=clock)
    with _granting_principal(ALICE), pytest.raises(ApprovalTimeout):
        provider.wait(request_id, timedelta(seconds=1))

    assert str(store.get_approval(request_id).status) == "pending"


# --- T324: a store that records one approver only ---------------------------------------------


def test_T324_a_store_that_records_one_approver_never_reaches_n(store, clock):
    """§4.5: it fails closed, and never behaves as N=1."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)

    store.grant_approval(request_id, "cli:local")
    store.grant_approval(request_id, "cli:local-again")

    record = store.get_approval(request_id)
    assert str(record.status) == "pending", "an unverified grant never reaches N"
    with pytest.raises(ApprovalMismatch):
        _present(control, action, request_id)


# --- T325: G19 in the catalogue ---------------------------------------------------------------


VERIFY_DOCUMENT = """
schema: ctrlrun.policy/v6
environment: production
actions:
  aaa.single:
    decision: approve
  zzz.refund:
    decision: approve
    approvals_required: 2
"""


def test_T325_G19_is_in_the_catalogue():
    from ctrlrun.verify import guarantees as reg

    assert "G19" in reg.BY_ID
    assert len(reg.BY_ID["G19"].title) <= 32


def test_T325_G19_passes_on_a_document_that_asks_for_two(tmp_path, monkeypatch):
    """The catalogue assertion above is not evidence: it grades nothing.

    G17 shipped with exactly that shape of coverage and its scenario was broken on every
    document that used the feature, because nothing ever ran the body. This runs it. The
    document also sorts an action needing **one** approval first by codepoint, so a scenario
    asking about whichever action `select` reached first would report "no action requires more
    than one approval" of a document that asks for two.
    """
    from ctrlrun.verify import run

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(VERIFY_DOCUMENT, encoding="utf-8")

    report = run(path)

    result = {guarantee.id: guarantee for guarantee in report.guarantees}["G19"]
    assert str(result.status) in ("Status.PASS", "pass"), (
        f"G19 reported {result.status}: {result.counterexample}"
    )
    assert result.detail.get("approvals_required") == 2


def test_T325_G19_is_not_applicable_only_where_every_action_takes_one(tmp_path, monkeypatch):
    """§11.7: the reason is a statement about the operator's document."""
    from ctrlrun.verify import guarantees as reg
    from ctrlrun.verify import run

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(VERIFY_DOCUMENT.replace("    approvals_required: 2\n", ""), encoding="utf-8")

    report = run(path)

    result = {guarantee.id: guarantee for guarantee in report.guarantees}["G19"]
    assert "not_applicable" in str(result.status).lower()
    assert result.reason == reg.NO_M_OF_N

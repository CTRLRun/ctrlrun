"""T310 to T325: M-of-N on distinct verified principals (SPEC-v0.8 §4.2).

N distinct resolved principals, counted once each, decided by the store's write and never by a
read followed by one. Written before the implementation: a red suite is the specification.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApproverIdentity, _granting_principal
from ctrlrun.control import Control, with_approval
from ctrlrun.errors import (
    ActionDenied,
    ApprovalMismatch,
    ApprovalRequired,
    InvalidArgument,
    PolicyError,
)
from ctrlrun.identity import IdentityContext, StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore
from ctrlrun.webhook import sign

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
    extra = {} if clock is None else {"clock": clock}
    return Control(
        Policy.from_yaml(policy),
        store,
        **extra,
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


def test_T315_an_unentitled_yes_is_refused_at_consumption(store, clock):
    """§3.8, §14.4. §10.4 has named this test since the spec was written and it did not exist.

    The same shape as T314 and for the same reason: the store counts every verified approver,
    and the refusal is `Control`'s, by its own reason, naming the control. Both of a request's
    two approvers here are entitled to nothing, so the count reaches two and consumption still
    refuses: a threshold is not a way to outvote an entitlement.
    """
    control = _control(store, clock, policy=GATED_POLICY)
    action = _action(control)
    request_id = _requested(control, action)

    _grant(store, request_id, ALICE)
    _grant(store, request_id, BOB)

    record = store.get_approval(request_id)
    assert str(record.status) == "granted", "two distinct principals reached the threshold"
    assert len(record.approvers) == 2

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id)

    assert refused.value.reason == "approver_unentitled"
    assert str(store.get_approval(request_id).status) == "granted", (
        "the approval is left granted; nothing is consumed for an action that did not run"
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


@pytest.mark.parametrize("threshold", [0, -3, "2", 1.0, True])
def test_T320_a_corrupted_threshold_is_refused_on_read_not_clamped(threshold):
    """§4.2, §12. The other two columns raise on a corrupted value; this one was coerced.

    `count_grant` clamped with `max(1, ...)` and the SQLite read turned `0` into `1` with
    `or 1`, so a row tampered to nothing read back as an ordinary single-approval request.
    A store is the one place this can arrive from, which is the threat model `approvers`
    is already written against.
    """
    from ctrlrun.approval import ApprovalRequest

    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(InvalidArgument):
        ApprovalRequest(
            request_id="apr_corrupt",
            action_hash="sha256:00",
            action=None,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
            approvals_required=threshold,
        )


# --- T323: the three callers of the widened return ---------------------------------------------


def test_T323_the_cli_says_how_many_more_are_needed(tmp_path, clock):
    """§4.4. `None` means recorded and short of N, and each caller says so in its own idiom.

    Against SQLite on disk, because the CLI opens the store by URL.
    """
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    # The real clock on both sides: the CLI opens its own store and reads its own `now`, so a
    # request pinned to a frozen test clock is already expired by the time it runs.
    database = tmp_path / "state.db"
    opened = SQLiteStateStore(database)
    try:
        control = _control(opened, None)
        request_id = _requested(control, _action(control))
    finally:
        opened.close()

    result = CliRunner().invoke(
        main,
        ["approve", request_id, "--store-url", f"sqlite:///{database}"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "0 of 2 approvals" in result.output
    # §2.6: and it says why this answer will never be one of the two, rather than leaving an
    # operator to infer it from a number that does not move.
    assert "no verified approver" in result.output


def test_T323_the_webhook_answers_200_with_the_count(store, clock):
    """§4.4's caller table: 200, because the answer *was* recorded, and the body says what of."""
    from ctrlrun.webhook import handle_inbound

    control = _control(store, clock)
    request_id = _requested(control, _action(control))
    record = store.get_approval(request_id)
    body = json.dumps(
        {
            "request_id": request_id,
            "action_hash": record.action_hash,
            "decision": "grant",
            "approver": "slack:U123",
        }
    ).encode()
    secret = "s" * 32
    status, text = handle_inbound(
        store,
        request_id,
        body,
        sign(body, secret, at=datetime.now(UTC)),
        secret=secret,
        replay_window=timedelta(seconds=300),
    )

    assert status == 200
    assert text.startswith("recorded: 0 of 2 approvals"), (
        f"the sender was told {text!r} for an answer that moved nothing toward the threshold"
    )
    # Zero, and the body says why zero: this door verifies nobody, so its answer is recorded
    # and counts for nothing. A bare count here reads as a broken counter.
    assert "counts toward no threshold" in text
    assert str(store.get_approval(request_id).status) == "pending"


# --- T322b: what observe mode says about a threshold it cannot verify -------------------------


OBSERVE_POLICY = """
schema: ctrlrun.policy/v6
mode: observe
actions:
  payments.refund:
    decision: approve
    approvals_required: 2
"""


def test_T322_observe_mode_reports_the_threshold_it_could_not_verify(clock):
    """§4.2, §11.1. Observe mode exists to say what enforce mode would have done.

    It said `approval_required`, which is "a human would have been asked". Enforce mode denies
    every one of these before anybody is asked, because a threshold above one with no approver
    identity has no referent for "distinct principals". An operator piloting a two-approver
    policy got no signal that the thing they were piloting denies everything.
    """
    store = InMemoryStateStore(clock=clock)
    control = Control(
        Policy.from_yaml(OBSERVE_POLICY),
        store,
        clock=clock,
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )
    executor = _Executor()

    receipt = control.execute(_action(control), executor, KEY)

    assert executor.calls == 1, "observe mode runs the action; that is what it is"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "approvals_unverifiable"

    from ctrlrun.reporting import stats_document

    counted = stats_document(list(store.receipts()), mode="observe", boundary=None)
    assert counted["would_have_been_blocked"] == 1, f"the refusal is in no stats bucket: {counted}"


def test_T322_observe_mode_still_says_approval_required_where_one_yes_would_do(clock):
    """The other half: a threshold of one is the ordinary case and keeps the ordinary reason."""
    store = InMemoryStateStore(clock=clock)
    control = Control(
        Policy.from_yaml(OBSERVE_POLICY.replace("    approvals_required: 2\n", "")),
        store,
        clock=clock,
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )

    receipt = control.execute(_action(control), _Executor(), KEY)

    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "approval_required"


# --- T324b: the read-back, because pinning through a context variable can be lost -------------


class _OwnRequestProvider:
    """A third-party provider that builds its own `ApprovalRequest`, as the protocol allows.

    `ApprovalProvider` and `ApprovalRequest` are both exported from `ctrlrun`, `build_request`
    is package-internal, and nothing obliges a provider to use it. This one carries the fields
    it knows about and drops the two v0.8 added, which is what any provider written against
    0.7.0 does.
    """

    def __init__(self, store, clock):
        self._store = store
        self._clock = clock

    def request(self, action, ttl):
        from ctrlrun.approval import ApprovalRequest

        now = self._clock.now
        request = ApprovalRequest(
            request_id="apr_own_request_provider",
            action_hash=action.action_hash,
            action=action,
            created_at=now,
            expires_at=now + ttl,
        )
        self._store.put_approval_request(request)
        return request

    def wait(self, request, timeout):  # pragma: no cover - never reached
        raise AssertionError("the request pass refuses before anybody is asked")


class _DroppingStore(InMemoryStateStore):
    """A store that accepts the request and does not persist what v0.8 pins.

    SPEC-v0.8 §4.5 says there is no path on which a store that ignores the column behaves as
    though N were 1. There was: this one.
    """

    def put_approval_request(self, request):
        return super().put_approval_request(
            replace(request, required_roles=(), approvals_required=1)
        )


GATED_POLICY = """
schema: ctrlrun.policy/v6
controls:
  card-data-handling:
    title: t
    approver_role: payments-owner
actions:
  payments.refund:
    decision: approve
    approvals_required: 2
    controls: [card-data-handling]
"""


def test_T324_a_provider_that_drops_the_pinned_fields_is_refused(clock):
    """§4.5, §3.3. The review's first reproduction: it committed, approved once, by nobody
    holding the role."""
    store = InMemoryStateStore(clock=clock)
    control = Control(
        Policy.from_yaml(GATED_POLICY),
        store,
        clock=clock,
        approvals=_OwnRequestProvider(store, clock),
        approver_identity=ApproverIdentity(_Fixed(ALICE)),
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )
    action = _action(control)
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        control.execute(action, executor, KEY)

    assert refused.value.reason == "approval_unrecorded"
    assert executor.calls == 0
    assert "threshold of 2" in str(refused.value) or "roles" in str(refused.value)
    # The request the provider already recorded is withdrawn, so a human answering it cannot
    # leave a grant some later call spends with nothing compared (v0.7 §6.4).
    record = store.get_approval("apr_own_request_provider")
    assert record is not None and str(record.status) == "denied"


def test_T324_a_store_that_drops_the_pinned_columns_is_refused(clock):
    """§4.5's sentence, made true. The review's second reproduction."""
    store = _DroppingStore(clock=clock)
    control = Control(
        Policy.from_yaml(GATED_POLICY),
        store,
        clock=clock,
        approver_identity=ApproverIdentity(_Fixed(ALICE)),
        identity=StaticIdentityProvider(agent=AGENT.agent, user=AGENT.user),
    )
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), executor, KEY)

    assert refused.value.reason == "approval_unrecorded"
    assert executor.calls == 0
    assert store.get_effect(KEY) is None, "nothing was reserved for a request nobody may answer"


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

"""T297 to T309: entitlement from the control registry (SPEC-v0.8 §3).

A control names the role that may answer an approval the decision already required. Omission is
not entitlement, and a control naming no role gates nobody: two sentences that mean opposite
things, and the pair R2 exists for.
"""

from __future__ import annotations

import logging
import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import (
    ApproverIdentity,
    RequiredRole,
    VerifiedApprover,
    _granting_principal,
    entitled_controls,
    roles_held,
    unsatisfied,
)
from ctrlrun.control import Control, with_approval
from ctrlrun.errors import ApprovalMismatch, ApprovalRequired, InvalidArgument, PolicyError
from ctrlrun.identity import IdentityContext, StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.receipt import EventType
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POLICY = """
schema: ctrlrun.policy/v6
controls:
  card-data-handling:
    title: Cardholder data changes are approved by a named owner
    source: PCI DSS 7.2.1
    approver_role: payments-owner
  separation-of-duties:
    title: A second pair of eyes
    approver_role: second-pair
  change-log:
    title: Changes are written down
actions:
  payments.refund:
    decision: approve
    controls: [card-data-handling]
  payments.large:
    decision: approve
    controls: [card-data-handling, separation-of-duties]
  payments.logged:
    decision: approve
    controls: [change-log]
"""

KEY = "refund:EU-42"
UNENTITLED = "approver_unentitled"
AGENT = Principal(agent="ops-agent", user="ada")

#: The approver, and the roles it holds, vary per test; this is the shape they share.
APPROVER = Principal(agent="human:bob", user="bob@example.com", issuer="https://issuer.example")

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _Fixed:
    def __init__(self, principal: Principal | None) -> None:
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
    """Every shipped store: `required_roles` is written and read by each of them.

    Item 2's mutation table found what a single-store file hides, and the column this item pins
    the roles in is written by the same three write paths.
    """
    if request.param == "in-memory":
        made = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"entitle_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock, *, roles_claim="roles", policy=POLICY, provider=None):
    identity = (
        None
        if provider is False
        else ApproverIdentity(_Fixed(provider or APPROVER), roles_claim=roles_claim)
    )
    return Control(
        Policy.from_yaml(policy),
        store,
        clock=clock,
        approver_identity=identity,
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


def _grant(store, request_id, *, entitled=(), principal=APPROVER):
    with _granting_principal(principal, entitled=entitled):
        return store.grant_approval(request_id, "mcp-operator:bob")


def _present(control, action, request_id, executor=None, key: str | None = KEY):
    with with_approval(request_id):
        return control.execute(action, executor or _Executor(), key)


def _invalidated(store, request_id):
    return [
        event
        for event in store.events()
        if event.type is EventType.APPROVAL_INVALIDATED and event.approval_id == request_id
    ]


# --- T297, T298: the refusal names the control, and the control passes -------------------------


def test_T297_an_unentitled_approver_is_refused_and_the_control_is_named(store, clock):
    """§3.7: the reason, the message and the event all name the control and the role.

    Asserted by value and never by type: an entitlement refusal and an unverified one share
    `ApprovalMismatch`, and a test asserting only the type cannot tell which guard fired.
    """
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, entitled=())
    executor = _Executor()

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id, executor)

    assert refused.value.reason == UNENTITLED
    assert "payments-owner" in str(refused.value)
    assert "card-data-handling" in str(refused.value)
    assert executor.calls == 0
    data = _invalidated(store, request_id)[-1].data
    assert data["control"] == "card-data-handling"
    assert data["role"] == "payments-owner"
    assert str(store.get_approval(request_id).status) == "granted"


def test_T298_an_entitled_approver_approves_and_the_action_runs(store, clock):
    """The positive control. A check that refused everything would pass T297 alone."""
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, entitled=("card-data-handling",))
    executor = _Executor()

    receipt = _present(control, action, request_id, executor)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T299: the claim shapes -------------------------------------------------------------------


def test_T299_a_tuple_claim_entitles_for_each_of_its_roles():
    """§3.4: the case `ClaimValue`'s amendment exists for."""
    holder = Principal(agent="human:bob", claims={"roles": ["payments-owner", "second-pair"]})

    held = roles_held(holder, "roles")

    assert held == frozenset({"payments-owner", "second-pair"})
    required = (
        RequiredRole("card-data-handling", "payments-owner"),
        RequiredRole("separation-of-duties", "second-pair"),
    )
    assert entitled_controls(required, held) == (
        "card-data-handling",
        "separation-of-duties",
    )
    assert unsatisfied(required, entitled_controls(required, held)) is None


def test_T299_a_jwt_array_claim_is_carried_rather_than_dropped():
    """§3.4's first half, at the provider: an array claim arrived *absent* before v0.8."""
    principal = Principal(agent="human:bob", claims={"roles": ["payments-owner"]})

    assert principal.claims["roles"] == ("payments-owner",)


def test_T299b_a_tuple_claim_survives_the_json_round_trip(store, clock):
    """§3.4's second half: JSON has no tuple, so a stored claim comes back a **list**.

    Unamended, `_frozen_claims` refused a list, so every stored action and every receipt carrying
    an array claim raised on read, and for a receipt that breaks `Receipt.from_dict`'s
    never-raises contract by name (`v0.7 §6.11`).
    """
    from ctrlrun.receipt import Receipt, verify_chain

    holder = Principal(agent="ops-agent", user="ada", claims={"roles": ["payments-owner", "x"]})
    control = Control(
        Policy.from_yaml(POLICY),
        store,
        clock=clock,
        identity=StaticIdentityProvider(agent="ops-agent", user="ada"),
    )
    action = Action(
        name="payments.logged",
        arguments={"amount": 1},
        principal=holder,
        environment=control.environment,
    )
    request_id = _requested(control, action, "refund:EU-99")
    store.grant_approval(request_id, "cli:local")
    _present(control, action, request_id, key="refund:EU-99")

    stored = store.get_approval(request_id)
    assert stored.request.action.principal.claims["roles"] == ("payments-owner", "x")

    written = [r for r in store.receipts() if r.action_id == action.action_id][-1]
    assert written.principal.claims["roles"] == ("payments-owner", "x")
    reparsed = Receipt.from_dict(written.to_dict())
    assert reparsed.principal.claims["roles"] == ("payments-owner", "x")
    report = verify_chain(store)
    assert report.ok, report.breaks


# --- T300, T301: the two omissions ------------------------------------------------------------


def test_T300_a_principal_whose_claims_lack_the_role_is_not_entitled(store, clock, caplog):
    """Omission A (§3.4). A missing claim is a statement about a person, and the kernel refuses
    to invent one; the warning exists because the commonest cause is a provider nobody told."""
    bare = Principal(agent="human:bob", user="bob@example.com")
    control = _control(store, clock, provider=bare)
    action = _action(control)
    request_id = _requested(control, action)
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        _grant(store, request_id, entitled=(), principal=bare)

        with pytest.raises(ApprovalMismatch) as refused:
            _present(control, action, request_id)

    assert refused.value.reason == UNENTITLED
    # The docstring above claimed a warning existed and nothing asserted one, which is the
    # shape of false green this repository keeps finding: a sentence about behaviour, in a
    # test that would pass if the behaviour were deleted. A principal with no claims at all
    # holds no role and there is nothing silent about that, so what is asserted is the
    # refusal's own record: the event names the control and the role (§3.7).
    assert not [record for record in caplog.records if "names no role" in record.message], (
        "an absent claim is not a misconfiguration to warn about"
    )
    invalidated = _invalidated(store, request_id)
    assert invalidated and invalidated[0].data["control"] == "card-data-handling"
    assert invalidated[0].data["role"] == "payments-owner"


def test_T300_a_claim_in_a_shape_that_carries_no_role_warns(store, clock, caplog):
    """§3.4's *silent* failure, which is the one the warning is for.

    The issuer did send the claim. It sent an integer, every rule in `roles_held` reads it as
    absent, and without this the operator sees an approval refused for a role their identity
    provider believes it is sending.
    """
    numeric = Principal(agent="human:bob", user="bob@example.com", claims={"roles": 7})
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        held = roles_held(numeric, "roles")

    assert held == frozenset()
    assert [record for record in caplog.records if "names no role" in record.message], (
        f"no warning named the shape; records were {[r.message for r in caplog.records]}"
    )
    assert "'roles'" in caplog.text and "int" in caplog.text


def test_T301_a_control_naming_no_role_gates_nobody(store, clock):
    """Omission B (§3.5), and the opposite answer.

    Deleting either of T300 or T301 leaves the other passing under a reading that is wrong in
    the other direction, which is the pair R2 exists for.
    """
    control = _control(store, clock)
    action = _action(control, "payments.logged", amount=5)
    request_id = _requested(control, action, "refund:EU-77")
    _grant(store, request_id, entitled=())
    executor = _Executor()

    receipt = _present(control, action, request_id, executor, key="refund:EU-77")

    assert executor.calls == 1
    assert str(receipt.result) == "committed"


# --- T302, T303: what a role is and is not ----------------------------------------------------


@pytest.mark.parametrize("value", [3, True, "", 0])
def test_T302_an_int_a_bool_or_an_empty_claim_entitles_nothing(value):
    """§3.4: never coerced, never stringified. `True` is not the role `"True"`."""
    holder = Principal(agent="human:bob", claims={"roles": value})

    assert roles_held(holder, "roles") == frozenset()


@pytest.mark.parametrize("value", ["payments-owner ", "PAYMENTS-OWNER", "payments", "owner"])
@pytest.mark.parametrize("shape", ["string", "list"])
def test_T303_matching_is_byte_for_byte(value, shape):
    """§3.4: no folding, no trimming, no prefix matching, no pattern grammar.

    **Both claim shapes**, because they are two branches. The first version parametrised only the
    list shape, and the mutation table found what that hid: folding and trimming the *string*
    branch left every case green, since nothing exercised it.
    """
    claim = value if shape == "string" else [value]
    holder = Principal(agent="human:bob", claims={"roles": claim})
    required = (RequiredRole("card-data-handling", "payments-owner"),)

    assert entitled_controls(required, roles_held(holder, "roles")) == ()


@pytest.mark.parametrize("shape", ["string", "list"])
def test_T303_the_exact_role_matches_in_both_shapes(shape):
    """The positive control for the pair above: a matcher that matched nothing would pass it."""
    claim = "payments-owner" if shape == "string" else ["payments-owner"]
    holder = Principal(agent="human:bob", claims={"roles": claim})
    required = (RequiredRole("card-data-handling", "payments-owner"),)

    assert entitled_controls(required, roles_held(holder, "roles")) == ("card-data-handling",)


# --- T304: half a check fails closed ----------------------------------------------------------


def test_T304_no_roles_claim_configured_entitles_nobody(store, clock):
    """§3.4's last bullet: a deployment naming roles in its policy and no claim to read them
    from has configured half a check."""
    holder = Principal(agent="human:bob", claims={"roles": ["payments-owner"]})

    assert roles_held(holder, None) == frozenset()


def test_T304_a_provider_that_carries_no_claims_entitles_nobody(store, clock):
    """The shipped case: `HeaderIdentityProvider` carries no claims at all, by design, so the
    operator server run with `--principal-header` produces a verified-but-unentitled approver."""
    from ctrlrun.identity import HeaderIdentityProvider

    provider = HeaderIdentityProvider(agent_header="x-agent", user_header="x-user")
    resolved = provider.resolve(
        IdentityContext(
            action="payments.refund",
            environment="production",
            headers={"x-agent": "human:bob", "x-user": "bob@example.com"},
        )
    )

    assert resolved is not None
    assert roles_held(resolved, "roles") == frozenset()


# --- T305: every cited control must be satisfied ----------------------------------------------


def test_T305_holding_one_of_two_required_roles_is_refused(store, clock):
    """§3.6: all-of and never any-of, which would let the weakest control decide who may answer.

    The test asserts the refusal **and the control named**, so it cannot pass under an any-of
    reading by asserting only that something was refused.
    """
    control = _control(store, clock)
    action = _action(control, "payments.large", amount=900000)
    request_id = _requested(control, action, "refund:EU-88")
    _grant(store, request_id, entitled=("card-data-handling",))

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id, key="refund:EU-88")

    assert refused.value.reason == UNENTITLED
    assert "separation-of-duties" in str(refused.value)
    assert _invalidated(store, request_id)[-1].data["control"] == "separation-of-duties"


# --- T306: the roles are pinned at request time -----------------------------------------------


def test_T306_the_roles_in_force_at_the_request_are_the_ones_applied(store, clock):
    """§3.3, on `v0.6 §7.1`'s rule: the approval binds to what the human was shown.

    The policy's role changes between the request and the presentation. The approval was granted
    entitled for the control the *request* pinned, and it is still consumable: a `Control` loaded
    with a moved policy does not re-derive what the human was asked under.
    """
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, entitled=("card-data-handling",))

    moved = POLICY.replace("approver_role: payments-owner", "approver_role: someone-else")
    after = _control(store, clock, policy=moved)
    executor = _Executor()

    receipt = _present(after, action, request_id, executor)

    assert executor.calls == 1
    assert str(receipt.result) == "committed"
    assert store.get_approval(request_id).request.required_roles == (
        RequiredRole("card-data-handling", "payments-owner"),
    )


# --- T307: two defences, two tests ------------------------------------------------------------


def test_T307_the_consume_side_refuses_a_row_a_store_wrote_without_checking(store, clock):
    """§3.8: the guarantee is the consumption check, and this is the case the grant-side
    courtesy cannot reach.

    The row is written entitled for a control that is not the one the request pinned, which is
    what a store that recorded whatever it was handed would hold.
    """
    control = _control(store, clock)
    action = _action(control)
    request_id = _requested(control, action)
    _grant(store, request_id, entitled=("some-other-control",))

    with pytest.raises(ApprovalMismatch) as refused:
        _present(control, action, request_id)

    assert refused.value.reason == UNENTITLED


# --- T307b: the shapes a column can hold, and the one a reader must survive -------------------


@pytest.mark.parametrize("given", ["card-data-handling", 7, {"card-data-handling": 1}, [""], [3]])
def test_T307_a_row_whose_entitled_is_not_a_list_of_ids_is_refused(given):
    """§3.6. `tuple("c1")` is three control ids that entitle nothing and refuse nothing.

    The guard lives in `__post_init__`, and the defect this covers was a caller that wrapped
    the value in `tuple(...)` **before** the guard saw it, so a string arrived as a tuple of
    non-empty strings and every check downstream passed on nonsense. A mapping is refused for
    the same reason: iterating one yields its keys.
    """
    document = {"agent": "human:bob", "granted_at": "2026-01-01T00:00:00+00:00", "entitled": given}
    with pytest.raises(InvalidArgument):
        VerifiedApprover.from_dict(document)


def test_T307_a_string_cannot_be_handed_to_the_granting_context():
    """The same hazard on the write side (§2.5)."""
    with pytest.raises(InvalidArgument), _granting_principal(APPROVER, entitled="card-data"):
        pass  # pragma: no cover - the context manager refuses before the body


def test_T307_a_receipt_carrying_a_tampered_claim_still_parses():
    """`v0.7 §6.11`: `Receipt.from_dict` never raises, and §3.4 gave claims a new refusal.

    `_frozen_claims` refuses an array of numbers, which is a shape JSON holds and a tampered
    row can carry. Running it inside `from_dict` meant one bad field blinded every reader of
    the chain rather than costing that field, which is the opposite of what a receipt is for.
    """
    from ctrlrun.receipt import _claims_of

    kept = _claims_of(
        {"roles": [1, 2], "team": ["payments"], "level": 3, "on": True, "nested": {"a": 1}, "": "x"}
    )

    assert kept == {"team": ("payments",), "level": 3, "on": True}
    assert Principal(agent="a", user=None, claims=kept).claims["team"] == ("payments",)


# --- the vocabulary check that stops this being found a third time ----------------------------


def test_every_approval_refusal_reason_is_counted_by_stats():
    """`ctrlrun stats` must have a bucket for every reason an approval can be refused with.

    Enumerated from `approval.py` rather than restated here, which is the whole point: a set
    written out by hand is a set somebody adds a reason without. It has been missed twice. Item
    2 shipped `approval_denied` in no bucket, so an observe-mode run in which a **human said
    no** was counted nowhere; items 3 and 4 then coined `approver_unentitled` and
    `approvals_unverifiable` and did the same thing again. This test fails on the next one.
    """
    import re

    from ctrlrun import approval
    from ctrlrun.receipt import BLOCKED_BY_STATE

    source = pathlib.Path(approval.__file__).read_text(encoding="utf-8")
    names = re.findall(r"^([A-Z][A-Z_]*): Final = \"([a-z_]+)\"", source, re.MULTILINE)
    reasons = {
        value
        for name, value in names
        if name.startswith(("APPROVAL_", "APPROVER_", "APPROVALS_", "HASH_", "UNKNOWN_"))
    }
    assert reasons, "the enumeration found nothing, so this test is asserting nothing"

    missing = sorted(reason for reason in reasons if reason not in BLOCKED_BY_STATE)
    assert not missing, (
        f"these refusal reasons are in no `ctrlrun stats` bucket: {missing}. An observe-mode "
        "run that would have refused for one of them reports would_have_been_blocked = 0, "
        "which is a report saying nothing happened about the thing that did"
    )


# --- T308: the registry loads, and refuses what it must ---------------------------------------


@pytest.mark.parametrize("role", [" payments-owner", "payments-owner ", "payments-owner\n"])
def test_T308_a_padded_approver_role_is_refused_at_load(role):
    """A role that can never match is a control that refuses everything and explains nothing."""
    document = (
        "schema: ctrlrun.policy/v6\n"
        "controls:\n"
        "  card-data-handling:\n"
        "    title: t\n"
        f'    approver_role: "{role}"\n'
        "actions:\n"
        "  payments.refund:\n"
        "    decision: approve\n"
        "    controls: [card-data-handling]\n"
    )
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "whitespace" in str(refused.value)


def test_T308_the_registry_carries_approver_role_under_v6():
    policy = Policy.from_yaml(POLICY)

    assert policy.controls["card-data-handling"].approver_role == "payments-owner"
    assert policy.controls["change-log"].approver_role is None


@pytest.mark.parametrize("value", ["", "  ", "[]", "3"])
def test_T308_a_malformed_approver_role_is_refused_naming_the_key(value):
    document = POLICY.replace("approver_role: payments-owner", f"approver_role: {value}")

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "approver_role" in str(refused.value)


def test_T308_the_key_needs_v6():
    """`v0.6 §9.5`'s rule: an older reader refuses the document rather than ignoring the key,
    because a reader that ignored this would gate nobody and report a deployment as checking."""
    document = POLICY.replace("ctrlrun.policy/v6", "ctrlrun.policy/v5")

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "approver_role" in str(refused.value)
    assert "ctrlrun.policy/v6" in str(refused.value)


def test_T308_an_unknown_key_on_a_control_entry_is_still_refused():
    """The closed key set (`v0.6 §7.3`) grew by one and is still closed."""
    document = POLICY.replace(
        "    approver_role: payments-owner", "    approver_role: payments-owner\n    owner: bob"
    )

    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


# --- T309: G17 in verify ----------------------------------------------------------------------


GATED_DOCUMENT = """
schema: ctrlrun.policy/v6
environment: production
controls:
  card-data-handling:
    title: Cardholder data changes are approved by a named owner
    approver_role: payments-owner
actions:
  aaa.untouched:
    decision: approve
  zzz.refund:
    decision: approve
    controls: [card-data-handling]
"""


def test_T309_G17_passes_on_a_document_that_gates_entitlement(tmp_path, monkeypatch):
    """**The test that was missing, and its absence let a broken scenario ship.**

    Nothing anywhere graded G17 as PASS: every other assertion was about its `N/A` count, so the
    entire scenario body was uncovered and `ctrlrun verify` exited 1 on any document that used
    the feature item 3 ships. The scenario built its request straight from the provider, outside
    `Control._presented`, so nothing was pinned and nothing was refused.

    The document also puts an **ungated** approve action first by codepoint, which is the second
    thing the scenario got wrong: asking about whichever action `select` reached first reported
    "no cited control names an approver role" of a document that gates one.
    """
    from ctrlrun.verify import run

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(GATED_DOCUMENT, encoding="utf-8")

    report = run(path)

    results = {guarantee.id: guarantee for guarantee in report.guarantees}
    assert str(results["G17"].status) in ("Status.PASS", "pass"), (
        f"G17 reported {results['G17'].status}: {results['G17'].counterexample}"
    )


def test_T309_G17_is_not_applicable_only_where_the_document_gates_nothing(tmp_path, monkeypatch):
    """The other half: the `N/A` reason is about the document, per §11.7."""
    from ctrlrun.verify import run

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(
        GATED_DOCUMENT.replace("    controls: [card-data-handling]\n", ""), encoding="utf-8"
    )

    report = run(path)

    result = {guarantee.id: guarantee for guarantee in report.guarantees}["G17"]
    assert "not_applicable" in str(result.status).lower()
    assert result.reason == "no cited control names an approver role"


def test_T309_G17_is_in_the_catalogue_with_a_document_true_na_reason():
    from ctrlrun.verify import guarantees as reg

    assert "G17" in reg.BY_ID
    assert reg.BY_ID["G17"].title == "an unentitled approver refused"
    assert reg.NO_APPROVER_ROLE == "no cited control names an approver role"

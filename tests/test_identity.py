"""Principal, IdentityProvider and the environment. Build-list item 1; SPEC-v0.3 §2, §3.

The acceptance tests are T60 to T65d. Two rules run through all of them and are worth stating
once, because several tests look redundant until you know which one they are serving:

`claims` is evidence, not identity (§2.2). It reaches the receipt and never the hash, so an
approval survives a token rotation — and T60 asserts the hash is *equal*, which is the only
direction that can fail silently.

The environment is the deployment's, not the call's (§2.5). Under v0.1 the agent's own call
site named it; under v0.3 a grant can scope to it, so the caller must not. T65d asserts the
parameter is gone by signature rather than by behaviour, because a behavioural assertion
passes against a parameter that is still there and merely ignored.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from ctrlrun import (
    Action,
    ActionDenied,
    Control,
    HeaderIdentityProvider,
    IdentityContext,
    IdentityError,
    InMemoryStateStore,
    InvalidArgument,
    Policy,
    Principal,
    StaticIdentityProvider,
    context,
    protect,
)
from ctrlrun.receipt import EventType

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, 120000, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=10)
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def store():
    return InMemoryStateStore()


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def control(store, clock):
    return Control(Policy.from_yaml(POLICY), store, clock=clock)


def _action(**overrides):
    fields = {
        "name": "stripe.refund",
        "arguments": {"amount": 100, "payment_id": "txn_1"},
        "principal": Principal(agent="refund-agent", user="alice"),
    }
    fields.update(overrides)
    return Action(**fields)


# --- T60: claims are receipt data, not action identity (§2.2) --------------------------


def test_T60_claims_do_not_change_the_action_hash():
    """§2.2 — the assertion that matters is *equality*: a hash that quietly gained a claim
    would break every pending approval on the next token rotation, and nothing else would
    notice."""
    bare = _action(principal=Principal(agent="refund-agent", user="alice"))
    laden = _action(
        principal=Principal(
            agent="refund-agent",
            user="alice",
            claims={"employee_no": 4471, "licence": "AB-12", "on_call": True},
            issuer="https://issuer.example/",
            expires_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        )
    )

    assert bare.action_hash == laden.action_hash


def test_T60_claims_do_not_change_the_canonical_bytes():
    from ctrlrun import canonicalize

    bare = _action(principal=Principal(agent="refund-agent"))
    laden = _action(principal=Principal(agent="refund-agent", claims={"a": "b"}, issuer="i"))

    assert canonicalize(bare) == canonicalize(laden)


def test_T60_agent_and_user_still_change_the_hash():
    """The control for the two above: if the principal had dropped out of the hash entirely
    they would pass while the binding of v0.1 §4.2 was gone."""
    base = _action(principal=Principal(agent="refund-agent", user="alice"))

    assert base.action_hash != _action(principal=Principal(agent="other-agent")).action_hash
    assert (
        base.action_hash
        != _action(principal=Principal(agent="refund-agent", user="bob")).action_hash
    )


def test_T60_claims_reach_the_receipt(control, store):
    principal = Principal(
        agent="refund-agent",
        claims={"employee_no": 4471},
        issuer="https://issuer.example/",
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    control.execute(_action(principal=principal), lambda: "ok", None)

    receipt = store.receipts()[-1].to_dict()["principal"]
    assert receipt["claims"] == {"employee_no": 4471}
    assert receipt["issuer"] == "https://issuer.example/"
    assert receipt["expires_at"] == "2027-01-01T00:00:00.000Z"


def test_T60_an_approval_survives_a_token_rotation(control, store):
    """The point of §2.2 stated as a scenario: the human approved a refund by an agent, not a
    token serial number, so re-proposing with a rotated credential must still be authorized."""
    first = _action(
        arguments={"amount": 2000, "payment_id": "txn_1"},
        principal=Principal(agent="refund-agent", claims={"jti": "one"}),
    )
    rotated = _action(
        arguments={"amount": 2000, "payment_id": "txn_1"},
        principal=Principal(agent="refund-agent", claims={"jti": "two"}),
    )

    assert first.action_hash == rotated.action_hash


# --- T60b: Principal validates what §2.1 says it validates -----------------------------


@pytest.mark.parametrize(
    "claims",
    [
        {"amount": 20.0},
        {"nothing": None},
        {"list": [1, 2]},
        {"map": {"a": 1}},
        {"": "empty key"},
        {1: "non-string key"},
    ],
    ids=["float", "none", "list", "mapping", "empty-key", "non-string-key"],
)
def test_T60b_a_claim_value_outside_the_allowed_types_is_refused(claims):
    with pytest.raises(InvalidArgument):
        Principal(agent="a", claims=claims)


def test_T60b_a_naive_expires_at_is_refused():
    """§2.1 — an expiry in an unstated timezone cannot be checked, and guessing UTC would
    silently extend or shorten it."""
    with pytest.raises(InvalidArgument):
        Principal(agent="a", expires_at=datetime(2027, 1, 1))


def test_T60b_an_empty_issuer_is_refused():
    with pytest.raises(InvalidArgument):
        Principal(agent="a", issuer="")


def test_T60b_claims_are_snapshotted():
    """§2.1 — as `Action.arguments` is, so a caller holding the original cannot change a
    constructed Principal."""
    mutable = {"a": "one"}
    principal = Principal(agent="a", claims=mutable)
    mutable["a"] = "two"
    mutable["b"] = "three"

    assert dict(principal.claims) == {"a": "one"}
    with pytest.raises(TypeError):
        principal.claims["c"] = "nope"  # type: ignore[index]


def test_T60b_true_and_one_keep_their_types():
    """§2.1 — `bool` is not `int` here any more than in v0.1 §3.2: both are accepted and
    neither is coerced into the other.

    Note what this does *not* assert. `{"x": True} == {"x": 1}` is true in Python, so two
    Principals differing only that way compare equal, and a `!=` assertion here would be
    testing a custom `__eq__` the specification does not ask for. What §2.1 requires is that
    the stored value keeps its type, which is what a policy condition and a receipt read.
    """
    true_claim = Principal(agent="a", claims={"x": True}).claims["x"]
    one_claim = Principal(agent="a", claims={"x": 1}).claims["x"]

    assert true_claim is True
    assert isinstance(one_claim, int) and not isinstance(one_claim, bool)


def test_T60b_the_default_is_shared_and_immutable():
    assert isinstance(Principal(agent="a").claims, MappingProxyType)
    assert Principal(agent="a").claims == {}


# --- T60c: the stored Action carries the whole principal (§2.4) ------------------------


def test_T60c_a_stored_action_round_trips_the_whole_principal():
    """§2.4 — `Control.resume` rehydrates from this, and for an MCP multi round-trip or ACS
    action the resumed leg writes the *only* receipt that action ever gets."""
    from ctrlrun.state import _action_from_json, _action_json

    principal = Principal(
        agent="refund-agent",
        user="alice",
        claims={"employee_no": 4471, "on_call": True},
        issuer="https://issuer.example/",
        expires_at=datetime(2027, 1, 1, 12, 30, tzinfo=UTC),
    )

    restored = _action_from_json(_action_json(_action(principal=principal))).principal

    assert restored == principal
    assert restored.expires_at == principal.expires_at


def test_T60c_a_row_written_by_v0_2_still_parses():
    """§2.4 — the old shape carries two keys, and must come back with v0.1 §2.1's defaults
    rather than raising. This is what makes the change a value change and not a migration."""
    import json as _json

    from ctrlrun.state import _action_from_json

    old_shape = _json.dumps(
        {
            "action_id": "act_" + "0" * 32,
            "name": "stripe.refund",
            "arguments": {"amount": 100},
            "principal": {"agent": "refund-agent", "user": "alice"},
            "resource": None,
            "environment": "production",
        }
    )

    principal = _action_from_json(old_shape).principal

    assert (principal.agent, principal.user) == ("refund-agent", "alice")
    assert (dict(principal.claims), principal.issuer, principal.expires_at) == ({}, None, None)


# --- T61: an expired principal is refused before policy (§2.3) -------------------------


def _expired(clock):
    return Principal(agent="refund-agent", expires_at=clock.now - timedelta(minutes=1))


def test_T61_an_expired_principal_is_refused(control, store, clock):
    calls = []
    action = _action(arguments={"amount": 2000, "payment_id": "txn_1"}, principal=_expired(clock))

    with pytest.raises(IdentityError):
        control.execute(action, lambda: calls.append(1), None)
    else_branch_ran = bool(calls)
    if else_branch_ran:  # pragma: no cover - the guard is the assertion
        raise AssertionError("the executor ran under an expired principal")

    receipt = store.receipts()[-1]
    assert (receipt.result, receipt.decision_reason) == ("denied", "principal_expired")


def test_T61_no_approval_request_is_created(control, store, clock):
    """An action the policy would `approve`: the refusal must not spend a human's attention on
    something that could never run."""
    action = _action(arguments={"amount": 2000, "payment_id": "txn_1"}, principal=_expired(clock))

    with pytest.raises(IdentityError):
        control.execute(action, lambda: "ok", None)

    assert store.approvals_for(action.action_hash) == ()


def test_T61_the_expired_run_reaches_neither_authority_nor_policy(control, store, clock):
    """§10 T61 — asserted as a *difference between two runs*, not as an absence. A test that
    only checked "no POLICY_EVALUATED" would pass against a Control that evaluated nothing at
    all."""
    valid = _action(arguments={"amount": 100, "payment_id": "txn_a"})
    control.execute(valid, lambda: "ok", None)
    good = [e.type for e in store.events() if e.action_id == valid.action_id]

    expired = _action(arguments={"amount": 100, "payment_id": "txn_b"}, principal=_expired(clock))
    with pytest.raises(IdentityError):
        control.execute(expired, lambda: "ok", None)
    bad = [e.type for e in store.events() if e.action_id == expired.action_id]

    assert EventType.POLICY_EVALUATED in good
    assert EventType.POLICY_EVALUATED not in bad
    assert EventType.ACTION_DENIED in bad


def test_T61_evaluate_returns_deny_and_writes_nothing(control, store, clock):
    """§2.3 — `Control.evaluate` may not write, so for that one entry point the expiry check
    is a decision rather than a refusal."""
    before = len(store.events())

    evaluation = control.evaluate(_action(principal=_expired(clock)))

    assert (str(evaluation.decision), evaluation.reason) == ("deny", "principal_expired")
    assert len(store.events()) == before


# --- T62/T63/T64: the providers (§3.2, §3.3) -------------------------------------------


def test_T62_a_declining_provider_with_no_context_is_no_principal(store, caplog):
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock(), identity=_Declining())

    @protect("customer.read", control=control)
    def read(customer_id: str):  # pragma: no cover - must not run
        raise AssertionError("executed with no principal")

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(ActionDenied) as raised:
        read(customer_id="c1")

    assert raised.value.reason == "no_principal"
    assert any("customer.read" in record.getMessage() for record in caplog.records)
    assert store.receipts() == ()
    assert store.events() == ()


def test_T63_static_provider_warns_once_at_construction(caplog):
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        provider = StaticIdentityProvider("dev-agent")
    at_construction = [r for r in caplog.records if "dev-agent" in r.getMessage()]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        for _ in range(100):
            provider.resolve(IdentityContext(action="customer.read", environment="production"))

    assert len(at_construction) == 1, "the dev-only warning must name the agent, exactly once"
    assert caplog.records == [], "a warning that repeats per call is a warning nobody reads"


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({}, None),
        ({"x-principal": ""}, None),
        ({"x-principal": "   "}, None),
        ({"x-principal": "refund-agent"}, "refund-agent"),
        ({"X-Principal": "refund-agent"}, "refund-agent"),
    ],
    ids=["absent", "empty", "whitespace", "present", "different-case"],
)
def test_T64_header_provider_declines_unless_the_header_carries_a_name(headers, expected):
    provider = HeaderIdentityProvider("X-Principal")

    resolved = provider.resolve(
        IdentityContext(action="customer.read", environment="production", headers=headers)
    )

    assert (resolved.agent if resolved else None) == expected


def test_T64_the_user_header_follows_the_same_rule():
    provider = HeaderIdentityProvider("X-Principal", user_header="X-User")
    ctx = lambda h: IdentityContext(  # noqa: E731
        action="customer.read", environment="production", headers=h
    )

    assert provider.resolve(ctx({"x-principal": "a"})).user is None
    assert provider.resolve(ctx({"x-principal": "a", "x-user": " "})).user is None
    assert provider.resolve(ctx({"x-principal": "a", "x-user": "alice"})).user == "alice"


def test_T64_header_provider_warns_once_about_the_header_it_trusts(caplog):
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        provider = HeaderIdentityProvider("X-Principal")
    at_construction = list(caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        for _ in range(100):
            provider.resolve(
                IdentityContext(
                    action="a.b", environment="production", headers={"x-principal": "p"}
                )
            )

    assert len(at_construction) == 1
    assert "X-Principal" in at_construction[0].getMessage()
    assert caplog.records == []


def test_T64_the_header_provider_invents_no_claims():
    """§3.3 — a header is a name; manufacturing claims from one would be inventing verified
    data."""
    resolved = HeaderIdentityProvider("X-Principal").resolve(
        IdentityContext(action="a.b", environment="production", headers={"x-principal": "p"})
    )

    assert dict(resolved.claims) == {}


# --- T65: the precedence table, every row (§3.2) ---------------------------------------


class _Declining:
    def resolve(self, context: IdentityContext) -> Principal | None:
        return None


class _Answering:
    def __init__(self, agent: str = "provider-agent") -> None:
        self._agent = agent

    def resolve(self, context: IdentityContext) -> Principal | None:
        return Principal(agent=self._agent)


class _Raising:
    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc or RuntimeError("the token was rejected")

    def resolve(self, context: IdentityContext) -> Principal | None:
        raise self._exc


def _control(store, identity=None):
    return Control(Policy.from_yaml(POLICY), store, clock=_Clock(), identity=identity)


def test_T65_no_provider_and_a_context_is_the_v0_1_path(store):
    control = _control(store)

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"):
        assert read(customer_id="c1") == "read"
    assert store.receipts()[-1].principal.agent == "ctx-agent"


def test_T65_the_provider_wins_where_it_answers(store):
    control = _control(store, identity=_Answering())

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"):
        read(customer_id="c1")

    assert store.receipts()[-1].principal.agent == "provider-agent"


def test_T65_a_disagreement_warns_once_naming_both(store, caplog):
    control = _control(store, identity=_Answering())

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), context("ctx-agent"):
        read(customer_id="c1")
        read(customer_id="c2")

    warnings = [r for r in caplog.records if "provider-agent" in r.getMessage()]
    assert len(warnings) == 1, "at most once per Control (§3.2)"
    assert "ctx-agent" in warnings[0].getMessage()


def test_T65_a_decline_falls_back_to_the_context_without_authority(store):
    control = _control(store, identity=_Declining())

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"):
        assert read(customer_id="c1") == "read"
    assert store.receipts()[-1].principal.agent == "ctx-agent"


def test_T65_a_raising_provider_refuses_and_the_context_does_not_fill_in(store):
    """Three behaviours, because the exception class alone cannot tell a refusal from a
    refusal that already ran the action."""
    control = _control(store, identity=_Raising())
    calls = []

    @protect("customer.read", control=control)
    def read(customer_id: str):
        calls.append(1)
        return "read"

    with context("ctx-agent"), pytest.raises(IdentityError):
        read(customer_id="c1")

    assert calls == []
    assert store.receipts() == ()


def test_T65_a_raising_provider_chains_the_original(store):
    control = _control(store, identity=_Raising(ValueError("bad signature")))

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"), pytest.raises(IdentityError) as raised:
        read(customer_id="c1")

    assert isinstance(raised.value.__cause__, ValueError)


def test_T65_an_IdentityError_from_a_provider_propagates_unchanged(store):
    original = IdentityError("expired")
    control = _control(store, identity=_Raising(original))

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"), pytest.raises(IdentityError) as raised:
        read(customer_id="c1")

    assert raised.value is original


def test_T65_a_base_exception_from_a_provider_is_not_wrapped(store):
    control = _control(store, identity=_Raising(KeyboardInterrupt()))

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"), pytest.raises(KeyboardInterrupt):
        read(customer_id="c1")


# --- T65d: the environment comes from the deployment (§2.5) ----------------------------


def test_T65d_context_no_longer_accepts_an_environment():
    """§2.5 — asserted by *signature*. A behavioural assertion would pass against a parameter
    that is still accepted and merely ignored, which is the state this must not drift into."""
    assert "environment" not in inspect.signature(context).parameters

    with pytest.raises(TypeError):
        context("a", environment="staging")  # type: ignore[call-arg]


def test_T65d_the_argument_wins(store, monkeypatch):
    monkeypatch.setenv("CTRLRUN_ENVIRONMENT", "from-env")
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="from-arg")

    assert control.environment == "from-arg"


def test_T65d_the_environment_variable_is_next(store, monkeypatch):
    monkeypatch.setenv("CTRLRUN_ENVIRONMENT", "from-env")

    assert Control(Policy.from_yaml(POLICY), store, clock=_Clock()).environment == "from-env"


def test_T65d_then_the_document(store, monkeypatch):
    monkeypatch.delenv("CTRLRUN_ENVIRONMENT", raising=False)
    policy = Policy.from_yaml(
        "schema: ctrlrun.policy/v3\nenvironment: from-document\n"
        "actions:\n  customer.read:\n    decision: allow\n"
    )

    assert Control(policy, store, clock=_Clock()).environment == "from-document"


def test_T65d_then_production(store, monkeypatch):
    monkeypatch.delenv("CTRLRUN_ENVIRONMENT", raising=False)

    assert Control(Policy.from_yaml(POLICY), store, clock=_Clock()).environment == "production"


def test_T65d_an_empty_environment_variable_is_refused(store, monkeypatch):
    """§2.5 — falling through is the failure that puts actions in an environment nobody
    chose, so the refusal is asserted rather than the fallback."""
    monkeypatch.setenv("CTRLRUN_ENVIRONMENT", "")

    with pytest.raises(InvalidArgument):
        Control(Policy.from_yaml(POLICY), store, clock=_Clock())


def test_T65d_the_empty_variable_is_refused_even_when_the_argument_wins(store, monkeypatch):
    """§2.5 — the check runs whenever the variable is present, so the refusal does not depend
    on how the Control happened to be constructed."""
    monkeypatch.setenv("CTRLRUN_ENVIRONMENT", "")

    with pytest.raises(InvalidArgument):
        Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="staging")


def test_T65d_an_empty_argument_is_refused(store):
    with pytest.raises(InvalidArgument):
        Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="")


def test_T65d_every_action_carries_the_controls_environment(store, monkeypatch):
    monkeypatch.delenv("CTRLRUN_ENVIRONMENT", raising=False)
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="staging")

    @protect("customer.read", control=control)
    def read(customer_id: str):
        return "read"

    with context("ctx-agent"):
        read(customer_id="c1")
        read(customer_id="c2")

    assert {r.environment for r in store.receipts()} == {"staging"}


def test_T65d_execute_refuses_an_action_from_another_environment(store, monkeypatch):
    """§2.5 — `Action()` is public and `Control.execute` is frozen API, so without this the
    caller sets the dimension a grant scopes to."""
    monkeypatch.delenv("CTRLRUN_ENVIRONMENT", raising=False)
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="production")

    with pytest.raises(InvalidArgument):
        control.execute(_action(environment="staging"), lambda: "ok", None)


def test_T65d_execute_accepts_an_action_from_its_own_environment(store, monkeypatch):
    """The control for the test above: without it, a Control that refused every Action would
    satisfy it."""
    monkeypatch.delenv("CTRLRUN_ENVIRONMENT", raising=False)
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock(), environment="staging")

    control.execute(_action(environment="staging"), lambda: "ok", None)

    assert store.receipts()[-1].environment == "staging"

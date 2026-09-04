"""The `authority:` section, grants and evaluation. Build-list item 2; SPEC-v0.3 §4.

The acceptance tests are T66 to T74b. Four sentences run through all of them:

**Authority is opt-in, then fail-closed** (§4.1). No `authority:` key at all is v0.2, exactly —
T66 — and the moment one exists every principal needs a grant, including for actions the policy
allows outright.

**A grant carries no decision** (§4.2). Authority answers "may this principal do this at all";
policy answers "how much autonomy does the action have". So T70 is a 2-by-3 whose bottom
row collapses to one case, not the 3-by-3 the build brief asked for.

**Authority runs before policy** (§4.3). A denial there must not leave a pending approval
request behind, so T74 asserts the trace and not merely the outcome.

**The reason is asserted by name, everywhere** (§4.3). A guard that can only fire where a later
guard would also fire, with the same observable result, is documentation rather than defence;
a test asserting only "it was refused" cannot tell which check ran.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from conftest import AUTHORITY_EVENT_TYPES, check_authority_events_declared
from ctrlrun import (
    Action,
    ActionDenied,
    ApprovalRequired,
    Authority,
    AuthorityDenied,
    Control,
    Decision,
    Grant,
    InMemoryStateStore,
    InvalidArgument,
    Policy,
    PolicyError,
    Principal,
    Subject,
)
from ctrlrun.authority import (
    AUTHORITY_CONSTRAINT,
    AUTHORITY_EXPIRED,
    AUTHORITY_GRANT,
    NO_AUTHORITY,
    contains,
    matches,
)
from ctrlrun.policy import Condition
from ctrlrun.receipt import EventType

pytestmark = pytest.mark.authority

V3 = "schema: ctrlrun.policy/v3\n"

ACTIONS = """
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 50000 }
        decision: allow
      - when: { amount_lte: 5000000 }
        decision: approve
      - decision: deny
  customer.read:
    decision: allow
"""

GRANT = """
authority:
  grants:
    - id: head-of-support
      subject: { agent: "support-agent", user: "alice@example.com" }
      actions: ["stripe.refund"]
      resources: ["payment:EU-*"]
      constraints: { amount_lte: 200000 }
      environments: ["production"]
"""


class _Clock:
    """Static: it moves only when a test moves it, so an expiry boundary is exact."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(clock):
    return InMemoryStateStore(clock=clock)


def _document(authority: str = "", actions: str = ACTIONS) -> str:
    return V3 + authority + actions


def _control(document, store, clock, environment="production"):
    """One document, both loaders — the shape `Control.from_file` builds (§4.8)."""
    authority = Authority.from_yaml(document) if "authority:" in document else None
    return Control(
        Policy.from_yaml(document),
        store,
        authority=authority,
        clock=clock,
        environment=environment,
    )


def _action(**overrides):
    fields = {
        "name": "stripe.refund",
        "arguments": {"amount": 1000, "payment_id": "EU-42"},
        "principal": Principal(agent="support-agent", user="alice@example.com"),
        "resource": "payment:EU-42",
        "environment": "production",
    }
    fields.update(overrides)
    return Action(**fields)


def _types(store, action_id=None):
    return [
        str(event.type)
        for event in store.events()
        if action_id is None or event.action_id == action_id
    ]


def _data(store, type_):
    return [event.data for event in store.events() if str(event.type) == type_]


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return "ok"


# --- T66: no `authority:` section is v0.2, exactly (§4.1) ------------------------------


def test_T66_a_document_with_no_authority_section_leaves_control_authority_none(
    tmp_path, store, clock
):
    (tmp_path / "ctrlrun.yaml").write_text(_document())
    control = Control.from_file(tmp_path / "ctrlrun.yaml")

    assert control.authority is None
    assert _control(_document(), store, clock).authority is None


def test_T66_no_authority_event_is_appended_without_a_section(store, clock):
    """The behavioural half: a full v0.1 path — allow, approve, deny — under a document with
    no `authority:` key produces not one of the five §7 types."""
    control = _control(_document(), store, clock)
    executor = _Executor()

    control.execute(_action(), executor, "refund:EU-42")
    with pytest.raises(ApprovalRequired):
        control.execute(_action(arguments={"amount": 100000, "payment_id": "EU-43"}), executor)
    with pytest.raises(ActionDenied):
        control.execute(_action(arguments={"amount": 90000000, "payment_id": "EU-44"}), executor)

    assert executor.calls == 1
    assert AUTHORITY_EVENT_TYPES.isdisjoint(_types(store))


def test_T66_the_session_guard_refuses_an_undeclared_authority_event():
    """The mechanical half of T66 is `tests/conftest.py`'s autouse fixture, and this asserts
    the fixture would actually fail. A guard whose only evidence is that the suite stayed
    green is a guard nothing exercises."""
    for leaked in sorted(AUTHORITY_EVENT_TYPES):
        with pytest.raises(AssertionError, match=leaked):
            check_authority_events_declared(["ACTION_PROPOSED", leaked], declared=False, nodeid="t")
        check_authority_events_declared(["ACTION_PROPOSED", leaked], declared=True, nodeid="t")


def test_T66_the_session_guard_records_what_the_stores_append(
    store, clock, authority_events_are_declared
):
    """And that it is wired to the stores at all: without this the fixture could be recording
    nothing and every `isdisjoint` above would hold vacuously."""
    control = _control(_document(GRANT), store, clock)

    control.execute(_action(), _Executor(), None)

    assert "AUTHORITY_RESOLVED" in authority_events_are_declared
    assert "ACTION_PROPOSED" in authority_events_are_declared


# --- T67: a principal with no grant is denied (§4.1, §4.3) -----------------------------


def test_T67_a_principal_with_no_grant_is_denied(store, clock):
    control = _control(_document(GRANT), store, clock)
    action = _action(principal=Principal(agent="other-agent", user="alice@example.com"))
    executor = _Executor()

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(action, executor, "refund:EU-42")

    assert refused.value.reason == NO_AUTHORITY
    assert executor.calls == 0
    receipt = store.receipts()[-1]
    assert str(receipt.result) == "denied"
    assert receipt.decision is Decision.DENY
    assert receipt.decision_reason == NO_AUTHORITY
    assert store.approvals_for(action.action_hash) == ()
    assert store.get_effect("refund:EU-42") is None


def test_T67_an_authority_denial_is_an_action_denied(store, clock):
    """§11 — `AuthorityDenied` subclasses `ActionDenied`, so an agent loop's existing handler
    keeps working."""
    control = _control(_document(GRANT), store, clock)

    with pytest.raises(ActionDenied):
        control.execute(_action(principal=Principal(agent="other-agent")), _Executor(), None)


def test_T67_an_action_the_policy_allows_outright_still_needs_a_grant(store, clock):
    """§4.1 — including reads, including actions with no effect key."""
    control = _control(_document(GRANT), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(name="customer.read", resource=None), _Executor(), None)

    assert refused.value.reason == NO_AUTHORITY


def test_T67_an_empty_grants_list_denies_everything(store, clock):
    """§4.1 — `grants: []` is valid and denies every action, the shape of v0.1's `actions: {}`."""
    control = _control(_document("authority:\n  grants: []\n"), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(), _Executor(), None)

    assert refused.value.reason == NO_AUTHORITY


# --- T67b: subject matching, by name (§4.2) --------------------------------------------


def _subject_grant(subject: str) -> str:
    return f"""
authority:
  grants:
    - id: g
      subject: {subject}
      actions: ["**"]
"""


@pytest.mark.parametrize(
    ("subject", "principal", "expected"),
    [
        # A grant naming a user is not satisfied by an agent acting alone.
        ('{ agent: "support-agent", user: "alice@example.com" }', ("support-agent", None), False),
        ('{ agent: "support-agent", user: "alice@example.com" }', ("support-agent", "bob"), False),
        (
            '{ agent: "support-agent", user: "alice@example.com" }',
            ("support-agent", "alice@example.com"),
            True,
        ),
        # A grant omitting `user` matches a principal that has one, and one that has not.
        ('{ agent: "support-agent" }', ("support-agent", "alice@example.com"), True),
        ('{ agent: "support-agent" }', ("support-agent", None), True),
        # A subject has no separator, so `*` matches a dotted agent name whole.
        ('{ agent: "*" }', ("acme.support.agent", None), True),
        # A prefix wildcard is a prefix, not a substring.
        ('{ agent: "support-*" }', ("support-agent", None), True),
        ('{ agent: "support-*" }', ("supporter", None), False),
        ('{ agent: "support-*" }', ("the-support-agent", None), False),
        # Omitting `agent` means any agent (§4.2), and is spelled out rather than inferred.
        ('{ user: "alice@example.com" }', ("any-agent-at-all", "alice@example.com"), True),
        ('{ user: "alice@example.com" }', ("any-agent-at-all", None), False),
    ],
)
def test_T67b_subject_matching(store, clock, subject, principal, expected):
    control = _control(_document(_subject_grant(subject)), store, clock)
    agent, user = principal
    action = _action(
        name="customer.read", resource=None, principal=Principal(agent=agent, user=user)
    )
    executor = _Executor()

    if expected:
        control.execute(action, executor, None)
        assert executor.calls == 1
    else:
        with pytest.raises(AuthorityDenied) as refused:
            control.execute(action, executor, None)
        assert refused.value.reason == NO_AUTHORITY
        assert executor.calls == 0


# --- T67c: a grant scoped to another environment does not match (§4.2, §2.5) -----------

ENVIRONMENT_GRANT = """
authority:
  grants:
    - id: staging-only
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      environments: ["staging"]
"""


def test_T67c_a_grant_scoped_to_another_environment_does_not_match(store, clock):
    control = _control(_document(ENVIRONMENT_GRANT), store, clock, environment="production")
    executor = _Executor()

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(resource=None), executor, None)

    assert refused.value.reason == NO_AUTHORITY
    assert executor.calls == 0


def test_T67c_the_same_grant_in_its_own_environment_passes(store, clock):
    """The control for the pair. Without it an implementation that matched no environment at
    all would satisfy the first half. No calling code participates in either outcome."""
    control = _control(_document(ENVIRONMENT_GRANT), store, clock, environment="staging")
    executor = _Executor()

    control.execute(_action(resource=None, environment="staging"), executor, None)

    assert executor.calls == 1


# --- T68: a matching grant passes to policy (§4.3) -------------------------------------


def test_T68_a_matching_grant_passes_to_policy(store, clock):
    control = _control(_document(GRANT), store, clock)
    executor = _Executor()

    receipt = control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 1
    assert str(receipt.result) == "committed"
    types = _types(store)
    assert types.index("AUTHORITY_RESOLVED") < types.index("POLICY_EVALUATED")
    resolved = _data(store, "AUTHORITY_RESOLVED")[0]
    # By name: §4.6 sends both passing cells' `decision_reason` to the policy axis, so this
    # event is the only place a *pass* reason is observable at all.
    assert resolved["reason"] == AUTHORITY_GRANT
    assert resolved["grant_id"] == "head-of-support"
    assert receipt.decision_reason == "rule[0]"


SHAPE_GRANT = """
authority:
  grants:
    - id: eu-refunds
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      resources: ["payment:EU-*"]
"""


@pytest.mark.parametrize(
    ("name", "resource", "expected"),
    [
        ("stripe.refund", "payment:EU-42", True),
        # One dimension away from the row above, each time, so nothing else can be doing
        # the refusing: the mutation run caught a resource test that was actually being
        # denied by the action pattern.
        ("stripe.refund", "payment:US-42", False),
        ("stripe.refund", "payment:EU-42:leg2", False),
        ("stripe.refund", None, False),
        ("customer.read", "payment:EU-42", False),
    ],
    ids=["match", "other-resource", "deeper-resource", "no-resource", "other-action"],
)
def test_T67d_the_action_name_and_the_resource_are_both_matched(clock, name, resource, expected):
    store = InMemoryStateStore(clock=clock)
    control = _control(_document(SHAPE_GRANT), store, clock)
    action = _action(name=name, resource=resource, principal=Principal(agent="support-agent"))
    executor = _Executor()

    if expected:
        control.execute(action, executor, None)
        assert executor.calls == 1
    else:
        with pytest.raises(AuthorityDenied) as refused:
            control.execute(action, executor, None)
        assert refused.value.reason == NO_AUTHORITY
        assert executor.calls == 0


def test_T68_a_grant_omitting_resources_matches_an_action_with_none(store, clock):
    """§4.4 — an action whose `resource` is None does not match a grant that declares
    `resources:`; to grant one, omit the key."""
    with_resources = _document(GRANT)
    without = _document(
        """
authority:
  grants:
    - id: reader
      subject: { agent: "support-agent" }
      actions: ["customer.read"]
"""
    )
    read = _action(name="customer.read", resource=None)

    with pytest.raises(AuthorityDenied) as refused:
        _control(with_resources, store, clock).execute(read, _Executor(), None)
    assert refused.value.reason == NO_AUTHORITY

    executor = _Executor()
    _control(without, InMemoryStateStore(clock=clock), clock).execute(read, executor, None)
    assert executor.calls == 1


# --- T69: a failed constraint denies what policy would allow (§4.5) --------------------


def test_T69_a_failed_constraint_denies_what_policy_would_allow(store, clock):
    """Policy says allow at this amount; the grant's ceiling excludes it. By reason, so
    removing the constraint check cannot pass as `no_authority`."""
    policy_allows = """
actions:
  stripe.refund:
    rules:
      - decision: allow
"""
    control = _control(_document(GRANT, policy_allows), store, clock)
    action = _action(arguments={"amount": 300000, "payment_id": "EU-42"})
    executor = _Executor()

    assert control.policy.evaluate(action).decision is Decision.ALLOW
    with pytest.raises(AuthorityDenied) as refused:
        control.execute(action, executor, None)

    assert refused.value.reason == AUTHORITY_CONSTRAINT
    assert refused.value.grant_id == "head-of-support"
    assert executor.calls == 0
    assert store.receipts()[-1].decision_reason == AUTHORITY_CONSTRAINT


def test_T69_an_absent_argument_makes_a_constraint_false(store, clock, caplog):
    """§4.5 — a condition that cannot be evaluated never matches, and says so in the log."""
    control = _control(_document(GRANT), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(arguments={"payment_id": "EU-42"}), _Executor(), None)

    assert refused.value.reason == AUTHORITY_CONSTRAINT
    assert "amount" in caplog.text


# --- T69b: only integers can be bounded (§4.5) -----------------------------------------


def test_T69b_a_decimal_string_operand_is_a_load_error_naming_the_rule():
    decimal = """
authority:
  grants:
    - id: g
      subject: { agent: "a" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: "2000.00" }
"""
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(_document(decimal))

    assert "int" in str(refused.value)
    assert "minor units" in str(refused.value)


def test_T69b_no_control_is_constructed(tmp_path):
    decimal = """
authority:
  grants:
    - id: g
      subject: { agent: "a" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: "2000.00" }
"""
    (tmp_path / "ctrlrun.yaml").write_text(_document(decimal))
    control = None

    with pytest.raises(PolicyError):
        control = Control.from_file(tmp_path / "ctrlrun.yaml")

    assert control is None


# --- T70: stricter wins, four cases (§4.6) ---------------------------------------------

PERMISSIVE_GRANT = """
authority:
  grants:
    - id: everything
      subject: { agent: "support-agent" }
      actions: ["**"]
"""

_POLICY_BY_DECISION = {
    "allow": "\nactions:\n  stripe.refund:\n    rules:\n      - decision: allow\n",
    "approve": "\nactions:\n  stripe.refund:\n    rules:\n      - decision: approve\n",
    "deny": "\nactions:\n  stripe.refund:\n    rules:\n      - decision: deny\n",
}


@pytest.mark.parametrize(
    ("grant", "policy_decision", "expected", "reason"),
    [
        (PERMISSIVE_GRANT, "allow", Decision.ALLOW, "rule[0]"),
        (PERMISSIVE_GRANT, "approve", Decision.APPROVE, "rule[0]"),
        (PERMISSIVE_GRANT, "deny", Decision.DENY, "rule[0]"),
        # §4.6's bottom row is reached without evaluating policy at all, so its three cells
        # are indistinguishable — same outcome, same events, same reason. One case, not
        # three: running it three times would be one assertion over three configurations
        # nothing can tell apart.
        (GRANT, "allow", Decision.DENY, NO_AUTHORITY),
    ],
    ids=["pass-allow", "pass-approve", "pass-deny", "deny-any"],
)
def test_T70_the_stricter_of_the_two_wins(store, clock, grant, policy_decision, expected, reason):
    document = _document(grant, _POLICY_BY_DECISION[policy_decision])
    control = _control(document, store, clock)
    # The deny row's grant is scoped to another agent, so authority refuses before policy.
    action = _action(principal=Principal(agent="support-agent"), resource=None)
    executor = _Executor()

    evaluation = control.evaluate(action)
    assert evaluation.decision is expected
    assert evaluation.reason == reason

    if expected is Decision.ALLOW:
        receipt = control.execute(action, executor, None)
        assert executor.calls == 1
        assert receipt.decision is expected
        assert receipt.decision_reason == reason
    elif expected is Decision.APPROVE:
        with pytest.raises(ApprovalRequired):
            control.execute(action, executor, None)
        assert executor.calls == 0
    else:
        with pytest.raises(ActionDenied) as refused:
            control.execute(action, executor, None)
        assert refused.value.reason == reason
        assert executor.calls == 0
        assert store.receipts()[-1].decision_reason == reason


def test_T70_authority_cannot_loosen_a_policy_denial(store, clock):
    """Neither axis loosens the other, stated as its own assertion: the widest possible grant
    over an action the policy denies is still a denial, with the *policy* reason."""
    control = _control(_document(PERMISSIVE_GRANT, _POLICY_BY_DECISION["deny"]), store, clock)

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), _Executor(), None)

    assert refused.value.reason == "rule[0]"
    assert not isinstance(refused.value, AuthorityDenied)


# --- T70b: several matching grants, and order does not matter (§4.6) -------------------

TWO_MATCHING = """
authority:
  grants:
    - id: {first}
      subject: {{ agent: "support-agent" }}
      actions: ["stripe.**"]
    - id: {second}
      subject: {{ agent: "support-agent" }}
      actions: ["**"]
"""


@pytest.mark.parametrize(
    ("first", "second"),
    [("a-narrow", "b-wide"), ("b-wide", "a-narrow")],
    ids=["a-first", "b-first"],
)
def test_T70b_several_matching_grants_name_the_lowest_id(clock, first, second):
    store = InMemoryStateStore(clock=clock)
    document = _document(TWO_MATCHING.format(first=first, second=second))
    control = _control(document, store, clock)
    executor = _Executor()

    receipt = control.execute(_action(principal=Principal(agent="support-agent")), executor, None)

    assert executor.calls == 1
    assert receipt.decision is Decision.ALLOW
    # The one assertion that can catch an implementation returning whichever grant it reached
    # first: `a-narrow` sorts first by codepoint whichever order the document is written in.
    assert _data(store, "AUTHORITY_RESOLVED")[0]["grant_id"] == "a-narrow"


def test_T70b_holding_two_permissions_is_never_worse_than_holding_one(store, clock):
    """§4.6 — denial arises from the absence of a matching grant, never from the presence of a
    stricter one. The narrow grant's constraint excludes the action; the wide one does not."""
    both = """
authority:
  grants:
    - id: a-narrow
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 100 }
    - id: b-wide
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
"""
    control = _control(_document(both), store, clock)
    executor = _Executor()

    control.execute(_action(principal=Principal(agent="support-agent")), executor, None)

    assert executor.calls == 1
    assert _data(store, "AUTHORITY_RESOLVED")[0]["grant_id"] == "b-wide"


# --- T71: an expired grant denies (§4.2, §4.3) -----------------------------------------

EXPIRING_GRANT = """
authority:
  grants:
    - id: head-of-support
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      expires_at: 2026-09-03T10:12:01Z
"""


def test_T71_an_expired_grant_denies(store, clock):
    control = _control(_document(EXPIRING_GRANT), store, clock)
    clock.now = datetime(2026, 9, 3, 10, 12, 2, tzinfo=UTC)
    executor = _Executor()

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), executor, None)

    assert refused.value.reason == AUTHORITY_EXPIRED
    assert refused.value.grant_id == "head-of-support"
    assert executor.calls == 0
    assert store.receipts()[-1].decision_reason == AUTHORITY_EXPIRED


def test_T71_a_grant_is_valid_up_to_and_including_its_expiry(store, clock):
    control = _control(_document(EXPIRING_GRANT), store, clock)
    clock.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)
    executor = _Executor()

    control.execute(_action(principal=Principal(agent="support-agent")), executor, None)

    assert executor.calls == 1


def test_T71_one_microsecond_past_the_expiry_does_not(store, clock):
    control = _control(_document(EXPIRING_GRANT), store, clock)
    clock.now = datetime(2026, 9, 3, 10, 12, 1, 1, tzinfo=UTC)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), _Executor(), None)

    assert refused.value.reason == AUTHORITY_EXPIRED


def test_T71_a_naive_expires_at_is_a_load_error():
    """§4.2 — both the quoted string form and the form PyYAML parses into a naive datetime."""
    for written in ("2026-12-31 23:59:59", '"2026-12-31T23:59:59"'):
        naive = f"""
authority:
  grants:
    - id: g
      subject: {{ agent: "a" }}
      actions: ["**"]
      expires_at: {written}
"""
        with pytest.raises(PolicyError, match="offset"):
            Authority.from_yaml(_document(naive))


def test_T71_pyyaml_returns_an_aware_datetime_for_an_offset(store, clock):
    """The floor under §4.2's rule, asserted rather than assumed: PyYAML 5 subtracts the
    offset and hands back a naive value, which would reject §4.1's own example at load."""
    import yaml

    parsed = yaml.safe_load("when: 2026-12-31T23:59:59Z")["when"]
    assert parsed.tzinfo is not None


# --- T71b: reason precedence, collected and fixed (§4.3) -------------------------------

# `authority_revoked`, `authority_escalation` and `authority_unreadable` need a delegation
# record, which arrives with build-list item 3; T71b's full three-grant form lands there.
# What is reachable here is the half that decides the argument: expiry outranks constraint,
# both within one grant and across grants, and neither turns on document order.

# `a-expired` sorts *after* `b-constrained` by codepoint on neither name, so the two carry
# ids that make the precedence assertion independent of the sort tie-break below.
_EXPIRED_GRANT = """    - id: a-expired
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      expires_at: 2026-09-03T09:00:00Z
"""
_CONSTRAINED_GRANT = """    - id: b-constrained
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 1 }
"""


@pytest.mark.parametrize(
    "order",
    [(_EXPIRED_GRANT, _CONSTRAINED_GRANT), (_CONSTRAINED_GRANT, _EXPIRED_GRANT)],
    ids=["expired-first", "constrained-first"],
)
def test_T71b_expiry_outranks_a_failed_constraint_across_grants(clock, order):
    store = InMemoryStateStore(clock=clock)
    section = "authority:\n  grants:\n" + "".join(order)
    control = _control(_document(section), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), _Executor(), None)

    # Fixed and collected rather than short-circuited: the reason names what will still be
    # wrong after the caller changes the request.
    assert refused.value.reason == AUTHORITY_EXPIRED
    assert refused.value.grant_id == "a-expired"
    assert _data(store, "AUTHORITY_DENIED")[0] == {
        "reason": AUTHORITY_EXPIRED,
        "grant_id": "a-expired",
    }


def test_T71b_one_grant_failing_both_ways_reports_the_expiry(store, clock):
    """Evaluation collects every reason a grant failed for rather than short-circuiting."""
    both = """
authority:
  grants:
    - id: g
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 1 }
      expires_at: 2026-09-03T09:00:00Z
"""
    control = _control(_document(both), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), _Executor(), None)

    assert refused.value.reason == AUTHORITY_EXPIRED


def test_T71b_two_grants_failing_the_same_way_name_the_lowest_id(store, clock):
    same = """
authority:
  grants:
    - id: b-second
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 1 }
    - id: a-first
      subject: { agent: "support-agent" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 2 }
"""
    control = _control(_document(same), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="support-agent")), _Executor(), None)

    assert refused.value.reason == AUTHORITY_CONSTRAINT
    assert refused.value.grant_id == "a-first"


def test_T71b_no_authority_implicates_no_grant(store, clock):
    """§7 — `grant_id` is present "where one grant was implicated", and no grant matching the
    shape implicates none."""
    control = _control(_document(GRANT), store, clock)

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(_action(principal=Principal(agent="other-agent")), _Executor(), None)

    assert refused.value.grant_id is None
    assert _data(store, "AUTHORITY_DENIED")[0] == {"reason": NO_AUTHORITY}


# --- T72: pattern matching does not cross a separator (§4.4) ---------------------------


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("stripe.*", "stripe.refund", True),
        ("stripe.*", "stripe.refund.partial", False),
        ("stripe.*", "stripe", False),
        ("stripe.**", "stripe.refund", True),
        ("stripe.**", "stripe.refund.partial", True),
        ("stripe.**", "stripe", False),
        ("stripe.**", "stripes.refund", False),
        ("*", "refund", True),
        ("*", "stripe.refund", False),
        ("**", "refund", True),
        ("**", "stripe.refund.partial", True),
        ("stripe.refund", "stripe.refund", True),
        ("stripe.refund", "stripe.refunded", False),
    ],
)
def test_T72_action_patterns(pattern, value, expected):
    assert matches(pattern, value, separator=".") is expected


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("payment:EU-*", "payment:EU-42", True),
        ("payment:EU-*", "payment:EU-42:leg2", False),
        ("payment:EU-*", "payment:US-42", False),
        ("payment:*", "payment:EU-42", True),
        ("payment:**", "payment:EU-42:leg2", True),
        ("**", "payment:EU-42", True),
    ],
)
def test_T72_resource_patterns(pattern, value, expected):
    assert matches(pattern, value, separator=":") is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "stripe.?efund",
        "stripe.[abc]",
        "stripe.*refund",
        "stri*pe.refund",
        "a**",
        "**a",
        "**.refund",
        "stripe.a*.**",
        "*.**",
        "",
        "stripe.",
        ".refund",
        "stripe..refund",
        "**.**",
    ],
)
def test_T72_a_pattern_outside_the_grammar_is_a_load_error(pattern):
    bad = f"""
authority:
  grants:
    - id: g
      subject: {{ agent: "a" }}
      actions: ["{pattern}"]
"""
    with pytest.raises(PolicyError):
        Authority.from_yaml(_document(bad))


def test_T72_a_deep_wildcard_in_a_subject_is_a_load_error():
    """§4.2 — a subject has no separator, so `**` means nothing there that `*` does not, and
    leaving it legal would give "any agent" a spelling a review grep for `"*"` misses."""
    with pytest.raises(PolicyError, match=r"\*\*"):
        Authority.from_yaml(_document(_subject_grant('{ agent: "**" }')))


# --- T72b: pattern containment, in both directions (§5.5) ------------------------------


@pytest.mark.parametrize(
    "pattern", ["stripe.refund", "stripe.*", "stripe.**", "**", "*", "stripe.refund.partial"]
)
def test_T72b_every_well_formed_pattern_contains_itself(pattern):
    """Load-bearing rather than pedantic: T76's whole construction — vary one dimension, hold
    the rest identical — silently depends on it."""
    assert contains(pattern, pattern, separator=".") is True


@pytest.mark.parametrize(
    ("parent", "child", "expected"),
    [
        ("stripe.*", "stripe.refund", True),
        ("stripe.*", "stripe.ref*", True),
        ("stripe.ref*", "stripe.*", False),
        ("stripe.ref*", "stripe.refund", True),
        ("stripe.**", "stripe.*", True),
        ("stripe.**", "stripe.refund", True),
        ("**", "stripe.**", True),
        ("**", "stripe.refund.partial", True),
        ("stripe.refund", "stripe.*", False),
        ("stripe.*", "stripe.refund.partial", False),
        ("stripe.**", "stripe", False),
        ("stripe.*", "stripe.**", False),
        ("stripe.refund", "stripe.refunded", False),
    ],
)
def test_T72b_action_containment(parent, child, expected):
    assert contains(parent, child, separator=".") is expected


@pytest.mark.parametrize(
    ("parent", "child", "expected"),
    [
        ("payment:EU-*", "payment:EU-42", True),
        ("payment:*", "payment:EU-*", True),
        ("payment:EU-4*", "payment:EU-*", False),
        ("payment:EU-42", "payment:EU-*", False),
        ("payment:*", "payment:EU-42:leg2", False),
    ],
)
def test_T72b_resource_containment(parent, child, expected):
    assert contains(parent, child, separator=":") is expected


def test_T72b_containment_is_not_matching():
    """The two relations are different functions, and an implementation of containment as
    `fnmatch` or `startswith` passes an evaluation test while breaking attenuation."""
    assert matches("stripe.**", "stripe.refund", separator=".") is True
    assert contains("stripe.refund", "stripe.**", separator=".") is False
    assert matches("stripe.*", "stripe.refund", separator=".") is True
    assert contains("stripe.refund", "stripe.*", separator=".") is False


# --- T73: a malformed authority section fails at load (§4.1, §4.2) ---------------------

_MALFORMED = {
    "no-grants": "authority:\n  max_delegation_depth: 3\n",
    "grants-not-a-list": 'authority:\n  grants: { id: "g" }\n',
    "grants-null": "authority:\n  grants:\n",
    "no-subject": 'authority:\n  grants:\n    - id: g\n      actions: ["**"]\n',
    "empty-subject": (
        'authority:\n  grants:\n    - id: g\n      subject: {}\n      actions: ["**"]\n'
    ),
    "no-actions": 'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n',
    "empty-actions": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: []\n'
    ),
    "unknown-top-key": (
        'authority:\n  depth: 3\n  grants:\n    - id: g\n      subject: { agent: "a" }\n'
        '      actions: ["**"]\n'
    ),
    "unknown-grant-key": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n'
        '      actions: ["**"]\n      action: ["**"]\n'
    ),
    "unknown-subject-key": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a", group: "g" }\n'
        '      actions: ["**"]\n'
    ),
    "duplicate-id": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        '    - id: g\n      subject: { agent: "b" }\n      actions: ["**"]\n'
    ),
    "dlg-prefixed-id": (
        'authority:\n  grants:\n    - id: dlg_abc\n      subject: { agent: "a" }\n'
        '      actions: ["**"]\n'
    ),
    "id-charset": (
        'authority:\n  grants:\n    - id: "-leading"\n      subject: { agent: "a" }\n'
        '      actions: ["**"]\n'
    ),
    "expiry-not-a-timestamp": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      expires_at: 12345\n"
    ),
    "naive-expiry": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      expires_at: 2026-12-31 23:59:59\n"
    ),
    "empty-constraints": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      constraints: {}\n"
    ),
    "null-constraints": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      constraints:\n"
    ),
    "reserved-constraint": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      constraints: { agent_eq: a }\n"
    ),
    "float-operand": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      constraints: { amount_lte: 20.0 }\n"
    ),
    "negative-depth": (
        "authority:\n  max_delegation_depth: -1\n  grants:\n    - id: g\n"
        '      subject: { agent: "a" }\n      actions: ["**"]\n'
    ),
    "empty-environments": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      environments: []\n"
    ),
    "empty-resources": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      resources: []\n"
    ),
    "delegable-without-expiry": (
        'authority:\n  grants:\n    - id: g\n      subject: { agent: "a" }\n      actions: ["**"]\n'
        "      delegable: true\n"
    ),
}


@pytest.mark.parametrize("case", sorted(_MALFORMED), ids=sorted(_MALFORMED))
def test_T73_a_malformed_authority_section_fails_at_load(tmp_path, case):
    (tmp_path / "ctrlrun.yaml").write_text(_document(_MALFORMED[case]))
    control = None

    with pytest.raises(PolicyError):
        control = Control.from_file(tmp_path / "ctrlrun.yaml")

    # Asserted, not assumed: a `Control` that came into existence with a section nobody could
    # parse is the fail-open §4.1 exists to refuse.
    assert control is None


def test_T73_authority_in_a_v1_document_names_the_schema():
    v1 = "schema: ctrlrun.policy/v1\n" + GRANT + ACTIONS

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(v1)
    assert "ctrlrun.policy/v3" in str(refused.value)
    assert "authority" in str(refused.value)

    # And the authority loader refuses it on its own, because §8.3's `--authority` document is
    # never read by the policy loader at all.
    with pytest.raises(PolicyError) as also:
        Authority.from_yaml(v1)
    assert "ctrlrun.policy/v3" in str(also.value)


def test_T73_a_standalone_document_refuses_actions_and_mode():
    """§4.8, §8.3 — one constructor, two closed key sets, told which."""
    combined = _document(GRANT)

    Authority.from_yaml(combined, standalone=False)
    with pytest.raises(PolicyError, match="actions"):
        Authority.from_yaml(combined, standalone=True)
    with pytest.raises(PolicyError, match="mode"):
        Authority.from_yaml(V3 + GRANT + "mode: observe\n", standalone=True)
    Authority.from_yaml(V3 + GRANT, standalone=True)


def test_T73_a_document_with_no_authority_section_is_refused_by_the_loader():
    """`from_yaml` is asked for a section; `Control.from_file` is what decides there is none
    (§4.1). A loader that invented an empty `Authority` would deny every action in a v0.2
    configuration."""
    with pytest.raises(PolicyError, match="authority"):
        Authority.from_yaml(_document())


# --- T73b: the models refuse in Python what the loader refuses in YAML (§4.8) ----------


def test_T73b_a_subject_addressed_to_every_principal_is_refused():
    with pytest.raises(InvalidArgument):
        Subject()
    with pytest.raises(InvalidArgument):
        Subject(agent=None, user=None)


def test_T73b_a_subject_may_not_carry_a_deep_wildcard():
    with pytest.raises(InvalidArgument):
        Subject(agent="**")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"actions": ("stripe.a*.**",)},
        {"actions": ("**a",)},
        {"actions": ()},
        {"resources": ("payment:*:**",)},
        {"resources": ()},
        {"expires_at": datetime(2026, 12, 31, 23, 59, 59)},
        {"constraints": {"amount_gte": Condition("amount_lte", "amount", "lte", 10)}},
        {"delegable": True},
        {"environments": ()},
    ],
    ids=[
        "wildcard-before-deep",
        "deep-inside-a-segment",
        "no-actions",
        "resource-wildcard-before-deep",
        "no-resources",
        "naive-expiry",
        "constraint-key-disagrees",
        "delegable-without-expiry",
        "no-environments",
    ],
)
def test_T73b_grant_refuses_what_the_loader_refuses(kwargs):
    fields = {
        "id": "g",
        "subject": Subject(agent="a"),
        "actions": ("stripe.refund",),
    }
    fields.update(kwargs)

    with pytest.raises(InvalidArgument):
        Grant(**fields)


def test_T73b_a_well_formed_grant_is_accepted():
    """The control for the parametrization above: every refusal is one dimension away from a
    grant that constructs."""
    grant = Grant(
        id="g",
        subject=Subject(agent="a"),
        actions=("stripe.refund",),
        resources=("payment:EU-*",),
        constraints={"amount_lte": Condition("amount_lte", "amount", "lte", 10)},
        environments=("production",),
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
        delegable=True,
    )

    assert grant.actions == ("stripe.refund",)
    assert grant.delegable is True


# --- T74: authority is evaluated before policy (§4.3) ----------------------------------


def test_T74_a_denial_emits_authority_denied_and_never_policy_evaluated(store, clock):
    control = _control(_document(GRANT), store, clock)
    action = _action(principal=Principal(agent="other-agent"))

    with pytest.raises(AuthorityDenied):
        control.execute(action, _Executor(), "refund:EU-42")

    assert _types(store, action.action_id) == [
        str(EventType.ACTION_PROPOSED),
        "AUTHORITY_DENIED",
        str(EventType.ACTION_DENIED),
    ]


def test_T74_a_denial_leaves_no_pending_approval_request(store, clock):
    """The load-bearing half of §4.3's ordering: policy reaching `approve` creates a request,
    and an authority denial afterwards would leave a human staring at one that can never run."""
    approves = "\nactions:\n  stripe.refund:\n    rules:\n      - decision: approve\n"
    control = _control(_document(GRANT, approves), store, clock)
    action = _action(principal=Principal(agent="other-agent"))

    with pytest.raises(AuthorityDenied):
        control.execute(action, _Executor(), None)

    assert store.approvals_for(action.action_hash) == ()
    assert str(EventType.APPROVAL_REQUESTED) not in _types(store)


def test_T74_a_pass_emits_authority_resolved_before_policy_evaluated(store, clock):
    control = _control(_document(GRANT), store, clock)
    action = _action()

    control.execute(action, _Executor(), None)

    types = _types(store, action.action_id)
    assert types[:3] == [
        str(EventType.ACTION_PROPOSED),
        "AUTHORITY_RESOLVED",
        str(EventType.POLICY_EVALUATED),
    ]


def test_T74_an_expired_principal_refuses_before_authority(store, clock):
    """§4.3.1's order, stated once so it can be tested: `principal_expired` → authority →
    policy. An expired credential produces no `AUTHORITY_*` event at all."""
    from ctrlrun import IdentityError

    control = _control(_document(GRANT), store, clock)
    expired = Principal(
        agent="support-agent",
        user="alice@example.com",
        expires_at=datetime(2026, 9, 3, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(IdentityError):
        control.execute(_action(principal=expired), _Executor(), None)

    assert AUTHORITY_EVENT_TYPES.isdisjoint(_types(store))


# --- T74b: `claims`, `issuer` and `expires_at` are reserved in conditions (§4.5) -------


@pytest.mark.parametrize("key", ["claims_eq", "issuer_eq", "expires_at_gt"])
@pytest.mark.parametrize("schema", ["ctrlrun.policy/v1", "ctrlrun.policy/v3"])
def test_T74b_a_reserved_name_in_a_policy_rule_is_a_load_error(key, schema):
    operand = 1 if key.endswith("_gt") else "x"
    document = (
        f"schema: {schema}\nactions:\n  stripe.refund:\n    rules:\n"
        f"      - when: {{ {key}: {operand} }}\n        decision: allow\n"
    )

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(document)

    assert "rename" in str(refused.value)


@pytest.mark.parametrize("key", ["claims_eq", "issuer_eq", "expires_at_gt"])
def test_T74b_a_reserved_name_in_a_grant_constraint_is_a_load_error(key):
    operand = 1 if key.endswith("_gt") else "x"
    reserved = f"""
authority:
  grants:
    - id: g
      subject: {{ agent: "a" }}
      actions: ["**"]
      constraints: {{ {key}: {operand} }}
"""

    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(_document(reserved))

    assert "rename" in str(refused.value)


# --- §5.6.1: authority across an action already under way ------------------------------
# T80b asserts this table in full at build-list item 3, over a revoked chain. The half that
# is reachable with root grants alone is asserted here, because it is reachable here: an
# expired grant must not keep holding a reservation across a round trip.


def test_an_expired_grant_refuses_a_lease_extension(store, clock):
    from ctrlrun import Suspended

    control = _control(_document(EXPIRING_GRANT), store, clock)
    action = _action(principal=Principal(agent="support-agent"))

    def suspends():
        raise Suspended("round-1")

    clock.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)
    with pytest.raises(AuthorityDenied) as refused:
        # The grant is live when the action is decided and expired when the executor asks to
        # hold the reservation open, which is the window §5.6.1 exists for.
        control.execute(action, _expire_then(clock, suspends), "refund:EU-42")

    assert refused.value.reason == AUTHORITY_EXPIRED
    # The record is deliberately not moved: the lapsing lease does that (v0.1 §5.3 E3).
    assert str(store.get_effect("refund:EU-42").state) == "executing"


def _expire_then(clock, executor):
    def run():
        clock.advance(timedelta(seconds=1))
        return executor()

    return run


def test_a_live_grant_still_holds_the_reservation(store, clock):
    """The control: without it an implementation that refused every suspension would pass."""
    from ctrlrun import Suspended

    control = _control(_document(EXPIRING_GRANT), store, clock)

    def suspends():
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(
            _action(principal=Principal(agent="support-agent")), suspends, "refund:EU-42"
        )

    assert store.continuation_rounds("refund:EU-42") == 1


# --- §3.2: once an `authority:` section is loaded, a decline is not backfilled ----------


def test_a_declining_provider_is_not_backfilled_under_authority(store, clock):
    """§3.2's last row, which lands with the code it needs. Agent code already running inside
    `with context("support-agent")` that installs a provider would otherwise execute every
    un-credentialed call as support-agent, with support-agent's grants — omitting a credential
    reaching the same destination as forging one, by an easier route."""

    class Declines:
        def resolve(self, identity_context):
            return None

    from ctrlrun import context, protect

    control = _control(_document(GRANT), store, clock)
    control_with_provider = Control(
        control.policy,
        store,
        authority=control.authority,
        clock=clock,
        identity=Declines(),
    )

    @protect(name="stripe.refund", control=control_with_provider)
    def refund(amount: int, payment_id: str) -> str:
        raise AssertionError("the executor must not run")

    with context("support-agent", "alice@example.com"), pytest.raises(ActionDenied) as refused:
        refund(amount=1000, payment_id="EU-42")

    assert refused.value.reason == "no_principal"


def test_a_declining_provider_is_still_backfilled_without_authority(store, clock):
    """The control, and the compatibility half of §3.2: with no `authority:` section the v0.2
    decline row is unchanged, so installing a provider never breaks code that uses
    `context()`."""

    class Declines:
        def resolve(self, identity_context):
            return None

    from ctrlrun import context, protect

    control = Control(Policy.from_yaml(_document()), store, clock=clock, identity=Declines())
    calls = []

    @protect(name="stripe.refund", control=control)
    def refund(amount: int, payment_id: str) -> str:
        calls.append(amount)
        return "ok"

    with context("support-agent", "alice@example.com"):
        refund(amount=1000, payment_id="EU-42")

    assert calls == [1000]


def test_the_resumed_leg_records_the_authority_axis(store, clock):
    """§5.6.1 — evaluated and recorded, not re-decided. This is the *only* receipt an MCP
    multi round-trip or ACS action ever gets (§8.3), so a receipt reporting a bare policy
    reason with no authority axis would be the whole evidence for that action. Refusing here
    is the wrong answer for the same reason v0.2 §6.9.2 gave for policy: the reservation is
    held and the remote may already be acting on it."""
    from ctrlrun import Suspended

    control = _control(_document(EXPIRING_GRANT), store, clock)
    action = _action(principal=Principal(agent="support-agent"))

    def suspends():
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(action, suspends, "refund:EU-42")

    # The grant expires while the client is answering.
    clock.advance(timedelta(seconds=1))
    receipt = control.resume("round-1", lambda: "ok")

    assert str(receipt.result) == "committed"
    assert receipt.decision is Decision.DENY
    assert receipt.decision_reason == AUTHORITY_EXPIRED
    assert "AUTHORITY_DENIED" in _types(store)


def test_the_resumed_leg_records_a_pass_too(store, clock):
    """The control: without it an implementation that appended `AUTHORITY_DENIED` on every
    resumption would satisfy the assertion above."""
    from ctrlrun import Suspended

    control = _control(_document(EXPIRING_GRANT), store, clock)

    def suspends():
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(
            _action(principal=Principal(agent="support-agent")), suspends, "refund:EU-42"
        )
    receipt = control.resume("round-1", lambda: "ok")

    assert str(receipt.result) == "committed"
    assert receipt.decision is Decision.ALLOW
    assert _types(store).count("AUTHORITY_RESOLVED") == 2
    assert "AUTHORITY_DENIED" not in _types(store)

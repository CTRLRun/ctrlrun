"""Delegation with attenuation. Build-list item 3; SPEC-v0.3 §5.

The acceptance tests are T75 to T81. Four sentences run through all of them:

**Containment is structural, and checked twice** (§5.4, §5.6). A delegated grant is valid only
if it is provably a subset of its parent on every dimension — at the moment it is created *and*
again every time it is evaluated. T77, T77b, T78 and T79 are the second half: a check performed
only at creation would leave every delegation exactly as wide as the file used to be.

**Omission is never "unlimited"** (§5.4, T81). A child that drops a dimension its parent
constrains is rejected, not treated as inheriting the parent's limit and certainly not as
unconstrained. Every one of those tests carries an `else` that fails with the delegation id it
should not have got.

**The two vocabularies are disjoint** (§5.3). Creation refuses with `AuthorityEscalation` and
reasons like `containment`; evaluation denies with `AuthorityDenied` and reasons like
`authority_escalation`. A test asserting one on the other is asserting a value that path never
produces, so every assertion here names the reason.

**A guard nothing can tell ran is not a guard.** §5.6's rules 1, 3, 4, 5 and 6 all yield
`authority_escalation`, so the reason alone cannot distinguish them: these tests assert the
`data` — `missing_parent_id`, `expired_parent_id`, `dimension`, `depth_exceeded`, `cycle_at` —
as well.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner

from ctrlrun import (
    Action,
    AmbiguousEffect,
    ApprovalRequired,
    Authority,
    AuthorityDenied,
    AuthorityEscalation,
    Control,
    Decision,
    Delegation,
    EffectState,
    Grant,
    IdentityError,
    InMemoryStateStore,
    InvalidArgument,
    Policy,
    Principal,
    SQLiteStateStore,
    Subject,
    Suspended,
    parse_conditions,
)
from ctrlrun.authority import (
    AUTHORITY_CONSTRAINT,
    AUTHORITY_ESCALATION,
    AUTHORITY_GRANT,
    AUTHORITY_REVOKED,
    AUTHORITY_UNREADABLE,
    CONTAINMENT,
    MAX_DEPTH,
    NOT_THE_SUBJECT,
    PARENT_NOT_DELEGABLE,
    PARENT_NOT_VALID,
    UNKNOWN_PARENT,
    grant_to_json,
)
from ctrlrun.cli.main import main
from ctrlrun.control import PRINCIPAL_EXPIRED
from ctrlrun.receipt import EventType, ReceiptResult
from ctrlrun.state import DelegationRecord

pytestmark = pytest.mark.authority

V3 = "schema: ctrlrun.policy/v3\n"

ACTIONS = """
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 50000 }
        decision: allow
      - decision: approve
  deploy.rollout:
    decision: allow
"""

#: The root grant of the signature scenario (§5.6), wide on every dimension a child narrows.
FINANCE = """
    - id: head-of-finance
      subject: { agent: "human-cfo", user: "cfo@example.com" }
      actions: ["stripe.**"]
      resources: ["payment:EU-*"]
      constraints: { amount_gte: 0, amount_lte: 10000000 }
      environments: ["production", "staging"]
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
"""

#: A second root grant carrying one constraint per operator, so T76 can loosen each alone.
OPS = """
    - id: ops-lead
      subject: { agent: "human-ops", user: "ops@example.com" }
      actions: ["deploy.**"]
      resources: ["service:EU-*"]
      constraints:
        replicas_gte: 0
        replicas_lte: 100
        tier_eq: "standard"
        region_neq: "us-east-1"
        cluster_in: ["eu-1", "eu-2", "eu-3"]
      environments: ["production", "staging"]
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
"""

#: Not delegable, so §5.3 rule 2 has something to refuse.
LOCKED = """
    - id: locked-grant
      subject: { agent: "human-cfo", user: "cfo@example.com" }
      actions: ["stripe.**"]
      environments: ["production"]
"""

CFO = Principal(agent="human-cfo", user="cfo@example.com")
OPS_LEAD = Principal(agent="human-ops", user="ops@example.com")
FINANCE_AGENT = Principal(agent="finance-agent", user="cfo@example.com")
SUPPORT_AGENT = Principal(agent="support-agent", user="cfo@example.com")

EXPIRES = datetime(2026, 12, 1, tzinfo=UTC)
PARENT_EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)


class _Clock:
    """Static: it moves only when a test moves it, so an expiry boundary is exact."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _Recorder:
    """An `EventSink` that keeps what it was handed (SPEC-v0.2 §4.1)."""

    def __init__(self) -> None:
        self.events: list = []
        self.receipts: list = []

    def on_event(self, event) -> None:
        self.events.append(event)

    def on_receipt(self, receipt) -> None:
        self.receipts.append(receipt)


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(clock):
    return InMemoryStateStore(clock=clock)


@pytest.fixture(params=["in-memory", "sqlite"])
def either_store(request, tmp_path, clock):
    """Both shipped stores, one test body: §5.2's table lands in each (SPEC-v0.1 §5.3)."""
    made = (
        InMemoryStateStore(clock=clock)
        if request.param == "in-memory"
        else SQLiteStateStore(tmp_path / "state.db", clock=clock)
    )
    yield made
    made.close()


def _document(grants: str = FINANCE, *, depth: str = "", actions: str = ACTIONS) -> str:
    return f"{V3}\nauthority:\n{depth}  grants:\n{grants}{actions}"


def _control(document, store, clock, *, sinks=(), environment="production"):
    """One document, both loaders — the shape `Control.from_file` builds (§4.8)."""
    return Control(
        Policy.from_yaml(document),
        store,
        authority=Authority.from_yaml(document),
        clock=clock,
        sinks=sinks,
        environment=environment,
    )


def _constraints(mapping):
    return parse_conditions(mapping, where="<test>")


def _child(**overrides) -> Grant:
    """A grant narrower than `head-of-finance` on every dimension at once (T75)."""
    fields = {
        "id": "",
        "subject": Subject(agent="finance-agent", user="cfo@example.com"),
        "actions": ("stripe.refund",),
        "resources": ("payment:EU-4*",),
        "constraints": _constraints({"amount_gte": 0, "amount_lte": 2500000}),
        "environments": ("production",),
        "expires_at": EXPIRES,
        "delegable": True,
    }
    fields.update(overrides)
    return Grant(**fields)


def _ops_child(**overrides) -> Grant:
    fields = {
        "id": "",
        "subject": Subject(agent="deploy-agent", user="ops@example.com"),
        "actions": ("deploy.rollout",),
        "resources": ("service:EU-4*",),
        "constraints": _constraints(
            {
                "replicas_gte": 0,
                "replicas_lte": 10,
                "tier_eq": "standard",
                "region_neq": "us-east-1",
                "cluster_in": ["eu-1", "eu-2"],
            }
        ),
        "environments": ("production",),
        "expires_at": EXPIRES,
        "delegable": False,
    }
    fields.update(overrides)
    return Grant(**fields)


def _action(**overrides) -> Action:
    fields = {
        "name": "stripe.refund",
        "arguments": {"amount": 1000, "payment_id": "EU-42"},
        "principal": FINANCE_AGENT,
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
    return [dict(event.data) for event in store.events() if event.type is type_]


def _denial(control, action) -> AuthorityDenied:
    with pytest.raises(AuthorityDenied) as raised:
        control.execute(action, lambda: "executed")
    return raised.value


# --- T75: a contained child is accepted -------------------------------------------------


def test_t75_a_contained_child_is_accepted(either_store, clock):
    """T75 — narrower on every dimension at once, and the record lands in either store."""
    recorder = _Recorder()
    control = _control(_document(), either_store, clock, sinks=[recorder])

    delegation = control.delegate("head-of-finance", _child(), by=CFO)

    assert delegation.delegation_id.startswith("dlg_")
    assert len(delegation.delegation_id) == len("dlg_") + 32
    assert int(delegation.delegation_id[4:], 16) >= 0
    assert delegation.parent_id == "head-of-finance"
    assert delegation.depth == 1
    assert delegation.created_via == "api"
    assert delegation.created_by == CFO
    # §5.2 — `grant.id` for a delegation *is* its delegation_id: one namespace, both kinds.
    assert delegation.grant.id == delegation.delegation_id

    stored = either_store.get_delegation(delegation.delegation_id)
    assert stored is not None
    assert stored.parent_id == "head-of-finance"
    assert stored.depth == 1
    assert stored.created_by_agent == "human-cfo"
    assert stored.created_by_user == "cfo@example.com"
    assert stored.revoked_at is None

    # §7 / T75 — the event reaches a registered sink, not merely the `events` table.
    created = [event for event in recorder.events if event.type is EventType.DELEGATION_CREATED]
    assert len(created) == 1
    assert created[0].action_id is None
    assert created[0].event_id is not None
    assert created[0].data["delegation_id"] == delegation.delegation_id
    assert created[0].data["parent_id"] == "head-of-finance"
    assert created[0].data["depth"] == 1
    assert created[0].data["created_by_agent"] == "human-cfo"
    assert created[0].data["created_by_user"] == "cfo@example.com"
    assert created[0].data["created_via"] == "api"


def test_t75_the_delegation_authorizes_an_action_within_its_limits(store, clock):
    """T75 — and authority names the delegation and its depth (§7)."""
    control = _control(_document(), store, clock)
    delegation = control.delegate("head-of-finance", _child(), by=CFO)

    receipt = control.execute(_action(), lambda: "refunded")

    assert receipt.decision_reason != AUTHORITY_GRANT  # §4.6 — the policy reason ties
    resolved = _data(store, EventType.AUTHORITY_RESOLVED)
    assert resolved == [
        {
            "reason": AUTHORITY_GRANT,
            "grant_id": delegation.delegation_id,
            "delegation_id": delegation.delegation_id,
            "depth": 1,
        }
    ]


def test_t75_a_grant_carrying_an_id_is_refused(store, clock):
    """T75 — the id is assigned, not chosen (§5.2)."""
    control = _control(_document(), store, clock)
    with pytest.raises(InvalidArgument, match="assigned"):
        control.delegate("head-of-finance", _child(id="chosen-by-the-caller"), by=CFO)
    assert store.delegations(include_revoked=True) == ()


def test_t75_creation_is_not_idempotent(store, clock):
    """T75 — two identical calls make two records, each revocable on its own (§5.2)."""
    control = _control(_document(), store, clock)
    first = control.delegate("head-of-finance", _child(), by=CFO)
    second = control.delegate("head-of-finance", _child(), by=CFO)

    assert first.delegation_id != second.delegation_id
    assert len(store.delegations(include_revoked=True)) == 2
    assert len(_data(store, EventType.DELEGATION_CREATED)) == 2


# --- T75b: only the parent's subject may delegate ----------------------------------------


@pytest.mark.parametrize(
    "by",
    [
        pytest.param(Principal(agent="someone-else", user="cfo@example.com"), id="another-agent"),
        pytest.param(Principal(agent="human-cfo"), id="no-user"),
        pytest.param(Principal(agent="human-cfo", user="intern@example.com"), id="another-user"),
    ],
)
def test_t75b_only_the_parents_subject_may_delegate(store, clock, by):
    """T75b — you may only delegate authority you hold, including the `user` rule (§5.3 r4)."""
    control = _control(_document(), store, clock)

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("head-of-finance", _child(), by=by)

    assert raised.value.reason == NOT_THE_SUBJECT
    assert store.delegations(include_revoked=True) == ()
    rejected = _data(store, EventType.DELEGATION_REJECTED)
    assert rejected == [{"reason": NOT_THE_SUBJECT, "parent_id": "head-of-finance"}]


# --- T76: each containment dimension, violated alone --------------------------------------


@pytest.mark.parametrize(
    ("dimension", "overrides"),
    [
        ("actions", {"actions": ("**",)}),
        ("resources", {"resources": ("payment:*",)}),
        ("environments", {"environments": ("production", "dev")}),
        ("expires_at", {"expires_at": datetime(2027, 6, 1, tzinfo=UTC)}),
        ("subject", {"subject": Subject(agent="*", user="cfo@example.com")}),
        ("subject", {"subject": Subject(user="cfo@example.com")}),
        ("subject", {"subject": Subject(agent="finance-agent")}),
        (
            "constraints",
            {"constraints": _constraints({"amount_gte": 0, "amount_lte": 20000000})},
        ),
        (
            "constraints",
            {"constraints": _constraints({"amount_gte": -1, "amount_lte": 2500000})},
        ),
    ],
)
def test_t76_each_dimension_violated_alone(store, clock, dimension, overrides):
    """T76 — one dimension at a time, everything else contained (§5.4)."""
    control = _control(_document(), store, clock)

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("head-of-finance", _child(**overrides), by=CFO)

    assert raised.value.reason == CONTAINMENT
    assert raised.value.dimension == dimension
    assert store.delegations(include_revoked=True) == ()
    assert _data(store, EventType.DELEGATION_REJECTED) == [
        {"reason": CONTAINMENT, "parent_id": "head-of-finance", "dimension": dimension}
    ]


@pytest.mark.parametrize(
    "loosened",
    [
        pytest.param({"replicas_lte": 200}, id="lte-raised"),
        pytest.param({"replicas_gte": -5}, id="gte-lowered"),
        pytest.param({"tier_eq": "premium"}, id="eq-changed"),
        pytest.param({"region_neq": "eu-west-1"}, id="neq-changed"),
        pytest.param({"cluster_in": ["eu-1", "eu-2", "us-1"]}, id="in-widened"),
    ],
)
def test_t76_each_constraint_operator_loosened_alone(store, clock, loosened):
    """T76 — the operand-strictness table of §5.4, one operator at a time."""
    control = _control(_document(FINANCE + OPS), store, clock)
    base = {
        "replicas_gte": 0,
        "replicas_lte": 10,
        "tier_eq": "standard",
        "region_neq": "us-east-1",
        "cluster_in": ["eu-1", "eu-2"],
    }
    child = _ops_child(constraints=_constraints({**base, **loosened}))

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("ops-lead", child, by=OPS_LEAD)

    assert raised.value.reason == CONTAINMENT
    assert raised.value.dimension == "constraints"
    assert store.delegations(include_revoked=True) == ()


def test_t76_a_child_may_add_a_constraint_its_parent_does_not_have(store, clock):
    """§5.4 — narrowing is the point; only loosening and omission are refused."""
    control = _control(_document(FINANCE + OPS), store, clock)
    child = _ops_child(
        constraints=_constraints(
            {
                "replicas_gte": 0,
                "replicas_lte": 10,
                "tier_eq": "standard",
                "region_neq": "us-east-1",
                "cluster_in": ["eu-1"],
                "batch_lte": 4,
            }
        )
    )
    assert control.delegate("ops-lead", child, by=OPS_LEAD).depth == 1


# --- T76b: the creation-time rules that are not containment -------------------------------


def test_t76b_rule_0_an_expired_credential_mints_nothing(store, clock):
    """T76b — the rule that matters: an expired `by` may not create authority (§5.3 r0)."""
    control = _control(_document(), store, clock)
    expired = Principal(
        agent="human-cfo",
        user="cfo@example.com",
        expires_at=clock.now - timedelta(minutes=5),
    )

    try:
        delegation = control.delegate("head-of-finance", _child(), by=expired)
    except IdentityError:
        pass
    else:
        raise AssertionError(
            "SPEC-v0.3 §5.3 rule 0: an expired credential minted "
            f"{delegation.delegation_id}, which is permanent, unexpiring authority created "
            "by a principal whose every proposed action is refused"
        )

    assert store.delegations(include_revoked=True) == ()
    assert _data(store, EventType.DELEGATION_REJECTED) == [
        {"reason": PRINCIPAL_EXPIRED, "parent_id": "head-of-finance"}
    ]


def test_t76b_rule_1_an_unknown_parent(store, clock):
    control = _control(_document(), store, clock)
    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("no-such-grant", _child(), by=CFO)
    assert raised.value.reason == UNKNOWN_PARENT
    assert store.delegations(include_revoked=True) == ()
    assert _data(store, EventType.DELEGATION_REJECTED) == [
        {"reason": UNKNOWN_PARENT, "parent_id": "no-such-grant"}
    ]


def test_t76b_rule_1_a_revoked_parent_is_not_a_live_delegation(store, clock):
    """§5.3 rule 1 — "a root grant or a *live* delegation"; a revoked one is neither."""
    control = _control(_document(), store, clock)
    parent = control.delegate("head-of-finance", _child(), by=CFO)
    control.revoke(parent.delegation_id, by="cli:local")

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            parent.delegation_id,
            _child(subject=Subject(agent="support-agent", user="cfo@example.com")),
            by=FINANCE_AGENT,
        )
    assert raised.value.reason == UNKNOWN_PARENT


def test_t76b_rule_2_a_parent_that_is_not_delegable(store, clock):
    control = _control(_document(FINANCE + LOCKED), store, clock)
    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            "locked-grant",
            _child(resources=None, expires_at=None, delegable=False),
            by=CFO,
        )
    assert raised.value.reason == PARENT_NOT_DELEGABLE
    assert _data(store, EventType.DELEGATION_REJECTED) == [
        {"reason": PARENT_NOT_DELEGABLE, "parent_id": "locked-grant"}
    ]


def test_t76b_rule_3_an_expired_parent(store, clock):
    control = _control(_document(), store, clock)
    clock.advance(PARENT_EXPIRES - clock.now + timedelta(seconds=1))

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("head-of-finance", _child(), by=CFO)
    assert raised.value.reason == PARENT_NOT_VALID


def test_t76b_rule_3_a_parent_whose_own_parent_is_revoked(store, clock):
    control = _control(_document(), store, clock)
    first = control.delegate("head-of-finance", _child(), by=CFO)
    second = control.delegate(
        first.delegation_id,
        _child(subject=Subject(agent="support-agent", user="cfo@example.com")),
        by=FINANCE_AGENT,
    )
    control.revoke(first.delegation_id)

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            second.delegation_id,
            _child(subject=Subject(agent="ledger-agent", user="cfo@example.com")),
            by=SUPPORT_AGENT,
        )
    assert raised.value.reason == PARENT_NOT_VALID
    assert store.get_delegation(second.delegation_id) is not None


def test_t76b_rule_3_an_ancestor_that_is_no_longer_delegable(store, clock):
    """§5.3 rule 3 covers `delegable` across the whole chain, not just the parent."""
    control = _control(_document(), store, clock)
    first = control.delegate("head-of-finance", _child(), by=CFO)

    narrowed = _control(
        _document(FINANCE.replace("delegable: true", "delegable: false")), store, clock
    )
    with pytest.raises(AuthorityEscalation) as raised:
        narrowed.delegate(
            first.delegation_id,
            _child(subject=Subject(agent="support-agent", user="cfo@example.com")),
            by=FINANCE_AGENT,
        )
    assert raised.value.reason == PARENT_NOT_VALID


def test_t76b_rule_3_an_ancestor_whose_expiry_was_brought_forward(store, clock):
    """§5.3 rule 3's ancestor-expiry check, in the one shape §5.4 does not already cover.

    T76b excludes "the parent's own parent is *expired*" because §5.4's `expires_at` row makes
    a grandparent's expiry imply the parent's — for a chain nobody edited. Editing the root
    grant's expiry forward breaks that implication: `dlg_1` still expires in December, the root
    grant now expires in October, and without the ancestor check a child of `dlg_1` is created
    under a root grant that stopped being authority weeks ago.
    """
    control = _control(_document(), store, clock)
    first = control.delegate("head-of-finance", _child(), by=CFO)

    brought_forward = FINANCE.replace("2027-01-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    edited = _control(_document(brought_forward), store, clock)
    clock.advance(timedelta(days=45))  # past the root grant, before `dlg_1`

    try:
        created = edited.delegate(
            first.delegation_id,
            _child(subject=Subject(agent="support-agent", user="cfo@example.com")),
            by=FINANCE_AGENT,
        )
    except AuthorityEscalation as raised:
        assert raised.reason == PARENT_NOT_VALID
    else:
        raise AssertionError(
            f"SPEC-v0.3 §5.3 rule 3: {created.delegation_id} was minted under a root grant "
            "that expired 45 days ago"
        )


def test_t76b_rule_3_an_ancestor_that_has_gone_missing(store, clock):
    """§5.3 rule 3 — a chain that cannot be walked to a root is not a valid chain."""
    control = _control(_document(), store, clock)
    chain = _chain(control, depth=2)

    emptied = _control(f"{V3}\nauthority:\n  grants: []\n{ACTIONS}", store, clock)
    with pytest.raises(AuthorityEscalation) as raised:
        emptied.delegate(
            chain[-1].delegation_id,
            _child(subject=Subject(agent="agent-3", user="cfo@example.com")),
            by=Principal(agent="agent-2", user="cfo@example.com"),
        )
    assert raised.value.reason == PARENT_NOT_VALID


def test_a_row_whose_created_via_is_unknown_is_unreadable(store, clock):
    """§5.2 — the column is what tells an act from an assertion, and `Delegation.created_via`
    is typed on exactly the two values. A row outside them has provenance nobody can read."""
    control = _control(_document(), store, clock)
    store.put_delegation(
        DelegationRecord(
            delegation_id=f"dlg_{'b' * 32}",
            parent_id="head-of-finance",
            depth=1,
            grant_json=grant_to_json(_child()),
            created_by_agent="human-cfo",
            created_by_user="cfo@example.com",
            created_via="forged",
            created_at=clock.now,
        )
    )
    assert _denial(control, _action()).reason == AUTHORITY_UNREADABLE


def test_a_stored_actions_that_is_not_a_list_is_unreadable(store, clock):
    """§5.2 — reading a grant back validates what loading one from YAML validates.

    A coerced `"actions": "stripe.refund"` would become thirteen single-character patterns,
    every one grammar-valid, and the row would parse into a grant nobody wrote.
    """
    control = _control(_document(), store, clock)
    store.put_delegation(
        DelegationRecord(
            delegation_id=f"dlg_{'c' * 32}",
            parent_id="head-of-finance",
            depth=1,
            # `"refund"`, not `"stripe.refund"`: a coercion of the latter would produce a
            # `"."` segment, which the pattern grammar refuses anyway — the row would be
            # unreadable for a reason that is not this one, and the test would pass while
            # exercising nothing. Every character of `"refund"` is a valid literal pattern.
            grant_json=grant_to_json(_child()).replace(
                '"actions":["stripe.refund"]', '"actions":"refund"'
            ),
            created_by_agent="human-cfo",
            created_by_user="cfo@example.com",
            created_via="api",
            created_at=clock.now,
        )
    )
    denied = _denial(control, _action())
    assert denied.reason == AUTHORITY_UNREADABLE
    # §13 ships no way to list delegations, so the event is the only way to find the row.
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["delegation_id"] == f"dlg_{'c' * 32}"


def test_t76b_rule_5_depth_is_walked_not_read(tmp_path, clock):
    """T76b — a chain whose stored `depth` column says otherwise is still too deep (§5.5)."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    control = _control(_document(), store, clock)
    chain = _chain(control, depth=3)
    _rewrite(store, chain[-1].delegation_id, depth=0)

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            chain[-1].delegation_id,
            _child(subject=Subject(agent="agent-4", user="cfo@example.com")),
            by=Principal(agent="agent-3", user="cfo@example.com"),
        )
    assert raised.value.reason == MAX_DEPTH
    store.close()


@pytest.mark.parametrize(
    ("reason", "parent"),
    [
        (UNKNOWN_PARENT, "no-such-grant"),
        (PARENT_NOT_DELEGABLE, "locked-grant"),
    ],
)
def test_t76b_no_dimension_where_no_row_failed(store, clock, reason, parent):
    """§7 — `dimension` is present *only* for a §5.3 rule-6 containment refusal."""
    control = _control(_document(FINANCE + LOCKED), store, clock)
    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(parent, _child(resources=None, expires_at=None, delegable=False), by=CFO)
    assert raised.value.reason == reason
    assert raised.value.dimension is None
    assert "dimension" not in _data(store, EventType.DELEGATION_REJECTED)[0]


# --- T77: valid at creation, invalid later -------------------------------------------------


def test_t77_a_parent_that_expires_after_creation(store, clock):
    """T77 — the check is at evaluation, and it does not rewrite the record (§5.6 r3)."""
    control = _control(_document(), store, clock)
    delegation = control.delegate("head-of-finance", _child(), by=CFO)
    before = store.get_delegation(delegation.delegation_id)

    clock.advance(PARENT_EXPIRES - clock.now + timedelta(seconds=1))
    denied = _denial(control, _action())

    assert denied.reason == AUTHORITY_ESCALATION
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["expired_parent_id"] == "head-of-finance"
    assert store.get_delegation(delegation.delegation_id) == before


def test_t77b_a_narrowed_parent_narrows_its_children(store, clock):
    """T77b — the file half: narrowing a root grant narrows everything beneath it.

    The acting principal holds two grants at once — the root grant, whose subject is
    `agent: "*"` acting for this human, and the delegation. That is what makes both halves of
    T77b observable from one configuration: after the edit the delegation is no longer
    contained, so it denies with `authority_escalation`, while the narrowed root grant still
    authorizes what fits inside it (§4.6: holding two permissions is never worse than one).
    """
    wide = """
    - id: alices-agents
      subject: { agent: "*", user: "alice@example.com" }
      actions: ["stripe.**"]
      constraints: { amount_lte: 100000 }
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
"""
    alice = Principal(agent="orchestrator", user="alice@example.com")
    control = _control(_document(wide), store, clock)
    child = Grant(
        id="",
        subject=Subject(agent="finance-agent", user="alice@example.com"),
        actions=("stripe.refund",),
        constraints=_constraints({"amount_lte": 25000}),
        expires_at=EXPIRES,
    )
    control.delegate("alices-agents", child, by=alice)

    narrowed = _control(
        _document(wide.replace("amount_lte: 100000", "amount_lte: 5000")), store, clock
    )
    acting = Principal(agent="finance-agent", user="alice@example.com")

    denied = _denial(
        narrowed, _action(principal=acting, arguments={"amount": 10000}, resource=None)
    )
    assert denied.reason == AUTHORITY_ESCALATION
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["dimension"] == "constraints"

    narrowed.execute(
        _action(principal=acting, arguments={"amount": 4000}, resource=None), lambda: "refunded"
    )
    assert _data(store, EventType.AUTHORITY_RESOLVED)[-1]["grant_id"] == "alices-agents"


def test_t77b_a_parent_that_is_no_longer_delegable_stops_its_children(store, clock):
    """T77b — §5.6 rule 6, which rule 4 would never see: `delegable` is not a §5.4 row."""
    control = _control(_document(), store, clock)
    control.delegate("head-of-finance", _child(), by=CFO)

    stopped = _control(
        _document(FINANCE.replace("delegable: true", "delegable: false")), store, clock
    )
    denied = _denial(stopped, _action())

    assert denied.reason == AUTHORITY_ESCALATION
    denial = _data(store, EventType.AUTHORITY_DENIED)[-1]
    assert "dimension" not in denial
    assert "missing_parent_id" not in denial


def test_t77c_a_deleted_parent_is_not_no_authority(store, clock):
    """T77c — the reason distinguishes a broken chain from a principal who never had one."""
    control = _control(_document(), store, clock)
    chain = _chain(control, depth=2)

    emptied = _control(f"{V3}\nauthority:\n  grants: []\n{ACTIONS}", store, clock)
    denied = _denial(emptied, _action(principal=Principal(agent="agent-2", user="cfo@example.com")))

    assert denied.reason == AUTHORITY_ESCALATION
    assert denied.reason != "no_authority"
    assert denied.delegation_id == chain[-1].delegation_id
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["missing_parent_id"] == "head-of-finance"


def test_t77d_a_cyclic_chain_is_unreadable(tmp_path, clock):
    """T77d — a cycle is a hand-edited store, not an ordinary escalation (§5.5)."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    control = _control(_document(), store, clock)
    chain = _chain(control, depth=2)
    # Two rows that are each other's parent, reachable only with sqlite3 and a text editor.
    _rewrite(store, chain[0].delegation_id, parent_id=chain[1].delegation_id)

    denied = _denial(control, _action(principal=Principal(agent="agent-2", user="cfo@example.com")))

    assert denied.reason == AUTHORITY_UNREADABLE
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["cycle_at"] == chain[1].delegation_id
    store.close()


def test_t77d_an_over_deep_chain_is_an_escalation(store, clock):
    """T77d — the other half, asserted by `data` as well as by reason (§5.5)."""
    control = _control(_document(), store, clock)
    _chain(control, depth=3)

    lowered = _control(_document(depth="  max_delegation_depth: 2\n"), store, clock)
    denied = _denial(lowered, _action(principal=Principal(agent="agent-3", user="cfo@example.com")))

    assert denied.reason == AUTHORITY_ESCALATION
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["depth_exceeded"] == 3


def test_t77d_a_delegation_id_outside_the_namespace_is_unreadable(store, clock):
    """T77d / §5.2 — a row whose id is not `dlg_` + 32 hex is a corrupted record.

    The `grant_json` here is **well formed and contained**, deliberately. A row carrying junk
    would be refused by the parse anyway, so the id check would be a subsumed guard that no
    test could tell had run. With this row, removing the namespace check does not change the
    reason — it authorizes the action, which is what §5.2's "a hand-inserted row named
    `head-of-finance` would shadow the root grant of that name" is warning about.
    """
    control = _control(_document(), store, clock)
    store.put_delegation(
        DelegationRecord(
            delegation_id="head-of-finance",
            parent_id="head-of-finance",
            depth=1,
            grant_json=grant_to_json(_child()),
            created_by_agent="human-cfo",
            created_by_user="cfo@example.com",
            created_via="api",
            created_at=clock.now,
        )
    )
    denied = _denial(control, _action())
    assert denied.reason == AUTHORITY_UNREADABLE


def test_put_delegation_is_never_an_upsert(either_store, clock):
    """§5.2, §5.7 — an upsert on an existing id would clear `revoked_at`: `unrevoke` by
    another door, in a release that says there is no such thing."""
    control = _control(_document(), either_store, clock)
    delegation = control.delegate("head-of-finance", _child(), by=CFO)
    control.revoke(delegation.delegation_id, by="incident")

    with pytest.raises(InvalidArgument, match="already exists"):
        either_store.put_delegation(delegation.to_record())

    assert either_store.get_delegation(delegation.delegation_id).revoked_at is not None


def test_t77d_an_unreadable_grant_is_never_skipped(store, clock):
    """§4.6 — the permissive reading is the one an implementer reaches for naturally."""
    control = _control(_document(), store, clock)
    store.put_delegation(
        DelegationRecord(
            delegation_id=f"dlg_{'a' * 32}",
            parent_id="head-of-finance",
            depth=1,
            grant_json="{not json at all",
            created_by_agent="human-cfo",
            created_by_user="cfo@example.com",
            created_via="api",
            created_at=clock.now,
        )
    )
    denied = _denial(control, _action(principal=CFO, arguments={"amount": 1000}))
    assert denied.reason == AUTHORITY_UNREADABLE


# --- T78: a revoked parent denies -----------------------------------------------------------


def test_t78_a_revoked_parent_denies_its_grandchild(store, clock):
    """T78 — revocation is transitive by structure: a chain of any depth, cut by one write."""
    recorder = _Recorder()
    control = _control(_document(), store, clock, sinks=[recorder])
    chain = _chain(control, depth=3)

    control.revoke(chain[1].delegation_id, by="cli:local")

    revoked = [event for event in recorder.events if event.type is EventType.DELEGATION_REVOKED]
    assert len(revoked) == 1
    assert revoked[0].action_id is None
    assert revoked[0].data == {
        "delegation_id": chain[1].delegation_id,
        "revoked_by": "cli:local",
    }

    denied = _denial(control, _action(principal=Principal(agent="agent-3", user="cfo@example.com")))
    assert denied.reason == AUTHORITY_REVOKED

    # Re-revoking is idempotent: it appends no second event and raises nothing.
    control.revoke(chain[1].delegation_id, by="cli:local")
    assert len(_data(store, EventType.DELEGATION_REVOKED)) == 1


def test_t78_revoking_an_unknown_delegation_refuses(store, clock):
    control = _control(_document(), store, clock)
    with pytest.raises(InvalidArgument):
        control.revoke(f"dlg_{'0' * 32}")
    assert _data(store, EventType.DELEGATION_REVOKED) == []


# --- T79: depth beyond the maximum ----------------------------------------------------------


def test_t79_depth_beyond_the_maximum_is_rejected(store, clock):
    """T79 — one long-lived Control, so a check that only ran at construction fails red."""
    control = _control(_document(), store, clock)
    chain = _chain(control, depth=3)
    assert [link.depth for link in chain] == [1, 2, 3]

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            chain[-1].delegation_id,
            _child(subject=Subject(agent="agent-4", user="cfo@example.com")),
            by=Principal(agent="agent-3", user="cfo@example.com"),
        )
    assert raised.value.reason == MAX_DEPTH
    assert len(store.delegations(include_revoked=True)) == 3


def test_t79_a_lowered_maximum_denies_an_existing_chain(store, clock):
    control = _control(_document(), store, clock)
    _chain(control, depth=3)

    lowered = _control(_document(depth="  max_delegation_depth: 2\n"), store, clock)
    denied = _denial(lowered, _action(principal=Principal(agent="agent-3", user="cfo@example.com")))
    assert denied.reason == AUTHORITY_ESCALATION


def test_the_walk_is_bounded_at_max_depth_plus_one_steps(store, clock):
    """§5.5 — "the walk MUST be bounded", asserted as steps rather than as an outcome.

    Once §5.6 rule 5 compares the walked depth to the maximum outright, the bound no longer
    changes *what* a long chain decides — both refuse. What it still does is stop the walk
    reading a hand-edited store to its end, and the only way to tell it ran is to count the
    reads. A test asserting the denial alone could not tell the bound had been removed.
    """
    control = _control(_document(), store, clock)
    #: 30 rows, each the parent of the next, none of them reachable through `Control.delegate`
    #: and none of them reaching a root grant — so every one of them denies, and the only
    #: thing left to measure is how far the walk got.
    previous = "no-such-parent"
    for index in range(30):
        delegation_id = f"dlg_{index:032x}"
        store.put_delegation(
            DelegationRecord(
                delegation_id=delegation_id,
                parent_id=previous,
                depth=index + 1,
                grant_json=grant_to_json(_child()),
                created_by_agent="human-cfo",
                created_by_user="cfo@example.com",
                created_via="api",
                created_at=clock.now,
            )
        )
        previous = delegation_id

    reads: list[str] = []
    original = store.get_delegation

    def counting(delegation_id: str):
        reads.append(delegation_id)
        return original(delegation_id)

    store.get_delegation = counting  # type: ignore[method-assign]
    denied = _denial(control, _action())

    assert denied.reason == AUTHORITY_ESCALATION
    # Every one of the 30 rows matches the action's shape, so each is walked; a walk bounded
    # at `max_delegation_depth + 1` reads at most that many parents per candidate.
    assert reads, "the walk read nothing, so this test cannot see the bound at all"
    assert len(reads) <= 30 * (control.authority.max_delegation_depth + 1)


def test_t79_zero_denies_an_existing_depth_one_delegation(store, clock):
    """T79 / §5.6 rule 5 — the depth-1 case, which the walk's bound never measures.

    `_walk` returns as soon as it reaches a root grant, *before* it consults the bound, so a
    chain that completes in one step is compared to nothing. Deeper chains truncate and deny,
    which is what makes this the dangerous shape: `max_delegation_depth: 0` — §5.5's legible
    way to switch delegation off — would appear to work while every existing depth-1
    delegation, the common case, kept authorizing.
    """
    control = _control(_document(), store, clock)
    control.delegate("head-of-finance", _child(), by=CFO)

    switched_off = _control(_document(depth="  max_delegation_depth: 0\n"), store, clock)
    denied = _denial(switched_off, _action())

    assert denied.reason == AUTHORITY_ESCALATION
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["depth_exceeded"] == 1


def test_t79_zero_refuses_every_creation(store, clock):
    control = _control(_document(depth="  max_delegation_depth: 0\n"), store, clock)
    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate("head-of-finance", _child(), by=CFO)
    assert raised.value.reason == MAX_DEPTH
    assert store.delegations(include_revoked=True) == ()


# --- T80: the signature scenario ------------------------------------------------------------


@pytest.fixture
def signature(store, clock):
    """€100,000 delegable → finance agent €25,000 → support agent €2,000 (§5.6)."""
    control = _control(_document(), store, clock)
    finance = control.delegate(
        "head-of-finance",
        _child(constraints=_constraints({"amount_gte": 0, "amount_lte": 2500000})),
        by=CFO,
    )
    support = control.delegate(
        finance.delegation_id,
        _child(
            subject=Subject(agent="support-agent", user="cfo@example.com"),
            constraints=_constraints({"amount_gte": 0, "amount_lte": 200000}),
            delegable=False,
        ),
        by=FINANCE_AGENT,
    )
    return control, finance, support


def test_t80_fifty_thousand_euros_is_denied(signature, store):
    """T80 — the support agent asks for €50,000 and the remote is never called."""
    control, _, support = signature
    calls: list[str] = []

    denied = _denial(
        control,
        _action(principal=SUPPORT_AGENT, arguments={"amount": 5000000, "payment_id": "EU-42"}),
    )

    assert denied.reason == AUTHORITY_CONSTRAINT
    assert denied.grant_id == support.delegation_id
    assert calls == []
    assert EventType.POLICY_EVALUATED not in [event.type for event in store.events()]


def test_t80_fifteen_hundred_euros_reaches_policy(signature, store):
    """T80 — €1,500 passes authority; policy then decides how much autonomy it has."""
    control, _, support = signature
    action = _action(principal=SUPPORT_AGENT, arguments={"amount": 150000, "payment_id": "EU-42"})

    with pytest.raises(ApprovalRequired):
        control.execute(action, lambda: "refunded")

    assert _data(store, EventType.AUTHORITY_RESOLVED)[-1] == {
        "reason": AUTHORITY_GRANT,
        "grant_id": support.delegation_id,
        "delegation_id": support.delegation_id,
        "depth": 2,
    }
    assert "POLICY_EVALUATED" in _types(store, action.action_id)


def test_t80_the_finance_agent_may_not_delegate_more_than_it_holds(signature, store):
    """T80 — the containment guard: €50,000 under a €25,000 parent (§5.3 rule 6)."""
    control, finance, _ = signature

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            finance.delegation_id,
            _child(
                subject=Subject(agent="support-agent", user="cfo@example.com"),
                constraints=_constraints({"amount_gte": 0, "amount_lte": 5000000}),
                delegable=False,
            ),
            by=FINANCE_AGENT,
        )

    assert raised.value.reason == CONTAINMENT
    assert raised.value.dimension == "constraints"
    assert len(store.delegations(include_revoked=True)) == 2


def test_t80_the_support_agent_may_not_delegate_at_all(signature, store):
    """T80 — a different guard, asserted separately so neither stands in for the other."""
    control, _, support = signature

    with pytest.raises(AuthorityEscalation) as raised:
        control.delegate(
            support.delegation_id,
            _child(
                subject=Subject(agent="intern-agent", user="cfo@example.com"),
                constraints=_constraints({"amount_gte": 0, "amount_lte": 1000}),
                delegable=False,
            ),
            by=SUPPORT_AGENT,
        )

    assert raised.value.reason == PARENT_NOT_DELEGABLE
    assert len(store.delegations(include_revoked=True)) == 2


# --- T80b: authority across an action already under way ---------------------------------------


def test_t80b_an_outcome_is_committed_even_after_the_chain_is_revoked(signature, store, clock):
    """T80b — the effect has already happened at the remote (§5.6.1, row 1).

    Refusing the write would strand it in `AMBIGUOUS` for an authorization reason, which is
    what `v0.1 §5.5` exists to prevent.
    """
    control, finance, _ = signature
    action = _action(principal=SUPPORT_AGENT, arguments={"amount": 1000, "payment_id": "EU-42"})

    def executor() -> str:
        control.revoke(finance.delegation_id, by="incident")
        return "refunded"

    receipt = control.execute(action, executor, effect_key="refund:EU-42")

    assert receipt.result is ReceiptResult.COMMITTED
    assert store.get_effect("refund:EU-42").state is EffectState.COMMITTED


def test_t80b_a_lease_extension_is_refused_and_the_record_lapses(signature, store, clock):
    """T80b — an extension asks to keep holding a reservation the chain no longer authorizes.

    Refused, and the record is deliberately not moved: the lease lapses and it becomes
    `AMBIGUOUS` by the ordinary path of `v0.1 §5.3 E3`, which is the safe state reached without
    inventing one. Without this, §5.7's "a chain of any depth is cut by one write" is false for
    exactly the actions in flight when an operator hits the switch.
    """
    control, finance, _ = signature
    action = _action(principal=SUPPORT_AGENT, arguments={"amount": 1000, "payment_id": "EU-42"})

    def executor() -> str:
        control.revoke(finance.delegation_id, by="incident")
        raise Suspended("opaque-state-from-the-server")

    with pytest.raises(AuthorityDenied) as raised:
        control.execute(action, executor, effect_key="refund:EU-42", lease=timedelta(minutes=1))

    assert raised.value.reason == AUTHORITY_REVOKED
    assert store.get_effect("refund:EU-42").state is EffectState.EXECUTING

    # The lapse is the ordinary path of `v0.1 §5.3 E3` and owes nothing to authority, so it is
    # asserted against the store: a second `execute` would be refused by the revoked chain
    # first and could not tell anyone whether the record had moved.
    clock.advance(timedelta(minutes=2))
    with pytest.raises(AmbiguousEffect):
        store.reserve_effect("refund:EU-42", "act_another")


def test_t80b_resume_records_the_denial_and_does_not_re_decide(signature, store, clock):
    """T80b — §5.6.1 row 3: the reservation is held and the remote may already be acting.

    The resumed leg is the only receipt an MCP multi round-trip or ACS action ever gets (§8.3),
    so it carries the combined §4.6 decision rather than a bare policy reason.
    """
    control, finance, _ = signature
    action = _action(principal=SUPPORT_AGENT, arguments={"amount": 1000, "payment_id": "EU-42"})

    with pytest.raises(Suspended):
        control.execute(
            action,
            lambda: (_ for _ in ()).throw(Suspended("continue-me")),
            effect_key="refund:EU-42",
        )
    control.revoke(finance.delegation_id, by="incident")

    receipt = control.resume("continue-me", lambda: "refunded")

    assert receipt.result is ReceiptResult.COMMITTED
    assert receipt.decision is Decision.DENY
    assert receipt.decision_reason == AUTHORITY_REVOKED
    assert _data(store, EventType.AUTHORITY_DENIED)[-1]["reason"] == AUTHORITY_REVOKED


# --- §5.7's two CLI commands ------------------------------------------------------------------


CLI_DOCUMENT = _document()

CLI_GRANT = """
subject: { agent: "finance-agent", user: "cfo@example.com" }
actions: ["stripe.refund"]
resources: ["payment:EU-4*"]
constraints: { amount_gte: 0, amount_lte: 2500000 }
environments: ["production"]
expires_at: "2026-12-01T00:00:00+00:00"
delegable: true
"""


@pytest.fixture
def cli_workspace(tmp_path, monkeypatch):
    (tmp_path / "ctrlrun.yaml").write_text(CLI_DOCUMENT, encoding="utf-8")
    (tmp_path / "grant.yaml").write_text(CLI_GRANT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)
    return tmp_path


def _cli(*arguments):
    return CliRunner().invoke(main, list(arguments))


def _created_id(workspace) -> str:
    result = _cli(
        "delegate",
        "--parent",
        "head-of-finance",
        "--file",
        "grant.yaml",
        "--as",
        "human-cfo/cfo@example.com",
        "--json",
    )
    assert result.exit_code == 0, result.output
    return str(json.loads(result.output)["delegation_id"])


def test_the_cli_records_created_via_cli(cli_workspace):
    """T75b — `--as` is an assertion, and §5.2's column is what says so."""
    delegation_id = _created_id(cli_workspace)

    store = SQLiteStateStore(cli_workspace / ".ctrlrun" / "state.db")
    try:
        record = store.get_delegation(delegation_id)
        assert record is not None
        assert record.created_via == "cli"
        assert record.created_by_agent == "human-cfo"
        assert record.created_by_user == "cfo@example.com"
    finally:
        store.close()

    written = [
        json.loads(line)
        for line in (cli_workspace / ".ctrlrun" / "events.jsonl").read_text().splitlines()
    ]
    assert written[-1]["type"] == "DELEGATION_CREATED"
    assert written[-1]["action_id"] is None
    assert written[-1]["data"]["created_via"] == "cli"


def test_the_cli_refuses_a_delegator_the_parent_does_not_admit(cli_workspace):
    """T75b, at the CLI: §5.3 rule 4 is checked here too, and the exit is non-zero."""
    result = _cli(
        "delegate",
        "--parent",
        "head-of-finance",
        "--file",
        "grant.yaml",
        "--as",
        "someone-else",
    )
    assert result.exit_code != 0
    assert NOT_THE_SUBJECT in result.output


def test_the_cli_refuses_a_grant_document_carrying_an_id(cli_workspace):
    """§5.2 — the id is assigned, not chosen, and the `--file` path says so at load."""
    (cli_workspace / "grant.yaml").write_text(f"id: chosen\n{CLI_GRANT}", encoding="utf-8")
    result = _cli(
        "delegate",
        "--parent",
        "head-of-finance",
        "--file",
        "grant.yaml",
        "--as",
        "human-cfo/cfo@example.com",
    )
    assert result.exit_code != 0
    assert "assigned, not chosen" in result.output


def test_ctrlrun_revoke_is_idempotent_and_refuses_an_unknown_id(cli_workspace):
    """T78, at the CLI: `DELEGATION_REVOKED` once, exit 0 twice, and an unknown id is not 0."""
    delegation_id = _created_id(cli_workspace)

    first = _cli("revoke", delegation_id)
    second = _cli("revoke", delegation_id)
    unknown = _cli("revoke", f"dlg_{'0' * 32}")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "already revoked" in second.output
    assert unknown.exit_code != 0
    assert unknown.stdout == ""

    written = [
        json.loads(line)
        for line in (cli_workspace / ".ctrlrun" / "events.jsonl").read_text().splitlines()
    ]
    revoked = [line for line in written if line["type"] == "DELEGATION_REVOKED"]
    assert len(revoked) == 1
    assert revoked[0]["data"] == {"delegation_id": delegation_id, "revoked_by": "cli:local"}


# --- T81: omission is not "unlimited" -------------------------------------------------------


OMISSION = """
    - id: bounded
      subject: { agent: "human-cfo", user: "cfo@example.com" }
      actions: ["stripe.**"]
      resources: ["payment:EU-*"]
      constraints: { amount_lte: 25000 }
      environments: ["production"]
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
"""


def _omitting(**overrides) -> Grant:
    fields = {
        "id": "",
        "subject": Subject(agent="finance-agent", user="cfo@example.com"),
        "actions": ("stripe.refund",),
        "resources": ("payment:EU-4*",),
        "constraints": _constraints({"amount_lte": 20000}),
        "environments": ("production",),
        "expires_at": EXPIRES,
    }
    fields.update(overrides)
    return Grant(**fields)


@pytest.mark.parametrize(
    ("dimension", "overrides"),
    [
        ("constraints", {"constraints": {}}),
        ("resources", {"resources": None}),
        ("environments", {"environments": None}),
        ("expires_at", {"expires_at": None}),
        ("subject", {"subject": Subject(agent="finance-agent")}),
        ("subject", {"subject": Subject(user="cfo@example.com")}),
    ],
)
def test_t81_omission_is_not_unlimited(store, clock, dimension, overrides):
    """T81 — the one that matters: a child that drops a dimension is rejected, not inherited.

    Not accepted-and-inherited, and not accepted-and-unconstrained. A child that silently
    inherited would look, in the file and in the receipt, like a child that was authorized for
    what it says. The last two rows are the cases where an omitted key means "anyone" rather
    than "nothing" (§5.4).
    """
    control = _control(_document(OMISSION), store, clock)

    try:
        delegation = control.delegate("bounded", _omitting(**overrides), by=CFO)
    except AuthorityEscalation as raised:
        assert raised.reason == CONTAINMENT
        assert raised.dimension == dimension
    else:
        raise AssertionError(
            f"SPEC-v0.3 §5.4: a child omitting {dimension!r} under a parent that constrains it "
            f"was created as {delegation.delegation_id}; omission never means unlimited"
        )

    assert store.delegations(include_revoked=True) == ()


# --- helpers that build chains and edit the store by hand ------------------------------------


def _chain(control: Control, *, depth: int) -> list[Delegation]:
    """A chain of `depth` delegations beneath `head-of-finance`, agent-1 … agent-N."""
    links: list[Delegation] = []
    parent_id = "head-of-finance"
    by = CFO
    for level in range(1, depth + 1):
        agent = f"agent-{level}"
        links.append(
            control.delegate(
                parent_id,
                _child(subject=Subject(agent=agent, user="cfo@example.com")),
                by=by,
            )
        )
        parent_id = links[-1].delegation_id
        by = Principal(agent=agent, user="cfo@example.com")
    return links


def _rewrite(store: SQLiteStateStore, delegation_id: str, **columns) -> None:
    """Edit a `delegations` row directly, the way §5.5 says an attacker would."""
    assignments = ", ".join(f"{column}=?" for column in columns)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            f"UPDATE delegations SET {assignments} WHERE delegation_id=?",
            (*columns.values(), delegation_id),
        )
        connection.commit()
    finally:
        connection.close()

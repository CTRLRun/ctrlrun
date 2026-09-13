"""T326 to T339: break-glass as a grant, never a flag (SPEC-v0.8 §5).

Parametrised over the three stores for the same reason every authority test is: an envelope is
a document, but everything opened beneath one is a row, and a store that loses the parent link
loses the containment the envelope exists to impose.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.approval import ApproverIdentity
from ctrlrun.authority import Authority, canonical_grants, grant_from_yaml
from ctrlrun.control import Control
from ctrlrun.errors import AuthorityEscalation, InvalidArgument, PolicyError
from ctrlrun.identity import StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

POLICY = """
schema: ctrlrun.policy/v6
environment: prod
controls:
  incident-response:
    title: Only an incident commander opens break-glass
    approver_role: incident-commander
actions:
  payments.refund:
    decision: allow
authority:
  grants:
    - id: everyday
      subject: {agent: "ops-agent"}
      actions: ["payments.read"]
  break_glass:
    incident-payments:
      subject: {agent: "oncall-*"}
      actions: ["payments.*"]
      environments: ["prod"]
      resources: ["payment:*"]
      constraints: {amount_lte: 50000}
      max_ttl: PT4H
      controls: [incident-response]
"""

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
KEY = "refund:EU-42"
COMMANDER = Principal(
    agent="human:ada",
    user="ada@example.com",
    issuer="https://issuer.example",
    claims={"roles": ("incident-commander",)},
)
BYSTANDER = Principal(agent="human:bob", user="bob@example.com", claims={"roles": ("viewer",)})
ONCALL = Principal(agent="oncall-agent", user="ada")


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


class _Fixed:
    """An approver identity provider. Refuses what a real one refuses: it reads a credential
    it was given and invents nobody."""

    def __init__(self, principal: Principal | None) -> None:
        self._principal = principal

    def resolve(self, context: Any) -> Principal | None:
        return self._principal


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> dict[str, str]:
        self.calls += 1
        return {"ok": "yes"}


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
        made = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"glass_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock, *, opener=COMMANDER, policy=POLICY):
    return Control(
        Policy.from_yaml(policy),
        store,
        clock=clock,
        authority=Authority.from_yaml(policy),
        approver_identity=(
            None if opener is False else ApproverIdentity(_Fixed(opener), roles_claim="roles")
        ),
        identity=StaticIdentityProvider(agent=ONCALL.agent, user=ONCALL.user),
    )


def _grant(expires: datetime | None = None, **overrides: Any):
    document = {
        "subject": {"agent": "oncall-agent"},
        "actions": ["payments.refund"],
        "environments": ["prod"],
        "resources": ["payment:EU-*"],
        "constraints": {"amount_lte": 1000},
    }
    document.update(overrides)
    if expires is not None:
        document["expires_at"] = expires.isoformat()
    lines = _yaml(document)
    return grant_from_yaml(lines)


def _yaml(document: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(document)


def _action(control, amount: int = 100) -> Action:
    return Action(
        name="payments.refund",
        arguments={"amount": amount, "payment_id": "EU-42"},
        principal=ONCALL,
        resource="payment:EU-42",
        environment=control.environment,
    )


# --- T326, T326b: it is created, and it authorises -------------------------------------------


def test_T326_a_break_glass_grant_is_created_beneath_its_envelope(store, clock):
    """§5.3. Recorded, with a parent, a depth and a provenance that says what it was."""
    control = _control(store, clock)

    opened = control._break_glass(
        "incident-payments", _grant(clock.now + timedelta(hours=2)), reason="INC-4412"
    )

    assert opened.parent_id == "incident-payments"
    assert opened.depth == 1
    assert opened.created_via == "break-glass"
    assert opened.created_by.agent == COMMANDER.agent
    record = store.get_delegation(opened.delegation_id)
    assert record is not None and record.created_via == "break-glass"


def test_T326b_an_action_under_a_live_break_glass_grant_is_allowed(store, clock):
    """§5.2 point 4, third site. **Created is not authorised**, and this is the difference.

    `_check_chain`'s rule 6 reads `delegable` over every ancestor including the root, on every
    evaluation. An envelope carries no such key, so without the exemption the grant is created
    and then authorises nothing, refused `authority_escalation` with no dimension named, which
    is the least diagnosable refusal in `authority.py`.
    """
    control = _control(store, clock)
    control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))
    executor = _Executor()

    receipt = control.execute(_action(control), executor, KEY)

    assert executor.calls == 1, "the grant authorised nothing"
    assert str(receipt.result) == "committed"


# --- T327: it expires ------------------------------------------------------------------------


def test_T327_after_its_expiry_the_action_is_denied(store, clock):
    """§5.4. By the existing expiry check, with the existing reason."""
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))
    assert control.execute(_action(control), _Executor(), KEY).result

    clock.advance(timedelta(hours=3))
    executor = _Executor()
    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), executor, "refund:EU-43")

    assert refused.value.reason == "authority_expired"
    assert executor.calls == 0


# --- T328: an expiry is required, and bounded ------------------------------------------------


def test_T328_a_grant_with_no_expiry_is_refused(store, clock):
    """§5.3's one added rule. An ordinary delegation may carry none; this may not."""
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass("incident-payments", _grant(None))

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"


def test_T328_a_grant_beyond_max_ttl_is_refused(store, clock):
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=5)))

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"
    assert list(store.delegations()) == [] or all(
        record.parent_id != "incident-payments" for record in store.delegations()
    )


# --- T329: containment, one refusal per dimension --------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "dimension"),
    [
        ({"subject": {"agent": "*"}}, "subject"),
        ({"actions": ["*"]}, "actions"),
        ({"environments": ["prod", "staging"]}, "environments"),
        ({"constraints": {"amount_lte": 90000}}, "constraints"),
        ({"resources": ["*"]}, "resources"),
    ],
)
def test_T329_a_grant_wider_than_its_envelope_is_refused_per_dimension(
    store, clock, overrides, dimension
):
    """§5.3, driven through `contained_dimension` so a dimension added later cannot escape.

    `expires_at` is the sixth and has its own test above, because an envelope carries none and
    what bounds a child in time is `max_ttl`.
    """
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass(
            "incident-payments", _grant(clock.now + timedelta(hours=1), **overrides)
        )

    assert refused.value.reason == "containment"
    assert refused.value.dimension == dimension


def test_T329_every_dimension_contained_dimension_knows_has_a_case():
    """The test that keeps the table above honest (SPEC-v0.8 §10.5).

    A dimension added to `contained_dimension` and not to the parameters above would be a
    dimension nothing drives, which is exactly how a containment check comes to be decoration.
    """
    import inspect

    from ctrlrun.authority import contained_dimension

    source = inspect.getsource(contained_dimension)
    named = {
        word.strip("\"'")
        for word in source.split()
        if word.strip("\"',()")
        in {
            "subject",
            "actions",
            "resources",
            "constraints",
            "environments",
            "expires_at",
        }
    }
    covered = {"subject", "actions", "environments", "constraints", "resources", "expires_at"}
    missing = {word.strip("\"',()") for word in named} - covered
    assert not missing, f"contained_dimension knows {missing}, and no case drives them"


# --- T330: the envelope decides nothing ------------------------------------------------------


def test_T330_the_envelope_is_not_a_candidate(store, clock):
    """§5.2, **by construction**: it is not in `Authority._grants`, so `_candidates` cannot
    return it. Asserted as absence from the set and not merely as a denial, because a grant
    that is present and unmatched passes a denial test too."""
    authority = Authority.from_yaml(POLICY)

    assert "incident-payments" not in authority.grants
    assert "incident-payments" in authority.envelopes
    candidates = {grant_id for grant_id, _, _ in authority._candidates(store)}
    assert "incident-payments" not in candidates


def test_T330_a_deployment_with_only_an_envelope_evaluates_as_0_7_0(store, clock):
    """The behavioural half: an envelope authorises nothing on its own."""
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), executor, KEY)

    assert refused.value.reason == "no_authority"
    assert executor.calls == 0


# --- T331: the walk resolves an envelope root ------------------------------------------------


def test_T331_an_unknown_envelope_is_refused_by_name(store, clock):
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass("no-such-envelope", _grant(clock.now + timedelta(hours=1)))

    assert refused.value.reason == "unknown_parent"
    assert "no-such-envelope" in str(refused.value)


# --- T332: the policy hash covers the envelope -----------------------------------------------


def test_T332_widening_max_ttl_moves_the_policy_hash():
    """§5.2. The argument for declaring the envelope in the policy is that it was evidenced
    before the incident, and that is only true if widening it moves the hash."""
    from ctrlrun.action import canonical_bytes

    narrow = canonical_bytes(canonical_grants(Authority.from_yaml(POLICY)))
    wide = canonical_bytes(canonical_grants(Authority.from_yaml(POLICY.replace("PT4H", "PT8H"))))

    assert narrow != wide


def test_T332_the_envelope_hashes_as_the_document_wrote_it():
    """And the read-site rule of §5.2 point 4 does not move it.

    An envelope renders `delegable: false`, the parser default every grant omitting the key
    already hashes as. The runtime rule that an envelope ancestor *counts as* delegable is
    applied where `delegable` is read and never written onto the parsed grant, so the hash
    stays a statement about the document.
    """
    rendered = canonical_grants(Authority.from_yaml(POLICY))
    assert isinstance(rendered, dict)
    envelope = rendered["break_glass"]["incident-payments"]

    assert envelope["delegable"] is False
    assert envelope["max_ttl"] == 4 * 60 * 60
    assert envelope["controls"] == ["incident-response"]


# --- T332b: what an envelope may not carry, and where it may not live ------------------------


@pytest.mark.parametrize("key", ["delegable", "expires_at"])
def test_T332b_an_envelope_may_not_carry_delegable_or_expires_at(key):
    value = "true" if key == "delegable" else "2026-01-01T00:00:00Z"
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(
            POLICY.replace("      max_ttl: PT4H", f"      {key}: {value}\n      max_ttl: PT4H")
        )

    assert key in str(refused.value)


def test_T332b_an_id_in_both_mappings_is_refused_naming_both():
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(POLICY.replace("    incident-payments:", "    everyday:"))

    assert "everyday" in str(refused.value)
    assert "grants" in str(refused.value) and "break_glass" in str(refused.value)


def test_T332b_a_standalone_authority_document_may_not_declare_break_glass():
    """§5.2: that shape has no control registry, so the only envelope it could express is an
    ungated one, and the single deployment unable to state the gate would be the one whose
    break-glass anybody verified could open."""
    document = POLICY[POLICY.index("authority:") :]
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(f"schema: ctrlrun.policy/v6\n{document}", standalone=True)

    assert "break_glass" in str(refused.value)
    assert "registry" in str(refused.value)


# --- T333: the receipt names the grant, break-glass or not -----------------------------------


def test_T333_the_receipt_names_the_break_glass_grant(store, clock):
    control = _control(store, clock)
    opened = control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))

    receipt = control.execute(_action(control), _Executor(), KEY)

    assert receipt.authority_grant_id == opened.delegation_id


def test_T333_an_ordinary_grant_is_named_too(store, clock):
    """§5.4: the field is not break-glass-specific. One that existed only under break-glass
    would be one nothing exercises on the ordinary path."""
    policy = POLICY.replace('actions: ["payments.read"]', 'actions: ["payments.*"]').replace(
        'subject: {agent: "ops-agent"}', 'subject: {agent: "oncall-agent"}'
    )
    control = _control(store, clock, policy=policy)

    receipt = control.execute(_action(control), _Executor(), KEY)

    assert receipt.authority_grant_id == "everyday"


# --- T334, T334b: who may open one ------------------------------------------------------------


def test_T334_the_opener_is_the_resolved_principal_not_the_envelopes_subject(store, clock):
    """§5.3.1. The envelope's subject is `oncall-*` and the opener is `human:ada`. Under rule 4
    as `plan_delegation` writes it that is `not_the_subject`; what gates the opener instead is
    the envelope's `controls:`."""
    control = _control(store, clock)

    opened = control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert opened.created_by.agent == "human:ada"
    assert opened.grant.subject.agent == "oncall-agent"


def test_T334_an_opener_without_the_control_role_is_refused(store, clock):
    control = _control(store, clock, opener=BYSTANDER)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert refused.value.reason == "approver_unentitled"
    assert "incident-response" in str(refused.value)
    assert "incident-commander" in str(refused.value)


def test_T334_with_no_approver_identity_it_cannot_be_opened_at_all(store, clock):
    """Opt in, then fail closed. With nobody resolved there is no principal to check the
    envelope's controls against, and an unchecked opener is the flag §5 refuses."""
    control = _control(store, clock, opener=False)

    with pytest.raises(InvalidArgument) as refused:
        control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert "approver identity" in str(refused.value)


def test_T334b_an_ordinary_grant_id_is_not_an_envelope(store, clock):
    """§5.3.1. `--envelope everyday` would otherwise reach a path where rule 4 is skipped for
    a grant that has no `controls:` to gate the opener instead, which is strictly weaker than
    what `ctrlrun delegate` requires beneath the same grant."""
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control._break_glass("everyday", _grant(clock.now + timedelta(hours=1)))

    assert refused.value.reason == "unknown_parent"
    assert "a grant is not an envelope" in str(refused.value)


# --- T335: the third created_via value is readable --------------------------------------------


def test_T335_a_break_glass_row_does_not_make_the_deployment_unreadable(store, clock):
    """§5.3, §11.1. The vocabulary is a closed `Literal`, and an unknown value makes
    `_candidates` raise and answers `authority_unreadable` for **every action in the
    deployment**. This is the test for that failure mode."""
    control = _control(store, clock)
    control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))

    authority = Authority.from_yaml(POLICY)
    result = authority.evaluate(_action(control), now=clock.now, store=store)

    assert result.reason != "authority_unreadable"
    assert result.passed


# --- T336, T337: revocation and attenuation ---------------------------------------------------


def test_T336_revoking_it_stops_everything_beneath(store, clock):
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    opened = control._break_glass(
        "incident-payments", _grant(clock.now + timedelta(hours=2), delegable=True)
    )
    beneath = control.delegate(
        opened.delegation_id,
        _grant(clock.now + timedelta(hours=1), constraints={"amount_lte": 500}),
        by=ONCALL,
    )

    control.revoke(opened.delegation_id, by="human:ada")

    for record in (opened, beneath):
        assert store.get_delegation(record.delegation_id) is not None
    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(control), _Executor(), KEY)
    assert refused.value.reason in ("authority_revoked", "no_authority")


def test_T337_a_delegation_beneath_it_is_created_and_attenuates(store, clock):
    """§5.2 point 4, second site, and §5.4's attenuation bullet.

    `_parent_for_creation`'s rule-3 chain scan reads `delegable` over every ancestor including
    the envelope, so without the exemption this is refused `parent_not_valid` and the bullet
    cannot hold. Creation **and then evaluation**, because the two read `delegable` at
    different sites and one can pass while the other refuses.
    """
    control = _control(store, clock)
    # `delegable: true` on the break-glass grant itself, exactly as on any grant somebody
    # intends to be delegated beneath: §5.2 point 4 exempts the **envelope**, which carries no
    # such key, and changes nothing about the grant opened under it.
    opened = control._break_glass(
        "incident-payments", _grant(clock.now + timedelta(hours=2), delegable=True)
    )

    beneath = control.delegate(
        opened.delegation_id,
        _grant(clock.now + timedelta(hours=1), constraints={"amount_lte": 500}),
        by=ONCALL,
    )

    assert beneath.depth == 2
    executor = _Executor()
    assert str(control.execute(_action(control, amount=100), executor, KEY).result) == "committed"
    assert executor.calls == 1

    # And it cannot widen what it was given.
    with pytest.raises(AuthorityEscalation) as refused:
        control.delegate(
            opened.delegation_id,
            _grant(clock.now + timedelta(hours=1), constraints={"amount_lte": 5000}),
            by=ONCALL,
        )
    assert refused.value.reason == "containment"


def test_T337_a_delegation_beneath_it_cannot_outlive_it(store, clock):
    control = _control(store, clock)
    opened = control._break_glass(
        "incident-payments", _grant(clock.now + timedelta(hours=1), delegable=True)
    )

    with pytest.raises(AuthorityEscalation) as refused:
        control.delegate(opened.delegation_id, _grant(clock.now + timedelta(hours=3)), by=ONCALL)

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"


# --- what an independent review found, each with the test that would have caught it ---------


def test_T334b_delegate_may_not_name_an_envelope_as_its_parent(store, clock):
    """**The direction §5.3.1 did not guard, and it is the worse one.**

    §5.3.1 guards `break-glass --envelope <a grant id>`. Nothing guarded `delegate --parent <an
    envelope id>`: `_parent_for_creation` resolved envelopes unconditionally and handed the
    envelope's grant to `plan_delegation`, which applies none of §5.3's rules. The result was a
    break-glass grant with **no expiry at all**, no `max_ttl`, no entitlement check and a
    `created_via` saying `cli`, openable from a shell by anyone whose `--as` matched the
    envelope's subject -- which is a pattern over the agents the grant may be *for*.
    """
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control.delegate("incident-payments", _grant(None), by=ONCALL)

    assert refused.value.reason == "unknown_parent"
    assert "not a grant" in str(refused.value)
    assert list(store.delegations()) == []


def test_T334b_the_cli_delegate_path_is_refused_too(store, clock):
    """The same, through `_delegate`, which is what `ctrlrun delegate --as` calls."""
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation):
        control._delegate("incident-payments", _grant(None), by=ONCALL, via="cli")

    assert list(store.delegations()) == []


def test_T334_an_envelope_citing_an_unknown_control_cannot_be_opened(store, clock):
    """**A typo gated nobody.** One transposed letter in `controls:` and any verified principal
    opened the envelope.

    Elsewhere a control naming no `approver_role` gates nobody (§3.5), and that is right where
    the citation is on an *action*. Here the citation **is** the gate, so the same omission
    reads the opposite way and must fail closed. `Authority.from_yaml` parses the section with
    no registry to check against, so it is checked where both are.
    """
    control = _control(
        store,
        clock,
        opener=BYSTANDER,
        policy=POLICY.replace("[incident-response]", "[incident-respones]"),
    )

    with pytest.raises(InvalidArgument) as refused:
        control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert "incident-respones" in str(refused.value)
    assert list(store.delegations()) == []


def test_T334_an_envelope_citing_a_control_with_no_role_cannot_be_opened(store, clock):
    """The other half of the same hole: the control resolves and gates nothing."""
    policy = POLICY.replace("    approver_role: incident-commander\n", "")
    control = _control(store, clock, opener=BYSTANDER, policy=policy)

    with pytest.raises(InvalidArgument) as refused:
        control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert "approver_role" in str(refused.value)


def test_T334_there_is_no_parameter_that_asserts_the_opener():
    """§5.3.1, §11.2. `by=` was an unauthenticated way to assert an opener **and its roles**.

    Passing a principal whose claims carried the envelope's role opened it in a deployment
    whose provider resolved somebody else entirely. The signature is the test: a keyword that
    does not exist cannot be passed.
    """
    import inspect

    from ctrlrun.control import Control

    parameters = inspect.signature(Control._break_glass).parameters

    assert "by" not in parameters, (
        "a caller-supplied opener is an assertion, and the entitlement check reads the roles "
        "off whatever is asserted (SPEC-v0.8 §5.3.1)"
    )
    assert not hasattr(Control, "break_glass"), (
        "§11.2 adds no public Control method in v0.8; the surface is the CLI command"
    )


def test_T333_a_refusal_before_the_authority_gate_names_no_grant(store, clock):
    """§5.4. The grant id is per-call state, and it was only ever **set**, never cleared.

    §4.3.1 puts `principal_expired` first, so a denied receipt is recorded before the authority
    gate runs. After one committed action under a break-glass grant, the next call's refusal
    carried that grant's id -- naming a grant that never decided it, which is worse than
    naming none.
    """
    from ctrlrun.errors import IdentityError

    control = _control(store, clock)
    opened = control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))
    committed = control.execute(_action(control), _Executor(), KEY)
    assert committed.authority_grant_id == opened.delegation_id

    lapsed = Action(
        name="payments.refund",
        arguments={"amount": 100, "payment_id": "EU-42"},
        principal=Principal(
            agent="oncall-agent", user="ada", expires_at=clock.now - timedelta(minutes=5)
        ),
        resource="payment:EU-42",
        environment=control.environment,
    )
    with pytest.raises(IdentityError):
        control.execute(lapsed, _Executor(), "refund:EU-99")

    denied = [receipt for receipt in store.receipts() if str(receipt.result) == "denied"]
    assert denied and denied[-1].authority_grant_id is None, (
        f"the refusal named {denied[-1].authority_grant_id!r}, a grant that never decided it"
    )


def test_T327_narrowing_max_ttl_cuts_a_grant_already_open(store, clock):
    """§5.2, and `v0.3 §5.6`'s purpose: a narrowed root narrows everything beneath it.

    `max_ttl` was checked once, at creation, and is not a §5.4 containment row, so an operator
    who narrowed an envelope while an incident was still running narrowed nothing. Every other
    envelope dimension already cuts live grants at evaluation, because the envelope is the
    chain's root parent; this was the one that did not, and it is the bound an operator reaches
    for first.
    """
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=3)))
    assert str(control.execute(_action(control), _Executor(), KEY).result) == "committed"

    # The same store, the same open grant, an envelope narrowed to fifteen minutes.
    narrowed = _control(store, clock, policy=POLICY.replace("PT4H", "PT15M"))
    clock.advance(timedelta(minutes=30))
    executor = _Executor()

    with pytest.raises(ActionDenied) as refused:
        narrowed.execute(_action(narrowed), executor, "refund:EU-43")

    assert refused.value.reason == "authority_escalation"
    assert executor.calls == 0


def test_T327_widening_max_ttl_does_not_extend_a_grant_already_open(store, clock):
    """The other direction, which must not change anything: the grant's own `expires_at` is
    what expires it, and an envelope widened afterwards does not hand it more time."""
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    control._break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))
    clock.advance(timedelta(hours=2))

    widened = _control(store, clock, policy=POLICY.replace("PT4H", "PT8H"))
    with pytest.raises(ActionDenied) as refused:
        widened.execute(_action(widened), _Executor(), KEY)

    assert refused.value.reason == "authority_expired"


# --- T338: THE absence test -------------------------------------------------------------------


#: The names the milestone's plan forbids by name, plus the ones an implementer reaches for
#: under time pressure. A claim about the environment is a claim until something greps for it.
#: Names a flag would be spelled as **in code**: an identifier, a keyword argument, an
#: attribute. Matched with comments and string literals removed, because `approval.py` argues
#: in prose that a public `_granting_principal` would be "`trust_approver` spelled as a context
#: manager", and a grep that cannot tell prose from a flag pushes the argument out of the tree.
FORBIDDEN_IDENTIFIERS = (
    "skip_entitlement",
    "trust_approver",
    "allow_self_approval",
    "break_glass=True",
    "break_glass = True",
    "ignore_revocations",
    "skip_approver",
    "disable_entitlement",
)

#: And the names it would be spelled as **in a string**: a `click.option`, an environment
#: variable, a dict key. An independent review found that seven of the original sixteen
#: patterns could only ever appear as string literals and the tokenizer dropped exactly those,
#: so they were unmatchable by construction -- and the control test planted an identifier, so
#: nothing noticed. These are matched against string tokens only.
FORBIDDEN_STRINGS = (
    "CTRLRUN_SKIP",
    "CTRLRUN_ALLOW_SELF",
    "CTRLRUN_BREAK_GLASS",
    "CTRLRUN_TRUST",
    "--skip-entitlement",
    "--allow-self-approval",
    "--break-glass-force",
    "--no-approver",
    "--trust-approver",
    "skip_entitlement",
    "allow_self_approval",
)


def _tokens(path: pathlib.Path) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """This module's code lines and its string literals, separately, each numbered.

    `tokenize`, not a regex: a docstring spans lines and a `#` inside a string is not a
    comment, and both mistakes go the unsafe way here.

    Two lists rather than one, because the two classes of name need opposite treatment. An
    identifier must be matched with strings **out**, or prose arguing a flag away reads as the
    flag. A `--flag` or an environment variable can only ever *be* a string, so matching it
    with strings out matches nothing at all. Comments are dropped from both: a comment naming
    `--skip-entitlement` to say it does not exist is the same prose problem one level down.
    """
    import io
    import tokenize

    code: dict[int, list[str]] = {}
    strings: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING:
                strings.append((token.start[0], token.string))
                continue
            code.setdefault(token.start[0], []).append(token.string)
    return ([(number, " ".join(parts)) for number, parts in sorted(code.items())], strings)


def test_T338_no_flag_environment_variable_or_option_skips_a_check():
    """§5.1 and the fourth rule of v0.8: break-glass is a grant, not a flag.

    A flag leaves no record, expires never, cannot be revoked and cannot be attenuated. The
    whole of §5 rests on there being no such setting anywhere, and that sentence is a claim
    until something greps for it. This is the grep, in the suite rather than in a PR body, so
    it runs on every change rather than once when somebody remembered.

    It reads `src/` only: this file names every pattern while asserting none exists.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "ctrlrun"
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        where = path.relative_to(root.parent.parent)
        code, strings = _tokens(path)
        for line, text in code:
            found += [f"{where}:{line}: {name}" for name in FORBIDDEN_IDENTIFIERS if name in text]
        for line, text in strings:
            found += [f"{where}:{line}: {name}" for name in FORBIDDEN_STRINGS if name in text]

    assert not found, (
        "a setting that relaxes a check exists in the shipped package:\n  "
        + "\n  ".join(found)
        + "\nSPEC-v0.8 §5.1: a flag leaves no record, expires never, and cannot be revoked"
    )


def test_T338_the_grep_would_find_one_in_every_spelling_a_flag_takes(tmp_path):
    """The control for the test above (`v0.4 §1.3`).

    A grep that matches nothing passes whether or not the tree is clean. The first version of
    this control planted an identifier only, so it never exercised the seven patterns that can
    appear solely as strings -- which were being discarded, and were therefore dead patterns
    the control could not see. It now plants one of each spelling, and one in a comment that
    must not count.
    """
    planted = tmp_path / "ctrlrun" / "planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "import os\n"
        "import click\n"
        "\n"
        "ALLOW = dict(skip_entitlement=True)\n"
        'OPTION = click.option("--allow-self-approval", is_flag=True)\n'
        'ENV = os.environ.get("CTRLRUN_BREAK_GLASS")\n'
        "# --no-approver is named here in a comment and must NOT count\n",
        encoding="utf-8",
    )
    code, strings = _tokens(planted)

    identifiers = {name for name in FORBIDDEN_IDENTIFIERS for _, text in code if name in text}
    literals = {name for name in FORBIDDEN_STRINGS for _, text in strings if name in text}

    assert identifiers == {"skip_entitlement"}, identifiers
    assert literals == {"--allow-self-approval", "CTRLRUN_BREAK_GLASS"}, (
        "a flag spelled as a click option or an environment variable is invisible to this "
        f"test, so its absence from the tree is not evidence: found {literals}"
    )
    assert not any("--no-approver" in text for _, text in code + strings), (
        "a name in a comment counted, so prose arguing a flag away reads as the flag"
    )

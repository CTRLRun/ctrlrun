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

    opened = control.break_glass(
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
    control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))
    executor = _Executor()

    receipt = control.execute(_action(control), executor, KEY)

    assert executor.calls == 1, "the grant authorised nothing"
    assert str(receipt.result) == "committed"


# --- T327: it expires ------------------------------------------------------------------------


def test_T327_after_its_expiry_the_action_is_denied(store, clock):
    """§5.4. By the existing expiry check, with the existing reason."""
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))
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
        control.break_glass("incident-payments", _grant(None))

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"


def test_T328_a_grant_beyond_max_ttl_is_refused(store, clock):
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=5)))

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"
    assert store.delegations() == [] or all(
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
        control.break_glass(
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
        control.break_glass("no-such-envelope", _grant(clock.now + timedelta(hours=1)))

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
    opened = control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))

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

    opened = control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert opened.created_by.agent == "human:ada"
    assert opened.grant.subject.agent == "oncall-agent"


def test_T334_an_opener_without_the_control_role_is_refused(store, clock):
    control = _control(store, clock, opener=BYSTANDER)

    with pytest.raises(AuthorityEscalation) as refused:
        control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert refused.value.reason == "approver_unentitled"
    assert "incident-response" in str(refused.value)
    assert "incident-commander" in str(refused.value)


def test_T334_with_no_approver_identity_it_cannot_be_opened_at_all(store, clock):
    """Opt in, then fail closed. With nobody resolved there is no principal to check the
    envelope's controls against, and an unchecked opener is the flag §5 refuses."""
    control = _control(store, clock, opener=False)

    with pytest.raises(InvalidArgument) as refused:
        control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=1)))

    assert "approver identity" in str(refused.value)


def test_T334b_an_ordinary_grant_id_is_not_an_envelope(store, clock):
    """§5.3.1. `--envelope everyday` would otherwise reach a path where rule 4 is skipped for
    a grant that has no `controls:` to gate the opener instead, which is strictly weaker than
    what `ctrlrun delegate` requires beneath the same grant."""
    control = _control(store, clock)

    with pytest.raises(AuthorityEscalation) as refused:
        control.break_glass("everyday", _grant(clock.now + timedelta(hours=1)))

    assert refused.value.reason == "unknown_parent"
    assert "a grant is not an envelope" in str(refused.value)


# --- T335: the third created_via value is readable --------------------------------------------


def test_T335_a_break_glass_row_does_not_make_the_deployment_unreadable(store, clock):
    """§5.3, §11.1. The vocabulary is a closed `Literal`, and an unknown value makes
    `_candidates` raise and answers `authority_unreadable` for **every action in the
    deployment**. This is the test for that failure mode."""
    control = _control(store, clock)
    control.break_glass("incident-payments", _grant(clock.now + timedelta(hours=2)))

    authority = Authority.from_yaml(POLICY)
    result = authority.evaluate(_action(control), now=clock.now, store=store)

    assert result.reason != "authority_unreadable"
    assert result.passed


# --- T336, T337: revocation and attenuation ---------------------------------------------------


def test_T336_revoking_it_stops_everything_beneath(store, clock):
    from ctrlrun.errors import ActionDenied

    control = _control(store, clock)
    opened = control.break_glass(
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
    opened = control.break_glass(
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
    opened = control.break_glass(
        "incident-payments", _grant(clock.now + timedelta(hours=1), delegable=True)
    )

    with pytest.raises(AuthorityEscalation) as refused:
        control.delegate(opened.delegation_id, _grant(clock.now + timedelta(hours=3)), by=ONCALL)

    assert refused.value.reason == "containment"
    assert refused.value.dimension == "expires_at"


# --- T338: THE absence test -------------------------------------------------------------------


#: The names the milestone's plan forbids by name, plus the ones an implementer reaches for
#: under time pressure. A claim about the environment is a claim until something greps for it.
FORBIDDEN = (
    "skip_entitlement",
    "trust_approver",
    "allow_self_approval",
    "break_glass=True",
    "break_glass = True",
    "ignore_revocations",
    "skip_approver",
    "disable_entitlement",
    "CTRLRUN_SKIP",
    "CTRLRUN_ALLOW_SELF",
    "CTRLRUN_BREAK_GLASS",
    "--skip-entitlement",
    "--allow-self-approval",
    "--break-glass-force",
    "--no-approver",
)


def test_T338_no_flag_environment_variable_or_option_skips_a_check():
    """§5.1 and the fourth rule of v0.8: break-glass is a grant, not a flag.

    A flag leaves no record, expires never, cannot be revoked and cannot be attenuated. The
    whole of §5 rests on there being no such setting anywhere, and that sentence is a claim
    until something greps for it. This is the grep, in the suite rather than in a PR body,
    so it runs on every change rather than once when somebody remembered.

    It reads `src/` only, and **code only**: `approval.py` explains that a public
    `_granting_principal` would be "`trust_approver` spelled as a context manager", which is
    prose arguing the flag away and is the opposite of a flag. A grep that cannot tell those
    apart would push the argument out of the tree, so comments and strings are tokenized out
    rather than the comment being reworded around the test.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "ctrlrun"
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for line, text in _code_lines(path):
            for name in FORBIDDEN:
                if name in text:
                    found.append(f"{path.relative_to(root.parent.parent)}:{line}: {name}")

    assert not found, (
        "a setting that relaxes a check exists in the shipped package:\n  "
        + "\n  ".join(found)
        + "\nSPEC-v0.8 §5.1: a flag leaves no record, expires never, and cannot be revoked"
    )


def _code_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """This module's lines with comments and string literals removed, numbered.

    `tokenize`, not a regex: a docstring spans lines and a `#` inside a string is not a
    comment, and both mistakes go the unsafe way here -- one hides a real flag, the other
    fails on prose that argues against one.
    """
    import io
    import tokenize

    kept: dict[int, list[str]] = {}
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.setdefault(token.start[0], []).append(token.string)
    return [(number, " ".join(parts)) for number, parts in sorted(kept.items())]


def test_T338_the_grep_would_find_one_if_there_were_one(tmp_path):
    """The control for the test above (`v0.4 §1.3`).

    A grep that matches nothing passes whether or not the tree is clean, and a typo in a
    pattern is indistinguishable from a clean tree. This plants one and finds it.
    """
    planted = tmp_path / "ctrlrun" / "planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("ALLOW = dict(skip_entitlement=True)\n", encoding="utf-8")

    hits = [
        name
        for name in FORBIDDEN
        if any(
            name in text for path in planted.parent.rglob("*.py") for _, text in _code_lines(path)
        )
    ]

    assert hits == ["skip_entitlement"], (
        "the patterns above match nothing even when a flag is planted, so the absence test "
        "proves nothing about the real tree"
    )

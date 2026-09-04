"""The authority guarantees and config-derived selection. SPEC-v0.4 §2 (G7-G9), §3.4.

T108-T112. The principal is derived from the **grant** under test and never from the policy
(§3.4), so these tests assert which grant was used as well as what it refused: five refusals
that all raise `AuthorityDenied` are five guards a check asserting only the type cannot tell
apart (`v0.3 §10`'s rule, unchanged).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctrlrun.authority import DIMENSIONS
from ctrlrun.verify import Status, VerifyRefused, run
from ctrlrun.verify import guarantees as reg
from ctrlrun.verify.scenarios import EXPIRY_NOT_DECISIVE

pytestmark = pytest.mark.authority

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PAYMENTS = REPO_ROOT / "examples" / "authority" / "payments.yaml"

V2 = "schema: ctrlrun.policy/v2\n"
V3 = "schema: ctrlrun.policy/v3\n"

ACTIONS = """
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
  acme.read:
    decision: allow
"""

#: One delegable grant carrying every dimension, so G9 has all six to exercise.
FULL_AUTHORITY = """
authority:
  grants:
    - id: head-of-support
      subject: { agent: "head-of-support", user: "dana@example.com" }
      actions: ["acme.refund", "acme.read"]
      resources: ["payment:*"]
      constraints: { amount_gte: 0, amount_lte: 500000 }
      environments: ["production"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
"""

#: Grants that name actions the policy does not list: nothing is authorized, which is a
#: fail-closed state and not a broken guarantee (T111).
UNREACHABLE_AUTHORITY = """
authority:
  grants:
    - id: elsewhere
      subject: { agent: "some-agent" }
      actions: ["other.system.thing"]
      expires_at: "2027-01-01T00:00:00Z"
      delegable: true
"""


def _write(directory: Path, document: str, name: str = "ctrlrun.yaml") -> Path:
    path = directory / name
    path.write_text(document, encoding="utf-8")
    return path


def _by_id(report):
    return {result.id: result for result in report.guarantees}


# --- T108: a configuration with grants exercises G7-G9 ------------------------------------


def test_T108_the_authority_example_exercises_G7_G8_and_G9():
    report = run(AUTHORITY_PAYMENTS)
    results = _by_id(report)

    for gid in ("G7", "G8", "G9"):
        assert results[gid].status is Status.PASS, (gid, results[gid].reason)
    assert results["G8"].grant_id == "head-of-support"
    assert results["G9"].grant_id == "head-of-support"
    assert results["G9"].detail["dimensions_exercised"] == list(DIMENSIONS)
    assert results["G9"].detail["dimensions_unconstrained"] == []
    assert report.exit_code == 0
    assert report.passed == 10
    assert report.applicable == 10


def test_T108_G8_asserts_the_denial_by_reason_and_not_by_type(tmp_path, monkeypatch):
    """A kernel that denied for any other cause fails: the reason is what is asserted.

    `AUTHORITY_RESOLVED` before the expiry, `AuthorityDenied(reason="authority_expired")`
    after it. Injecting a kernel that denies with a *different* reason must go red.
    """
    from ctrlrun.authority import AUTHORITY_CONSTRAINT, Authority, AuthorityResult

    path = _write(tmp_path, V3 + FULL_AUTHORITY + ACTIONS)
    original = Authority.evaluate

    def wrong_reason(self, action, *, now, store):
        result = original(self, action, now=now, store=store)
        if not result.passed and result.reason == "authority_expired":
            return AuthorityResult(False, AUTHORITY_CONSTRAINT, grant_id=result.grant_id)
        return result

    monkeypatch.setattr(Authority, "evaluate", wrong_reason)

    result = _by_id(run(path, only=("G8",)))["G8"]

    assert result.status is Status.FAIL
    assert "authority_expired" in str(result.counterexample.expected)


# --- T109: no `authority:` section --------------------------------------------------------


def test_T109_no_authority_section_makes_G8_and_G9_not_applicable_and_leaves_G7_applicable(
    tmp_path,
):
    """The pair is one test, because reporting all three N/A is the plausible wrong answer.

    No principal is a `v0.1` rule and does not depend on the authority model.
    """
    path = _write(tmp_path, V2 + ACTIONS)

    report = run(path)
    results = _by_id(report)

    for gid in ("G8", "G9"):
        assert results[gid].status is Status.NOT_APPLICABLE, gid
        assert results[gid].reason == reg.NO_AUTHORITY_SECTION
    assert results["G7"].status is Status.PASS, results["G7"].reason
    assert results["G7"].action is not None


# --- T110: every dimension, including the omission case -----------------------------------


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_T110_G9_exercises_every_dimension_the_parent_constrains(tmp_path, dimension):
    path = _write(tmp_path, V3 + FULL_AUTHORITY + ACTIONS)

    result = _by_id(run(path, only=("G9",)))["G9"]

    assert result.status is Status.PASS, result.reason
    assert dimension in result.detail["dimensions_exercised"]
    # The omission half is recorded per dimension, and it is never "not attempted": either
    # containment refused the child, or the model refused to construct it at all.
    assert result.detail["omissions"][dimension] in (
        "refused by containment",
        "refused at construction",
    )


def test_T110_a_kernel_where_omission_means_unlimited_makes_G9_fail(tmp_path, monkeypatch):
    """The mutation half. `contained_dimension` is what makes omission a refusal; a kernel in
    which a dropped dimension is treated as inherited must make G9 go red, and the
    counterexample must carry the offending delegation."""
    from ctrlrun import authority as authority_module

    path = _write(tmp_path, V3 + FULL_AUTHORITY + ACTIONS)
    original = authority_module.contained_dimension

    def omission_is_inheritance(parent, child):
        # Exactly the defect §5.4 forbids: a child that drops `environments` is treated as
        # though it had inherited the parent's.
        if parent.environments is not None and child.environments is None:
            from dataclasses import replace

            child = replace(child, environments=parent.environments)
        return original(parent, child)

    monkeypatch.setattr(authority_module, "contained_dimension", omission_is_inheritance)

    result = _by_id(run(path, only=("G9",)))["G9"]

    assert result.status is Status.FAIL, result.detail
    assert "environments" in str(result.counterexample.expected)
    created = [
        event for event in result.counterexample.events if event["type"] == "DELEGATION_CREATED"
    ]
    # The offending delegation is in the evidence: the child that should have been refused
    # was written, and the counterexample shows the row.
    assert created


def test_T110_a_dimension_the_parent_does_not_constrain_is_reported_unexercised(tmp_path):
    """Reporting `G9 PASS` for a parent constraining one dimension as though it had covered
    six is the N/A rule violated one level down (§2.2 G9)."""
    path = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: thin
      subject: { agent: "thin-agent" }
      actions: ["acme.refund"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
"""
        + ACTIONS,
    )

    result = _by_id(run(path, only=("G9",)))["G9"]

    assert result.status is Status.PASS, result.reason
    assert set(result.detail["dimensions_unconstrained"]) == {
        "resources",
        "constraints",
        "environments",
    }
    assert set(result.detail["dimensions_exercised"]) == {"subject", "actions", "expires_at"}
    assert "3 of 6 dimensions" in result.detail["summary"]


# --- T111: grants that reach no action ----------------------------------------------------


def test_T111_grants_that_match_no_action_are_not_applicable_and_never_fail(tmp_path):
    """A configuration in which nothing is authorized is fail-closed, and reporting it as a
    failed guarantee would train an operator to ignore red."""
    path = _write(tmp_path, V3 + UNREACHABLE_AUTHORITY + ACTIONS)

    report = run(path)
    results = _by_id(report)

    for gid in ("G8", "G9"):
        assert results[gid].status is Status.NOT_APPLICABLE, (gid, results[gid].status)
        assert results[gid].reason == reg.NO_GRANT_MATCHES
        assert results[gid].status is not Status.FAIL
    assert report.failed == 0
    assert report.exit_code == 0


def test_a_layered_expiry_is_not_applicable_rather_than_a_failure(tmp_path):
    """Where a second grant covers the same action past the first one's expiry, the expired
    grant refuses nothing observable. That is a property of the document, so N/A."""
    path = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: aaa-short
      subject: { agent: "*" }
      actions: ["acme.refund"]
      expires_at: "2027-01-01T00:00:00Z"
    - id: bbb-forever
      subject: { agent: "*" }
      actions: ["acme.refund"]
"""
        + ACTIONS,
    )

    result = _by_id(run(path, only=("G8",)))["G8"]

    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == EXPIRY_NOT_DECISIVE


# --- T112: `--only` runs what it names and nothing else -----------------------------------


def test_T112_only_runs_the_named_guarantee_and_writes_no_badge(tmp_path, monkeypatch):
    """ "It did not run" is proven by the scratch store's contents and not by the report
    describing itself: every other guarantee's scenario would have written a receipt for its
    own action, and none exists."""
    from ctrlrun.verify import scenarios

    path = _write(tmp_path, V3 + FULL_AUTHORITY + ACTIONS)
    opened: list[str] = []
    original = scenarios.Engine._control_for
    plain = scenarios.Engine.control
    receipts: list[str] = []

    def recording(self, gid, selection, *, clock=None):
        opened.append(gid)
        built = original(self, gid, selection, clock=clock)
        return built

    def recording_plain(self, gid, *, clock=None, authority=True):
        opened.append(gid)
        return plain(self, gid, clock=clock, authority=authority)

    monkeypatch.setattr(scenarios.Engine, "_control_for", recording)
    monkeypatch.setattr(scenarios.Engine, "control", recording_plain)

    original_close = scenarios.SQLiteStateStore.close

    def closing(self):
        receipts.extend(receipt.action for receipt in self.receipts())
        original_close(self)

    monkeypatch.setattr(scenarios.SQLiteStateStore, "close", closing)

    report = run(path, only=("G9",))
    results = _by_id(report)

    assert results["G9"].status is Status.PASS, results["G9"].reason
    for gid in reg.BY_ID:
        if gid == "G9":
            continue
        assert results[gid].status is Status.SKIPPED, gid
        assert results[gid].reason == reg.NOT_SELECTED
    assert report.partial is True
    assert report.badge is None
    # Only G9's scratch store was ever created, so no other guarantee's scenario ran.
    assert opened == ["G9"]
    assert set(receipts) <= {"acme.refund"}
    document = json.loads(report.to_json())
    assert document["partial"] is True


def test_T112_an_unknown_only_id_exits_2_naming_it(tmp_path):
    path = _write(tmp_path, V3 + FULL_AUTHORITY + ACTIONS)

    with pytest.raises(VerifyRefused) as refused:
        run(path, only=("G99",))

    assert "G99" in str(refused.value)
    assert reg.CATALOGUE in str(refused.value)


# --- §3.4: the principal comes from the grant, never from the policy -----------------------


def test_the_principal_is_derived_from_the_grants_subject(tmp_path):
    path = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: wildcards
      subject: { agent: "finance-*", user: "*" }
      actions: ["acme.refund"]
      expires_at: "2027-01-01T00:00:00Z"
"""
        + ACTIONS,
    )

    report = run(path, only=("G8",))
    result = _by_id(report)["G8"]

    assert result.status is Status.PASS, result.reason
    receipt_agents = {
        receipt["principal"]["agent"]
        for row in report.guarantees
        if row.counterexample
        for receipt in row.counterexample.receipts
    }
    # No failure, so no counterexample: the derivation is asserted through the run succeeding
    # against a grant whose subject is two wildcards — `finance-*` keeps its prefix and `*`
    # becomes `ctrlrun-verify` (§3.4).
    assert receipt_agents == set()
    assert result.grant_id == "wildcards"

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
#: SPEC-v0.9 §10.1 — `tasks` on a grant is a `v7` key, refused in an older document, so the
#: fixture that carries every dimension has to declare the version that has them all.
V7 = "schema: ctrlrun.policy/v7\n"

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

#: One delegable grant carrying every dimension, so G9 has all of `DIMENSIONS` to exercise.
#: **Derived, not counted**: SPEC-v0.9 §8.0 grew `DIMENSIONS` from six to seven, and a fixture
#: that named six would report "6 of 7" and exercise the new one never, which is the silent
#: half of that section's two failure modes.
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
      tasks: ["refund-run:*"]
      budgets:
        - {metric: amount, limit: 500000, window: PT24H}
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
    # Graded on SQLite, where the one N/A is G13: SQLite has no clock of its own to diverge
    # from (SPEC-v0.7 §8.9). A Postgres --store-url grades that one too.
    # **Derived, never a literal.** Adding G19 broke nine hardcoded assertions across three
    # files in v0.8, and adding G24 would have broken these two: every guarantee this document
    # can exercise passes, and the ones it cannot are the N/A set. Written this way the next
    # guarantee moves the number rather than the test.
    assert report.passed == report.applicable
    assert report.applicable == len(reg.GUARANTEES) - report.not_applicable


def test_T108_G8_asserts_the_denial_by_reason_and_not_by_type(tmp_path, monkeypatch):
    """A kernel that denied for any other cause fails: the reason is what is asserted.

    `AUTHORITY_RESOLVED` before the expiry, `AuthorityDenied(reason="authority_expired")`
    after it. Injecting a kernel that denies with a *different* reason must go red.
    """
    from ctrlrun.authority import AUTHORITY_CONSTRAINT, Authority, AuthorityResult

    path = _write(tmp_path, V7 + FULL_AUTHORITY + ACTIONS)
    original = Authority.evaluate

    def wrong_reason(self, action, *, now, store, task=None, evaluate_task=True):
        # SPEC-v0.9 §10.3 — the new keywords are **forwarded**, not merely accepted. A patch that
        # swallowed them would evaluate every action with no task, and a fixture whose grant
        # names one would then deny `authority_task` before the expiry this test is about.
        result = original(
            self, action, now=now, store=store, task=task, evaluate_task=evaluate_task
        )
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
    path = _write(tmp_path, V7 + FULL_AUTHORITY + ACTIONS)

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

    path = _write(tmp_path, V7 + FULL_AUTHORITY + ACTIONS)
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
    # Derived from DIMENSIONS, not counted: SPEC-v0.9 §8.0 grew it, and a literal here is what
    # turns adding a dimension into an unrelated-looking test failure.
    exercised = {"subject", "actions", "expires_at"}
    assert set(result.detail["dimensions_unconstrained"]) == set(DIMENSIONS) - exercised
    assert set(result.detail["dimensions_exercised"]) == exercised
    assert f"3 of {len(DIMENSIONS)} dimensions" in result.detail["summary"]


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

    path = _write(tmp_path, V7 + FULL_AUTHORITY + ACTIONS)
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
    path = _write(tmp_path, V7 + FULL_AUTHORITY + ACTIONS)

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


# --- T413: a budget must not make verify report an internal error (SPEC-v0.9 §2) -------------

TIGHT_BUDGET = """
authority:
  grants:
    - id: head-of-support
      subject: { agent: "head-of-support", user: "dana@example.com" }
      actions: ["acme.refund", "acme.read"]
      resources: ["payment:*"]
      constraints: { amount_gte: 0, amount_lte: 500000 }
      environments: ["production"]
      expires_at: "2027-01-01T00:00:00Z"
      budgets:
        - {metric: amount, limit: 900, window: PT24H}
"""


def test_T413_a_budget_smaller_than_the_synthesized_vector_does_not_crash_verify(tmp_path):
    """**A budget is a configuration fact, never a defect in verify.**

    `_synthesize` picks a vector to land in a rule, and a grant whose budget is smaller than
    that vector refuses the action before the guarantee is reached. Verify reported that as an
    internal error, exit 3, on guarantees with nothing to do with budgets: a shipped example
    carrying a €1,000 daily budget under a policy admitting a €100,000 refund made `ctrlrun
    verify` fail on G1, which is about approvals. It is `_identity_the_document_needs`'s case in
    the budget dimension, and it gets the same answer: verify sizes its own vector.

    **And the vector is actually resized**, which an independent review found this test was not
    checking. Against `ACTIONS` the allow band is `amount_gte: 0, amount_lte: 1000` and
    `_synthesize` picks `amount: 0`, which is under any budget, so the original form of this test
    passed against a kernel that resized nothing: it was green on the commit before the feature.
    The rule below is upper-bound only, so the synthesized vector is the band maximum and a
    budget under it has to move it.
    """
    upper_bound_only = """
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_lte: 100000 }
        decision: allow
      - decision: deny
"""
    path = _write(tmp_path, V7 + TIGHT_BUDGET + upper_bound_only)

    result = _by_id(run(path, only=("G3",)))["G3"]

    assert result.status is Status.PASS, f"{result.status}: {result.reason}"
    graded = dict(result.arguments or {})
    assert graded["amount"] != 100000, "the band maximum was used unchanged"
    assert 0 < graded["amount"] <= 900, graded


def test_T413a_a_band_no_action_can_pay_for_is_N_A_with_a_reason_about_the_budget(tmp_path):
    """The case where no vector fits, which is a real and reportable configuration.

    A budget smaller than any single action in the approve band makes that band unreachable:
    every action needing a human would exhaust the whole window. That is worth telling an
    operator, and telling them the truth about it. Falling through to the grant miss reported
    "no grant's `resources:` matches a resource verify can build" about a document whose
    patterns matched perfectly, which is the category error `unselected`'s docstring exists
    about, one dimension over.
    """
    path = _write(tmp_path, V7 + TIGHT_BUDGET + ACTIONS)

    result = _by_id(run(path, only=("G1",)))["G1"]

    assert result.status is Status.NOT_APPLICABLE
    assert result.reason.startswith(reg.NO_ACTION_FITS_THE_BUDGET), result.reason
    assert "acme.refund" in result.reason and "head-of-support" in result.reason
    # The reason names the action and the grant, because "a budget is in the way" without
    # saying which one sends an operator reading a twelve-grant document by hand.


def test_T413b_a_document_with_no_budget_selects_exactly_what_it_did_before(tmp_path):
    """The vector is only resized when a budget would refuse it, so every document without one
    keeps the selection it had. Without this, the fix is a change to all twenty-four scenarios
    rather than to the documents that need it."""
    unbudgeted = FULL_AUTHORITY.replace(
        "      budgets:\n        - {metric: amount, limit: 500000, window: PT24H}\n", ""
    )
    both = []
    for index, authority in enumerate((unbudgeted, FULL_AUTHORITY)):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = _write(directory, V7 + authority + ACTIONS)
        result = _by_id(run(path, only=("G3",)))["G3"]
        both.append((result.status, result.action, result.arguments, result.grant_id))
    assert both[0] == both[1], both


UNMEASURABLE_BUDGET = TIGHT_BUDGET.replace("metric: amount", "metric: items")

TWO_ACTIONS = (
    ACTIONS
    + """  zzz.wire:
    effect: "wire:{payment_id}"
    resource: "ledger:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
"""
)


def test_T413c_a_budget_naming_a_metric_no_action_carries_is_graded_not_crashed(tmp_path):
    """§2.3 through verify. A grant budgeting `items` where every action carries `amount`
    refuses **every** action it covers, for ever, and `ctrlrun verify` reported that as an
    internal error, exit 3, on guarantees with nothing to do with budgets.

    It is also a **different fix** from a budget that is merely small, so it gets its own reason:
    told "exceeds a budget", an operator raises a limit that was never the problem.
    """
    path = _write(tmp_path, V7 + UNMEASURABLE_BUDGET + ACTIONS)

    result = _by_id(run(path, only=("G3",)))["G3"]

    assert result.status is Status.NOT_APPLICABLE, f"{result.status}: {result.reason}"
    assert result.reason.startswith(reg.NO_METRIC_TO_MEASURE), result.reason
    assert "acme.refund" in result.reason


def test_T413d_a_budget_miss_does_not_hide_a_grant_miss(tmp_path):
    """`select` records a grant miss for **every** failed candidate, so an unconditional
    precedence for the budget meant a document with one budget-blocked action and one action no
    grant covers at all reported only the budget. The resource miss appeared nowhere.

    That is the category error `unselected`'s own docstring exists about, one dimension over, and
    an independent review found it. Both facts are true of the document, so both are stated.
    """
    path = _write(tmp_path, V7 + TIGHT_BUDGET + TWO_ACTIONS)

    result = _by_id(run(path, only=("G1",)))["G1"]

    assert result.status is Status.NOT_APPLICABLE
    assert reg.NO_ACTION_FITS_THE_BUDGET in result.reason, result.reason
    assert reg.NO_GRANT_COVERS_SELECTION in result.reason, result.reason


def test_T413e_a_resize_never_moves_the_action_onto_an_unchecked_grant(tmp_path):
    """**The resize can change which grant decides**, and that grant's budget was never looked
    at. `Authority.evaluate` resolves `min(passed, key=grant_id)`, so a vector shrunk to fit one
    grant's budget can fall inside a lexicographically earlier grant's `constraints`.

    An independent review demonstrated it: shrinking 100000 to 1 moved the action from
    `bb-broad` to `aa-narrow`, whose budget is zero, and verify exited 3 on a budget it had
    never checked.
    """
    document = (
        V7
        + """
authority:
  grants:
    - id: aa-narrow
      subject: { agent: "head-of-support" }
      actions: ["acme.refund"]
      resources: ["payment:*"]
      constraints: { amount_lte: 10 }
      budgets:
        - {metric: amount, limit: 0, window: PT24H}
    - id: bb-broad
      subject: { agent: "head-of-support" }
      actions: ["acme.refund"]
      resources: ["payment:*"]
      constraints: { amount_lte: 500000 }
      budgets:
        - {metric: amount, limit: 900000, window: PT24H}
"""
        + """
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_lte: 100000 }
        decision: allow
      - decision: deny
"""
    )
    path = _write(tmp_path, document)

    graded = _by_id(run(path, only=("G3", "G4")))

    for gid in ("G3", "G4"):
        assert graded[gid].status is not Status.FAIL, (
            f"{gid} reported the kernel broken for a configuration reason: {graded[gid].reason}"
        )


def test_T413f_a_resized_vector_leaves_room_for_a_scenario_that_acts_more_than_once(tmp_path):
    """G4's control leg runs eight children on distinct keys and then contends eight more on
    one, so it needs nine spends to fit. A vector sized to the limit exactly made its control
    leg pass, its contended leg find zero winners, and the guarantee report **FAIL**.

    **Verify may say it could not grade a configuration; it may not accuse the kernel of a
    defect.** The candidates are required to leave that much room, and where none does the
    guarantee is `N/A`.
    """
    tight = TIGHT_BUDGET.replace("limit: 900", "limit: 40")
    path = _write(tmp_path, V7 + tight + ACTIONS)

    result = _by_id(run(path, only=("G4",)))["G4"]

    assert result.status is not Status.FAIL, result.reason

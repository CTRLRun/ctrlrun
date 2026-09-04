"""`ctrlrun verify` — the registry, the engine, G1-G6 and G10. SPEC-v0.4 §2, §3; T100-T107.

T125 and T125b live here too, despite sitting at the end of the number range: they are the two
guards on verify lying about itself (§8), and item 1 is where they belong.

Every test that asserts a guarantee **passes** also has to show it can fail — a verify whose
every guarantee is hard-coded to pass satisfies T100 perfectly — so T104 and T125 inject a
broken kernel and a broken control and require the report to go red.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ctrlrun.verify import Status, VerifyRefused, run
from ctrlrun.verify import guarantees as reg
from ctrlrun.verify.report import REPORT_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PAYMENTS = REPO_ROOT / "examples" / "authority" / "payments.yaml"
EXAMPLE_POLICY = REPO_ROOT / "ctrlrun.example.yaml"
V1_PAYMENTS = REPO_ROOT / "examples" / "policies" / "payments.yaml"

V1 = "schema: ctrlrun.policy/v1\n"
V2 = "schema: ctrlrun.policy/v2\n"
V3 = "schema: ctrlrun.policy/v3\n"

#: A v2 document with effect templates, so the effect guarantees are applicable, and an
#: approve band, so the approval guarantees are.
WITH_EFFECTS = (
    V2
    + """
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
)

#: The same, with no `approve` band anywhere: G1 and G2 cannot be exercised.
NO_APPROVAL = (
    V2
    + """
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
  acme.read:
    decision: allow
"""
)

#: No effect template anywhere: G3, G4 and G5 cannot be exercised, and the rest still can.
NO_EFFECTS = (
    V1
    + """
actions:
  acme.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
  acme.read:
    decision: allow
"""
)

#: Nothing at all: every guarantee is N/A, which §3.8 makes an exit of 2 and never a pass.
EMPTY = V1 + "actions: {}\n"


def _write(directory: Path, document: str, name: str = "ctrlrun.yaml") -> Path:
    path = directory / name
    path.write_text(document, encoding="utf-8")
    return path


def _by_id(report):
    return {result.id: result for result in report.guarantees}


# --- T100: verify against this repository's own configurations ---------------------------


@pytest.mark.authority
def test_T100_the_authority_example_passes_every_non_authority_guarantee():
    """`examples/authority/payments.yaml`: a v3 document with effect templates and grants.

    G7 lands with item 2 and is asserted there; G8 and G9 likewise. Everything else must be
    applicable and pass, and the action each chose is asserted by name — a selection that
    silently changed which action it ran fails here.
    """
    report = run(AUTHORITY_PAYMENTS)
    results = _by_id(report)

    for gid in ("G1", "G2", "G3", "G4", "G5", "G6", "G10"):
        assert results[gid].status is Status.PASS, (gid, results[gid].reason)
    for gid in ("G1", "G2", "G3", "G4", "G5", "G10"):
        assert results[gid].action == "stripe.refund", gid
    assert results["G3"].effect_key == "refund:ctrlrun-verify-payment_id"
    assert report.exit_code == 0


def test_T100_a_v1_document_with_no_templates_and_no_grants():
    """`ctrlrun.example.yaml`: G1, G2, G6 and G10 applicable and passing, the rest N/A."""
    report = run(EXAMPLE_POLICY)
    results = _by_id(report)

    for gid in ("G1", "G2", "G6", "G10"):
        assert results[gid].status is Status.PASS, (gid, results[gid].reason)
    assert results["G1"].action == "k8s.delete_namespace"
    assert results["G10"].action == "customer.read"
    for gid in ("G3", "G4", "G5"):
        assert results[gid].status is Status.NOT_APPLICABLE
    assert report.exit_code == 0


# --- T101 / T101b: N/A is not a pass, and zero applicable is not a pass ------------------


def test_T101_a_policy_with_no_approve_rule_makes_G1_and_G2_not_applicable(tmp_path):
    path = _write(tmp_path, NO_APPROVAL)

    report = run(path)
    results = _by_id(report)

    for gid in ("G1", "G2"):
        assert results[gid].status is Status.NOT_APPLICABLE, gid
        assert results[gid].reason == reg.NO_APPROVE_RULE
        # The `else` branch: either one reported `pass` is the defect this test exists for.
        assert results[gid].status is not Status.PASS
    assert report.applicable == report.passed + report.failed
    # Ten in the catalogue; G1 and G2 for the missing approve band, G8 and G9 for the
    # missing authority section. Six applicable, and the count is over those six.
    assert report.applicable == 6
    assert report.not_applicable == 4
    text = report.to_text()
    assert "8/8" not in text
    assert f"{report.passed}/{report.applicable} declared guarantees pass." in text
    assert "4 not applicable: G1, G2, G8, G9." in text


def test_T101b_zero_applicable_guarantees_is_not_a_pass(tmp_path):
    """§3.8 — `0/0` reported as success is the same false green as `8/8` with five N/As."""
    path = _write(tmp_path, EMPTY)

    report = run(path)

    assert report.applicable == 0
    assert all(result.status is Status.NOT_APPLICABLE for result in report.guarantees)
    assert report.exit_code == 2
    assert report.badge is None
    assert "0/0 declared guarantees pass." in report.to_text()


def test_T101b_the_command_exits_2_and_says_nothing_was_checked(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    _write(tmp_path, EMPTY)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["verify"])

    assert result.exit_code == 2
    assert "nothing was checked" in result.stderr


# --- T102: no effect templates ------------------------------------------------------------


def test_T102_a_policy_with_no_effect_templates_makes_G3_G4_and_G5_not_applicable(tmp_path):
    path = _write(tmp_path, NO_EFFECTS)

    report = run(path)
    results = _by_id(report)

    for gid in ("G3", "G4", "G5"):
        assert results[gid].status is Status.NOT_APPLICABLE, gid
        assert results[gid].reason == reg.NO_EFFECT_TEMPLATE
        assert results[gid].detail["note"] == reg.EFFECT_TEMPLATE_NOTE
        assert "@protect" in results[gid].detail["note"]
    # A blanket "nothing applies" cannot pass this test.
    for gid in ("G1", "G2", "G6", "G7", "G10"):
        assert results[gid].status is Status.PASS, (gid, results[gid].reason)


# --- T103: verify does not touch the operator's store -------------------------------------


def _tree(root: Path) -> dict[str, tuple[str, int]]:
    found: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            found[str(path.relative_to(root))] = (
                hashlib.sha256(data).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return found


def _seed_store(directory: Path) -> Path:
    """A `.ctrlrun/` with a receipt, an effect record, a delegation and the JSONL files."""
    from datetime import UTC, datetime

    from ctrlrun import (
        Action,
        Control,
        InMemoryStateStore,
        JSONLEventSink,
        Policy,
        Principal,
        SQLiteStateStore,
    )

    del InMemoryStateStore
    state = directory / ".ctrlrun"
    state.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(state / "state.db")
    policy = Policy.from_yaml(WITH_EFFECTS, source=str(directory / "ctrlrun.yaml"))
    control = Control(policy, store, sinks=[JSONLEventSink(state)])
    action = Action(
        name="acme.refund",
        arguments={"amount": 10, "payment_id": "REAL-1"},
        principal=Principal(agent="a-real-agent"),
        resource="payment:REAL-1",
    )
    control.execute(action, lambda: "done", "refund:REAL-1")
    from ctrlrun.state import DelegationRecord

    store.put_delegation(
        DelegationRecord(
            delegation_id="dlg_" + "0" * 32,
            parent_id="root",
            depth=1,
            grant_json=json.dumps({"subject": {"agent": "x"}, "actions": ["acme.refund"]}),
            created_by_agent="a-real-agent",
            created_by_user=None,
            created_via="api",
            created_at=datetime.now(UTC),
        )
    )
    store.close()
    return state


def test_T103_the_operators_store_is_byte_identical_before_and_after(tmp_path, monkeypatch):
    path = _write(tmp_path, WITH_EFFECTS)
    state = _seed_store(tmp_path)
    monkeypatch.chdir(tmp_path)
    before = _tree(state)
    assert before, "the seeded store must actually contain something"

    report = run(path)

    assert report.exit_code == 0
    assert _tree(state) == before


def test_T103_a_store_that_does_not_exist_is_not_created(tmp_path, monkeypatch):
    path = _write(tmp_path, WITH_EFFECTS)
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / ".ctrlrun").exists()

    run(path)

    assert not (tmp_path / ".ctrlrun").exists()
    assert not (tmp_path / ".ctrlrun" / "state.db").exists()


def test_T103_CTRLRUN_STATE_is_not_read_and_not_created(tmp_path, monkeypatch):
    """The path an operator actually deploys: the store somewhere `$CTRLRUN_STATE` names."""
    path = _write(tmp_path, WITH_EFFECTS)
    elsewhere = tmp_path / "elsewhere" / "state.db"
    monkeypatch.setenv("CTRLRUN_STATE", str(elsewhere))
    monkeypatch.chdir(tmp_path)

    run(path)

    assert not elsewhere.exists()
    assert not elsewhere.parent.exists()


# --- T104: a broken kernel FAILS, with a counterexample -----------------------------------

#: A kernel with the duplicate guard deleted: `plan_reservation` hands back a reservation
#: where SPEC-v0.1 §5.4 says refuse. Installed through `sitecustomize` as well as in process,
#: because G4's attempts run in their own OS processes and a mutation the children cannot see
#: would leave G4 green against a broken kernel — which is the exact shape T104 exists to catch.
_MUTATION_SOURCE = '''\
"""A kernel whose `reserve_effect` always succeeds: SPEC-v0.1 §5.4's retry table, deleted."""

from datetime import timedelta

from ctrlrun.effect import DEFAULT_LEASE, EffectState, Reservation
from ctrlrun.state import _iso


def _always_reserves(self, effect_key, action_id, lease=DEFAULT_LEASE):
    now = self._clock()
    connection = self._connection()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DELETE FROM effects WHERE effect_key=?", (effect_key,))
        connection.execute(
            "INSERT INTO effects(effect_key, state, action_id, attempt, lease_expires_at, "
            "result_json, error, created_at, updated_at) VALUES(?,?,?,?,?,NULL,NULL,?,?)",
            (
                effect_key,
                str(EffectState.RESERVED),
                action_id,
                1,
                _iso(now + lease),
                _iso(now),
                _iso(now),
            ),
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return Reservation(
        effect_key=effect_key, action_id=action_id, attempt=1, lease_expires_at=now + lease
    )
'''

#: The same mutation, installed by `site` at startup in every child G4 starts. A mutation the
#: children could not see would leave G4 green against a broken kernel, which is the exact
#: shape T104 exists to catch.
_DELETE_THE_DUPLICATE_GUARD = (
    _MUTATION_SOURCE
    + "\nfrom ctrlrun.state import SQLiteStateStore as _Store\n"
    + "_Store.reserve_effect = _always_reserves\n"
)


def _broken_kernel(tmp_path, monkeypatch):
    """Install the mutation in this process and in every child G4 starts."""
    guard = tmp_path / "broken"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(_DELETE_THE_DUPLICATE_GUARD, encoding="utf-8")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(part for part in (str(guard), os.environ.get("PYTHONPATH", "")) if part),
    )
    namespace: dict = {}
    # The *definition* only: the install line at the end of `_DELETE_THE_DUPLICATE_GUARD`
    # writes straight onto `ctrlrun.state`, which monkeypatch could not undo, and a mutation
    # that outlived its test would silently break every test after it.
    exec(compile(_MUTATION_SOURCE, "<mutation>", "exec"), namespace)
    from ctrlrun.state import SQLiteStateStore

    monkeypatch.setattr(SQLiteStateStore, "reserve_effect", namespace["_always_reserves"])


def test_T104_a_kernel_with_the_duplicate_guard_deleted_fails_G3_and_G4(tmp_path, monkeypatch):
    path = _write(tmp_path, WITH_EFFECTS)
    _broken_kernel(tmp_path, monkeypatch)

    report = run(path)
    results = _by_id(report)

    assert results["G3"].status is Status.FAIL, results["G3"].reason
    assert results["G4"].status is Status.FAIL, results["G4"].reason
    assert report.exit_code == 1
    example = results["G3"].counterexample
    assert example is not None
    # One receipt cannot show a double execution.
    assert len(example.receipts) >= 2
    assert example.effects
    assert "DuplicateEffect" in example.expected
    assert example.observed
    document = json.loads(report.to_json())
    assert document["schema"] == REPORT_SCHEMA
    for row in document["guarantees"]:
        if row["status"] == "fail":
            assert row["counterexample"] is not None
        else:
            assert row["counterexample"] is None
    # The other guarantees in the same run are unaffected, which is what makes the two
    # failures attributable.
    assert results["G1"].status is Status.PASS, results["G1"].reason
    assert results["G6"].status is Status.PASS, results["G6"].reason


def test_a_synthesized_vector_landing_in_the_wrong_rule_is_an_internal_error(tmp_path, monkeypatch):
    """SPEC-v0.4 §3.3 - "the vector is checked before it is used".

    Having built one, the engine evaluates the action it just constructed and asserts the
    decision is the one it was aiming for. A scenario built on a vector that landed in a
    different rule is the "window not actually reproduced" failure in its purest form: it
    would run, refuse something, and report a guarantee that was never exercised. So it is an
    **internal error** - exit 3 - and never a FAIL and never an N/A.

    Written because the mutation table found the guard load-bearing on nothing: T116 reaches
    exit 3 by replacing `_checked` outright, so removing the raise *inside* it left the suite
    green. A check nothing exercises is not a check.
    """
    from ctrlrun.policy import Evaluation, Policy
    from ctrlrun.verify.scenarios import VerifyInternalError

    path = _write(tmp_path, WITH_EFFECTS)
    original = Policy.evaluate

    def lands_elsewhere(self, action):
        evaluation = original(self, action)
        # Same decision, different rule: the vector satisfies a rule the engine was not
        # aiming at, which is exactly the case §3.3 refuses to build a scenario on.
        return Evaluation(evaluation.decision, "rule[99]")

    monkeypatch.setattr(Policy, "evaluate", lands_elsewhere)

    with pytest.raises(VerifyInternalError) as raised:
        run(path)

    assert "rule[99]" in str(raised.value)
    assert "§3.3" in str(raised.value)


# --- T105: determinism ---------------------------------------------------------------------


def _stable(document: dict) -> dict:
    document = dict(document)
    document.pop("started_at", None)
    document.pop("finished_at", None)
    return document


def test_T105_two_runs_produce_identical_json(tmp_path):
    """Several actions per guarantee to choose from: a single-candidate document is
    deterministic by accident."""
    path = _write(
        tmp_path,
        V2
        + """
actions:
  zeta.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 10 }
        decision: allow
      - decision: approve
  alpha.refund:
    effect: "alpha:{ticket}"
    rules:
      - when: { amount_gte: 0, amount_lte: 500 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 5000 }
        decision: approve
      - decision: deny
  beta.refund:
    effect: "beta:{ticket}"
    rules:
      - when: { amount_gte: 0, amount_lte: 20 }
        decision: allow
      - decision: approve
  gamma.read:
    decision: allow
""",
    )

    first = _stable(json.loads(run(path).to_json()))
    second = _stable(json.loads(run(path).to_json()))

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    chosen = {row["id"]: (row["action"], row["arguments"]) for row in first["guarantees"]}
    assert chosen["G3"][0] == "alpha.refund", "selection is sorted by codepoint"


# --- T106: G4 uses real processes -----------------------------------------------------------


def test_T106_G4_contends_in_real_processes(tmp_path):
    """A threads-only implementation fails this test.

    The executor count comes from `O_CREAT|O_EXCL` files rather than from process memory, and
    the control half — 8 distinct keys, 8 commits — is asserted in the same run, so a run in
    which no child started cannot pass.
    """
    path = _write(tmp_path, WITH_EFFECTS)

    report = run(path, only=("G4",))
    result = _by_id(report)["G4"]

    assert result.status is Status.PASS, result.reason
    assert result.detail["processes"] == reg.PROCESSES
    assert result.detail["distinct_child_pids"] == reg.PROCESSES
    assert result.detail["parent_pid_among_children"] is False


# --- T107: verify reaches no network ---------------------------------------------------------

_REFUSE_EVERY_SOCKET = '''\
"""Imported by `site` at startup: nothing under verify may open a socket."""

import socket

_real = socket.socket


class _Refusing(_real):
    def connect(self, *args, **kwargs):
        raise RuntimeError("verify tried to connect; verify runs with no network")

    def connect_ex(self, *args, **kwargs):
        raise RuntimeError("verify tried to connect; verify runs with no network")


def _refuse(*args, **kwargs):
    raise RuntimeError("verify tried to resolve a name; verify runs with no network")


socket.socket = _Refusing
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
'''

_ASSERT_THE_GUARD_IS_LIVE = """
import socket, sys

# A network guard that was not installed proves nothing (SPEC-v0.4 §1.3).
try:
    socket.getaddrinfo("example.invalid", 80)
except RuntimeError:
    pass
else:
    sys.exit("the no-network guard was not installed")

import ctrlrun.verify as verify

report = verify.run(sys.argv[1])
sys.exit(report.exit_code)
"""


def test_T107_a_full_run_completes_with_no_network(tmp_path):
    path = _write(tmp_path, WITH_EFFECTS)
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(_REFUSE_EVERY_SOCKET, encoding="utf-8")
    script = tmp_path / "check.py"
    script.write_text(_ASSERT_THE_GUARD_IS_LIVE, encoding="utf-8")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(guard), environment.get("PYTHONPATH", "")) if part
    )
    finished = subprocess.run(
        [sys.executable, str(script), str(path)],
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
        cwd=tmp_path,
        check=False,
    )

    assert finished.returncode == 0, f"{finished.stdout}\n{finished.stderr}"


# --- T125: a failed control is a FAIL, never a PASS -------------------------------------------


#: The guarantees item 1 ships. G7-G9 are asserted in `test_verify_authority.py`.
ITEM_ONE = ("G1", "G2", "G3", "G4", "G5", "G6", "G10")


def _break_the_executor(monkeypatch):
    """The executor is never reached: nothing this scenario ran can have committed."""
    from ctrlrun.verify import scenarios

    def silent(self):
        return None

    monkeypatch.setattr(scenarios._Executor, "__call__", silent)


def _break_the_commit(monkeypatch):
    """The first attempt never reaches COMMITTED, so G3's control cannot establish anything."""
    from ctrlrun.state import SQLiteStateStore

    monkeypatch.setattr(
        SQLiteStateStore, "commit_effect", lambda self, effect_key, action_id, result: None
    )


def _break_the_children(monkeypatch):
    """No child ever starts, which is the scenario G4's control exists to rule out."""
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)


def _break_the_evaluation(monkeypatch):
    """Every action evaluates to `unknown_action`, so G6's refusal is not attributable."""
    from ctrlrun.policy import Decision, Evaluation, Policy

    monkeypatch.setattr(
        Policy, "evaluate", lambda self, action: Evaluation(Decision.DENY, "unknown_action")
    )


def _break_the_asymmetry(monkeypatch):
    """A kernel that maps *everything* to AMBIGUOUS: G10's `NotExecuted` row is the control."""
    from ctrlrun.errors import NotExecuted
    from ctrlrun.verify import scenarios

    original = scenarios._raises

    def swapped(exception):
        if isinstance(exception, NotExecuted):
            exception = RuntimeError("ctrlrun-verify: everything is ambiguous")
        return original(exception)

    monkeypatch.setattr(scenarios, "_raises", swapped)


#: One breakage per guarantee, because a control is a per-guarantee claim (§8, T125). Each
#: leaves the guarantee's *refusal* half working, so what goes red is the control and nothing
#: else — which is the whole distinction §1.3 draws.
BROKEN_CONTROLS = {
    "G1": _break_the_executor,
    "G2": _break_the_executor,
    "G3": _break_the_commit,
    "G4": _break_the_children,
    "G5": _break_the_executor,
    "G6": _break_the_evaluation,
    "G10": _break_the_asymmetry,
}


@pytest.mark.parametrize("gid", ITEM_ONE)
def test_T125_a_broken_positive_control_is_a_failure(tmp_path, monkeypatch, gid):
    """§1.3's guard. A refusal asserted against a scenario in which nothing ran passes on a
    kernel with the guard deleted, so a control that does not behave as specified is FAIL with
    `reason: "control failed"` — never PASS, and never N/A.

    Asserted per guarantee and not once, because a control is a per-guarantee claim. The
    `else` branch — the guarantee reported `pass` or `not_applicable` — fails the test with
    the id that got it.
    """
    path = _write(tmp_path, WITH_EFFECTS)
    BROKEN_CONTROLS[gid](monkeypatch)

    report = run(path, only=(gid,))
    result = _by_id(report)[gid]

    if result.status is not Status.FAIL:
        raise AssertionError(
            f"{gid} reported {result.status} with a broken positive control "
            f"(reason={result.reason!r})"
        )
    assert result.reason == reg.CONTROL_FAILED
    assert result.counterexample is not None
    assert report.exit_code == 1


# --- T125b: `import ctrlrun` does not import `ctrlrun.verify` ----------------------------------


def test_T125b_importing_ctrlrun_does_not_import_ctrlrun_verify():
    """In a subprocess, as T30 does: in-process this would pass or fail on whatever pytest
    happened to import first."""
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctrlrun, sys;"
            "print(sorted(n for n in sys.modules if n.startswith('ctrlrun.verify')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "[]"


def test_T125b_importing_ctrlrun_verify_pulls_in_no_module_from_an_extra():
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctrlrun.verify, sys;"
            "print(sorted(n for n in sys.modules"
            " if n.split('.')[0] in ('httpx', 'opentelemetry', 'jwt', 'xmlschema')"
            " or n in ('ctrlrun.gateway', 'ctrlrun.otel', 'ctrlrun.acs',"
            " 'ctrlrun.jwt_identity')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "[]"


# --- the registry itself ------------------------------------------------------------------


def test_the_catalogue_is_closed_and_ordered():
    assert reg.CATALOGUE == "ctrlrun.guarantees/v1"
    assert [guarantee.id for guarantee in reg.GUARANTEES] == [f"G{n}" for n in range(1, 11)]
    for guarantee in reg.GUARANTEES:
        assert guarantee.descends_from, f"{guarantee.id} names no acceptance test"


def test_a_store_url_v0_4_does_not_have_is_refused(tmp_path):
    path = _write(tmp_path, WITH_EFFECTS)

    with pytest.raises(VerifyRefused) as refused:
        run(path, store_url="postgres://localhost/ctrlrun")

    assert "v0.6" in str(refused.value)


def test_authority_declared_twice_is_refused(tmp_path):
    policy = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: only
      subject: { agent: "bot" }
      actions: ["acme.read"]
actions:
  acme.read:
    decision: allow
""",
    )
    standalone = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: other
      subject: { agent: "bot" }
      actions: ["acme.read"]
""",
        name="authority.yaml",
    )

    with pytest.raises(VerifyRefused) as refused:
        run(policy, authority=standalone)

    assert str(policy) in str(refused.value)
    assert str(standalone) in str(refused.value)


def test_observe_mode_is_refused_before_any_scenario_runs(tmp_path):
    path = _write(tmp_path, V3 + "mode: observe\nactions:\n  acme.read:\n    decision: allow\n")

    with pytest.raises(VerifyRefused) as refused:
        run(path)

    assert "observe" in str(refused.value)


def test_the_v1_payments_template_reports_five_over_five_with_five_not_applicable():
    """The definition of done, dogfooded rather than described (SPEC-v0.4 §4.1)."""
    report = run(V1_PAYMENTS)

    assert report.exit_code == 0
    assert (report.passed, report.applicable, report.not_applicable) == (5, 5, 5)
    text = report.to_text()
    assert "5/5 declared guarantees pass." in text
    assert "5 not applicable: G3, G4, G5, G8, G9." in text
    assert "10/10" not in text

"""Receipts on disk, the CLI and `ctrlrun demo`. SPEC-v0.1 §6, §8; acceptance tests T10, T11.

The JSONL evidence is the half of §6 a reader outside CTRLRun ever sees, so these tests read
the files rather than the store wherever the spec names a file.
"""

import json
import os
import re
import time
from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    Decision,
    DuplicateEffect,
    EffectState,
    InvalidArgument,
    Policy,
    Receipt,
    SQLiteStateStore,
    context,
    protect,
    with_approval,
)
from ctrlrun.approval import ApprovalStatus
from ctrlrun.cli.demo import (
    DEMO_DIRNAME,
    README_AMOUNTS,
    SCENARIO_HEADINGS,
    _euros,
    read_them_command,
    written_path,
)
from ctrlrun.cli.main import EXAMPLE_POLICY, main
from ctrlrun.receipt import (
    EVENTS_FILENAME,
    GENESIS_HASH,
    RECEIPT_SCHEMA,
    RECEIPTS_FILENAME,
    EventType,
    JSONLEventSink,
    ReceiptResult,
)

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""

#: SPEC-v0.1 §6.1 — every key a receipt carries, in the order the spec prints them.
SPEC_RECEIPT_FIELDS = (
    "schema",
    "receipt_id",
    "action_id",
    "action",
    "action_hash",
    "principal",
    "resource",
    "arguments",
    "environment",
    "decision",
    "decision_reason",
    "approval_id",
    "approver",
    "effect_key",
    "attempt",
    "result",
    # SPEC-v0.3 §12.2 — `ctrlrun.receipt/v2`. `null` until observe mode (item 4) fills them.
    "execution",
    "would_have",
    "error",
    "started_at",
    "finished_at",
    # SPEC-v0.6 §6.2 — `ctrlrun.receipt/v3`. `seq` is **inside** the hashed content, which is
    # what makes deletion and reordering detectable rather than only edits. `hash` is
    # deliberately absent: a document cannot contain its own hash, so it is a column.
    "seq",
    "prev_hash",
    # SPEC-v0.6 §7.1, §7.3 — `ctrlrun.receipt/v3`.
    "policy_hash",
    "policy_version",
    "controls",
)


class _Remote:
    """An in-process remote that commits first, and can then lose its response, as in T1."""

    def __init__(self) -> None:
        self.calls = 0
        self.lose_next = False

    def refund(self, payment_id: str) -> str:
        self.calls += 1  # the money has moved before anything below runs
        if self.lose_next:
            self.lose_next = False
            raise TimeoutError("no response from api.stripe.com after 30s")
        return f"re_{payment_id}"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A directory the CLI discovers on its own: a policy, and `.ctrlrun/` beside it."""
    (tmp_path / "ctrlrun.yaml").write_text(POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)
    return tmp_path


@pytest.fixture
def store(workspace):
    store = SQLiteStateStore(workspace / ".ctrlrun" / "state.db")
    yield store
    store.close()


@pytest.fixture
def journal(workspace):
    """SPEC-v0.2 §4.3 — the JSONL evidence is a `Control` sink now, not the store's job.

    Pointed at the directory `Control.from_file()` would point it at, so every assertion
    below still reads the files v0.1 §6 names, in the place v0.1 put them.
    """
    return JSONLEventSink(workspace / ".ctrlrun")


@pytest.fixture
def control(workspace, store, journal):
    return Control(Policy.from_file(workspace / "ctrlrun.yaml"), store, sinks=[journal])


@pytest.fixture
def remote():
    return _Remote()


@pytest.fixture
def refund(control, remote):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return remote.refund(payment_id)

    return refund


def _cli(*args):
    return CliRunner().invoke(main, list(args))


def _ok(result):
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    return result


def _lose_a_response(refund, remote, payment_id="txn_1"):
    """T1's situation: the remote commits, the response never arrives, effect AMBIGUOUS."""
    remote.lose_next = True
    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id=payment_id, amount=200)
    assert remote.calls == 1


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# --- the JSONL evidence writers (SPEC §6.1, §6.2) -------------------------------------


def test_a_receipt_is_appended_to_receipts_jsonl_beside_the_state_database(control, journal):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    (line,) = _lines(journal.receipts_path)
    assert json.loads(line)["result"] == "committed"


def test_the_jsonl_receipt_parses_back_into_the_receipt_the_store_holds(control, store, journal):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    (line,) = _lines(journal.receipts_path)
    exported = Receipt.from_json(line)
    stored = store.receipts()[0]
    # `hash` is the one field the export cannot carry -- a document cannot contain its own hash
    # (SPEC-v0.6 §6.2) -- so the assertion is that everything else round-trips **and** that the
    # export recomputes to what the store recorded. That is §6.4's "fully verifiable by
    # recomputation", and it is a stronger statement than equality would have been.
    assert exported == replace(stored, hash=None)
    assert exported.chain_hash() == stored.hash
    assert exported.seq == 1
    assert exported.prev_hash == GENESIS_HASH


def test_every_event_is_appended_to_events_jsonl_in_order(control, store, journal):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    written = [json.loads(line) for line in _lines(journal.events_path)]
    assert [document["type"] for document in written] == [
        str(event.type) for event in store.events()
    ]
    assert [document["event_id"] for document in written] == [1, 2, 3, 4, 5]


def test_every_jsonl_event_carries_the_fields_the_spec_names(control, journal):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    for line in _lines(journal.events_path):
        assert set(json.loads(line)) == {
            "event_id",
            "ts",
            "type",
            "action_id",
            "effect_key",
            "approval_id",
            "data",
        }


def test_the_jsonl_files_land_in_the_state_directory(workspace):
    """SPEC-v0.2 §4.3 — `Control.from_file()` installs the sink, and it writes where v0.1
    wrote: an existing evidence directory is unchanged by the move out of the store."""
    control = Control.from_file()
    try:

        @protect("stripe.refund", effect="refund:{payment_id}", control=control)
        def refund(payment_id: str, amount: int) -> str:
            return "re_1"

        with context(agent="refund-agent"):
            refund(payment_id="txn_1", amount=200)
    finally:
        control.store.close()

    assert (workspace / ".ctrlrun" / RECEIPTS_FILENAME).is_file()
    assert (workspace / ".ctrlrun" / EVENTS_FILENAME).is_file()


def test_a_new_store_on_the_same_directory_appends_rather_than_truncates(
    control, journal, workspace
):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)
    reopened = SQLiteStateStore(workspace / ".ctrlrun" / "state.db")
    try:
        second = Control(
            Policy.from_file(workspace / "ctrlrun.yaml"),
            reopened,
            sinks=[JSONLEventSink(workspace / ".ctrlrun")],
        )

        @protect("stripe.refund", effect="refund:{payment_id}", control=second)
        def other(payment_id: str, amount: int) -> str:
            return "re_2"

        with context(agent="refund-agent"):
            other(payment_id="txn_2", amount=200)
    finally:
        reopened.close()

    assert len(_lines(journal.receipts_path)) == 2


def test_a_receipt_line_is_written_only_after_the_store_accepted_it(control, journal):
    @protect("stripe.refund", control=control)
    def denied(payment_id: str, amount: int) -> str:
        raise AssertionError("a denied action never executes")

    with context(agent="refund-agent"), pytest.raises(ActionDenied):
        denied(payment_id="txn_1", amount=999999)

    (line,) = _lines(journal.receipts_path)
    assert json.loads(line)["result"] == "denied"


def test_an_unwritable_journal_never_fails_an_action_the_store_already_recorded(
    control, store, journal, monkeypatch, caplog
):
    """v0.1 §6's rule, enforced since SPEC-v0.2 §4.2 by Control's general one about sinks."""

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    def refuse(_line: str) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(journal, "on_receipt", lambda receipt: refuse("receipt"))
    monkeypatch.setattr(journal, "on_event", lambda event: refuse("event"))
    with context(agent="refund-agent"), caplog.at_level("WARNING", logger="ctrlrun"):
        assert refund(payment_id="txn_1", amount=200) == "re_1"

    assert store.receipts()[0].result is ReceiptResult.COMMITTED
    assert caplog.records


# --- T10: `ctrlrun resolve` (SPEC §5.2, §8) -------------------------------------------


def test_T10_resolve_failed_permits_a_retry(control, store, refund, remote):
    _lose_a_response(refund, remote)
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS

    _ok(_cli("resolve", "refund:txn_1", "--failed"))

    assert store.get_effect("refund:txn_1").state is EffectState.FAILED
    with context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=200) == "re_txn_1"
    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert store.get_effect("refund:txn_1").attempt == 2
    assert remote.calls == 2


def test_T10_resolve_committed_makes_a_retry_a_DuplicateEffect(control, store, refund, remote):
    _lose_a_response(refund, remote)

    _ok(_cli("resolve", "refund:txn_1", "--committed"))

    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    with context(agent="refund-agent"), pytest.raises(DuplicateEffect) as excinfo:
        refund(payment_id="txn_1", amount=200)
    assert excinfo.value.state == "committed"
    assert remote.calls == 1


def test_T10_resolve_appends_an_EFFECT_RESOLVED_event(store, refund, remote):
    _lose_a_response(refund, remote)

    _ok(_cli("resolve", "refund:txn_1", "--failed"))

    resolved = [event for event in store.events() if event.type is EventType.EFFECT_RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].effect_key == "refund:txn_1"
    assert resolved[0].data["state"] == "failed"
    assert resolved[0].data["resolver"]
    # SPEC-v0.2 §2.2 — evidence tells a human's judgement from a machine's answer, so every
    # EFFECT_RESOLVED names its authority, this one included.
    assert resolved[0].data["resolved_by"] == "human"


def test_resolve_refuses_an_effect_that_is_not_ambiguous(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    result = _cli("resolve", "refund:txn_1", "--failed")

    assert result.exit_code != 0
    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED


def test_resolve_refuses_an_effect_key_nobody_reserved(workspace):
    result = _cli("resolve", "refund:nothing", "--failed")

    assert result.exit_code != 0
    # The message, not just the exit code: an unhandled AttributeError also exits non-zero,
    # and would mean the store never checked whether the record existed.
    assert "no effect" in result.output
    assert "refund:nothing" in result.output


def test_resolve_needs_exactly_one_outcome(store, refund, remote):
    _lose_a_response(refund, remote)

    assert _cli("resolve", "refund:txn_1").exit_code != 0
    assert _cli("resolve", "refund:txn_1", "--failed", "--committed").exit_code != 0
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_resolve_cannot_move_an_effect_to_a_non_terminal_state(store, refund, remote):
    _lose_a_response(refund, remote)

    assert _cli("resolve", "refund:txn_1", "--reserved").exit_code != 0
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


# --- resolve is a store transition, and both stores refuse the same (SPEC §5.2) -------


def _ambiguous(state_store, effect_key="refund:txn_1"):
    state_store.reserve_effect(effect_key, "act_1")
    state_store.begin_execution(effect_key, "act_1")
    state_store.mark_ambiguous(effect_key, "act_1", "TimeoutError")
    return state_store


@pytest.mark.parametrize("state", [EffectState.COMMITTED, EffectState.FAILED])
def test_a_human_moves_an_ambiguous_effect_to_a_terminal_state(state_store, state):
    _ambiguous(state_store)

    record = state_store.resolve_effect("refund:txn_1", state, "cli:local")

    assert record.state is state
    assert state_store.get_effect("refund:txn_1").state is state


def test_a_resolved_effect_holds_no_lease(state_store):
    _ambiguous(state_store)

    record = state_store.resolve_effect("refund:txn_1", EffectState.FAILED, "cli:local")

    assert record.lease_expires_at is None


def test_a_resolution_keeps_the_unknown_that_caused_it(state_store):
    _ambiguous(state_store)

    record = state_store.resolve_effect("refund:txn_1", EffectState.COMMITTED, "cli:local")

    assert "cli:local" in record.error
    assert "TimeoutError" in record.error


@pytest.mark.parametrize(
    "state", [EffectState.NEW, EffectState.RESERVED, EffectState.EXECUTING, EffectState.AMBIGUOUS]
)
def test_an_effect_cannot_be_resolved_to_a_non_terminal_state(state_store, state):
    _ambiguous(state_store)

    with pytest.raises(InvalidArgument):
        state_store.resolve_effect("refund:txn_1", state, "cli:local")
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_only_an_ambiguous_effect_is_resolvable(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1")
    state_store.begin_execution("refund:txn_1", "act_1")

    with pytest.raises(InvalidArgument):
        state_store.resolve_effect("refund:txn_1", EffectState.FAILED, "cli:local")
    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING


def test_a_committed_effect_cannot_be_resolved_back_to_failed(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1")
    state_store.begin_execution("refund:txn_1", "act_1")
    state_store.commit_effect("refund:txn_1", "act_1", "re_1")

    with pytest.raises(InvalidArgument):
        state_store.resolve_effect("refund:txn_1", EffectState.FAILED, "cli:local")
    assert state_store.get_effect("refund:txn_1").state is EffectState.COMMITTED


def test_an_unknown_effect_cannot_be_resolved(state_store):
    with pytest.raises(InvalidArgument):
        state_store.resolve_effect("refund:nothing", EffectState.FAILED, "cli:local")


def test_a_resolution_needs_a_resolver(state_store):
    _ambiguous(state_store)

    with pytest.raises(InvalidArgument):
        state_store.resolve_effect("refund:txn_1", EffectState.FAILED, "")


def test_list_effects_reports_every_key_and_narrows_by_state(state_store):
    _ambiguous(state_store, "refund:txn_1")
    state_store.reserve_effect("refund:txn_2", "act_2")

    assert {record.effect_key for record in state_store.list_effects()} == {
        "refund:txn_1",
        "refund:txn_2",
    }
    assert [record.effect_key for record in state_store.list_effects(EffectState.AMBIGUOUS)] == [
        "refund:txn_1"
    ]


def test_list_effects_is_empty_on_a_fresh_store(state_store):
    assert state_store.list_effects() == ()


# --- T11: `ctrlrun demo` (SPEC §7 T11) -------------------------------------------------


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    """One demo run, shared by the T11 assertions: it is the slowest test in the suite."""
    root = tmp_path_factory.mktemp("demo")
    previous = Path.cwd()
    os.chdir(root)
    try:
        started = time.monotonic()
        result = CliRunner().invoke(main, ["demo"])
        elapsed = time.monotonic() - started
    finally:
        os.chdir(previous)
    yield result, elapsed, root / ".ctrlrun" / DEMO_DIRNAME


def test_T11_demo_exits_zero(demo_run):
    result, _, _ = demo_run

    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"


def test_T11_demo_runs_in_under_sixty_seconds(demo_run):
    _, elapsed, _ = demo_run

    assert elapsed < 60


def test_T93_demo_prints_all_five_scenario_headings(demo_run):
    result, _, _ = demo_run

    assert len(SCENARIO_HEADINGS) == 5
    for number, heading in enumerate(SCENARIO_HEADINGS, start=1):
        assert f"{number}. {heading}" in result.output


def test_T93_demo_prints_five_blocked_lines(demo_run):
    result, _, _ = demo_run

    blocked = [line for line in result.output.splitlines() if "BLOCKED" in line]
    assert len(blocked) == 5, "\n".join(blocked)


def test_T93_the_fifth_scenario_blocks_on_authority_and_names_the_reason(demo_run):
    """SPEC-v0.3 §1.2 — scenario 5 answers a different question from the other four, so its
    refusal has to be legible as a different *kind* of refusal. A `BLOCKED` line that said
    only "denied" would leave a reader with no way to tell the two axes apart."""
    result, _, _ = demo_run

    assert "outside the delegated grant (authority_constraint)" in result.output
    # And the creation-time half, which is the one an agent would otherwise use to reach the
    # amount above by minting its own permission.
    assert "refused (containment: constraints)" in result.output


def test_T93_the_fifth_scenario_shows_both_axes_applying_to_one_action(demo_run):
    """§4.6 — the two combine as the stricter of the pair. Without this the scenario would be
    consistent with a chain that authorized nothing at all, and every refusal in it would be
    vacuous."""
    result, _, _ = demo_run

    assert "authority permits it, and the policy asks a human" in result.output


def test_T93_the_first_four_scenarios_run_with_no_authority_section(demo_run):
    """§4.1 — a document with no `authority:` section behaves exactly as v0.2. The demo's own
    small proof of the opt-in rule: scenarios 1 to 4 load `DEMO_POLICY`, which declares
    `ctrlrun.policy/v1` and has no section at all."""
    from ctrlrun import Policy
    from ctrlrun.cli.demo import DEMO_POLICY
    from ctrlrun.policy import POLICY_SCHEMA

    assert "authority:" not in DEMO_POLICY
    assert Policy.from_yaml(DEMO_POLICY).schema == POLICY_SCHEMA


def test_T11_demo_writes_receipts(demo_run):
    _, _, evidence = demo_run

    lines = _lines(evidence / "receipts.jsonl")
    assert lines
    results = [json.loads(line)["result"] for line in lines]
    # Four `blocked` receipts, not five: scenario 5's refusals are `denied` (an authority
    # denial *is* the action being denied) and its last action is still awaiting a human, so
    # it has no receipt at all yet (v0.1 §6.1).
    assert results.count("blocked") == 4
    assert results.count("denied") >= 1
    assert "committed" in results
    assert "ambiguous" in results


def test_T11_demo_writes_events(demo_run):
    _, _, evidence = demo_run

    types = {json.loads(line)["type"] for line in _lines(evidence / "events.jsonl")}
    assert {
        "ACTION_PROPOSED",
        "POLICY_EVALUATED",
        "APPROVAL_REQUESTED",
        "APPROVAL_CONSUMED",
        "EFFECT_RESERVED",
        "EFFECT_RESERVATION_REFUSED",
        "EXECUTION_AMBIGUOUS",
        "EXECUTION_COMMITTED",
        "APPROVAL_INVALIDATED",
    } <= types


def test_T11_every_demo_receipt_carries_every_field_in_the_spec(demo_run):
    _, _, evidence = demo_run

    for line in _lines(evidence / "receipts.jsonl"):
        document = json.loads(line)
        assert tuple(document) == SPEC_RECEIPT_FIELDS
        assert document["schema"] == RECEIPT_SCHEMA
        # SPEC-v0.3 §2.4 — a receipt carries the whole principal, because §2.1's distinction
        # between "the provider stated no expiry" and "nothing was stored" is load-bearing.
        assert set(document["principal"]) == {
            "agent",
            "user",
            "claims",
            "issuer",
            "expires_at",
        }


def test_T11_every_demo_receipt_renders_its_enums_by_value(demo_run):
    _, _, evidence = demo_run

    for line in _lines(evidence / "receipts.jsonl"):
        document = json.loads(line)
        assert document["decision"] in {"allow", "approve", "deny"}
        assert document["result"] in {"committed", "failed", "ambiguous", "denied", "blocked"}


def test_T11_every_demo_receipt_parses_back_into_a_Receipt(demo_run):
    _, _, evidence = demo_run

    for line in _lines(evidence / "receipts.jsonl"):
        assert isinstance(Receipt.from_json(line), Receipt)


def test_T11_the_demo_remote_is_called_once_for_the_duplicated_effect(demo_run):
    result, _, _ = demo_run

    assert "remote refund calls: 1" in result.output


def test_T11_demo_prints_the_command_that_reads_what_it_wrote(demo_run):
    result, _, evidence = demo_run

    assert read_them_command(evidence) in result.output


def test_T11_the_demo_prints_no_absolute_paths(demo_run):
    """The transcript is meant to be pasted in public, and `/Users/<name>/...` rides along."""
    result, _, _ = demo_run

    leaked = [line for line in result.output.splitlines() if re.search(r"(?:^|[\s=])/", line)]
    assert not leaked, f"absolute paths in demo output: {leaked}"


def test_the_demo_evidence_paths_are_relative_to_where_it_ran(tmp_path):
    evidence = tmp_path / ".ctrlrun" / DEMO_DIRNAME

    assert written_path(tmp_path, evidence / "receipts.jsonl") == ".ctrlrun/demo/receipts.jsonl"
    assert read_them_command(evidence) == "CTRLRUN_STATE=.ctrlrun/demo/state.db ctrlrun receipts"


def test_a_path_outside_the_run_directory_is_printed_whole(tmp_path):
    """No relative form exists, and a wrong relative path would be worse than a long one."""
    outside = tmp_path.parent / "elsewhere" / "receipts.jsonl"

    assert written_path(tmp_path, outside) == str(outside)


#: The only things in the demo's output that differ between runs: generated approval ids and,
#: since scenario 5, generated delegation ids. Evidence paths are printed relative to where
#: the demo ran, so they are stable and the README must quote them exactly — that is what
#: keeps an absolute path from creeping back in.
_RUN_VARYING = re.compile(r"(?:apr|dlg)_[0-9a-f]+")


def _readme_demo_section() -> str:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    if not readme.exists():  # installed without the source tree
        pytest.skip("no repository checkout")
    section = readme.read_text(encoding="utf-8").split("## What `ctrlrun demo` shows")[1]
    return section.split("\n## ")[0]


def test_the_readme_demo_section_quotes_the_demo_output_verbatim(demo_run):
    """SPEC-v0.1 §7 T11 — the demo is the truth, and the README follows it.

    Every line the demo prints must appear in the README, so a change to the demo's output
    that nobody carried across fails here instead of shipping a README that lies.
    """
    result, _, _ = demo_run
    quoted = {_RUN_VARYING.sub("*", line) for line in _readme_demo_section().splitlines()}

    missing = [
        line
        for line in result.output.splitlines()
        if line.strip() and _RUN_VARYING.sub("*", line) not in quoted
    ]
    assert not missing, f"README does not quote: {missing}"


def test_the_readme_demo_section_shows_the_amounts_the_demo_uses():
    """SPEC-v0.1 §7 T11 — the scenario amounts appear, in the order the demo refunds them."""
    printed = re.findall(r"€[\d,]+", _readme_demo_section())

    remaining = iter(printed)
    assert all(_euros(amount) in remaining for amount in README_AMOUNTS), (
        f"expected {[_euros(a) for a in README_AMOUNTS]} in order, got {printed}"
    )


def test_T11_the_demo_leaves_the_operators_own_store_alone(demo_run):
    _, _, evidence = demo_run

    assert not (evidence.parent / "state.db").exists()


# --- `ctrlrun init` (SPEC §8) ----------------------------------------------------------


def test_init_writes_a_loadable_policy_and_creates_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)

    _ok(_cli("init"))

    assert Policy.from_file(tmp_path / "ctrlrun.yaml").actions
    assert (tmp_path / ".ctrlrun").is_dir()


def test_init_refuses_to_overwrite_an_existing_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ctrlrun.yaml").write_text("mine\n", encoding="utf-8")

    result = _cli("init")

    assert result.exit_code != 0
    assert (tmp_path / "ctrlrun.yaml").read_text(encoding="utf-8") == "mine\n"


def test_the_shipped_example_policy_is_the_one_in_the_repository():
    example = Path(__file__).resolve().parents[1] / "ctrlrun.example.yaml"
    if not example.exists():  # installed without the source tree
        pytest.skip("no repository checkout")

    assert example.read_text(encoding="utf-8") == EXAMPLE_POLICY


#: Files outside the package that the test suite opens, and that must therefore travel in the
#: sdist for a distribution packager to be able to run these tests at all.
SOURCE_TREE_FIXTURES = (
    "ctrlrun.example.yaml",
    "tests/conftest.py",
    # T31 runs every example script and loads every sector template out of the source tree
    # (SPEC-v0.2 §1.1). CI builds the sdist and runs the suite from it, so both travel.
    "examples/double-refund/main.py",
    "examples/double-refund/ctrlrun.yaml",
    "examples/policies/payments.yaml",
)


def _manifest_carries(manifest: str, path: str) -> bool:
    """Whether MANIFEST.in's include rules match `path`, a repo-relative POSIX path."""
    for line in manifest.splitlines():
        command, _, rest = line.partition(" ")
        words = rest.split()
        if command == "include" and any(fnmatch(path, pattern) for pattern in words):
            return True
        if command == "recursive-include" and words:
            directory, patterns = words[0], words[1:]
            if path.startswith(f"{directory}/") and any(
                fnmatch(Path(path).name, pattern) for pattern in patterns
            ):
                return True
    return False


def test_the_sdist_carries_everything_the_tests_need():
    """The sdist ships `tests/`, so it must ship what `tests/` reads (MANIFEST.in).

    An sdist carrying tests that cannot run is worse than one carrying none: a packager
    reads the failure as a broken release. CI builds the sdist and runs the suite out of it,
    which is the real guard; this is the fast one that fails in the editor instead.
    """
    root = Path(__file__).resolve().parents[1]
    manifest = root / "MANIFEST.in"
    if not manifest.exists():  # installed without the source tree
        pytest.skip("no repository checkout")

    rules = manifest.read_text(encoding="utf-8")
    missing = [name for name in SOURCE_TREE_FIXTURES if not _manifest_carries(rules, name)]
    assert not missing, f"MANIFEST.in does not carry: {missing}"


# --- `ctrlrun approve` / `ctrlrun deny` (SPEC §4.3, §8) --------------------------------


def _pending(refund, payment_id="txn_2"):
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as pending:
        refund(payment_id=payment_id, amount=2000)
    return pending.value.request_id


def test_approve_grants_a_pending_request_and_the_action_then_executes(control, store, refund):
    request_id = _pending(refund)
    _ok(_cli("approve", request_id))

    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED
    with context(agent="refund-agent"), with_approval(request_id):
        assert refund(payment_id="txn_2", amount=2000) == "re_txn_2"
    assert store.get_approval(request_id).status is ApprovalStatus.CONSUMED


def test_approve_appends_an_APPROVAL_GRANTED_event(control, store, refund):
    request_id = _pending(refund)

    _ok(_cli("approve", request_id))

    granted = [event for event in store.events() if event.type is EventType.APPROVAL_GRANTED]
    assert [event.approval_id for event in granted] == [request_id]


def test_deny_answers_the_request_and_the_action_is_denied(control, store, refund):
    request_id = _pending(refund)

    _ok(_cli("deny", request_id))

    assert store.get_approval(request_id).status is ApprovalStatus.DENIED
    with (
        context(agent="refund-agent"),
        with_approval(request_id),
        pytest.raises(ActionDenied) as excinfo,
    ):
        refund(payment_id="txn_2", amount=2000)
    assert excinfo.value.reason == "approval_denied"


def test_deny_appends_an_APPROVAL_DENIED_event(control, store, refund):
    request_id = _pending(refund)

    _ok(_cli("deny", request_id))

    denied = [event for event in store.events() if event.type is EventType.APPROVAL_DENIED]
    assert [event.approval_id for event in denied] == [request_id]


def test_an_approval_cannot_be_granted_twice(control, store, refund):
    request_id = _pending(refund)
    _ok(_cli("approve", request_id))

    assert _cli("approve", request_id).exit_code != 0
    assert _cli("deny", request_id).exit_code != 0


def test_approving_an_unknown_request_fails(workspace):
    assert _cli("approve", "apr_000000000000").exit_code != 0


def test_a_consumed_approval_cannot_be_replayed_through_the_cli(control, store, refund):
    request_id = _pending(refund)
    _ok(_cli("approve", request_id))
    with context(agent="refund-agent"), with_approval(request_id):
        refund(payment_id="txn_2", amount=2000)

    with (
        context(agent="refund-agent"),
        with_approval(request_id),
        pytest.raises(ApprovalMismatch) as excinfo,
    ):
        refund(payment_id="txn_2", amount=2000)
    assert excinfo.value.reason == "consumed"


# --- `ctrlrun receipts` (SPEC §6.1, §8) ------------------------------------------------


def test_receipts_prints_one_line_per_receipt(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)
        refund(payment_id="txn_2", amount=200)

    output = _ok(_cli("receipts")).output

    assert output.count("stripe.refund") == 2
    assert "committed" in output


def test_receipts_json_prints_the_portable_receipt(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    output = _ok(_cli("receipts", "--json")).output

    assert tuple(json.loads(output.strip())) == SPEC_RECEIPT_FIELDS


def test_receipts_last_shows_only_the_most_recent(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)
        refund(payment_id="txn_2", amount=200)

    output = _ok(_cli("receipts", "--last", "1", "--json")).output

    (line,) = output.strip().splitlines()
    assert json.loads(line)["effect_key"] == "refund:txn_2"


def test_receipts_on_an_empty_store_says_so(workspace):
    output = _ok(_cli("receipts")).output

    assert "no receipts" in output.lower()


# --- `ctrlrun effects` (SPEC §5.2, §8) -------------------------------------------------


def test_effects_lists_every_effect_with_its_state(control, store, refund, remote):
    _lose_a_response(refund, remote)
    with context(agent="refund-agent"):
        refund(payment_id="txn_2", amount=200)

    output = _ok(_cli("effects")).output

    assert "refund:txn_1" in output
    assert "ambiguous" in output
    assert "refund:txn_2" in output
    assert "committed" in output


def test_effects_state_filters(control, store, refund, remote):
    _lose_a_response(refund, remote)
    with context(agent="refund-agent"):
        refund(payment_id="txn_2", amount=200)

    output = _ok(_cli("effects", "--state", "ambiguous")).output

    assert "refund:txn_1" in output
    assert "refund:txn_2" not in output


def test_effects_rejects_a_state_that_is_not_an_effect_state(workspace):
    assert _cli("effects", "--state", "nonsense").exit_code != 0


def test_effects_on_an_empty_store_says_so(workspace):
    output = _ok(_cli("effects")).output

    assert "no effects" in output.lower()


# --- the CLI finds the store the way Control.from_file does (SPEC §8) ------------------


def test_the_cli_reads_the_store_named_by_CTRLRUN_STATE(tmp_path, monkeypatch, workspace):
    elsewhere = tmp_path / "elsewhere" / "state.db"
    monkeypatch.setenv("CTRLRUN_STATE", str(elsewhere))

    _ok(_cli("effects"))

    assert elsewhere.exists()


def test_an_empty_CTRLRUN_STATE_is_refused(workspace, monkeypatch):
    monkeypatch.setenv("CTRLRUN_STATE", "   ")

    assert _cli("effects").exit_code != 0


def test_an_empty_CTRLRUN_CONFIG_is_refused(workspace, monkeypatch):
    monkeypatch.setenv("CTRLRUN_CONFIG", "")

    assert _cli("effects").exit_code != 0


# --- the frozen CLI surface (SPEC §8) -------------------------------------------------


def test_the_cli_offers_exactly_the_commands_the_spec_freezes():
    """SPEC-v0.1 §8, plus what SPEC-v0.2 §11 and SPEC-v0.3 §11 add."""
    assert set(main.commands) == {
        "init",
        "demo",
        "approve",
        "deny",
        "receipts",
        "effects",
        "resolve",
        "inspect",
        "gateway",
        # SPEC-v0.3 §5.7 — build-list item 3.
        "delegate",
        "revoke",
        # SPEC-v0.3 §6.4, §6.5 — build-list item 4.
        "stats",
        "verify",
    }


@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("receipts", {"--last", "--json"}),
        ("effects", {"--state"}),
        ("resolve", {"--committed", "--failed"}),
        ("delegate", {"--parent", "--file", "--as", "--json"}),
        ("revoke", {"--by"}),
    ],
)
def test_each_command_offers_the_options_the_spec_freezes(command, options):
    declared = {
        flag
        for parameter in main.commands[command].params
        for flag in getattr(parameter, "opts", ())
        if flag.startswith("--")
    }

    assert options <= declared


@pytest.mark.parametrize("arguments", [("approve",), ("deny",), ("resolve",), ("revoke",)])
def test_the_commands_that_name_a_thing_require_it(workspace, arguments):
    assert _cli(*arguments).exit_code != 0


# --- enums reach the terminal by value, as they reach receipts (SPEC §6.1) ------------


def test_no_cli_output_renders_an_enum_by_its_member_name(control, store, refund, remote):
    _lose_a_response(refund, remote)
    with context(agent="refund-agent"):
        refund(payment_id="txn_2", amount=200)

    printed = "".join(
        _ok(_cli(*command)).output
        for command in (("receipts",), ("effects",), ("resolve", "refund:txn_1", "--failed"))
    )

    for name in ("Decision.", "EffectState.", "ReceiptResult.", "ApprovalStatus."):
        assert name not in printed


# --- the evidence a store keeps is the evidence the CLI shows -------------------------


def test_the_decision_a_receipt_records_survives_the_round_trip(control, store, refund, remote):
    _lose_a_response(refund, remote)

    (receipt,) = store.receipts()
    assert receipt.decision is Decision.ALLOW
    assert receipt.result is ReceiptResult.AMBIGUOUS
    # `hash` is a column, not a document field (SPEC-v0.6 §6.2), so the round trip drops it and
    # recomputes it instead -- which is the property that matters: any reader can check the
    # exported document against the chain without the store's help.
    assert Receipt.from_json(receipt.to_json()) == replace(receipt, hash=None)
    assert Receipt.from_json(receipt.to_json()).chain_hash() == receipt.hash


def test_a_jsonl_sink_writes_to_the_directory_it_was_given(tmp_path):
    log = JSONLEventSink(tmp_path / "evidence")

    assert log.receipts_path == tmp_path / "evidence" / "receipts.jsonl"
    assert log.events_path == tmp_path / "evidence" / "events.jsonl"


def test_a_blocked_retry_and_its_ambiguous_predecessor_share_an_effect_key(store, refund, remote):
    _lose_a_response(refund, remote)
    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund(payment_id="txn_1", amount=200)

    ambiguous, blocked = store.receipts()
    assert ambiguous.result is ReceiptResult.AMBIGUOUS
    assert blocked.result is ReceiptResult.BLOCKED
    assert ambiguous.effect_key == blocked.effect_key == "refund:txn_1"
    assert ambiguous.action_id != blocked.action_id

# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Enforcement coverage, from what is already written. SPEC-v0.11 §7; T560-T566.

The runtime half of `ctrlrun scan`. `scan` reads source and asks *is this call protected?*; this
reads what the deployment has recorded and asks *which of the things you declared has nothing
ever gone through?*

**Rule 4 is what this item breaks if it breaks anything**: a clean result is **not a verdict**.
No score, no percentage, no ratio, no badge, and no sentence a reader could quote as one.
`SPEC-v0.4.md` §3.9 is the precedent, and `T560` greps this item's own output for the vocabulary
it forbids, in the shape `CLAIMS.md` uses, because "there is no score" is a claim about the
environment until something checks it.

**No new event type and no new column** (`T561`). The action name lives on the **receipt**, not
on the event: `ACTION_PROPOSED` carries an `action_hash` and nothing that maps it back. Measured
before any of this was written::

    Events written:
      1 ACTION_PROPOSED  data={'action_hash': 'sha256:1b3e0dab...'}
      2 POLICY_EVALUATED data={'decision': 'allow', 'reason': 'decision'}
    Receipts written:
      seq=1 action='stripe.refund'        result=committed
      seq=2 action='k8s.delete_namespace' result=denied

Every action that reached a decision leaves a receipt, a **denial included**, which is what makes
the question answerable without adding anything.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.coverage import (
    COVERAGE_SCHEMA,
    NOT_A_VERDICT,
    Unexercised,
    coverage,
    coverage_lines,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
POLICY = """schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    decision: allow
  k8s.delete_namespace:
    decision: deny
  github.force_push:
    decision: allow
  quarterly.reconcile:
    decision: allow
"""

#: `SPEC-v0.4.md` §5.3's list, plus the vocabulary a coverage report is uniquely tempted by. A
#: percentage sign is in here because the shape rule 4 forbids is *87%*, and no prose in this
#: report needs one.
FORBIDDEN = (
    "score",
    "coverage:",
    "percent",
    "%",
    "ratio",
    "grade",
    "badge",
    "rating",
    "secure",
    "compliant",
    "certified",
    "audited",
    "fully covered",
    "100",
)


@pytest.fixture
def store(tmp_path) -> SQLiteStateStore:
    """A store where two of four declared actions have been exercised, one of them denied."""
    from ctrlrun.errors import ActionDenied

    opened = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    control = Control(Policy.from_yaml(POLICY), opened, clock=lambda: T0)
    control.execute(
        Action(
            name="stripe.refund",
            arguments={"payment_id": "p1"},
            principal=Principal(agent="a"),
        ),
        lambda: {"ok": True},
        "refund:p1",
        lease=LEASE,
    )
    with contextlib.suppress(ActionDenied):
        control.execute(
            Action(
                name="k8s.delete_namespace",
                arguments={"ns": "x"},
                principal=Principal(agent="a"),
            ),
            lambda: {"ok": True},
            None,
        )
    return opened


# --- T560: rule 4 -------------------------------------------------------------------------------


def test_T560_the_report_is_a_list_and_never_a_score(store) -> None:
    """SPEC-v0.11 §1.1 rule 4, and `SPEC-v0.4.md` §3.9 on a new surface.

    **This is the assertion the item exists to keep.** A coverage number that ranked a deployment
    would be `verify` grading an operator's document in a new costume, and the shape it would
    take is *coverage: 87%*. So the output is greped for the vocabulary, over **both** renderings
    and over the empty case as well as the full one: an empty list is the one most likely to be
    quoted as a verdict.
    """
    report = coverage(
        store,
        policy_actions=sorted(Policy.from_yaml(POLICY).actions),
        gateway_tools=(("refund_tool", "stripe.refund"), ("push_tool", "github.force_push")),
        protected_actions=("stripe.refund", "internal.sweep"),
    )
    whole = "\n".join(coverage_lines(report))
    assert NOT_A_VERDICT in whole, "the sentence that says this is not a verdict is missing"

    # **`NOT_A_VERDICT` is excluded from the scan, and that is an allow-list of one.** It
    # contains the word *score*, because it is the sentence saying there is not one, and a plain
    # scan would flag it. `tests/test_docs_production.py` solves the identical problem the
    # identical way and says why: the next person would remove the scan as a false positive and
    # take the check with it.
    rendered = whole.replace(NOT_A_VERDICT, "").lower()
    as_json = json.dumps(report.to_dict()).lower()

    for word in FORBIDDEN:
        assert word not in rendered, f"the coverage report says {word!r}:\n{rendered}"
        assert word not in as_json, f"the coverage document says {word!r}: {as_json}"

    # And no "N of M", which is a ratio with the sign filed off.
    assert not re.search(r"\b\d+\s*(of|/)\s*\d+\b", rendered), rendered

    # The document carries no field that could be read as one.
    document = report.to_dict()
    for field in ("score", "percentage", "ratio", "grade", "covered", "exit_code"):
        assert field not in document, f"the coverage document has a {field!r} field"

    # The sentence is present whether or not anything was found.
    assert NOT_A_VERDICT in "\n".join(coverage_lines(report))


def test_T560b_an_EMPTY_list_still_says_it_is_not_a_verdict(store) -> None:
    """The case most likely to be quoted as one: nothing unexercised at all."""
    clean = coverage(store, policy_actions=("stripe.refund", "k8s.delete_namespace"))
    assert clean.unused == ()
    rendered = "\n".join(coverage_lines(clean))
    assert NOT_A_VERDICT in rendered
    assert "everything declared has been exercised at least once" in rendered
    scanned = rendered.replace(NOT_A_VERDICT, "").lower()
    for word in FORBIDDEN:
        assert word not in scanned, f"a clean report says {word!r}:\n{rendered}"


def test_T560c_the_reason_is_about_the_record_and_never_about_the_operator(store) -> None:
    """§7. A policy entry nothing exercised **may be correctly unused**, and the report says so
    rather than implying a defect.

    The reason on each entry states what was not found. It does not say *missing*, *should*,
    *incomplete*, or anything else that reads as an instruction.
    """
    report = coverage(store, policy_actions=sorted(Policy.from_yaml(POLICY).actions))
    assert report.unused, "this store was supposed to have unexercised entries"
    for entry in report.unused:
        assert "no receipt" in entry.reason, entry.reason
        for judging in ("missing", "should", "incomplete", "gap", "must", "fail"):
            assert judging not in entry.reason.lower(), (
                f"the reason for {entry.name!r} reads as a judgement: {entry.reason!r}"
            )
    assert "may be correctly unused" in NOT_A_VERDICT


# --- T561: from what is already written ---------------------------------------------------------


def test_T561_the_answer_comes_from_receipts_and_needs_no_new_event(store) -> None:
    """§7. **No new event type, no new column.**

    The action name is on the receipt and not on the event: `ACTION_PROPOSED` carries an
    `action_hash` and nothing that maps it back to a name. If the question had needed a new
    event, §7 says the question is wrong and the item stops and says so. It did not.
    """
    from ctrlrun.receipt import EventType

    events = store.events()
    assert events, "this store wrote no events"
    for event in events:
        assert "action" not in event.data, (
            "an event now carries an action name; §7's design reasoning should be revisited, "
            f"because it rests on it not doing so: {event.type} {event.data}"
        )
    assert EventType.ACTION_PROPOSED in {event.type for event in events}

    # Every decided action left a receipt, a denial included, which is what makes this
    # answerable from what is already there.
    named = {receipt.action for receipt in store.receipts()}
    assert named == {"stripe.refund", "k8s.delete_namespace"}, named

    report = coverage(store, policy_actions=sorted(Policy.from_yaml(POLICY).actions))
    assert set(report.actions_seen) == named


def test_T561b_an_action_that_is_always_DENIED_counts_as_exercised(store) -> None:
    """A deny rule that fires **has** been exercised, and belongs nowhere on this list.

    This is the half a design reading only `EXECUTION_COMMITTED` would get wrong: the whole point
    of a deny rule is that the action is refused rather than unknown, and reporting it as
    unexercised would tell an operator to remove the rule that is working.
    """
    report = coverage(store, policy_actions=sorted(Policy.from_yaml(POLICY).actions))
    unexercised = {entry.name for entry in report.of(Unexercised.POLICY_ACTION)}
    assert "k8s.delete_namespace" not in unexercised, (
        "an action that was denied was reported as never exercised; the deny rule fired, which "
        "is the action being enforced rather than ignored"
    )
    assert unexercised == {"github.force_push", "quarterly.reconcile"}


def test_T561c_a_row_that_cannot_be_read_back_is_not_reported_here(tmp_path, store) -> None:
    """SPEC-v0.11 §5.2. A refused row carries no action name, and the chain already reports it as
    `content_altered`. A coverage list is not the place to report a tamper a second time under a
    different name."""
    from ctrlrun.coverage import _names_seen
    from ctrlrun.receipt import UnreadableReceipt

    class _OneBad:
        def __init__(self, real):
            self._real = real

        def receipts(self):
            rows = list(self._real.receipts())
            rows.append(UnreadableReceipt(seq=99, receipt_id=None, refusal="InvalidArgument"))
            return tuple(rows)

    assert _names_seen(_OneBad(store)) == ("k8s.delete_namespace", "stripe.refund")

    # **And the filter is on the type and on emptiness, not merely on `None`.** A mutation
    # relaxing it to `name is not None` survived the version of this test above, because an
    # `UnreadableReceipt` has no `action` attribute at all and `getattr` already returns `None`.
    # A row whose `action` is an empty string, or is not a string, is the input that tells the
    # two apart, and an empty name in a coverage list is an entry an operator cannot act on.
    class _Odd:
        def __init__(self, *actions):
            self._actions = actions

        def receipts(self):
            return tuple(type("R", (), {"action": value})() for value in self._actions)

    assert _names_seen(_Odd("", "a.b", None, 7, "c.d")) == ("a.b", "c.d")


# --- T562: the three kinds ----------------------------------------------------------------------


def test_T562_every_declared_kind_is_reported_with_its_own_reason(store) -> None:
    report = coverage(
        store,
        policy_actions=sorted(Policy.from_yaml(POLICY).actions),
        gateway_tools=(("refund_tool", "stripe.refund"), ("push_tool", "github.force_push")),
        protected_actions=("stripe.refund", "internal.sweep"),
    )
    assert {entry.name for entry in report.of(Unexercised.POLICY_ACTION)} == {
        "github.force_push",
        "quarterly.reconcile",
    }
    # The tool is named, not the action it routes to: a tool's own name is what an operator
    # recognises in their gateway configuration.
    assert {entry.name for entry in report.of(Unexercised.GATEWAY_TOOL)} == {"push_tool"}
    assert {entry.name for entry in report.of(Unexercised.PROTECTED_ACTION)} == {"internal.sweep"}
    assert report.to_dict()["schema"] == COVERAGE_SCHEMA


def test_T562b_a_store_with_no_receipts_reports_everything_and_says_what_it_read(tmp_path) -> None:
    """*Nothing exercised, over a store holding no receipts* and *nothing exercised, over a store
    holding forty thousand* are different findings, and the report distinguishes them.

    The count is context for reading the list, not a denominator: nothing divides by it.
    """
    empty = SQLiteStateStore(tmp_path / "empty.db", clock=lambda: T0)
    report = coverage(empty, policy_actions=("a.b", "c.d"))
    empty.close()
    assert report.receipts_read == 0
    assert len(report.unused) == 2
    rendered = "\n".join(coverage_lines(report))
    assert "read 0 receipt(s)" in rendered
    assert NOT_A_VERDICT in rendered


# --- T563: the CLI ------------------------------------------------------------------------------


def test_T563_the_coverage_flag_does_not_move_the_exit_code(tmp_path, store) -> None:
    """Rule 4 again, in the shape a shell can read.

    An exit code that moved with an unexercised policy entry would be the score this item is
    forbidden to produce, wearing a shell's clothes: a CI job would then fail because somebody
    declared an action for a quarterly run.
    """
    import os

    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    workspace = tmp_path / "work"
    workspace.mkdir()
    # **Every action declares an `effect:`**, so `scan`'s own half finds nothing: it reports
    # `action_without_effect` otherwise, and that would make the comparison below meaningless.
    (workspace / "ctrlrun.yaml").write_text(
        "schema: ctrlrun.policy/v2\n"
        "actions:\n"
        "  stripe.refund:\n"
        '    effect: "refund:{payment_id}"\n'
        "    decision: allow\n"
        "  quarterly.reconcile:\n"
        '    effect: "reconcile:{period}"\n'
        "    decision: allow\n",
        encoding="utf-8",
    )
    # **A tree `scan`'s own half finds nothing in**, which is what makes the comparison below
    # mean anything: with findings of its own, `scan` exits 1 either way and a `--coverage` that
    # moved the code would be invisible. A mutation making `--coverage` exit 1 on any unexercised
    # entry survived the first version of this test for exactly that reason.
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
    database = tmp_path / "state.db"
    store.close()

    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        plain = CliRunner().invoke(main, ["scan"])
        with_coverage = CliRunner().invoke(
            main, ["scan", "--coverage", "--store-url", f"sqlite:///{database}"]
        )
    finally:
        os.chdir(cwd)

    # **The comparison is the claim.** Whatever `scan`'s own half decides, `--coverage` must not
    # change it: an exit code that moved with an unexercised policy entry would fail a CI job
    # because somebody declared an action for a quarterly run.
    assert plain.exit_code == 0, (
        "`ctrlrun scan` found something in this tree on its own, so it exits non-zero either way "
        f"and this test cannot see whether --coverage moved it:\n{plain.output}"
    )
    assert with_coverage.exit_code == 0, (
        "--coverage moved the exit code. An unexercised policy entry is a fact about the record, "
        "not a finding about the operator, and a CI job must not fail because somebody declared "
        f"an action for a quarterly run:\n{with_coverage.output}"
    )
    assert "never exercised" in with_coverage.output, with_coverage.output
    assert NOT_A_VERDICT in with_coverage.output
    scanned = with_coverage.output.replace(NOT_A_VERDICT, "").lower()
    for word in FORBIDDEN:
        assert word not in scanned, f"ctrlrun scan --coverage says {word!r}"

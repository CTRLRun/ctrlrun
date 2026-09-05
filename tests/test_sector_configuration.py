"""The throwaway sector configuration. Item 7; SPEC-v0.6 §7.5, §8 T177b.

**The artefact is disposable; what it finds is the deliverable.** §7.5 asks item 7 to write one
sector's configuration as YAML against the primitives, assert it loads and drives a decision, and
**ship it nowhere** — it lives here, in no `packs/` directory, no `examples/`, and no
distribution. This is `v0.5 §8`'s device, the third adapter written against the contract alone,
and its output is the same shape: a list of what the primitives could not express.

The sector is a hospital's patient-record system, chosen because it exercises every primitive at
once — labels on arguments, a rule that has to see them, controls citing a clinical governance
document, and a threshold that decides who has to say yes.

## What it found

Recorded here rather than in a commit message, because §7.5 says the findings are the point.

1. **`redact:` was not needed, and this configuration is the reason it is being cut.** See
   `test_the_finding_that_redaction_was_not_needed` below, which is the argument in full.

2. **A control cannot be cited by a `decision:` shorthand entry and a rule at once**, because an
   entry has *either* `decision:` or `rules:`. That is not a defect: an action with one decision
   has one place to cite from. Written down because the first draft of this file tried it.

3. **`data:` labels an argument, not a return value.** A protected function that *returns* PHI
   declares nothing, and `data_scope` sees only what went in. That is the right scope for v0.6 —
   a decision is made before the call, so a label on the result could not inform it — but a pack
   author will expect otherwise and the section should say so. **This is an edit to §7.4.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)

#: One sector's configuration, written against §7.3 and §7.4 and nothing else. It is not an
#: example, not a template and not a recommendation: no clinical or regulatory claim is made by
#: it, and `source:` strings are cited exactly as an operator would write them -- CTRLRun does not
#: know what any of them mean (§7.3).
HOSPITAL = """
schema: ctrlrun.policy/v4
version: "records-2026.02"
environment: production

controls:
  clinician-of-record:
    title: "A patient record is amended by the clinician responsible for that patient"
    source: "Trust information governance policy IG-11 §4"
  second-clinician-for-diagnosis:
    title: "A diagnosis is changed only with a second clinician's agreement"
    source: "Trust information governance policy IG-11 §7.2"
  minimum-necessary:
    title: "Only the fields needed for the task are read"
    source: "Trust information governance policy IG-3"

actions:
  patient.record.read:
    controls: [minimum-necessary]
    effect: null
    decision: allow

  patient.record.amend:
    controls: [clinician-of-record]
    data:
      patient_id: phi
      diagnosis: phi
      allergy: phi
      ward_note: internal
    effect: "amend:{patient_id}:{field}"
    rules:
      - when: {data_scope_in: [phi]}
        decision: approve
        controls: [second-clinician-for-diagnosis]
      - decision: allow

  ward.roster.update:
    data:
      shift_note: internal
    effect: "roster:{ward}"
    rules:
      - when: {data_scope_in: [phi]}
        decision: deny
      - decision: allow
"""


def an_action(name: str, **arguments) -> Action:
    return Action(
        name=name, arguments=arguments, principal=Principal(agent="records-agent", user="dr-ada")
    )


def test_T177b_the_configuration_loads() -> None:
    """§7.5's first half: it loads, and every citation resolves."""
    policy = Policy.from_yaml(HOSPITAL)

    assert policy.version == "records-2026.02"
    assert set(policy.controls) == {
        "clinician-of-record",
        "second-clinician-for-diagnosis",
        "minimum-necessary",
    }
    assert policy.controls["clinician-of-record"].source.startswith("Trust information governance")
    assert policy.policy_hash.startswith("sha256:")


def test_T177b_the_configuration_drives_a_decision() -> None:
    """§7.5's second half, and the whole point of writing it: the primitives compose.

    A ward note is internal and goes through; a diagnosis is PHI and needs a second clinician.
    The rule sees that difference **only** because `data:` labelled the arguments.
    """
    policy = Policy.from_yaml(HOSPITAL)

    note = policy.evaluate(an_action("patient.record.amend", patient_id="p1", ward_note="tidy"))
    assert note.decision.value == "approve", (
        "amending a record still carries `patient_id`, which is PHI -- so this is approve, and "
        "the test that would have been wrong is one asserting `allow`"
    )

    only_note = policy.evaluate(an_action("patient.record.amend", ward_note="tidy"))
    assert only_note.decision.value == "allow"
    assert only_note.controls == ("clinician-of-record",)

    diagnosis = policy.evaluate(
        an_action("patient.record.amend", patient_id="p1", diagnosis="revised")
    )
    assert diagnosis.decision.value == "approve"
    assert diagnosis.controls == ("clinician-of-record", "second-clinician-for-diagnosis"), (
        "the receipt must name both the action's control and the rule's, so an auditor reading "
        "it can go from the decision to the two written expectations it serves"
    )

    roster = policy.evaluate(an_action("ward.roster.update", ward="w3", shift_note="swap"))
    assert roster.decision.value == "allow"


def test_T177b_the_receipt_carries_the_provenance_an_auditor_needs(tmp_path) -> None:
    """What the configuration is *for*: a receipt that answers "under what, and by which rules"."""
    policy = Policy.from_yaml(HOSPITAL)
    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    control = Control(policy, store, clock=lambda: T0)

    receipt = control.execute(
        an_action("ward.roster.update", ward="w3", shift_note="swap"),
        lambda: {"ok": True},
        "roster:w3",
        lease=LEASE,
    )

    assert receipt.policy_hash == policy.policy_hash
    assert receipt.policy_version == "records-2026.02"
    assert receipt.controls == ()
    document = receipt.to_dict()
    assert document["policy_version"] == "records-2026.02"
    store.close()


def test_the_finding_that_redaction_was_not_needed() -> None:
    """**§7.4 put `redact:` on probation and this configuration does not earn it. It is cut.**

    The reasoning, because §7.4 asks for the cut to be recorded rather than merely made:

    - The configuration needs a rule to *see* that an argument is PHI, which `data:` gives it.
      That is the primitive a pack cannot be written without, and it is not on probation.
    - It does **not** need the value hidden from the evidence. A trust that may not have a
      diagnosis in its receipt store may not have it in the record system either; redaction in
      CTRLRun's evidence would be a second, weaker copy of a control that has to live upstream,
      and shipping it invites an operator to believe the weaker one is the control.
    - The one place a value must be visible is the **approval payload**, which §7.4 already
      exempts -- and once a human has to see the real diagnosis to approve the change, redacting
      the same value in the receipt hides it from the auditor and not from anybody else.
    - `v0.6`'s scope rule is that a field added for a pack nobody is writing is a guess that gets
      frozen. `redact:` is exactly that, and this is the configuration that was supposed to earn
      it.

    So the primitive is not implemented, the `redact:` key is **refused at load** rather than
    silently ignored, and §7.4 and §12 record the cut. Refusing rather than ignoring is the
    important half: an operator who writes `redact: true` and gets no error would believe the
    value is hidden.
    """
    from ctrlrun.errors import PolicyError

    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(
            HOSPITAL.replace("      diagnosis: phi", "      diagnosis: {label: phi, redact: true}")
        )
    assert "redact" in str(refused.value)
    assert "not in v0.6" in str(refused.value), (
        f"the refusal does not say the feature was cut: {refused.value}"
    )


def test_T177b_it_ships_nowhere() -> None:
    """§7.5: *"it lives in the test suite and in no `packs/` directory, no `examples/`, and no
    distribution."*

    The packaging half is in `tests/test_packaging.py`; this is the half that would catch
    somebody moving the document into the tree because it looked useful.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert not (root / "packs").exists(), "a `packs/` directory appeared; §11 ships no pack"
    for example in (root / "examples").rglob("*.yaml"):
        text = example.read_text(encoding="utf-8")
        assert "clinician-of-record" not in text, (
            f"the throwaway configuration was copied into {example}; §7.5 ships it nowhere"
        )

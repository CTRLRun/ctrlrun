"""The soak harness. Item 8; SPEC-v0.6 §8.1.

The harness lives in `research/soak/`, outside `src/` and never packaged — but **its definition
of "unexplained" is the release gate**, so it is tested here rather than trusted. `ROADMAP.md`'s
exit criterion is a week with no unexplained `AMBIGUOUS`, and a harness that miscounts is a
criterion that cannot be met or cannot be failed.

What these assert is the two ways the count can lie:

- **counting an injected ambiguity as unexplained**, which would make the gate unpassable and
  invite somebody to relax it;
- **counting an unexplained one as explained**, which is the failure that matters — it publishes
  a zero that means nothing.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

RESEARCH = Path(__file__).resolve().parents[1] / "research" / "soak"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from soak.ledger import (  # noqa: E402
    CAUSES,
    EXPLAINED,
    Ambiguity,
    Injection,
    Ledger,
    classify,
    render,
    report,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def an_ambiguity(key: str = "soak:a", action_id: str = "act_1") -> Ambiguity:
    return Ambiguity(effect_key=key, action_id=action_id, error="lost", resolved_by=None)


def test_an_injected_ambiguity_is_explained(tmp_path) -> None:
    """The ordinary case: the harness caused it, so it is not a finding."""
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.record(
        Injection(at=T0, effect_key="soak:a", action_id="act_1", cause="executor_timeout")
    )
    assert classify([an_ambiguity()], ledger) == []


def test_an_ambiguity_with_no_injection_is_a_finding(tmp_path) -> None:
    """The positive control, as a unit. **This is the whole release gate.**

    A harness that could not produce a finding here would publish "zero unexplained" from a run
    in which it was incapable of reporting anything else — `v0.4 §1.3`'s false green, applied to
    the number `ROADMAP.md` gates the release on.
    """
    ledger = Ledger(tmp_path / "ledger.db")
    findings = classify([an_ambiguity()], ledger)
    assert len(findings) == 1
    assert findings[0].effect_key == "soak:a"
    assert findings[0].error == "lost"


def test_an_injection_against_another_attempt_explains_nothing(tmp_path) -> None:
    """Keyed on the **attempt**, not the effect key.

    One key may be attempted more than once — `v0.1 §5.4`'s retry is the ordinary way — and an
    injection against attempt 1 says nothing about attempt 2. Keying on the effect key alone
    would let one recorded injection excuse every later ambiguity on that key, which is exactly
    how a real finding would be absorbed and never reported.
    """
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.record(
        Injection(at=T0, effect_key="soak:a", action_id="act_1", cause="executor_timeout")
    )
    findings = classify([an_ambiguity(action_id="act_2")], ledger)
    assert len(findings) == 1, "an injection against a different attempt excused this one"


def test_the_control_cause_never_explains_anything(tmp_path) -> None:
    """`uncontrolled` is injected and deliberately unrecorded, so it must not be in `EXPLAINED`.

    If it were, the positive control would silently stop firing and every later run would report
    a zero nobody had earned.
    """
    assert "uncontrolled" in CAUSES
    assert "uncontrolled" not in EXPLAINED

    ledger = Ledger(tmp_path / "ledger.db")
    ledger.record(Injection(at=T0, effect_key="soak:a", action_id="act_1", cause="uncontrolled"))
    assert len(classify([an_ambiguity()], ledger)) == 1


def test_an_unknown_cause_is_refused_rather_than_counted(tmp_path) -> None:
    """A cause the harness does not know is a defect in the harness, not a finding about the
    kernel. Accepting it would let a typo in an injection turn every ambiguity it caused into an
    unexplained one — a false finding, which is the other way this number can lie."""
    ledger = Ledger(tmp_path / "ledger.db")
    with pytest.raises(ValueError, match="unknown cause"):
        ledger.record(Injection(at=T0, effect_key="soak:a", action_id="act_1", cause="typo_here"))


def test_the_table_says_the_criterion_is_unmet_when_the_control_did_not_fire() -> None:
    """§8.1: the count is published either way, and publication is not the criterion.

    A run whose control did not fire has no evidence about unexplained ambiguity at all, so it
    must not report the criterion met even with zero findings — and the rendered table has to say
    so in words, because the number alone looks like a pass.
    """
    document = report(
        started=T0,
        ended=T0 + timedelta(hours=2),
        actions=1000,
        ambiguities=[],
        findings=[],
        control_fired=False,
        backend="sqlite",
    )
    assert document["unexplained"] == 0
    assert document["exit_criterion_met"] is False, (
        "a run that could not see an unexplained ambiguity reported the criterion met"
    )
    assert "did not fire" in render(document)


def test_the_table_reports_the_duration_it_measured_and_asserts_nothing_about_it() -> None:
    """§8.1's week is calendar time and does not compress, so the harness **measures** it.

    A harness that decided for itself whether the criterion's duration was met would be making
    the one claim §8.1 says would be exactly as false as it looks. `exit_criterion_met` is about
    the ambiguity count; the clock is reported and left to a human.
    """
    document = report(
        started=T0,
        ended=T0 + timedelta(minutes=7),
        actions=10,
        ambiguities=[],
        findings=[],
        control_fired=True,
        backend="sqlite",
    )
    assert document["elapsed_seconds"] == 420.0
    assert document["elapsed_human"] == "7m 0s"
    assert document["exit_criterion_met"] is True
    text = render(document)
    assert "7m 0s" in text
    assert "week" not in text.lower(), (
        "the table claims a duration it did not measure; §8.1's week is the maintainer's "
        "criterion and not something a thirty-minute run may assert"
    )


def test_a_finding_carries_what_an_operator_needs_to_investigate() -> None:
    """A count with no rows behind it is a number nobody can act on. §8.1 says a non-zero count
    is investigated before v0.6 is tagged, which needs the key, the attempt and the error."""
    document = report(
        started=T0,
        ended=T0 + timedelta(hours=1),
        actions=5,
        ambiguities=[an_ambiguity()],
        findings=classify([an_ambiguity()], _empty_ledger()),
        control_fired=True,
        backend="postgres",
    )
    assert document["exit_criterion_met"] is False
    (finding,) = document["findings"]
    assert finding["effect_key"] == "soak:a"
    assert finding["action_id"] == "act_1"
    assert "UNEXPLAINED" in render(document)


def _empty_ledger() -> Ledger:
    import tempfile

    return Ledger(Path(tempfile.mkdtemp()) / "ledger.db")


def test_the_harness_ships_nowhere() -> None:
    """§8.1: `research/soak/` is outside `src/` and never packaged, on
    `research/framework-probe/`'s precedent."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "research" / "soak" / "run.py").exists()
    assert not (root / "src" / "ctrlrun" / "soak").exists()
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")
    assert "research/soak" not in manifest

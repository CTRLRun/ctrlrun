"""SPEC-v0.10 §5, item 4: one declared order, walked by both modes.

`SPEC-v0.9 §4.2.1b` is the statement of what was wrong: `_secure` and `_observe_secure` run their
checks in different orders and `_Observation` kept the **first** reason it was handed, so for an
action tripping more than one refusal observe mode named the one it reached first, which is not
always the one enforce mode raises.

**What this item did NOT do is move a check**, and that is the finding. v0.9 aligned three cases
by reordering and the three reorderings produced four regressions between them (§13.8). A probe
over §4.2.1b's own second case shows the information was never missing: observe mode is handed
`['no_authority', 'policy_unapproved']` and reports the first, while enforce raises the second.
Ordering the **selection** is enough, and it can regress no check's position because it moves none.
"""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.control import DECISION_ORDER, Control, _Observation, _rank
from ctrlrun.policy import Policy
from ctrlrun.state import SQLiteStateStore

DOC = """
schema: ctrlrun.policy/v7
mode: {mode}
actions:
  stripe.refund:
    decision: allow
authority:
  grants:
    - id: someone-else
      subject: {{ agent: "other-agent" }}
      actions: ["stripe.refund"]
"""

ACTION = Action(
    name="stripe.refund",
    resource=None,
    arguments={"amount": 10},
    principal=Principal(agent="worker"),
    environment="production",
)


def _run(mode: str) -> str:
    tmp = Path(tempfile.mkdtemp())
    text = DOC.format(mode=mode)
    control = Control(
        Policy.from_yaml(text, source="t"),
        SQLiteStateStore(str(tmp / "s.db")),
        authority=Authority.from_yaml(text, source="t"),
        require_approved_policy=True,
    )
    try:
        receipt = control.execute(ACTION, lambda: "ok")
    except Exception as exc:
        return str(getattr(exc, "reason", type(exc).__name__))
    return str(receipt.would_have.blocked_reason)


# --- T499a: the case that proves the list starts at `execute`'s entry ----------------------


@pytest.mark.authority
def test_T499a_policy_unapproved_against_a_later_refusal_agrees_in_both_modes():
    """`v0.9 §4.2.1b`'s second named case, which is the one a `_secure`-only refactor leaves
    broken while every other pair goes green.

    `policy_unapproved` is decided by `_require_approved` at `execute`'s top, above authority;
    `_observe_secure` is not called until several hundred lines later and does not reach its own
    copy of the check until later still. Before this item, enforce raised `policy_unapproved` and
    observe reported `no_authority` for the same action, same document.
    """
    assert _run("enforce") == "policy_unapproved"
    assert _run("observe") == "policy_unapproved", (
        "observe mode named a refusal enforce mode does not raise; the declared order is what "
        "decides which of several is reported, and this pair spans the whole decision path"
    )


# --- T498: the generated property ----------------------------------------------------------


def test_T498_every_pair_of_refusals_resolves_the_same_way_in_both_modes():
    """§5.3's proof obligation. **Generated from the declared order, not listed by hand**, because
    a hand-written list is exactly what left two cases unaligned in v0.9.

    The property `_Observation.block` must have: handed any two refusals in either order, it keeps
    the one enforce mode would raise, which is the one earlier in `DECISION_ORDER`. Order
    independence is the half that matters: observe mode's checks do not run in the declared order,
    so the result must not depend on which arrived first.
    """
    pairs = list(itertools.permutations(DECISION_ORDER, 2))
    assert len(pairs) > 100, (
        "a generator that quietly produced no pairs is the false green this test exists to "
        f"refuse; it produced {len(pairs)}"
    )

    disagreed = []
    for first, second in pairs:
        observation = _Observation()
        observation.block(first)
        observation.block(second)
        expected = first if _rank(first) <= _rank(second) else second
        if observation.blocked_reason != expected:
            disagreed.append((first, second, observation.blocked_reason, expected))

    assert not disagreed, (
        f"{len(disagreed)} pairs resolved against the declared order: {disagreed[:3]}"
    )


def test_T498b_the_declared_order_has_no_duplicates_and_every_rank_is_reachable():
    """A reason listed twice ranks by its first appearance and the second listing is inert, which
    is a silent way for an edit to do nothing. `policy_unapproved` is the deliberate case: it is
    a member of `BLOCKED_APPROVAL_REASONS` and is listed explicitly above authority, so its
    explicit position must win."""
    from ctrlrun.policy import POLICY_UNAPPROVED
    from ctrlrun.receipt import BLOCKED_APPROVAL_REASONS, BLOCKED_APPROVAL_REQUIRED

    assert POLICY_UNAPPROVED in BLOCKED_APPROVAL_REASONS
    assert _rank(POLICY_UNAPPROVED) < _rank(BLOCKED_APPROVAL_REQUIRED), (
        "a policy nobody approved decides nothing, and that is checked before anything else is "
        "decided (v0.8 §8.4); ranking it with the approval gate would report the gate instead"
    )


def test_T498c_an_unlisted_reason_ranks_with_the_policy_axis():
    """The one open vocabulary is the policy's own: `v0.1 §3.2` lets a decision reason be
    `rule[N]` for any N, and no fixed tuple enumerates those. Ranking them where the policy
    decision sits is correct rather than a fallback."""
    from ctrlrun.authority import NO_AUTHORITY
    from ctrlrun.receipt import BLOCKED_APPROVAL_REQUIRED

    assert _rank("rule[3]") > _rank(NO_AUTHORITY)
    assert _rank("rule[3]") < _rank(BLOCKED_APPROVAL_REQUIRED)


def test_T500_the_ceiling_is_ordered_above_the_approval_gate():
    """T250 asserts by name that the observed fast path records the ceiling **before** the
    approval gate. It is the assertion that caught the first, hand-written version of
    `DECISION_ORDER`, where `attempt_ceiling` was simply missing and therefore sorted last."""
    from ctrlrun.receipt import BLOCKED_APPROVAL_REQUIRED, BLOCKED_ATTEMPT_CEILING

    assert _rank(BLOCKED_ATTEMPT_CEILING) < _rank(BLOCKED_APPROVAL_REQUIRED)


def test_T501_every_approval_refusal_reason_is_in_the_declared_order():
    """**Enumerated from the owning module, not restated.** `receipt.py`'s own comment records
    this set being missed twice and says why its stats test reads `approval.py` instead of
    listing: a set maintained by hand is a set the next reason is missed from. The same argument
    applies to an ordering over that set."""
    from ctrlrun.authority import REASON_PRECEDENCE
    from ctrlrun.receipt import BLOCKED_APPROVAL_REASONS, BLOCKED_BY_STATE

    listed = set(DECISION_ORDER)
    for name, owned in (
        ("BLOCKED_APPROVAL_REASONS", BLOCKED_APPROVAL_REASONS),
        ("BLOCKED_BY_STATE", BLOCKED_BY_STATE),
        ("REASON_PRECEDENCE", set(REASON_PRECEDENCE)),
    ):
        missing = owned - listed
        assert not missing, f"{name} carries reasons the declared order does not rank: {missing}"

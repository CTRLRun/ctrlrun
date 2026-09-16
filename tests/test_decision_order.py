# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
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

import contextlib
import tempfile
from pathlib import Path

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.control import Control, DecisionPoint
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


# --- T498: the property, run END TO END in both modes ----------------------------------------

DIGEST = "sha256:" + "a" * 64


def _both_modes(
    document: str,
    prepare=None,
    *,
    effect_key: str | None = "refund:EU-1",
    needs_approved_policy: bool = False,
) -> tuple:
    """Run one document in enforce and in observe, and report what each said.

    **This is what §5.3 asks for and what the first version of T498 did not do.** That version
    called `_Observation.block()` twice and compared the result against the same rank function
    `block()` itself calls: a restatement of a one-line implementation, green against three live
    enforce/observe disagreements. An independent review found all three. The property is about
    `Control.execute`, so the test has to call it.
    """
    out = []
    for mode in ("enforce", "observe"):
        tmp = Path(tempfile.mkdtemp())
        store = SQLiteStateStore(str(tmp / "s.db"))
        text = document.replace("MODE", mode).replace("DIGEST", DIGEST)
        if prepare is not None:
            prepare(store, text)
        control = Control(
            Policy.from_yaml(text, source="t"),
            store,
            authority=Authority.from_yaml(text, source="t") if "authority:" in text else None,
            require_approved_policy=needs_approved_policy,
        )
        try:
            receipt = control.execute(_action(), lambda: "ok", effect_key)
            out.append(str(receipt.would_have.blocked_reason) if receipt.would_have else "ran")
        except Exception as exc:
            out.append(str(getattr(exc, "reason", type(exc).__name__)))
    return tuple(out)


def _action():
    return Action(
        name="stripe.refund",
        resource="payment:EU-1",
        arguments={"amount": 10, "payment": "EU-1"},
        principal=Principal(agent="worker"),
        environment="production",
    )


def _burn_the_one_attempt(store, text: str) -> None:
    """Leave the effect FAILED at attempt 1, so `max_attempts: 1` is spent and the ceiling fast
    path refuses the next proposal. Driven under a policy that pins nothing, because a pin would
    refuse this setup call too."""
    from ctrlrun.errors import NotExecuted

    plain = text.replace("mode: observe", "mode: enforce")
    for marker in ("    upstream:\n", "      tls_cert_sha256:"):
        plain = "\n".join(line for line in plain.splitlines() if marker.strip() not in line)
    setup = Control(Policy.from_yaml(plain, source="t"), store)

    def fails():
        raise NotExecuted("the connection was never established")

    with contextlib.suppress(NotExecuted):
        setup.execute(_action(), fails, "refund:EU-1")


CEILING_VS_UPSTREAM = """schema: ctrlrun.policy/v8
mode: MODE
actions:
  stripe.refund:
    effect: "refund:{payment}"
    decision: allow
    max_attempts: 1
    upstream:
      tls_cert_sha256: ["DIGEST"]
"""

UPSTREAM_VS_APPROVAL = """schema: ctrlrun.policy/v8
mode: MODE
actions:
  stripe.refund:
    effect: "refund:{payment}"
    decision: approve
    upstream:
      tls_cert_sha256: ["DIGEST"]
"""

UNAPPROVED_VS_AUTHORITY = """schema: ctrlrun.policy/v8
mode: MODE
actions:
  stripe.refund:
    decision: allow
authority:
  grants:
    - id: someone-else
      subject: { agent: "other-agent" }
      actions: ["stripe.refund"]
"""


@pytest.mark.authority
@pytest.mark.parametrize(
    ("name", "document", "prepare", "expected"),
    [
        # The regression an independent review demonstrated: the ceiling fast path runs in
        # `execute` BEFORE `_secure`, so enforce decides it above the upstream pin. A
        # reason-ranked order put `attempt_ceiling` below `upstream_*` and observe mode reported
        # the wrong one. It also fell into no `ctrlrun stats` bucket, so the refusal was counted
        # nowhere.
        ("ceiling above upstream", CEILING_VS_UPSTREAM, _burn_the_one_attempt, "attempt_ceiling"),
        # `v0.10 §4.3`'s check 2 is above the approval gate on T446's argument: the pin depends
        # on nothing a human says.
        ("upstream above the gate", UPSTREAM_VS_APPROVAL, None, "upstream_unverified"),
        # `v0.9 §4.2.1b`'s second named case, which spans the whole decision path.
        ("policy_unapproved above authority", UNAPPROVED_VS_AUTHORITY, None, "policy_unapproved"),
    ],
)
def test_T498_both_modes_agree_end_to_end(name, document, prepare, expected):
    """§5.3's proof obligation, as a property over `Control.execute` rather than over `_rank`."""
    enforce, observe = _both_modes(
        document,
        prepare,
        effect_key="refund:EU-1" if "effect:" in document else None,
        needs_approved_policy=expected == "policy_unapproved",
    )

    assert enforce == expected, f"{name}: enforce raised {enforce!r}"
    assert observe == expected, (
        f"{name}: enforce raised {enforce!r} and observe reported {observe!r}; observe mode "
        "named a refusal enforce mode does not raise"
    )


def test_T501_every_block_call_site_declares_its_decision_point():
    """**Enumerated from the source, because a hand-maintained list is one the next reason is
    missed from** and this file has been caught by that once already.

    `DecisionPoint.UNDECLARED` sorts last, so a site that forgets still reports a refusal and
    still loses to every site that declares one. That is fail-safe and it is not free: the
    forgotten site's reason can never win, so the report names a later refusal than enforce mode
    raises. The grep is what keeps the set complete.
    """
    source = Path("src/ctrlrun/control.py").read_text()
    calls = []
    marker = "observation.block("
    at = source.find(marker)
    while at != -1:
        # Scan to the MATCHING close paren. A regex cannot: `_blocked_by(refused)` nests, and a
        # non-greedy match stops at the inner one, which is how the first version of this test
        # reported two false positives.
        depth, i = 0, at + len(marker) - 1
        while i < len(source):
            depth += (source[i] == "(") - (source[i] == ")")
            if depth == 0:
                break
            i += 1
        calls.append(source[at + len(marker) : i])
        at = source.find(marker, i)

    undeclared = [call for call in calls if "DecisionPoint." not in call]

    assert calls, "the grep found no block() call sites, so it is testing nothing"
    assert not undeclared, (
        f"{len(undeclared)} observation.block() call site(s) declare no DecisionPoint, so their "
        f"reason can never outrank a later one: {undeclared[:2]}"
    )


def test_T498b_the_points_are_in_enforce_modes_order():
    """The order is the one `control.py:1296`'s comment has carried since v0.3, plus the checks
    `_secure` adds. Asserted here so a reordering is a deliberate edit rather than a side effect.

    **The ceiling sits above everything `_secure` decides**, because its fast path runs in
    `execute` before `_secure` is called. An earlier version ranked it below the upstream pin and
    the budget, and an independent review demonstrated two live mis-reports from that.

    **The approval axis straddles the scope check**, which is why these are points and not
    reasons: `_presented` raises `approval_required` above `_in_scope`, while `_recheck` and
    `_take` raise `precondition_changed`, `consumed` and the rest below it. The same reason at
    two positions cannot have one rank.
    """
    order = [
        DecisionPoint.PRINCIPAL,
        DecisionPoint.POLICY_UNAPPROVED,
        DecisionPoint.AUTHORITY,
        DecisionPoint.POLICY,
        DecisionPoint.CEILING,
        DecisionPoint.UPSTREAM,
        DecisionPoint.BUDGET,
        DecisionPoint.APPROVAL_GATE,
        DecisionPoint.SCOPE,
        DecisionPoint.APPROVAL_TAKE,
        DecisionPoint.RESERVATION,
    ]

    assert order == sorted(order), "the declared points are out of order"
    assert DecisionPoint.CEILING < DecisionPoint.UPSTREAM < DecisionPoint.BUDGET
    assert DecisionPoint.APPROVAL_GATE < DecisionPoint.SCOPE < DecisionPoint.APPROVAL_TAKE
    assert max(order) < DecisionPoint.UNDECLARED, "UNDECLARED must sort last"

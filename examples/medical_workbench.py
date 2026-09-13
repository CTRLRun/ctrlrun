"""Synthetic evidence workbench: real policy, approval, effects, and receipts.

The curated fixture validator is deliberately narrow. It is not a scientific
entailment model. Every destination write stays in memory.
"""

import json

from ctrlrun import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    DuplicateEffect,
    EffectState,
    InMemoryStateStore,
    LocalApprovalProvider,
    Policy,
    context,
    protect,
    with_approval,
)

POLICY = """
schema: ctrlrun.policy/v2
actions:
  medical.brief.release:
    effect: "medical-brief:{inquiry_id}:archive"
    rules:
      - when: { validation_ok_eq: false }
        decision: deny
      - decision: approve
"""

CLAIMS = {
    "C1": (
        0,
        "R1",
        [
            "At week 12, response occurred in 60% of the Compound X group and 40% of the "
            "placebo group, an absolute difference of 20 percentage points.",
            "In the 12-week randomized trial, 60 of 100 participants on Compound X and 40 "
            "of 100 on placebo met the response endpoint (20 percentage points apart).",
        ],
    ),
    "C2": (
        0,
        "R1",
        [
            "Adverse events were reported in 12 of 100 Compound X participants and 8 of 100 "
            "placebo participants during the 12-week trial."
        ],
    ),
    "C3": (
        1,
        "E1",
        ["The single-arm extension cannot establish long-term comparative safety."],
    ),
}
SOURCES = [
    {"id": "DEMO-RCT-01", "version": "Fixture 1.0"},
    {"id": "DEMO-EXT-02", "version": "Fixture 1.0"},
]


def validate(snapshot: dict[str, object]) -> dict[str, object]:
    """Check exact curated passages, calculations, scope, and source versions."""
    issues = []
    claims = snapshot.get("claims", [])
    if [claim.get("id") for claim in claims] != list(CLAIMS):
        issues.append("The evidence brief must contain C1, C2 and C3 without unsupported claims.")
    for claim in claims:
        expected = CLAIMS.get(claim.get("id"))
        if expected is None or (
            claim.get("source"),
            claim.get("span"),
            claim.get("text") in expected[2],
        ) != (expected[0], expected[1], True):
            issues.append(f"{claim.get('id', 'Claim')} does not match a supported fixture passage.")
    if snapshot.get("sources") != SOURCES:
        issues.append("The source snapshot changed; review the new evidence.")
    if snapshot.get("validationVersion") != snapshot.get("version"):
        issues.append("The validation report belongs to a different document version.")
    if snapshot.get("destination") != "Internal medical review archive":
        issues.append("This demonstration only releases to the internal review archive.")
    if snapshot.get("inquiry") != "DEMO-001":
        issues.append("The inquiry is outside this demonstration.")
    return {"passed": not issues, "issues": issues, "difference_pp": 60 - 40}


store = InMemoryStateStore()
control = Control(Policy.from_yaml(POLICY), store, LocalApprovalProvider(store))
deliveries = []
approval = None
approval_version = None
lose_reply = False


@protect("medical.brief.release", control=control)
def release(inquiry_id: str, document_json: str, validation_ok: bool) -> dict[str, str]:
    deliveries.append(json.loads(document_json))
    if lose_reply:
        raise TimeoutError("The simulated archive accepted the brief but its reply was lost.")
    return {"document": inquiry_id, "status": "received"}


def invoke(snapshot: dict[str, object], approval_id: str | None = None) -> dict[str, str]:
    # Recompute the decision input here; a client-provided passed flag is ignored.
    args = {
        "inquiry_id": snapshot.get("inquiry", "DEMO-001"),
        "document_json": json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
        "validation_ok": validate(snapshot)["passed"],
    }
    if approval_id:
        with with_approval(approval_id):
            return release(**args)
    return release(**args)


def step(request_json: str) -> str:
    global approval, approval_version, lose_reply
    request = json.loads(request_json)
    snapshot = request["snapshot"]
    op = request["op"]
    result = {"outcome": "validated", "validation": validate(snapshot)}
    try:
        with context(agent="medical-evidence-demo"):
            if op == "approve":
                if not request.get("reviewed"):
                    result["outcome"] = "review_required"
                else:
                    try:
                        invoke(snapshot)
                    except ApprovalRequired as pending:
                        approved = store.grant_approval(
                            pending.request_id, "human:demo-medical-reviewer"
                        )
                        if approved is None:
                            # SPEC-v0.8 §4.4. This policy asks for one approval, so this
                            # branch is unreachable here; it raises rather than passing,
                            # because an example that silently carried `None` forward would
                            # fail later somewhere that says nothing about why.
                            raise SystemExit(
                                "the demo policy asks for one approval and this grant "
                                "recorded without reaching it"
                            ) from None
                        approval = approved.approval_id
                        approval_version = snapshot["version"]
                        result.update(outcome="approved", action_hash=approved.action_hash)
            elif op == "release":
                lose_reply = bool(request.get("lose_reply"))
                invoke(snapshot, approval)
                result["outcome"] = "committed"
            elif op == "reconcile":
                key = "medical-brief:DEMO-001:archive"
                effect = store.get_effect(key)
                if deliveries and effect and effect.state == EffectState.AMBIGUOUS:
                    store.resolve_effect(key, EffectState.COMMITTED, "human:demo-medical-reviewer")
                    result["outcome"] = "reconciled"
                else:
                    result["outcome"] = "nothing_to_reconcile"
    except ApprovalRequired:
        result["outcome"] = "approval_required"
    except ApprovalMismatch as exc:
        result.update(outcome="approval_mismatch", reason=exc.reason)
    except ActionDenied as exc:
        result.update(outcome="validation_blocked", reason=exc.reason)
    except DuplicateEffect as exc:
        result.update(outcome="duplicate", reason=str(exc.state))
    except AmbiguousEffect:
        result["outcome"] = "ambiguous_retry"
    except TimeoutError:
        result["outcome"] = "reply_lost"
    effect = store.get_effect("medical-brief:DEMO-001:archive")
    result.update(
        approval_id=approval,
        approval_version=approval_version,
        writes=len(deliveries),
        effect_state=str(effect.state).upper() if effect else "Not attempted",
        receipts=[receipt.to_dict() for receipt in store.receipts()],
        events=[event.to_dict() for event in store.events()],
    )
    return json.dumps(result)

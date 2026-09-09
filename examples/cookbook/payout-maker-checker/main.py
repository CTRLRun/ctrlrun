# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/payout-maker-checker.mdx — edit the page, never this file.
from pathlib import Path

from ctrlrun import (
    Action,
    ApprovalRequired,
    Authority,
    AuthorityDenied,
    AuthorityEscalation,
    Control,
    Policy,
    Principal,
    SQLiteStateStore,
    with_approval,
)
from ctrlrun.authority import grant_from_yaml

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

LEAD = Principal(agent="treasury-lead", user="mira@example.com")
AGENT = Principal(agent="payout-agent", user="mira@example.com")
document = (HERE / "ctrlrun.yaml").read_text(encoding="utf-8")
store = SQLiteStateStore(STATE / "state.db")
control = Control(
    Policy.from_yaml(document, source="ctrlrun.yaml"),
    store,
    authority=Authority.from_yaml(document, source="ctrlrun.yaml"),
    environment="production",
)
paid: list[tuple[str, int]] = []


def payout(account: str, reference: str, amount: int) -> dict:
    paid.append((reference, amount))
    return {"reference": reference, "status": "sent"}


def proposal(who: Principal, account: str, reference: str, amount: int) -> Action:
    return Action(
        name="bank.payout",
        arguments={"account": account, "reference": reference, "amount": amount},
        principal=who,
        resource=f"account:{account}",
    )


# The lead hands the agent a €25,000 slice, itself delegable so the agent could pass a narrower
# one on. Every dimension is stated: omission is rejected, never inherited.
slice_ = grant_from_yaml("""
subject: { agent: "payout-agent", user: "mira@example.com" }
actions: ["bank.payout"]
resources: ["account:ops-*"]
constraints: { amount_gte: 0, amount_lte: 2500000 }
environments: ["production"]
delegable: true
expires_at: "2026-12-31T00:00:00Z"
""")
delegation = control.delegate("treasury-lead", slice_, by=LEAD)
print("delegated to the payout agent:", delegation.delegation_id)

# €8,000: inside the slice, inside the autonomous band. Runs.
control.execute(
    proposal(AGENT, "ops-eu", "inv-1042", 800000),
    lambda: payout("ops-eu", "inv-1042", 800000),
    "payout:ops-eu:inv-1042",
)
print("€8,000 payout: sent")

# €18,000: inside the slice, above the desk limit. A second person.
try:
    control.execute(
        proposal(AGENT, "ops-eu", "inv-1043", 1800000),
        lambda: payout("ops-eu", "inv-1043", 1800000),
        "payout:ops-eu:inv-1043",
    )
except ApprovalRequired as pending:
    print("€18,000 payout: a checker decides:", pending.request_id)
    store.grant_approval(pending.request_id, "checker:sam@example.com")
    with with_approval(pending.request_id):
        control.execute(
            proposal(AGENT, "ops-eu", "inv-1043", 1800000),
            lambda: payout("ops-eu", "inv-1043", 1800000),
            "payout:ops-eu:inv-1043",
        )
    print("€18,000 payout, checked: sent")
else:
    raise SystemExit("a payout above the desk limit ran without a checker")

# €40,000: outside the slice. Authority refuses before the policy is asked.
try:
    control.execute(
        proposal(AGENT, "ops-eu", "inv-1044", 4000000),
        lambda: payout("ops-eu", "inv-1044", 4000000),
        "payout:ops-eu:inv-1044",
    )
except AuthorityDenied as refused:
    print("€40,000 payout: refused,", refused.reason)
else:
    raise SystemExit("a payout outside the delegated grant ran")

# The agent tries to widen its own slice.
try:
    control.delegate(
        delegation.delegation_id,
        grant_from_yaml("""
subject: { agent: "payout-agent", user: "mira@example.com" }
actions: ["bank.payout"]
resources: ["account:*"]
constraints: { amount_gte: 0, amount_lte: 9000000 }
environments: ["production"]
expires_at: "2026-12-31T00:00:00Z"
"""),
        by=AGENT,
    )
except AuthorityEscalation as refused:
    print("widening the slice: refused,", refused.reason, refused.dimension)
else:
    raise SystemExit("an agent widened its own authority")

print("payouts sent:", len(paid))
store.close()

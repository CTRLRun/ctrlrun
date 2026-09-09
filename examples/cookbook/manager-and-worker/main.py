# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/manager-and-worker.mdx — edit the page, never this file.
from pathlib import Path

from ctrlrun import (
    Action,
    Authority,
    AuthorityDenied,
    AuthorityEscalation,
    Control,
    Policy,
    Principal,
    SQLiteStateStore,
)
from ctrlrun.authority import grant_from_yaml

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

MANAGER = Principal(agent="ops-manager")
WORKER = Principal(agent="scale-worker")
document = (HERE / "ctrlrun.yaml").read_text(encoding="utf-8")
store = SQLiteStateStore(STATE / "state.db")
control = Control(
    Policy.from_yaml(document, source="ctrlrun.yaml"),
    store,
    authority=Authority.from_yaml(document, source="ctrlrun.yaml"),
    environment="production",
)
scaled: list[tuple[str, int]] = []


def scale(who: Principal, cluster: str, deployment: str, replicas: int) -> None:
    control.execute(
        Action(
            name="k8s.scale",
            arguments={"cluster": cluster, "deployment": deployment, "replicas": replicas},
            principal=who,
            resource=f"cluster:{cluster}",
        ),
        lambda: scaled.append((deployment, replicas)),
        f"scale:{cluster}:{deployment}:{replicas}",
    )


# The worker gets scaling only, on one cluster, up to 10 replicas, for a day.
job = control.delegate(
    "ops-manager",
    grant_from_yaml("""
subject: { agent: "scale-worker" }
actions: ["k8s.scale"]
resources: ["cluster:prod-eu"]
constraints: { replicas_gte: 0, replicas_lte: 10 }
environments: ["production"]
expires_at: "2026-12-01T00:00:00Z"
"""),
    by=MANAGER,
)
print("worker's grant:", job.delegation_id)

scale(WORKER, "prod-eu", "checkout", 6)
print("worker scales checkout to 6 on prod-eu: done")

try:
    scale(WORKER, "prod-eu", "checkout", 40)
except AuthorityDenied as refused:
    print("worker scales to 40: refused,", refused.reason)
else:
    raise SystemExit("the worker exceeded its slice")

try:
    scale(WORKER, "prod-us", "checkout", 6)
except AuthorityDenied as refused:
    print("worker scales on prod-us: refused,", refused.reason)
else:
    raise SystemExit("the worker acted outside its cluster")

try:
    control.delegate(
        "ops-manager",
        grant_from_yaml("""
subject: { agent: "scale-worker" }
actions: ["k8s.scale"]
resources: ["cluster:prod-eu"]
environments: ["production"]
expires_at: "2026-12-01T00:00:00Z"
"""),
        by=MANAGER,
    )
except AuthorityEscalation as refused:
    print("a slice that omits constraints: refused,", refused.reason, refused.dimension)
else:
    raise SystemExit("an omitted dimension was inherited as unlimited")

# The job is over.
control.revoke(job.delegation_id, by="ops-manager")
try:
    scale(WORKER, "prod-eu", "checkout", 4)
except AuthorityDenied as refused:
    print("worker after revocation: refused,", refused.reason)
else:
    raise SystemExit("a revoked worker still acted")

print("scaling calls:", scaled)
store.close()

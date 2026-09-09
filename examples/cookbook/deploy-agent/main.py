# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/deploy-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

calls: list[str] = []


def kubectl(*args: str) -> str:
    calls.append(" ".join(args))
    return "ok"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("k8s.rollout_restart", effect="restart:{cluster}:{deployment}", control=control)
def restart(cluster: str, deployment: str) -> str:
    return kubectl("rollout", "restart", f"deployment/{deployment}", "--context", cluster)


@ctrlrun.protect("k8s.apply", effect="apply:{cluster}:{manifest_hash}", control=control)
def apply(cluster: str, manifest_hash: str) -> str:
    return kubectl("apply", "-f", manifest_hash, "--context", cluster)


@ctrlrun.protect("k8s.delete_namespace", control=control)
def delete_namespace(cluster: str, name: str) -> str:
    return kubectl("delete", "namespace", name, "--context", cluster)


with ctrlrun.context(agent="oncall-agent"):
    print("restart checkout on prod:", restart(cluster="prod-eu", deployment="checkout"))
    print("apply to staging:", apply(cluster="staging", manifest_hash="sha256:9c1f"))

    try:
        apply(cluster="prod-eu", manifest_hash="sha256:9c1f")
    except ctrlrun.ApprovalRequired as pending:
        print("apply to prod: a human decides:", pending.request_id)
    else:
        raise SystemExit("a production apply ran without a human")

    try:
        delete_namespace(cluster="prod-eu", name="checkout")
    except ctrlrun.ActionDenied as refused:
        print("delete namespace: refused,", refused.reason)
    else:
        raise SystemExit("a namespace delete ran")

    # A second worker picks up the same ticket.
    try:
        restart(cluster="prod-eu", deployment="checkout")
    except ctrlrun.DuplicateEffect:
        print("second worker restarts checkout: refused, already done")
    else:
        raise SystemExit("the same restart ran twice")

print("kubectl calls:", len(calls))

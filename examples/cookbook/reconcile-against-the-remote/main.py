# Extracted by tools/docs_audit/render_cookbook.py from
# docs/cookbook/reconcile-against-the-remote.mdx — edit the page, never this file.
import contextlib
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

stripe_refunds: dict[str, int] = {}  # what Stripe holds
replicas: dict[str, int] = {"checkout": 3}  # what the cluster holds
calls: list[str] = []


def stripe_refund(payment_id: str, amount: int) -> str:
    calls.append(f"refund {payment_id}")
    stripe_refunds[payment_id] = amount  # committed...
    raise TimeoutError("no response from api.stripe.com")  # ...reply lost


def kubectl_scale(deployment: str, count: int) -> str:
    calls.append(f"scale {deployment}")
    raise ConnectionResetError("connection reset by peer")  # never reached the API server


def ask_stripe(effect_key: str) -> ctrlrun.ReconcileOutcome:
    payment_id = effect_key.removeprefix("refund:")
    return "committed" if payment_id in stripe_refunds else "not_executed"


def ask_kubernetes(effect_key: str) -> ctrlrun.ReconcileOutcome:
    _, _, deployment, count = effect_key.split(":")
    return "committed" if replicas.get(deployment) == int(count) else "not_executed"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect(
    "stripe.refund",
    effect="refund:{payment_id}",
    reconcile=ask_stripe,
    reconcile_eagerly=True,
    control=control,
)
def refund(payment_id: str, amount: int) -> str:
    return stripe_refund(payment_id, amount)


@ctrlrun.protect(
    "k8s.scale",
    effect="scale:{cluster}:{deployment}:{replicas}",
    reconcile=ask_kubernetes,
    reconcile_eagerly=True,
    control=control,
)
def scale(cluster: str, deployment: str, replicas: int) -> str:
    return kubectl_scale(deployment, replicas)


with ctrlrun.context(agent="ops-agent"):
    try:
        refund(payment_id="txn_9", amount=50000)
    except TimeoutError:
        print("refund txn_9: reply lost; the hook asked Stripe")
    try:
        refund(payment_id="txn_9", amount=50000)
    except ctrlrun.DuplicateEffect:
        print("refund txn_9 again: refused, Stripe has it")
    else:
        raise SystemExit("a refund Stripe already holds ran again")

    try:
        scale(cluster="prod-eu", deployment="checkout", replicas=6)
    except ConnectionResetError:
        print("scale checkout to 6: connection reset; the hook asked the cluster")
    replicas["checkout"] = 6  # the retry succeeds this time
    with contextlib.suppress(ConnectionResetError):
        scale(cluster="prod-eu", deployment="checkout", replicas=6)
    print("scale checkout to 6 again: permitted, the cluster had not applied it")

print("remote calls:", calls)

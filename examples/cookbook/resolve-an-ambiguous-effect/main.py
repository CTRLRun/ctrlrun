# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/resolve-an-ambiguous-effect.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, EffectState, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
remote_calls: list[str] = []


@ctrlrun.protect("dns.update_record", effect="dns:{zone}:{name}", control=control)
def update_record(zone: str, name: str, value: str) -> str:
    remote_calls.append(f"dns {name}")
    raise ConnectionResetError("connection reset by peer")


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> str:
    remote_calls.append(f"refund {payment_id}")
    if len(remote_calls) == 2:
        raise TimeoutError("no response from api.stripe.com after 30s")
    return "refunded"


with ctrlrun.context(agent="ops-agent"):
    for call in (
        lambda: update_record(zone="example.com", name="api", value="203.0.113.7"),
        lambda: refund(payment_id="txn_7", amount=50000),
    ):
        try:
            call()
        except (ConnectionResetError, TimeoutError) as lost:
            print("the executor saw:", lost)

    ambiguous = [record.effect_key for record in store.list_effects(EffectState.AMBIGUOUS)]
    print("ambiguous effects:", ambiguous)

    # A human asks the DNS provider: the record was updated. Asks Stripe: no refund exists.
    # This is what `ctrlrun resolve <key> --committed` and `--failed` do.
    store.resolve_effect("dns:example.com:api", EffectState.COMMITTED, "human:ops")
    store.resolve_effect("refund:txn_7", EffectState.FAILED, "human:ops")

    try:
        update_record(zone="example.com", name="api", value="203.0.113.7")
    except ctrlrun.DuplicateEffect:
        print("DNS update again: refused, it committed")
    else:
        raise SystemExit("a committed update ran again")

    print("refund again:", refund(payment_id="txn_7", amount=50000))

for record in store.list_effects():
    print(f"{record.effect_key}: {record.state.value}, resolved by {record.resolved_by}")
print("remote calls:", remote_calls)

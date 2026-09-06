# Extracted by tools/docs_audit/render_cookbook.py from
# docs/cookbook/receipts-to-opentelemetry.mdx — edit the page, never this file.
import contextlib
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.otel import OTelEventSink

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

exporter = InMemorySpanExporter()  # your OTLP exporter in production
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))

store = SQLiteStateStore(STATE / "state.db")
control = Control(
    Policy.from_file(HERE / "ctrlrun.yaml"),
    store,
    sinks=[OTelEventSink(tracer_provider=provider)],
)


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> str:
    if payment_id == "txn_lost":
        raise TimeoutError("no response from api.stripe.com")
    return "refunded"


with ctrlrun.context(agent="support-agent"):
    refund(payment_id="txn_1", amount=12000)
    try:
        refund(payment_id="txn_2", amount=900000)
    except ctrlrun.ActionDenied:
        pass
    else:
        raise SystemExit("a €9,000 refund ran")
    with contextlib.suppress(TimeoutError):
        refund(payment_id="txn_lost", amount=12000)

for span in exporter.get_finished_spans():
    events = [event.name for event in span.events]
    result = span.attributes.get("ctrlrun.result")
    print(
        f"{span.name}  status={span.status.status_code.name}  result={result}  events={len(events)}"
    )
    if any("12000" in str(value) for value in span.attributes.values()):
        raise SystemExit("an argument value leaked into the span attributes")
print("argument values in attributes: none")

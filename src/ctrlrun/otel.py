"""The OpenTelemetry sink. Build-list item 8; SPEC-v0.2 §8. Ships in `ctrlrun[otel]`.

An `EventSink` (§4) and nothing more: it never decides, never blocks, and never raises. What
it exports is a copy of what the store already holds, so a failing exporter is a lost export
and never a failed action — §4.2's rule, and the reason this is a sink rather than a hook.

**One span per action**, opened on `ACTION_PROPOSED` and carrying one span event per `Event`.
A retry is a new action and therefore a new span; the two are correlated by
`ctrlrun.effect_key`, which is the identity that actually spans attempts (v0.1 §5).
"""

from __future__ import annotations

import importlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Final

from .errors import MissingDependency
from .receipt import Event, Receipt

_LOG = logging.getLogger(__name__)

#: Imported by name so a missing extra is a `MissingDependency` rather than a
#: `ModuleNotFoundError` from halfway down an import chain (SPEC-v0.2 §1.1).
_OTEL_MODULE: Final = "opentelemetry.trace"
_EXTRA: Final = "otel"

#: SPEC-v0.2 §8 — the attribute prefix, and the one exception to it being free.
PREFIX: Final = "ctrlrun."
ARGUMENT_PREFIX: Final = "ctrlrun.argument."

#: An action's span ends when its receipt arrives. A suspended one never gets a receipt, so
#: its span stays open; this bounds how many can accumulate in a long-running process.
DEFAULT_MAX_OPEN_SPANS: Final = 1024

#: `ReceiptResult` values that mark a span ERROR. A refusal is the system working as
#: designed, and marking it an error teaches an on-call rotation to ignore the signal that
#: matters — so `denied` and `blocked` leave the status unset.
_ERROR_RESULTS: Final = frozenset({"failed", "ambiguous"})
_OK_RESULTS: Final = frozenset({"committed"})


class OTelEventSink:
    """Export every `Event` and `Receipt` as OpenTelemetry spans (SPEC-v0.2 §8).

    **Never blocks.** Spans are handed to whatever span processor the application configured
    and nothing is flushed synchronously; with no configured tracer provider the API's no-op
    provider makes the whole thing free. A process that dies mid-action leaves that span
    unended — stated, not solved.
    """

    def __init__(
        self,
        *,
        tracer_provider: Any = None,
        arguments: bool = False,
        max_open_spans: int = DEFAULT_MAX_OPEN_SPANS,
    ) -> None:
        trace = _trace()
        self._trace = trace
        self._tracer = (
            tracer_provider.get_tracer("ctrlrun")
            if tracer_provider is not None
            else trace.get_tracer("ctrlrun")
        )
        # SPEC-v0.2 §8 — argument *values* are not attributes unless asked for. Arguments
        # carry customer identifiers and amounts, and a trace backend is not the receipt
        # store. The default is the fail-closed one.
        self._arguments = arguments
        self._max_open = max(1, max_open_spans)
        self._open: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def open_spans(self) -> int:
        """How many actions have a span open. Bounded; see `max_open_spans`."""
        with self._lock:
            return len(self._open)

    # --- EventSink ----------------------------------------------------------------------

    def on_event(self, event: Event) -> None:
        span = self._span_for(event)
        if span is None:
            return
        span.add_event(str(event.type), attributes=self._event_attributes(event))

    def on_receipt(self, receipt: Receipt) -> None:
        """End the action's span, carrying everything only the receipt knows.

        SPEC: §8 says the span ends on the action's *terminal event*. It ends here instead,
        one call later, because `ctrlrun.receipt_id`, `ctrlrun.result` and the final
        `ctrlrun.attempt` live on the receipt and a receipt always follows a terminal event
        in the same `Control` call. Ending a step earlier would mean exporting a span that
        cannot say how the action came out, which is the only thing a reader wants from it.
        """
        with self._lock:
            span = self._open.pop(receipt.action_id, None)
        if span is None:
            return
        # §8 — the span name is the action name, and only the receipt carries it: an `Event`
        # has no action name (v0.1 §6.2), so the span opens under a placeholder and is
        # renamed here. Renaming before `end()` is what `update_name` is for.
        span.update_name(receipt.action)
        for name, value in self._receipt_attributes(receipt).items():
            span.set_attribute(name, value)
        span.set_status(self._status(str(receipt.result), receipt.error))
        span.end()

    # --- internals ----------------------------------------------------------------------

    def _span_for(self, event: Event) -> Any:
        from .receipt import EventType

        with self._lock:
            span = self._open.get(event.action_id)
            if span is not None:
                return span
            if event.type is not EventType.ACTION_PROPOSED:
                # Every action opens with ACTION_PROPOSED (v0.1 §6.2). An event for an action
                # this sink never saw start belongs to a span somebody else owns — a process
                # that restarted, or a `ctrlrun resolve` run days later — and inventing one
                # would export a span with no beginning.
                return None
            span = self._tracer.start_span(
                PENDING_SPAN_NAME,
                attributes={f"{PREFIX}action_id": event.action_id},
            )
            self._open[event.action_id] = span
            self._evict_if_needed()
            return span

    def _evict_if_needed(self) -> None:
        """Bound the open-span map. Called under the lock.

        An action awaiting a human never reaches a receipt, so its span never ends. Without a
        bound a long-running gateway accumulates one per abandoned approval. Evicted spans are
        dropped rather than ended: ending one would export a span asserting an outcome that
        never happened.
        """
        while len(self._open) > self._max_open:
            action_id, _ = self._open.popitem(last=False)
            _LOG.warning(
                "dropped the open OTel span for %s: more than %d actions are awaiting a "
                "terminal state at once",
                action_id,
                self._max_open,
            )

    def _event_attributes(self, event: Event) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        if event.effect_key is not None:
            attributes[f"{PREFIX}effect_key"] = event.effect_key
        if event.approval_id is not None:
            attributes[f"{PREFIX}approval_id"] = event.approval_id
        for key, value in event.data.items():
            if _exportable(value):
                attributes[f"{PREFIX}{key}"] = value
        return attributes

    def _receipt_attributes(self, receipt: Receipt) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            f"{PREFIX}action_id": receipt.action_id,
            f"{PREFIX}action.name": receipt.action,
            f"{PREFIX}action_hash": receipt.action_hash,
            f"{PREFIX}principal.agent": receipt.principal.agent,
            f"{PREFIX}environment": receipt.environment,
            f"{PREFIX}decision": str(receipt.decision),
            f"{PREFIX}decision_reason": receipt.decision_reason,
            f"{PREFIX}attempt": receipt.attempt,
            f"{PREFIX}result": str(receipt.result),
            f"{PREFIX}receipt_id": receipt.receipt_id,
        }
        for name, value in (
            (f"{PREFIX}principal.user", receipt.principal.user),
            (f"{PREFIX}resource", receipt.resource),
            (f"{PREFIX}effect_key", receipt.effect_key),
            (f"{PREFIX}approval_id", receipt.approval_id),
            (f"{PREFIX}approver", receipt.approver),
        ):
            if value is not None:
                attributes[name] = value
        if self._arguments:
            for key, value in receipt.arguments.items():
                if _exportable(value):
                    attributes[f"{ARGUMENT_PREFIX}{key}"] = value
        return attributes

    def _status(self, result: str, error: str | None) -> Any:
        status = self._trace.Status
        code = self._trace.StatusCode
        if result in _ERROR_RESULTS:
            return status(code.ERROR, error)
        if result in _OK_RESULTS:
            return status(code.OK)
        # `denied` and `blocked`: unset, deliberately.
        return status(code.UNSET)


#: What a span is called between `ACTION_PROPOSED` and its receipt. An `Event` carries no
#: action name (v0.1 §6.2), so a span that never reaches a receipt — one awaiting a human —
#: keeps this name. It is deliberately not the tool's name: guessing one would be worse.
PENDING_SPAN_NAME: Final = "ctrlrun.action"


def _exportable(value: object) -> bool:
    """Whether OpenTelemetry can carry this value as an attribute without a conversion.

    Anything else is dropped rather than stringified: an attribute whose type changed on the
    way out is worse than one that is absent, because a query written against it silently
    stops matching.
    """
    return isinstance(value, str | bool | int | float)


def _trace() -> Any:
    try:
        return importlib.import_module(_OTEL_MODULE)
    except ImportError as exc:
        raise MissingDependency(_OTEL_MODULE, _EXTRA) from exc

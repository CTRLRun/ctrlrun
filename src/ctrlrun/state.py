"""StateStore protocol, SQLite and in-memory stores. Build-list item 6; SPEC-v0.1 §5.3.

Build-list item 3 needs only the evidence half of the protocol — events and receipts — and
an in-memory store to hold it. The effect-reservation and approval methods of SPEC §5.3, and
`SQLiteStateStore`, arrive with build-list items 4 and 6.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Protocol

from .receipt import Event, Receipt


class StateStore(Protocol):
    """Durable state behind a `Control` (SPEC-v0.1 §5.3)."""

    def append_event(self, event: Event) -> None:
        """Append an event, assigning it the next `event_id`."""
        ...

    def put_receipt(self, receipt: Receipt) -> None:
        """Record a receipt for an action that reached a terminal state."""
        ...


class InMemoryStateStore:
    """Evidence held in process memory: for tests and `ctrlrun demo`.

    Nothing here survives the process, and nothing here is shared between processes, so it
    cannot provide the atomic reservation of SPEC-v0.1 §5.3 E1. `SQLiteStateStore` (build-list
    item 6) is the store for anything that matters.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._receipts: list[Receipt] = []

    def append_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(replace(event, event_id=len(self._events) + 1))

    def put_receipt(self, receipt: Receipt) -> None:
        with self._lock:
            self._receipts.append(receipt)

    def events(self) -> tuple[Event, ...]:
        """An immutable snapshot of the event log, in append order."""
        with self._lock:
            return tuple(self._events)

    def receipts(self) -> tuple[Receipt, ...]:
        """An immutable snapshot of the receipts, in write order."""
        with self._lock:
            return tuple(self._receipts)

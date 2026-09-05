"""What a backend hands the suite, and the two that already exist. SPEC-v0.6 §2.2.

`StoreBackend` is the whole of what a backend author implements to be graded. It is deliberately
four methods and no hook: the suite has no fault injection and no way to reach inside a store,
because a seam in shipping code that exists only for a test is what §1.1's last bullet forbids.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from ...state import InMemoryStateStore, SQLiteStateStore, StateStore

#: The scheme the cross-process worker understands. One value in item 1; `postgresql://` joins
#: it in item 3, and the worker parses it rather than `--store-url` (§4.1: that flag is
#: verify's, it names a database an operator owns, and the two have different rules about
#: whose database they may touch).
SQLITE_SCHEME = "sqlite://"


@runtime_checkable
class StoreBackend(Protocol):
    """A live backend the suite may open, reopen and describe to a subprocess (§2.2)."""

    name: str

    def open(self) -> StateStore:
        """A store on this backend's storage. The first call creates it empty."""
        ...

    def reopen(self) -> StateStore | None:
        """A second, independent store on the SAME storage as `open()`, or `None`.

        Two handles, not one shared object: `durability` is about what survives a process
        losing its handle, and a backend that returned the same object would pass it by
        holding the answer in memory.

        `None` means the storage does not outlive the object that holds it, which is a
        property of the backend -- `InMemoryStateStore` says so in its own docstring. The
        `durability` cases are then `not_applicable` with that reason (§2.4), and
        `falsely-declares-no-url` is what keeps the declaration honest.
        """
        ...

    def url(self) -> str | None:
        """An address the conformance worker subprocess can open this storage by, or `None`.

        Deliberately **not** a `--store-url` value (§4.1). `None` means the storage cannot be
        reached from another OS process, and `reservation/e1-cross-process` is then
        `not_applicable` with that reason.
        """
        ...

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        """A store on this backend's storage, reading time from `clock`.

        The suite's **only** seam, and it is one every shipped store already takes. `v0.1 §5.3
        E3` is about a lease expiring and `v0.4 §3.6`'s rule -- no sleeps, anywhere -- applies
        here in full: a case that needs time to pass advances an injected clock. It is not a
        relaxation under §1.1, because nothing about what the store *refuses* changes; only
        what time it thinks it is.
        """
        ...

    def reset(self) -> None:
        """Discard everything. Called between cases; the suite never reuses state."""
        ...


class SQLiteBackend:
    """`SQLiteStateStore` on a real file. Durable, shareable, no N/A."""

    name = "sqlite"

    def __init__(self, root: Path) -> None:
        # A private subdirectory per instance. Two backends handed the same root -- which a
        # test comparing two fixtures does without thinking about it -- would otherwise share
        # one file, and the second would inherit the first's rows. That produces a failure
        # attributed to the wrong fixture, which is the worst kind: it reads as a finding.
        self._root = Path(root) / f"backend-{uuid4().hex}"
        self._root.mkdir(parents=True, exist_ok=True)
        self._open: list[StateStore] = []
        self._serial = 0

    @property
    def _path(self) -> Path:
        return self._root / f"conformance-{self._serial}.db"

    def open(self) -> StateStore:
        store = SQLiteStateStore(self._path)
        self._open.append(store)
        return store

    def reopen(self) -> StateStore | None:
        return self.open()

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        store = SQLiteStateStore(self._path, clock=clock)
        self._open.append(store)
        return store

    def url(self) -> str | None:
        return f"{SQLITE_SCHEME}{self._path}"

    def reset(self) -> None:
        for store in self._open:
            store.close()
        self._open.clear()
        # A new file rather than a truncated one: `close()` is not a fence (§2.7), so another
        # handle may still be usable, and reusing the path would let one case see another's
        # rows through it.
        self._serial += 1


class InMemoryBackend:
    """`InMemoryStateStore`. Two honest N/As and no others (§2.4).

    Its storage **is** the object -- *"Nothing here survives the process, and nothing here is
    shared between processes"* -- so `reopen()` and `url()` are `None`, and the suite reports
    `durability` and `reservation/e1-cross-process` inapplicable with that reason.
    """

    name = "memory"

    def __init__(self, root: Path | None = None) -> None:
        self._open: list[StateStore] = []

    def open(self) -> StateStore:
        """A **fresh** store each call, which is the truth about this backend.

        Returning a cached one would make two handles share, and §2.6's honesty check reads
        exactly that signal to decide whether an N/A declaration is real. A backend must not be
        able to earn an N/A by caching.
        """
        store = InMemoryStateStore()
        self._open.append(store)
        return store

    def reopen(self) -> StateStore | None:
        return None

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        store = InMemoryStateStore(clock=clock)
        self._open.append(store)
        return store

    def url(self) -> str | None:
        return None

    def reset(self) -> None:
        for store in self._open:
            store.close()
        self._open.clear()


def store_from_url(url: str) -> StateStore:
    """Open the storage a `url()` names. Used by the worker subprocess and by nothing else."""
    if url.startswith(SQLITE_SCHEME):
        return SQLiteStateStore(Path(url[len(SQLITE_SCHEME) :]))
    raise ValueError(f"no backend for {url!r}")

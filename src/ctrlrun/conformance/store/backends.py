"""What a backend hands the suite, and the two that already exist. SPEC-v0.6 §2.2.

`StoreBackend` is the whole of what a backend author implements to be graded. It is deliberately
four methods and no hook: the suite has no fault injection and no way to reach inside a store,
because a seam in shipping code that exists only for a test is what §1.1's last bullet forbids.
"""

from __future__ import annotations

import logging
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

#: Item 3's second value. The worker parses these; `--store-url` is verify's and has its own
#: rules about whose database it may touch (§4.1).
POSTGRES_SCHEMES = ("postgresql://", "postgres://")

#: How a conformance URL carries the schema its store must live in. CTRLRun's, not libpq's.
SCHEMA_PARAM = "ctrlrun_schema"


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


_LOG = logging.getLogger(__name__)


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
    if url.startswith(POSTGRES_SCHEMES):
        from ...postgres import PostgresStateStore

        bare, schema = _split_schema(url)
        if schema != "public":
            # A conformance URL names a schema CTRLRun owns, so the worker may create it. This
            # path is never reached by an operator's store: `PostgresStateStore` itself creates
            # nothing, and verify's scratch schema is made and dropped by the caller (§4.1).
            PostgresStateStore.create_schema(bare, schema)
        return PostgresStateStore(bare, schema=schema)
    raise ValueError(f"no backend for {url!r}")


def _split_schema(url: str) -> tuple[str, str]:
    """Peel CTRLRun's own schema parameter off a conformance URL."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != SCHEMA_PARAM]
    schema = dict(parse_qsl(parts.query)).get(SCHEMA_PARAM, "public")
    return urlunsplit(parts._replace(query=urlencode(query))), schema


class PostgresBackend:
    """`PostgresStateStore` against a live server. Durable, shareable, no N/A.

    Each instance takes a **private schema**, so two backends handed one URL cannot see each
    other's rows -- the isolation rule §2.7 states for every backend, which SQLite learned by
    producing a failure attributed to the wrong fixture.
    """

    name = "postgres"

    def __init__(self, url: str) -> None:
        self._url = url
        self._serial = 0
        self._prefix = f"conf_{uuid4().hex[:12]}"
        self._open: list[StateStore] = []

    @property
    def _schema(self) -> str:
        return f"{self._prefix}_{self._serial}"

    def _ensure(self) -> None:
        from ...postgres import PostgresStateStore

        PostgresStateStore.create_schema(self._url, self._schema)

    def open(self) -> StateStore:
        from ...postgres import PostgresStateStore

        self._ensure()
        store = PostgresStateStore(self._url, schema=self._schema)
        self._open.append(store)
        return store

    def reopen(self) -> StateStore | None:
        return self.open()

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        from ...postgres import PostgresStateStore

        self._ensure()
        store = PostgresStateStore(self._url, schema=self._schema, clock=clock)
        self._open.append(store)
        return store

    def url(self) -> str | None:
        # The schema travels as CTRLRun's own parameter, stripped by `store_from_url` before the
        # URL reaches the driver. Encoding it as libpq `options=-csearch_path=` would not work:
        # `PostgresStateStore` sets `search_path` itself on every connection, so a schema smuggled
        # through the driver would be silently overridden and eight contenders would race in
        # `public` instead. That is a bug the cross-process case found on its first Postgres run.
        joiner = "&" if "?" in self._url else "?"
        return f"{self._url}{joiner}{SCHEMA_PARAM}={self._schema}"

    def reset(self) -> None:
        """Close every store and **drop the schema they used**.

        A review counted 1137 `conf_*` schemas left in one database after a handful of runs: the
        backend made one per case and dropped none. In CI the service is ephemeral and it does
        not matter; in a developer's or an operator's database it does, and the catalogue bloat
        eventually shows up as something else entirely -- here it was `remaining connection slots
        are reserved`, reported by a case that had nothing to do with it.
        """
        from ...postgres import PostgresStateStore

        for store in self._open:
            store.close()
        self._open.clear()
        try:
            PostgresStateStore.drop_schema(self._url, self._schema)
        except Exception as refused:
            # Not suppressed. `contextlib.suppress` cannot catch a *block*, and this used to
            # wait forever behind one connection the suite had not closed -- a child process
            # left alive by an early return. Now `drop_schema` gives up, and a cleanup that
            # could not run says so instead of leaving a schema nobody will look for.
            _LOG.warning(
                "could not drop conformance schema %r: %s. It is left behind; drop it by hand "
                "if this database is not disposable",
                self._schema,
                refused,
            )
        self._serial += 1

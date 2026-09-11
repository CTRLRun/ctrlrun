"""Fixtures shared across the suite.

`state_store` is parametrized over both shipped implementations. A reservation test written
once therefore runs against `InMemoryStateStore` and `SQLiteStateStore`, which is how the
double is kept from drifting into refusing less than the real store (SPEC-v0.1 §5.3).

`no_network` is the other shared double, and it is here for the same reason. "Runs with no
network" is a claim until something takes the network away, and the guard that takes it away
was written twice -- once in `test_examples.py`, once in the documentation tools -- which is
two chances for one copy to refuse less than the other and make one suite's claim quietly
weaker. The documentation tools are a separate repository now; this is the library's one
definition, and both suites that need it take it from here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import InMemoryStateStore, SQLiteStateStore


class FakeClock:
    """A clock that only moves when a test moves it, so leases and expiry are exact."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture(params=["in-memory", "sqlite"])
def state_store(request, tmp_path, fake_clock):
    """Both StateStore implementations, one test body (SPEC-v0.1 §5.3)."""
    store = (
        InMemoryStateStore(clock=fake_clock)
        if request.param == "in-memory"
        else SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    )
    yield store
    store.close()


# --- SPEC-v0.3 §7 / T66: the five new event types stay inside authority's tests ----------

#: The event types SPEC-v0.3 §7 adds. A configuration with no `authority:` section must
#: never produce one (§4.1), and T66's mechanical half is the assertion that none of them
#: appeared anywhere in the run except in a test that built such a section.
AUTHORITY_EVENT_TYPES = frozenset(
    {
        "AUTHORITY_RESOLVED",
        "AUTHORITY_DENIED",
        "DELEGATION_CREATED",
        "DELEGATION_REVOKED",
        "DELEGATION_REJECTED",
    }
)


def check_authority_events_declared(appended, *, declared, nodeid="<test>"):
    """Raise unless every SPEC-v0.3 §7 event came from a test that opted into authority.

    A separate function so T66 can exercise the check itself. A guard whose only evidence is
    that the suite stayed green is a guard nothing exercises, and this one is only ever *not*
    triggered.
    """
    leaked = sorted(set(appended) & AUTHORITY_EVENT_TYPES)
    if leaked and not declared:
        raise AssertionError(
            f"{nodeid} appended {', '.join(leaked)} without an `authority` marker. "
            "SPEC-v0.3 §4.1: a configuration with no `authority:` section behaves exactly as "
            "v0.2, which means none of the §7 event types exists in it. Mark the test "
            "`@pytest.mark.authority` if it really does build an `authority:` section."
        )


@pytest.fixture(autouse=True)
def authority_events_are_declared(request, monkeypatch):
    """Record every event either shipped store appends, and check T66's rule per test.

    Per test rather than at session finish, so the failure names the test that leaked and
    fails red where a reader is looking.
    """
    appended: list[str] = []

    for store_class in (InMemoryStateStore, SQLiteStateStore):
        original = store_class.append_event

        def recording(self, event, _original=original):
            appended.append(str(event.type))
            return _original(self, event)

        monkeypatch.setattr(store_class, "append_event", recording)

    yield appended

    check_authority_events_declared(
        appended,
        declared=request.node.get_closest_marker("authority") is not None,
        nodeid=request.node.nodeid,
    )


#: SPEC-v0.7 §8.9 (G12, T230). "No network" means **no connection except to a loopback
#: listener this process bound itself**, which is the rule `v0.4 §3.7` became when verify gained a
#: guarantee that needs a peer it controls. Everything else is refused: any other address, any
#: bind that is not `127.0.0.1`, every `AF_UNIX` bind and connect, IPv6, and `localhost`, which
#: is checked by the literal string inside `connect` because a C-level connect resolves a name
#: without calling the patched `getaddrinfo`. One definition, used by T107, T230, the examples and
#: the cookbook, so no copy can come to refuse less than another.
_NO_NETWORK_GUARD = '''\
"""Imported by `site` at startup: no connection except to a loopback listener bound here."""

import socket

_real = socket.socket
_real_create_connection = socket.create_connection
_real_getaddrinfo = socket.getaddrinfo
_LOOPBACK = "127.0.0.1"
#: Every (host, port) bound through the patched class, from getsockname() after the bind, so a
#: bind to port 0 is recorded at the port the kernel chose.
_bound = set()


def _refuse(what):
    raise RuntimeError(f"tried to {what}; this process runs with no network")


def _literal(address):
    """The one address admitted: a two-element tuple whose host is the string "127.0.0.1".

    Every `AF_UNIX` address is a path and every IPv6 address a four-element tuple or another
    string, so neither is ever this, and both are refused by this check alone. There is no
    separate family check: it would refuse exactly what this refuses, with the same message.
    """
    return (
        isinstance(address, tuple)
        and len(address) == 2
        and type(address[0]) is str
        and address[0] == _LOOPBACK
    )


def _admitted(address):
    return _literal(address) and (address[0], address[1]) in _bound


class _Guarded(_real):
    """A socket that binds only to 127.0.0.1 and connects only to what the process bound.

    Replacing the *type* with a function breaks anything that subclasses it, and `ssl` does, so
    the refusal goes on the operations instead.
    """

    def bind(self, address):
        if not _literal(address):
            _refuse(f"bind {address!r}")
        super().bind(address)
        host, port = self.getsockname()[:2]
        _bound.add((host, port))

    def connect(self, address):
        if not _admitted(address):
            _refuse(f"connect to {address!r}")
        return super().connect(address)

    def connect_ex(self, address):
        if not _admitted(address):
            _refuse(f"connect to {address!r}")
        return super().connect_ex(address)


def _create_connection(address, *args, **kwargs):
    if not _admitted(address):
        _refuse(f"connect to {address!r}")
    return _real_create_connection(address, *args, **kwargs)


def _getaddrinfo(host, *args, **kwargs):
    if type(host) is not str or host != _LOOPBACK:
        _refuse(f"resolve {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket = _Guarded
socket.create_connection = _create_connection
socket.getaddrinfo = _getaddrinfo
'''


@pytest.fixture(scope="session")
def no_network(tmp_path_factory):
    """A `PYTHONPATH` entry whose `sitecustomize` takes the network away (SPEC-v0.2 §1.1).

    Everything but a loopback listener the process bound itself (SPEC-v0.7 §8.9).
    """
    directory = tmp_path_factory.mktemp("no-network")
    (directory / "sitecustomize.py").write_text(_NO_NETWORK_GUARD, encoding="utf-8")
    return directory

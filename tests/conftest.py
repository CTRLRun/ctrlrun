"""Fixtures shared by tests that must hold for *every* StateStore.

`state_store` is parametrized over both shipped implementations. A reservation test written
once therefore runs against `InMemoryStateStore` and `SQLiteStateStore`, which is how the
double is kept from drifting into refusing less than the real store (SPEC-v0.1 §5.3).
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

"""Fixtures shared by tests that must hold for *every* StateStore.

`state_store` is parametrized over both shipped implementations. A reservation test written
once therefore runs against `InMemoryStateStore` and `SQLiteStateStore`, which is how the
double is kept from drifting into refusing less than the real store (CLAUDE.md working
style; SPEC-v0.1 §5.3).
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

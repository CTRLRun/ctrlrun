"""Clock-skew detection. Build-list item 1 of v0.7; SPEC-v0.7 §3, §8.1 T209-T219.

**Item 1 observes and reports. It changes no decision.** Every lease is decided against the
application clock exactly as at 0.6.1 (§3.2), and T213 is the test that says so with skew present.

The injection point is the *application* clock, through `clock=`, and Postgres keeps its own. That
is the honest direction: it is the one an operator's hosts get wrong. The tests that need a server
skip without `CTRLRUN_TEST_POSTGRES`; the arithmetic, the threshold's refusals, `Control`'s handling
of the optional attribute, the conformance case on the two shipped backends and G13's `N/A` all
run without one.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Action, Control, Policy, Principal, SQLiteStateStore
from ctrlrun.conformance.report import SuiteStatus
from ctrlrun.conformance.store import run as run_conformance
from ctrlrun.conformance.store.backends import InMemoryBackend, SQLiteBackend
from ctrlrun.effect import DEFAULT_LEASE, LEASE_EXPIRED, EffectState, plan_reservation
from ctrlrun.errors import AmbiguousEffect, DuplicateEffect, InvalidArgument
from ctrlrun.receipt import EventType
from ctrlrun.state import ClockSkew
from ctrlrun.verify import Status
from ctrlrun.verify import guarantees as reg
from ctrlrun.verify import run as run_verify

URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

ONE_SECOND = timedelta(seconds=1)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  refund:\n    decision: allow\n"

#: The conformance case's `not_applicable` reason, verbatim from §8 T214.
NO_CLOCK_REASON = (
    "this backend exposes no clock measurement; SQLite and the in-memory store read only the "
    "application's clock and have none to expose"
)

#: G13's `N/A` reason, verbatim from §8.9.
G13_NA_REASON = (
    "the store verify was given reads only the application's clock, so there is no second clock "
    "to diverge from; pass --store-url postgresql://… to grade this"
)


def an_action(payment_id: str = "p1") -> Action:
    return Action("refund", {"payment_id": payment_id, "amount": 1}, Principal("skew-agent"))


class Recording:
    """A sink that keeps what it was handed, so a test can compare it with the store."""

    def __init__(self) -> None:
        self.events: list = []
        self.receipts: list = []

    def on_event(self, event) -> None:
        self.events.append(event)

    def on_receipt(self, receipt) -> None:
        self.receipts.append(receipt)


def skew_events(events) -> list:
    return [event for event in events if event.type is EventType.CLOCK_SKEW_DETECTED]


class Shifted:
    """The host's real clock, shifted by a fixed offset. Moves, as a real clock does."""

    def __init__(self, offset: timedelta = timedelta(0)) -> None:
        self.offset = offset

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset


class Frozen:
    """A clock that moves only when a test moves it."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


# --- the measurement's arithmetic (§3.4), no server needed -----------------------------------


def _measured(before, server, after, threshold=ONE_SECOND, trigger="open"):
    from ctrlrun.postgres import _measurement

    return _measurement(before, server, after, threshold, trigger)


T = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def test_T209_the_arithmetic_names_an_application_clock_ahead():
    """§3.4: `skew = midpoint - s`, positive when the application is ahead."""
    measured = _measured(T + timedelta(seconds=6), T, T + timedelta(seconds=6, milliseconds=2))
    assert measured.skew == timedelta(seconds=6, milliseconds=1)
    assert measured.bound == timedelta(milliseconds=1)
    assert measured.threshold == ONE_SECOND
    assert measured.measured_at == T + timedelta(seconds=6, milliseconds=1)
    assert measured.trigger == "open"
    assert measured.exceeded


def test_T210_the_arithmetic_names_an_application_clock_behind():
    measured = _measured(T - timedelta(seconds=6), T, T - timedelta(seconds=6))
    assert measured.skew == -timedelta(seconds=6)
    assert measured.bound == timedelta(0)
    assert measured.exceeded


def test_T212_latency_alone_is_never_reported_by_the_arithmetic():
    """§3.4's rule, stated as arithmetic. A symmetric round trip of twice the threshold that
    agrees at the midpoint reports nothing, and neither does the worst case: the whole delay on
    one side of the server's read, which puts the midpoint as far off as it can get."""
    symmetric = _measured(T - ONE_SECOND, T, T + ONE_SECOND)
    assert symmetric.skew == timedelta(0)
    assert symmetric.bound == ONE_SECOND
    assert not symmetric.exceeded

    for before, after in ((T - 4 * ONE_SECOND, T), (T, T + 4 * ONE_SECOND)):
        lopsided = _measured(before, T, after)
        assert abs(lopsided.skew) == 2 * ONE_SECOND
        assert lopsided.bound == 2 * ONE_SECOND
        assert not lopsided.exceeded, (before, after)

    # And the precondition: the same round trip with skew added *is* reported, so the two
    # assertions above are not passing because nothing is ever reported.
    skewed = _measured(T + 2 * ONE_SECOND, T, T + 4 * ONE_SECOND)
    assert skewed.skew == 3 * ONE_SECOND
    assert skewed.bound == ONE_SECOND
    assert skewed.exceeded


def test_exceeded_is_strictly_past_threshold_plus_bound():
    """The boundary, both sides: exactly `threshold + bound` is not reported."""
    at_edge = ClockSkew(
        skew=timedelta(seconds=2),
        bound=ONE_SECOND,
        threshold=ONE_SECOND,
        measured_at=T,
        trigger="open",
    )
    assert not at_edge.exceeded
    past = ClockSkew(
        skew=-timedelta(seconds=2, microseconds=1),
        bound=ONE_SECOND,
        threshold=ONE_SECOND,
        measured_at=T,
        trigger="lease_expired",
    )
    assert past.exceeded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("skew", 7),
        ("bound", -timedelta(microseconds=1)),
        ("threshold", timedelta(0)),
        ("measured_at", datetime(2026, 9, 11, 12, 0)),
        ("trigger", "periodic"),
    ],
    ids=["skew-not-a-timedelta", "negative-bound", "zero-threshold", "naive-time", "trigger"],
)
def test_a_malformed_measurement_fails_where_it_is_built(field, value):
    """A third-party store that builds a bad `ClockSkew` finds out at construction, not inside
    the `Control` that would report it."""
    fields = {
        "skew": ONE_SECOND,
        "bound": timedelta(0),
        "threshold": ONE_SECOND,
        "measured_at": T,
        "trigger": "open",
    }
    ClockSkew(**fields)  # the precondition: the unaltered fields are accepted
    with pytest.raises(InvalidArgument):
        ClockSkew(**{**fields, field: value})


# --- T218: the threshold refuses what it must --------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        timedelta(0),
        -ONE_SECOND,
        DEFAULT_LEASE + timedelta(microseconds=1),
        None,
        True,
        1,
        1.0,
        "1s",
    ],
    ids=["zero", "negative", "above-lease", "none", "bool", "int", "float", "string"],
)
def test_T218_the_threshold_refuses_what_it_must(value):
    """Refused at construction, before any connection is attempted: the URL below names no
    server, so a store that tried to connect first would raise something else."""
    from ctrlrun.postgres import PostgresStateStore

    with pytest.raises(InvalidArgument) as refused:
        PostgresStateStore("postgresql://nobody@127.0.0.1:1/none", clock_skew_threshold=value)
    assert "clock_skew_threshold" in str(refused.value)


def test_T218_the_default_threshold_is_one_second():
    from ctrlrun.postgres import DEFAULT_CLOCK_SKEW_THRESHOLD

    assert DEFAULT_CLOCK_SKEW_THRESHOLD == ONE_SECOND


# --- T214: the conformance case on the two shipped backends, and its fixtures ------------------


def _skew_case(report):
    suite = next(s for s in report.suites if s.name == "clock")
    return next(c for c in suite.cases if c.id == "skew-measured")


@pytest.mark.parametrize("backend", [SQLiteBackend, InMemoryBackend], ids=["sqlite", "memory"])
def test_T214_a_backend_with_no_clock_is_not_applicable_with_its_sentence(backend, tmp_path):
    report = run_conformance(backend(tmp_path), only=("skew-measured",))
    case = _skew_case(report)
    assert case.status is SuiteStatus.NOT_APPLICABLE, report.to_text()
    assert case.reason == NO_CLOCK_REASON


@pytest.mark.parametrize(
    ("fixture", "fragment"),
    [
        ("skew-look-alike", "not a ctrlrun.state.ClockSkew"),
        ("skew-read-raises", "reading clock_skew raised"),
        ("skew-never-reported", "was not reported"),
        ("skew-always-reported", "aligned with the store's was reported"),
    ],
)
def test_T214_a_broken_measurement_fails_the_case_by_name(fixture, fragment, tmp_path):
    """§8 T214: a present attribute that is not a `ClockSkew`, or whose read raises, fails, and
    `not_applicable` is reserved for an absent one. The two grading halves have a fixture each,
    so neither is a guard the other subsumes."""
    from ctrlrun.conformance.store.fixtures import FIXTURES

    chosen = next(f for f in FIXTURES if f.name == fixture)
    report = run_conformance(chosen.backend(tmp_path), only=("skew-measured",))
    case = _skew_case(report)
    assert case.status is SuiteStatus.FAIL, report.to_text()
    assert fragment in (case.reason or ""), case.reason


# --- T216 and T217 from Control's side, no server needed ---------------------------------------


EXCEEDED = ClockSkew(
    skew=timedelta(seconds=7, microseconds=5),
    bound=timedelta(microseconds=250),
    threshold=ONE_SECOND,
    measured_at=T,
    trigger="open",
)


class _ExposesSkew(SQLiteStateStore):
    """A real SQLite store that exposes a measurement it was handed, as a store with a clock
    would. Everything it decides is SQLite's own."""

    measurement: object = None

    @property
    def clock_skew(self):
        return self.measurement


@dataclass(frozen=True)
class ClockSkewLookAlike:
    skew: timedelta
    bound: timedelta
    threshold: timedelta
    measured_at: datetime
    trigger: str

    @property
    def exceeded(self) -> bool:
        return True


class _RaisesOnRead(SQLiteStateStore):
    @property
    def clock_skew(self):
        raise RuntimeError("the measurement is unavailable")


def _run_ten(store, clock) -> tuple[list, list, list]:
    """Ten actions through one Control: their outcomes, receipts' shapes and event types."""
    recording = Recording()
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock, sinks=[recording])
    outcomes = []
    for index in range(10):
        receipt = control.execute(an_action(f"p{index}"), lambda: "ok", f"refund:p{index}")
        outcomes.append((str(receipt.result), receipt.decision_reason, receipt.attempt))
    types = [str(event.type) for event in store.events()]
    return outcomes, recording.receipts, types


@pytest.mark.parametrize(
    "value",
    [
        "7s ahead",
        timedelta(seconds=7),
        ClockSkewLookAlike(timedelta(seconds=7), timedelta(0), ONE_SECOND, T, "open"),
    ],
    ids=["string", "timedelta", "look-alike"],
)
def test_T216_a_value_that_is_not_a_clock_skew_changes_nothing(value, tmp_path, caplog):
    clock = Frozen(T)
    baseline_store = SQLiteStateStore(tmp_path / "baseline.db", clock=clock)
    expected, expected_receipts, expected_types = _run_ten(baseline_store, clock)

    store = _ExposesSkew(tmp_path / "exposed.db", clock=clock)
    store.measurement = value
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        got, receipts, types = _run_ten(store, clock)

    assert got == expected
    assert types == expected_types
    assert [r.result for r in receipts] == [r.result for r in expected_receipts]
    assert EventType.CLOCK_SKEW_DETECTED.value not in types
    warned = [r for r in caplog.records if "clock_skew" in r.getMessage()]
    assert len(warned) == 1, [r.getMessage() for r in warned]
    assert warned[0].levelno == logging.WARNING
    assert "ClockSkew" in warned[0].getMessage()


def test_T216_a_clock_skew_read_that_raises_changes_nothing(tmp_path, caplog):
    clock = Frozen(T)
    expected, _, expected_types = _run_ten(SQLiteStateStore(tmp_path / "b.db", clock=clock), clock)
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        got, _, types = _run_ten(_RaisesOnRead(tmp_path / "r.db", clock=clock), clock)
    assert got == expected
    assert types == expected_types
    warned = [r for r in caplog.records if "clock_skew" in r.getMessage()]
    assert len(warned) == 1, [r.getMessage() for r in warned]
    assert "RuntimeError" in warned[0].getMessage()


def test_T216_each_kind_of_error_is_logged_once_per_store(tmp_path, caplog):
    """Two kinds on one store are two lines; the same kind again is none."""
    clock = Frozen(T)
    store = _ExposesSkew(tmp_path / "kinds.db", clock=clock)
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock)
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        store.measurement = "not a measurement"
        for index in range(3):
            control.execute(an_action(f"a{index}"), lambda: "ok")
        store.measurement = ClockSkewLookAlike(
            timedelta(seconds=7), timedelta(0), ONE_SECOND, T, ""
        )
        for index in range(3):
            control.execute(an_action(f"b{index}"), lambda: "ok")
    warned = [r for r in caplog.records if "clock_skew" in r.getMessage()]
    assert len(warned) == 1, "a wrong type is one kind of error, whatever the type"

    raising = _RaisesOnRead(tmp_path / "raising.db", clock=clock)
    other = Control(Policy.from_yaml(ALLOW), raising, clock=clock)
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        for index in range(3):
            other.execute(an_action(f"c{index}"), lambda: "ok")
    warned = [r for r in caplog.records if "clock_skew" in r.getMessage()]
    assert len(warned) == 2


def test_T217_the_event_reaches_every_sink_with_the_store_assigned_id(tmp_path):
    clock = Frozen(T)
    store = _ExposesSkew(tmp_path / "state.db", clock=clock)
    store.measurement = EXCEEDED
    first, second = Recording(), Recording()
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock, sinks=[first, second])

    control.execute(an_action("p1"), lambda: "ok", "refund:p1")

    stored = skew_events(store.events())
    assert len(stored) == 1
    event = stored[0]
    assert event.event_id is not None
    for sink in (first, second):
        handed = skew_events(sink.events)
        assert handed == [event], "every sink gets the stored event, with the store's id"
    assert event.action_id is None and event.effect_key is None
    assert store.events()[0] == event, "the at-open report precedes the first action"
    assert store.events()[1].type is EventType.ACTION_PROPOSED
    assert event.data == {
        "skew_us": 7_000_005,
        "bound_us": 250,
        "threshold_us": 1_000_000,
        "direction": "ahead",
        "trigger": "open",
        "measured_at": "2026-09-11T12:00:00.000Z",
    }

    control.execute(an_action("p2"), lambda: "ok", "refund:p2")
    assert len(skew_events(store.events())) == 1, "the same measurement is reported once"

    # And a different measurement is reported again: the rule is "not the one it last reported",
    # not "once per Control".
    store.measurement = ClockSkew(
        skew=-timedelta(seconds=3),
        bound=timedelta(0),
        threshold=ONE_SECOND,
        measured_at=T + timedelta(minutes=1),
        trigger="open",
    )
    control.execute(an_action("p3"), lambda: "ok", "refund:p3")
    reported = skew_events(store.events())
    assert len(reported) == 2
    assert reported[1].data["direction"] == "behind"
    assert reported[1].data["skew_us"] == -3_000_000


def test_T217_a_measurement_within_its_bound_is_never_appended(tmp_path):
    """The precondition for T211's silence: `Control` appends only an `exceeded` measurement."""
    clock = Frozen(T)
    store = _ExposesSkew(tmp_path / "state.db", clock=clock)
    store.measurement = ClockSkew(
        skew=timedelta(milliseconds=900),
        bound=timedelta(0),
        threshold=ONE_SECOND,
        measured_at=T,
        trigger="open",
    )
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock)
    control.execute(an_action(), lambda: "ok")
    assert skew_events(store.events()) == []


def test_T217_a_store_without_the_attribute_reports_nothing(tmp_path, caplog):
    clock = Frozen(T)
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        Control(Policy.from_yaml(ALLOW), store, clock=clock).execute(an_action(), lambda: "ok")
    assert skew_events(store.events()) == []
    assert not [r for r in caplog.records if "clock_skew" in r.getMessage()]


def test_T217_resume_reports_at_its_start(tmp_path):
    """§3.6: at the start of every `execute` **and** `resume`."""
    from ctrlrun import Suspended

    clock = Frozen(T)
    store = _ExposesSkew(tmp_path / "state.db", clock=clock)
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock)

    def suspend():
        raise Suspended("skew-continuation")

    with pytest.raises(Suspended):
        control.execute(an_action(), suspend, "refund:suspended")
    assert skew_events(store.events()) == []
    store.measurement = EXCEEDED
    control.resume("skew-continuation", lambda: "ok")
    types = [event.type for event in store.events()]
    assert EventType.CLOCK_SKEW_DETECTED in types
    assert types.index(EventType.CLOCK_SKEW_DETECTED) < types.index(EventType.EXECUTION_RESUMED)


class _MeasuresOnRefusal(_ExposesSkew):
    """A SQLite store that, like Postgres on `E3`'s path, retains a new measurement when a
    reservation meets an expired lease. Only `Control`'s wiring is under test here; the
    store-side re-measurement is T215's, against a real server."""

    def reserve_effect(self, effect_key, action_id, lease=DEFAULT_LEASE):
        try:
            return super().reserve_effect(effect_key, action_id, lease)
        except AmbiguousEffect:
            self.measurement = ClockSkew(
                skew=timedelta(seconds=9),
                bound=timedelta(0),
                threshold=ONE_SECOND,
                measured_at=self._clock(),
                trigger="lease_expired",
            )
            raise


@pytest.mark.parametrize("mode", ["enforce", "observe"])
def test_T215_the_report_beside_an_expired_lease_names_the_attempt(mode, tmp_path):
    """§3.6: `Control` pulls again immediately after a reservation is refused with
    `AmbiguousEffect`, in enforce mode and in observe mode, which reserves too."""
    clock = Frozen(T)
    store = _MeasuresOnRefusal(tmp_path / "state.db", clock=clock)
    document = (
        ALLOW
        if mode == "enforce"
        else "schema: ctrlrun.policy/v3\nmode: observe\nactions:\n  refund:\n    decision: allow\n"
    )
    control = Control(Policy.from_yaml(document), store, clock=clock)
    store.reserve_effect("refund:lapsed", "act_holder", timedelta(seconds=1))
    store.begin_execution("refund:lapsed", "act_holder")
    clock.advance(timedelta(seconds=2))

    attempt = an_action("late")
    if mode == "enforce":
        with pytest.raises(AmbiguousEffect):
            control.execute(attempt, lambda: "ok", "refund:lapsed")
    else:
        control.execute(attempt, lambda: "ok", "refund:lapsed")
    reported = skew_events(store.events())
    assert len(reported) == 1, [e.type for e in store.events()]
    assert reported[0].action_id == attempt.action_id
    assert reported[0].effect_key == "refund:lapsed"
    assert reported[0].data["trigger"] == "lease_expired"
    types = [e.type for e in store.events() if e.action_id == attempt.action_id]
    assert types.index(EventType.CLOCK_SKEW_DETECTED) < types.index(
        EventType.EFFECT_RESERVATION_REFUSED
    ), "the report sits beside the refusal, before it"


class _HidesBehindAttributeError(SQLiteStateStore):
    """Exposes the attribute, and reading it raises `AttributeError`, which `getattr` with a
    default would take for an absent attribute."""

    @property
    def clock_skew(self):
        raise AttributeError("the measurement was never set up")


class _HidingBackend(SQLiteBackend):
    name = "hides-behind-attribute-error"

    def open(self):
        store = _HidesBehindAttributeError(self._path)
        self._open.append(store)
        return store

    def open_with_clock(self, clock):
        store = _HidesBehindAttributeError(self._path, clock=clock)
        self._open.append(store)
        return store


def test_T214_a_present_attribute_whose_read_raises_attribute_error_is_not_absent(tmp_path):
    """`not_applicable` is reserved for an absent attribute. A property that raises
    `AttributeError` is present, and `Control` would silently ignore it, so it fails by name."""
    report = run_conformance(_HidingBackend(tmp_path), only=("skew-measured",))
    case = _skew_case(report)
    assert case.status is SuiteStatus.FAIL, report.to_text()
    assert "reading clock_skew raised AttributeError" in (case.reason or "")


# --- T219: G13's N/A, no server needed -------------------------------------------------------


def test_T219_G13_is_not_applicable_on_sqlite_with_its_sentence(tmp_path):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(ALLOW, encoding="utf-8")
    report = run_verify(path, only=("G13",))
    result = next(r for r in report.guarantees if r.id == "G13")
    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == G13_NA_REASON == reg.STORE_READS_APPLICATION_CLOCK


def test_T219_the_catalogue_is_v3_and_G13_is_in_it():
    assert reg.CATALOGUE == "ctrlrun.guarantees/v3"
    assert "G13" in reg.BY_ID
    assert "v0.1 §5.3 E3" in reg.BY_ID["G13"].descends_from


# ============================================================================================
# Against a real server
# ============================================================================================


@pytest.fixture
def schema():
    """A schema of this test's own, dropped afterwards. Stores opened in it are closed first."""
    from ctrlrun.postgres import PostgresStateStore

    assert URL
    name = f"skew_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(URL, name)
    opened: list = []
    yield name, opened
    for store in opened:
        store.close()
    PostgresStateStore.drop_schema(URL, name)


def _open(schema, clock, *, cls=None, **options):
    from ctrlrun.postgres import PostgresStateStore

    name, opened = schema
    store = (cls or PostgresStateStore)(URL, schema=name, clock=clock, **options)
    opened.append(store)
    return store


def _timed_open(schema, clock, **options):
    started = time.monotonic()
    store = _open(schema, clock, **options)
    return store, timedelta(seconds=time.monotonic() - started)


#: The server runs on this host (T211's premise), so its clock and the host's agree to well
#: within this. It absorbs scheduling noise, never the injected offset.
HOST_TOLERANCE = timedelta(milliseconds=500)


@postgres
@pytest.mark.parametrize(("direction", "sign"), [("ahead", 1), ("behind", -1)])
def test_T209_T210_an_injected_skew_is_named_with_its_bound(schema, direction, sign):
    from ctrlrun.postgres import DEFAULT_CLOCK_SKEW_THRESHOLD

    injected = sign * (DEFAULT_CLOCK_SKEW_THRESHOLD + timedelta(seconds=5))
    clock = Shifted(injected)
    store, round_trip = _timed_open(schema, clock)

    measured = store.clock_skew
    assert isinstance(measured, ClockSkew)
    assert measured.exceeded
    assert (measured.skew > timedelta(0)) is (sign > 0)
    assert measured.bound <= round_trip / 2, "the bound is at most half the round trip observed"
    assert abs(measured.skew - injected) <= measured.bound + HOST_TOLERANCE
    assert measured.trigger == "open"
    assert measured.threshold == DEFAULT_CLOCK_SKEW_THRESHOLD

    recording = Recording()
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock, sinks=[recording])
    control.execute(an_action(), lambda: "ok", "refund:p1")

    events = store.events()
    assert events[0].type is EventType.CLOCK_SKEW_DETECTED
    assert events[1].type is EventType.ACTION_PROPOSED
    data = events[0].data
    assert data["direction"] == direction
    assert data["trigger"] == "open"
    assert events[0].action_id is None
    for key in ("skew_us", "bound_us", "threshold_us"):
        assert isinstance(data[key], int) and not isinstance(data[key], bool), key
    assert data["skew_us"] == measured.skew // timedelta(microseconds=1)
    assert data["bound_us"] == measured.bound // timedelta(microseconds=1)
    assert data["threshold_us"] == 1_000_000
    assert (data["skew_us"] > 0) is (sign > 0)
    assert abs(data["skew_us"]) > data["threshold_us"] + data["bound_us"]
    assert skew_events(recording.events) == [events[0]]


@postgres
def test_T211_the_real_clock_is_measured_and_silent(schema):
    """The positive control. Both halves, or the second proves nothing."""
    store = _open(schema, Shifted())
    measured = store.clock_skew
    assert measured is not None, "the detector ran: a measurement is present"
    assert not measured.exceeded, measured
    recording = Recording()
    control = Control(Policy.from_yaml(ALLOW), store, clock=Shifted(), sinks=[recording])
    control.execute(an_action(), lambda: "ok", "refund:p1")
    assert skew_events(store.events()) == []
    assert skew_events(recording.events) == []


def _latency_store(schema, before: timedelta, after: timedelta):
    """A store whose application clock reads `before` off the host's clock until the server has
    read its own, and `after` from then on: a round trip of `after - before` with no skew."""
    from ctrlrun.postgres import PostgresStateStore

    clock = Shifted(before)

    class _Latent(PostgresStateStore):
        def _read_server_clock(self, connection):
            read = super()._read_server_clock(connection)
            clock.offset = after
            return read

    return _open(schema, clock, cls=_Latent)


@postgres
def test_T212_latency_alone_is_never_reported(schema):
    from ctrlrun.postgres import DEFAULT_CLOCK_SKEW_THRESHOLD as THRESHOLD

    store = _latency_store(schema, -THRESHOLD, THRESHOLD)
    measured = store.clock_skew
    assert measured is not None
    assert not measured.exceeded, measured
    assert THRESHOLD <= measured.bound <= THRESHOLD + HOST_TOLERANCE

    lopsided = _latency_store(schema, -4 * THRESHOLD, timedelta(0))
    assert lopsided.clock_skew is not None
    assert not lopsided.clock_skew.exceeded, lopsided.clock_skew

    # The precondition: the same injected round trip with skew on top IS reported, so the
    # silence above is not a detector that the injection switched off.
    skewed = _latency_store(schema, 2 * THRESHOLD, 4 * THRESHOLD)
    assert skewed.clock_skew is not None and skewed.clock_skew.exceeded


def _decide_on_both(store, sqlite_store, clock, scenario):
    """Drive the same reservations against both stores and return what each decided."""
    decided = []
    for key, action_id, lease in scenario:
        before = sqlite_store.get_effect(key)
        expected = plan_reservation(before, key, action_id, lease, clock())
        outcomes = []
        for target in (store, sqlite_store):
            try:
                reservation = target.reserve_effect(key, action_id, lease)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                outcomes.append((type(refused).__name__, getattr(refused, "state", None)))
            else:
                outcomes.append(("reserved", reservation.attempt, reservation.lease_expires_at))
        want = (
            ("reserved", expected.reservation.attempt, expected.reservation.lease_expires_at)
            if expected.reservation is not None
            else (type(expected.refusal).__name__, getattr(expected.refusal, "state", None))
        )
        decided.append((key, want, outcomes[0], outcomes[1]))
    return decided


@postgres
def test_T213_lease_evaluation_is_byte_for_byte_unchanged(schema, tmp_path):
    """With skew present and reported, every lease is decided by the application clock.

    The expected values come from `plan_reservation` with the application clock, and the records
    afterwards are compared field by field with a SQLite store driven through the same steps,
    which is 0.6.1's behaviour on a backend with no second clock.
    """
    clock = Frozen(datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
    store = _open(schema, clock)
    sqlite_store = SQLiteStateStore(tmp_path / "oracle.db", clock=clock)
    assert store.clock_skew is not None and store.clock_skew.exceeded, "skew is present"
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock)
    control.execute(an_action("warmup"), lambda: "ok", "refund:warmup")
    assert skew_events(store.events()), "and reported"

    for target in (store, sqlite_store):
        target.reserve_effect("refund:live", "act_live", timedelta(minutes=10))
        target.begin_execution("refund:live", "act_live")
        target.reserve_effect("refund:lapsing", "act_lapsing", timedelta(seconds=30))
        target.begin_execution("refund:lapsing", "act_lapsing")
        target.reserve_effect("refund:done", "act_done", DEFAULT_LEASE)
        target.begin_execution("refund:done", "act_done")
        target.commit_effect("refund:done", "act_done", {"ok": True})
        target.reserve_effect("refund:failed", "act_failed", DEFAULT_LEASE)
        target.begin_execution("refund:failed", "act_failed")
        target.fail_effect("refund:failed", "act_failed", "declined")
    clock.advance(timedelta(minutes=1))

    decided = _decide_on_both(
        store,
        sqlite_store,
        clock,
        [
            ("refund:live", "act_2", DEFAULT_LEASE),
            ("refund:lapsing", "act_3", DEFAULT_LEASE),
            ("refund:lapsing", "act_4", DEFAULT_LEASE),
            ("refund:done", "act_5", DEFAULT_LEASE),
            ("refund:failed", "act_6", DEFAULT_LEASE),
            ("refund:new", "act_7", DEFAULT_LEASE),
        ],
    )
    for key, want, on_postgres, on_sqlite in decided:
        assert on_postgres == want, (key, on_postgres, want)
        assert on_sqlite == want, (key, on_sqlite, want)
    assert [d[1][0] for d in decided] == [
        "DuplicateEffect",
        "AmbiguousEffect",
        "AmbiguousEffect",
        "DuplicateEffect",
        "reserved",
        "reserved",
    ], "the scenario covers a live lease, an expired one, a record left ambiguous, a commit"

    for key in ("refund:live", "refund:lapsing", "refund:done", "refund:failed", "refund:new"):
        mine, oracle = store.get_effect(key), sqlite_store.get_effect(key)
        assert mine == oracle, (key, mine, oracle)
    lapsed = store.get_effect("refund:lapsing")
    assert lapsed is not None and lapsed.state is EffectState.AMBIGUOUS
    assert lapsed.error == LEASE_EXPIRED
    sqlite_store.close()


class _Counted:
    """Counts the store's server-clock reads by wrapping the one method that makes them."""

    def __init__(self, monkeypatch) -> None:
        from ctrlrun.postgres import PostgresStateStore

        self.reads = 0
        original = PostgresStateStore._read_server_clock

        def counting(store, connection):
            self.reads += 1
            return original(store, connection)

        monkeypatch.setattr(PostgresStateStore, "_read_server_clock", counting)


def _lapse(store, clock, key: str) -> None:
    store.reserve_effect(key, f"act_{key}", timedelta(seconds=1))
    store.begin_execution(key, f"act_{key}")
    clock.advance(timedelta(seconds=2))


@postgres
def test_T215_when_it_measures_and_when_it_does_not(schema, monkeypatch):
    counted = _Counted(monkeypatch)
    clock = Frozen(datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
    store = _open(schema, clock)
    assert counted.reads == 1, "one at open"
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock)

    # Granted, then a live lease, then a committed record: none measures.
    control.execute(an_action("g"), lambda: "ok", "refund:granted")
    store.reserve_effect("refund:held", "act_held", timedelta(hours=1))
    with pytest.raises(DuplicateEffect):
        control.execute(an_action("h"), lambda: "ok", "refund:held")
    with pytest.raises(DuplicateEffect):
        control.execute(an_action("g2"), lambda: "ok", "refund:granted")
    assert counted.reads == 1

    # A renewal over FAILED measures nothing either.
    store.reserve_effect("refund:renewed", "act_r1", DEFAULT_LEASE)
    store.begin_execution("refund:renewed", "act_r1")
    store.fail_effect("refund:renewed", "act_r1", "declined")
    control.execute(an_action("r"), lambda: "ok", "refund:renewed")
    assert counted.reads == 1

    # An expired lease declared AMBIGUOUS: one read, and the report names this attempt.
    _lapse(store, clock, "refund:lapsed")
    lapsed_action = an_action("l")
    with pytest.raises(AmbiguousEffect):
        control.execute(lapsed_action, lambda: "ok", "refund:lapsed")
    assert counted.reads == 2
    reported = skew_events(store.events())
    assert [e.data["trigger"] for e in reported] == ["open", "lease_expired"]
    assert reported[1].action_id == lapsed_action.action_id
    assert reported[1].effect_key == "refund:lapsed"

    # An ambiguous record refuses without measuring.
    with pytest.raises(AmbiguousEffect):
        control.execute(an_action("l2"), lambda: "ok", "refund:lapsed")
    assert counted.reads == 2

    # A second expired lease within DEFAULT_LEASE of the first re-measures nothing.
    _lapse(store, clock, "refund:lapsed-again")
    with pytest.raises(AmbiguousEffect):
        control.execute(an_action("l3"), lambda: "ok", "refund:lapsed-again")
    assert counted.reads == 2
    assert len(skew_events(store.events())) == 2

    # And the interval is a window, not a one-shot: past it, an expired lease measures again.
    clock.advance(DEFAULT_LEASE)
    _lapse(store, clock, "refund:lapsed-later")
    with pytest.raises(AmbiguousEffect):
        control.execute(an_action("l4"), lambda: "ok", "refund:lapsed-later")
    assert counted.reads == 3


@postgres
def test_T216_a_failed_measurement_at_open_changes_nothing(schema, monkeypatch, caplog):
    from ctrlrun.postgres import PostgresStateStore

    def broken(store, connection):
        raise RuntimeError("clock_timestamp() is unavailable")

    monkeypatch.setattr(PostgresStateStore, "_read_server_clock", broken)
    with caplog.at_level(logging.WARNING, logger="ctrlrun.postgres"):
        store = _open(schema, Shifted(timedelta(hours=1)))
    assert store.clock_skew is None
    named = [r for r in caplog.records if "clock_timestamp() is unavailable" in r.getMessage()]
    assert named and named[0].name == "ctrlrun.postgres"
    # The store works: it opened, and it reserves.
    assert store.reserve_effect("refund:after", "act_after").attempt == 1


@postgres
def test_T216_a_failed_measurement_on_the_E3_path_changes_nothing(
    schema, monkeypatch, caplog, tmp_path
):
    from ctrlrun.postgres import PostgresStateStore

    clock = Frozen(datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
    store = _open(schema, clock)
    oracle = SQLiteStateStore(tmp_path / "oracle.db", clock=clock)
    retained = store.clock_skew
    assert retained is not None

    def broken(self, connection):
        raise RuntimeError("clock_timestamp() is unavailable")

    monkeypatch.setattr(PostgresStateStore, "_read_server_clock", broken)
    for target in (store, oracle):
        target.reserve_effect("refund:lapsed", "act_a", timedelta(seconds=1))
        target.begin_execution("refund:lapsed", "act_a")
    clock.advance(timedelta(seconds=2))

    raised = []
    with caplog.at_level(logging.WARNING, logger="ctrlrun.postgres"):
        for target in (store, oracle):
            with pytest.raises(AmbiguousEffect) as refused:
                target.reserve_effect("refund:lapsed", "act_b")
            raised.append((type(refused.value), str(refused.value), refused.value.action_id))
    assert raised[0] == raised[1]
    assert store.get_effect("refund:lapsed") == oracle.get_effect("refund:lapsed")
    assert store.clock_skew is retained, "the retained measurement is left as it was"
    assert [r for r in caplog.records if "clock_timestamp() is unavailable" in r.getMessage()]
    oracle.close()


@postgres
def test_T217_through_control_against_postgres(schema):
    clock = Shifted(timedelta(seconds=30))
    store = _open(schema, clock)
    recording = Recording()
    control = Control(Policy.from_yaml(ALLOW), store, clock=clock, sinks=[recording])
    control.execute(an_action("p1"), lambda: "ok", "refund:p1")
    control.execute(an_action("p2"), lambda: "ok", "refund:p2")
    stored = skew_events(store.events())
    assert len(stored) == 1, "a second execute against the same measurement appends nothing"
    assert stored[0].event_id is not None
    assert skew_events(recording.events) == stored


@postgres
def test_T217_an_event_about_no_action_reads_back_as_none_on_postgres(schema):
    """The store half of T217: `events()` holds the event the sink was handed. On Postgres a
    NULL `action_id` read back as the string "None" until this item; the same event on SQLite
    and in memory reads back as `None`, which is what `Event` documents."""
    from ctrlrun.receipt import Event

    store = _open(schema, Shifted())
    written = store.append_event(
        Event(type=EventType.DELEGATION_REVOKED, action_id=None, ts=T, data={"delegation_id": "d"})
    )
    (read,) = store.events()
    assert read.action_id is None
    assert read == written


@postgres
@pytest.mark.parametrize(
    "threshold", [timedelta(microseconds=1), DEFAULT_LEASE], ids=["smallest", "largest"]
)
def test_T218_no_accepted_value_stops_the_measurement(schema, threshold):
    store = _open(schema, Shifted(), clock_skew_threshold=threshold)
    assert store.clock_skew is not None
    assert store.clock_skew.threshold == threshold


@postgres
def test_T214_postgres_passes_the_skew_case(tmp_path):
    from ctrlrun.conformance.store.backends import PostgresBackend

    backend = PostgresBackend(URL)
    try:
        report = run_conformance(backend, only=("skew-measured",))
    finally:
        backend.reset()
    case = _skew_case(report)
    assert case.status is SuiteStatus.PASS, report.to_text()


# --- T219: G13 graded against Postgres, and its control both ways ------------------------------


def _g13(tmp_path):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(ALLOW, encoding="utf-8")
    report = run_verify(path, only=("G13",), store_url=URL)
    return next(r for r in report.guarantees if r.id == "G13")


@postgres
def test_T219_G13_passes_against_postgres(tmp_path):
    result = _g13(tmp_path)
    assert result.status is Status.PASS, (result.reason, result.counterexample)


@postgres
def test_T219_a_detector_that_always_fires_fails_G13(tmp_path, monkeypatch):
    monkeypatch.setattr(ClockSkew, "exceeded", property(lambda self: True))
    result = _g13(tmp_path)
    assert result.status is Status.FAIL
    assert result.reason == reg.CONTROL_FAILED


@postgres
def test_T219_a_detector_that_never_fires_fails_G13(tmp_path, monkeypatch):
    monkeypatch.setattr(ClockSkew, "exceeded", property(lambda self: False))
    result = _g13(tmp_path)
    assert result.status is Status.FAIL
    assert result.reason != reg.CONTROL_FAILED, "the observable fails, not the control"


@postgres
def test_T219_a_detector_that_never_runs_fails_G13s_control(tmp_path, monkeypatch):
    from ctrlrun.postgres import PostgresStateStore

    def broken(store, connection):
        raise RuntimeError("no clock")

    monkeypatch.setattr(PostgresStateStore, "_read_server_clock", broken)
    result = _g13(tmp_path)
    assert result.status is Status.FAIL
    assert result.reason == reg.CONTROL_FAILED

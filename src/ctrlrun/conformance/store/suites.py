"""The store suites. SPEC-v0.6 §2.3, §2.4, §2.5.

Every case is a statement about a `StateStore` method rather than about `Control`'s composition
of them. The suite predates the backend it grades: a Postgres store measured against a suite
written for Postgres has marked its own homework, which is why item 1 comes before item 3.

Two rules every case obeys, both `v0.4 §1.3`'s positive-control rule one layer down:

- **It names the exception and, where the kernel defines one, its `state` or `reason`.** A guard
  distinguishable from a later guard only by reading the source is documentation.
- **It reads the record back.** "The attempt was refused" is satisfied just as well by a store
  that refuses everything; what a case asserts is the refusal *and* what the record then says.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from ...action import Action, Principal
from ...approval import ApprovalStatus, build_request
from ...effect import COMMITTED_EFFECT, IN_PROGRESS_EFFECT, EffectState
from ...errors import (
    AmbiguousEffect,
    ApprovalMismatch,
    DuplicateEffect,
    InvalidArgument,
    NotExecuted,
)
from ...policy import Decision
from ...receipt import Event, EventType, Receipt, ReceiptResult
from ...state import DelegationRecord, StateStore
from ..report import CaseResult, SuiteStatus
from .backends import StoreBackend, store_from_url

#: A fixed instant, so a case that turns on expiry is exact rather than nearly exact. Every
#: clock the suite injects is one only the suite moves: `v0.4 §3.6` -- no sleeps, anywhere.
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)

#: `v0.1 §7` T3's standard, and the suite's own bound. Every loop here is bounded so a broken
#: check fails red rather than hanging CI.
CONTENDERS = 8

#: How many times `e1-in-process` repeats its contention.
#:
#: One round catches a store with **no** check every time, and a *check-then-act* store -- read
#: the record, then insert without a lock, which is the realistic bug -- only about 40% of the
#: time. A review measured it. One round would therefore have made `reservation` a flaky grade
#: for the store shape it most needs to catch, and CI would eventually have seen a red that was
#: not a regression. Twelve rounds puts a 40%-per-round miss below one in a hundred thousand,
#: and the case still runs in well under a second.
ROUNDS = 12


# --- case plumbing --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    body: Callable[[StoreBackend, int], CaseResult]


def case(identifier: str, title: str) -> Callable[..., Case]:
    def wrap(body: Callable[..., CaseResult]) -> Case:
        return Case(identifier, title, body)

    return wrap


def passed(case_id: str, title: str) -> CaseResult:
    return CaseResult(case_id, title, SuiteStatus.PASS)


def failed(case_id: str, title: str, reason: str, **detail: Any) -> CaseResult:
    return CaseResult(case_id, title, SuiteStatus.FAIL, reason, detail)


def na(case_id: str, title: str, reason: str) -> CaseResult:
    return CaseResult(case_id, title, SuiteStatus.NOT_APPLICABLE, reason)


def expect(
    case_id: str,
    title: str,
    call: Callable[[], Any],
    error: type[BaseException] | tuple[type[BaseException], ...],
    **attributes: Any,
) -> CaseResult | None:
    """Run `call`, requiring it to raise `error` with the given attribute values.

    Returns `None` when it did and the failing `CaseResult` when it did not, so a case body
    reads as a sequence of requirements rather than as nested try/except.
    """
    try:
        call()
    except error as raised:
        for name, want in attributes.items():
            got = getattr(raised, name, None)
            if str(got) != str(want):
                return failed(
                    case_id,
                    title,
                    f"raised {type(raised).__name__} with {name}={got!r}, expected {want!r}",
                )
        return None
    except BaseException as other:
        return failed(
            case_id,
            title,
            f"raised {type(other).__name__}: {other}; expected {_named(error)}",
        )
    return failed(case_id, title, f"did not raise {_named(error)}")


def _named(error: Any) -> str:
    if isinstance(error, tuple):
        return " or ".join(item.__name__ for item in error)
    return str(error.__name__)


def _differing_field(wrote: Any, read: Any) -> tuple[str, Any, Any] | None:
    """The first field on which two records of one dataclass differ, or `None`.

    A shallow walk rather than `dataclasses.asdict`, which deep-copies and cannot handle the
    read-only mappings `Action` freezes its arguments into (`v0.1 §2.2`).
    """
    for spec in fields(wrote):
        mine, theirs = getattr(wrote, spec.name), getattr(read, spec.name, None)
        if mine != theirs:
            return spec.name, mine, theirs
    return None


def an_action(payment_id: str = "txn_1", amount: int = 2000, **extra: Any) -> Action:
    arguments: dict[str, Any] = {"payment_id": payment_id, "amount": amount}
    arguments.update(extra)
    return Action(
        name="stripe.refund",
        arguments=arguments,
        principal=Principal(agent="conformance-agent", user="ada"),
        resource=f"payment:{payment_id}",
    )


def granted_approval(store: StateStore, action: Action, *, at: datetime = T0) -> str:
    """Record a request and grant it, the way `ctrlrun approve` does."""
    request = build_request(action, timedelta(minutes=15), at)
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:conformance")
    return request.request_id


# --- the N/A honesty check (SPEC-v0.6 §2.6) -------------------------------------------------


def storage_is_confined(backend: StoreBackend) -> bool:
    """Does this backend's storage really not outlive the object that holds it?

    §2.4 allows exactly two N/As and both rest on a **declaration** -- `url()` and `reopen()`
    returning `None`. A declaration that turns a suite off is a setting that relaxes a check
    (§1.1), so the kit does not take it on trust: it opens two independent handles, writes
    through one and reads through the other. If the second sees the first's write, the storage
    plainly outlives the object and the declaration is false.

    No backdoor and no per-backend knowledge: `open()` is on the protocol, and a backend that
    returned a cached object to hide this would be failing the same check for the same reason.
    """
    first = backend.open()
    second = backend.open()
    if first is second:
        return False
    first.reserve_effect("refund:confinement-probe", "act_probe", LEASE)
    try:
        return second.get_effect("refund:confinement-probe") is None
    finally:
        first.close()
        second.close()


def dishonest(case_id: str, title: str, what: str) -> CaseResult:
    return failed(
        case_id,
        title,
        f"{what} returned None, but two independent handles on this backend see each other's "
        "writes -- so the storage does outlive the object and the N/A is a declaration that "
        "turns a suite off (§2.6)",
    )


# --- reservation (v0.1 §7 T1, T3, T8, T9; §5.3 E1-E3; §5.4) ---------------------------------


@case("e1-in-process", "E1 holds against contenders in one process")
def reservation_e1_in_process(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """SPEC-v0.6 §2.4. The case a broken-store fixture can fail.

    All contenders are released from a barrier at once. The suite cannot open the window
    *inside* `reserve_effect` -- that would need a hook in shipping code, which §2.5 refuses to
    add -- so what it controls is that every contender is running concurrently when it calls.
    §2.7 records the correction.
    """
    title = reservation_e1_in_process.title
    store = backend.open()

    for round_number in range(ROUNDS):
        key = f"refund:e1-{round_number}"
        barrier = threading.Barrier(processes)
        won: list[str] = []
        refused: list[Exception] = []
        lock = threading.Lock()

        def contend(
            index: int,
            key: str = key,
            barrier: threading.Barrier = barrier,
            lock: threading.Lock = lock,
            won: list[str] = won,
            refused: list[Exception] = refused,
        ) -> None:
            # Every per-round object is bound as a default. They are rebuilt each round, and a
            # closure over the loop variable would have a thread from round N appending to
            # round N+1's list the moment the join ever became less than total.
            action_id = f"act_{index:032x}"
            barrier.wait(timeout=30)
            try:
                store.reserve_effect(key, action_id, LEASE)
            except Exception as refusal:
                with lock:
                    refused.append(refusal)  # noqa: B023 - `except`-bound, not loop-bound
                return
            with lock:
                won.append(action_id)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(processes)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            if thread.is_alive():
                return failed("e1-in-process", title, "a contender did not finish within its bound")

        if len(won) != 1:
            return failed(
                "e1-in-process",
                title,
                f"{len(won)} contenders reserved one effect key; exactly one may "
                f"(round {round_number + 1} of {ROUNDS})",
                winners=won,
                round=round_number,
            )
        if len(refused) != processes - 1:
            return failed(
                "e1-in-process", title, f"{len(refused)} refusals, expected {processes - 1}"
            )
        for refusal in refused:
            if not isinstance(refusal, DuplicateEffect | AmbiguousEffect):
                return failed(
                    "e1-in-process",
                    title,
                    f"a loser saw {type(refusal).__name__}: {refusal}; v0.1 §5.4's refusals are "
                    "DuplicateEffect and AmbiguousEffect",
                )
        record = store.get_effect(key)
        if record is None or record.action_id != won[0]:
            return failed("e1-in-process", title, "the record does not name the winner")
    return passed("e1-in-process", title)


@case("e1-cross-process", "E1 holds across OS processes")
def reservation_e1_cross_process(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.1 §7` T3's standard, reached through the suite rather than reimplemented."""
    title = reservation_e1_cross_process.title
    url = backend.url()
    if url is None:
        if not storage_is_confined(backend):
            return dishonest("e1-cross-process", title, "url()")
        return na(
            "e1-cross-process",
            title,
            "this backend's storage cannot be opened from another process",
        )
    # Create the storage BEFORE the children race for it, on the url they are given -- not
    # through `backend.open()`, which a later `reset()` would point at a different file. An
    # independent review found the first version creating `conformance-0.db` for the children
    # and then reading `conformance-1.db` back, so "one committed record" was unverifiable.
    store_from_url(url).close()

    with tempfile.TemporaryDirectory() as marker_dir, tempfile.TemporaryDirectory() as gate:
        payloads = [
            json.dumps(
                {
                    "url": url,
                    "effect_key": "refund:e1x",
                    "action_id": f"act_{index:032x}",
                    "marker_dir": marker_dir,
                    "rendezvous_dir": gate,
                    "contenders": processes,
                    "rendezvous_timeout": 30,
                }
            )
            for index in range(processes)
        ]
        children = [
            subprocess.Popen(
                [sys.executable, "-m", "ctrlrun.conformance.store.worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in payloads
        ]
        # **Every stdin is written and closed before any child is collected.** `communicate()`
        # in the collection loop was the defect: a child blocks on `stdin.read()` until its own
        # stdin closes, so collecting one at a time fed them one at a time and the eight
        # contenders never overlapped. The worker's own rendezvous is the barrier; this is what
        # lets every worker reach it.
        for child, payload in zip(children, payloads, strict=True):
            assert child.stdin is not None
            child.stdin.write(payload)
            child.stdin.close()

        results = []
        for child in children:
            try:
                child.wait(timeout=90)
            except subprocess.TimeoutExpired:
                child.kill()
                return failed("e1-cross-process", title, "a contender did not finish in 90s")
            assert child.stdout is not None and child.stderr is not None
            out, err = child.stdout.read(), child.stderr.read()
            child.stdout.close()
            child.stderr.close()
            if child.returncode != 0:
                return failed(
                    "e1-cross-process", title, f"a contender exited {child.returncode}: {err}"
                )
            results.append(json.loads(out))
        called = os.listdir(marker_dir)
        arrived = len(os.listdir(gate))

    if arrived != processes:
        return failed(
            "e1-cross-process",
            title,
            f"only {arrived} of {processes} contenders reached the barrier, so they did not "
            "contend; this case would have graded serialization rather than the store",
        )
    winners = [result for result in results if result["won"]]
    if len(winners) != 1:
        return failed(
            "e1-cross-process",
            title,
            f"{len(winners)} of {processes} processes reserved one key; exactly one may",
            results=results,
        )
    if winners[0]["error"] is not None:
        return failed("e1-cross-process", title, f"the winner broke: {winners[0]['message']}")
    if called != ["called"]:
        return failed("e1-cross-process", title, f"the fake remote was called {len(called)} times")
    for result in results:
        if result["won"]:
            continue
        if result["error"] not in ("DuplicateEffect", "AmbiguousEffect"):
            return failed(
                "e1-cross-process",
                title,
                f"a loser saw {result['error']}: {result['message']}; v0.1 §5.4's refusals are "
                "DuplicateEffect and AmbiguousEffect",
            )
    # The record is read back on the url the children actually used. Without this the case
    # asserts what the children *reported* and never what the store *holds*.
    holder = store_from_url(url)
    try:
        record = holder.get_effect("refund:e1x")
    finally:
        holder.close()
    if record is None:
        return failed(
            "e1-cross-process", title, "no record exists for the key eight processes contended for"
        )
    if record.state is not EffectState.COMMITTED:
        return failed(
            "e1-cross-process", title, f"the record is {record.state}, expected committed"
        )
    if record.action_id != winners[0]["action_id"]:
        return failed(
            "e1-cross-process",
            title,
            f"the record names {record.action_id}, but {winners[0]['action_id']} reported winning",
        )
    return passed("e1-cross-process", title)


@case("retry-table", "the retry table of v0.1 §5.4, every row")
def reservation_retry_table(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = reservation_retry_table.title
    store = backend.open()

    # COMMITTED -> refused
    store.reserve_effect("refund:done", "act_a", LEASE)
    store.begin_execution("refund:done", "act_a")
    store.commit_effect("refund:done", "act_a", {"ok": True})
    problem = expect(
        "retry-table",
        title,
        lambda: store.reserve_effect("refund:done", "act_b", LEASE),
        DuplicateEffect,
        state=COMMITTED_EFFECT,
    )
    if problem:
        return problem

    # RESERVED with a live lease -> refused
    store.reserve_effect("refund:live", "act_c", LEASE)
    problem = expect(
        "retry-table",
        title,
        lambda: store.reserve_effect("refund:live", "act_d", LEASE),
        DuplicateEffect,
        state=IN_PROGRESS_EFFECT,
    )
    if problem:
        return problem

    # EXECUTING with a live lease -> refused, the other half of §5.4's in-progress row
    store.reserve_effect("refund:running", "act_c2", LEASE)
    store.begin_execution("refund:running", "act_c2")
    problem = expect(
        "retry-table",
        title,
        lambda: store.reserve_effect("refund:running", "act_d2", LEASE),
        DuplicateEffect,
        state=IN_PROGRESS_EFFECT,
    )
    if problem:
        return problem

    # AMBIGUOUS -> refused, and only a human moves it
    store.reserve_effect("refund:unknown", "act_e", LEASE)
    store.begin_execution("refund:unknown", "act_e")
    store.mark_ambiguous("refund:unknown", "act_e", "the response was lost")
    problem = expect(
        "retry-table",
        title,
        lambda: store.reserve_effect("refund:unknown", "act_f", LEASE),
        AmbiguousEffect,
    )
    if problem:
        return problem

    # FAILED -> the one automatic retry, attempt + 1
    store.reserve_effect("refund:nope", "act_g", LEASE)
    store.begin_execution("refund:nope", "act_g")
    store.fail_effect("refund:nope", "act_g", "the remote refused before acting")
    reservation = store.reserve_effect("refund:nope", "act_h", LEASE)
    if reservation.attempt != 2:
        return failed(
            "retry-table",
            title,
            f"a retry after FAILED is attempt {reservation.attempt}, expected 2",
        )
    record = store.get_effect("refund:nope")
    if record is None or record.attempt != 2 or record.action_id != "act_h":
        return failed("retry-table", title, "the renewed record does not name the second attempt")
    return passed("retry-table", title)


@case("lease-expiry", "an expired lease becomes AMBIGUOUS and is never released")
def reservation_lease_expiry(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.1 §5.3 E3`, and SPEC-v0.6 §4.2.2's first kept write.

    The refusal is not the whole assertion: the record MUST be `AMBIGUOUS` afterwards. A store
    that refused without recording the lapse would leave the next contender to rediscover it,
    forever, and would pass a test that asserted only the exception.
    """
    title = reservation_lease_expiry.title
    now = T0
    store = backend.open()

    store = _clocked(backend, lambda: now)
    store.reserve_effect("refund:lapsed", "act_i", timedelta(seconds=1))
    store.begin_execution("refund:lapsed", "act_i")
    now = T0 + timedelta(minutes=1)

    problem = expect(
        "lease-expiry",
        title,
        lambda: store.reserve_effect("refund:lapsed", "act_j", LEASE),
        AmbiguousEffect,
    )
    if problem:
        return problem
    record = store.get_effect("refund:lapsed")
    if record is None:
        return failed("lease-expiry", title, "the record vanished")
    if record.state is not EffectState.AMBIGUOUS:
        return failed(
            "lease-expiry",
            title,
            f"the record is {record.state} after its lease lapsed; it must be ambiguous and "
            "the write must be kept even though the attempt was refused (§4.2.2)",
        )
    return passed("lease-expiry", title)


# --- approval (v0.1 §7 T2, T4, T5, T12; §4.2 A1-A4) -----------------------------------------


@case("binding", "an approval authorizes one action_hash and no other")
def approval_binding(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = approval_binding.title
    store = _clocked(backend, lambda: T0)
    action = an_action(amount=2000)
    approval_id = granted_approval(store, action)
    mutated = an_action(amount=5000)

    problem = expect(
        "binding",
        title,
        lambda: store.consume_approval(approval_id, mutated.action_hash),
        ApprovalMismatch,
    )
    if problem:
        return problem
    record = store.get_approval(approval_id)
    if record is None or record.status is not ApprovalStatus.GRANTED:
        return failed(
            "binding",
            title,
            f"the approval is {record.status if record else 'gone'} after a mismatch; "
            "a refusal must not spend it",
        )
    return passed("binding", title)


@case("single-use", "an approval is consumed exactly once")
def approval_single_use(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = approval_single_use.title
    store = _clocked(backend, lambda: T0)
    action = an_action(payment_id="txn_su")
    approval_id = granted_approval(store, action)

    store.consume_approval(approval_id, action.action_hash)
    record = store.get_approval(approval_id)
    if record is None or record.status is not ApprovalStatus.CONSUMED:
        return failed(
            "single-use",
            title,
            f"the approval is {record.status if record else 'gone'} after being consumed",
        )
    problem = expect(
        "single-use",
        title,
        lambda: store.consume_approval(approval_id, action.action_hash),
        ApprovalMismatch,
        reason="consumed",
    )
    return problem or passed("single-use", title)


@case("expiry", "expiry is checked at consumption, and the lapse is recorded")
def approval_expiry(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.1 §4.2 A3`, and SPEC-v0.6 §4.2.2's second kept write."""
    title = approval_expiry.title
    now = T0
    store = _clocked(backend, lambda: now)
    action = an_action(payment_id="txn_exp")
    approval_id = granted_approval(store, action, at=now)
    now = T0 + timedelta(hours=1)

    problem = expect(
        "expiry",
        title,
        lambda: store.consume_approval(approval_id, action.action_hash),
        ApprovalMismatch,
        reason="expired",
    )
    if problem:
        return problem
    record = store.get_approval(approval_id)
    if record is None or record.status is not ApprovalStatus.EXPIRED:
        return failed(
            "expiry",
            title,
            f"the approval is {record.status if record else 'gone'} after lapsing; a lapsed "
            "approval is evidence and the write is kept (§4.2.2)",
        )
    return passed("expiry", title)


@case("atomic-with-reservation", "a refused reservation leaves the approval granted")
def approval_atomic(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.1 §7` T12, driven against the real store rather than an injected failure.

    v0.1's T12 injects a store whose `reserve_effect` fails. Here the reservation is refused
    for a real reason -- the effect is already committed -- so what is asserted is the store's
    own transaction rather than a double's.
    """
    title = approval_atomic.title
    store = _clocked(backend, lambda: T0)
    action = an_action(payment_id="txn_atomic")
    approval_id = granted_approval(store, action)

    store.reserve_effect("refund:atomic", "act_k", LEASE)
    store.begin_execution("refund:atomic", "act_k")
    store.commit_effect("refund:atomic", "act_k", {"ok": True})

    problem = expect(
        "atomic-with-reservation",
        title,
        lambda: store.consume_approval_and_reserve(
            approval_id, action.action_hash, "refund:atomic", "act_l", LEASE
        ),
        DuplicateEffect,
        state=COMMITTED_EFFECT,
    )
    if problem:
        return problem
    record = store.get_approval(approval_id)
    if record is None or record.status is not ApprovalStatus.GRANTED:
        return failed(
            "atomic-with-reservation",
            title,
            f"the approval is {record.status if record else 'gone'}; a refused reservation "
            "must leave it granted (v0.1 §4.2 A4)",
        )
    return passed("atomic-with-reservation", title)


@case("approval-checked-first", "the approval's refusal is the one raised")
def approval_checked_first(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.1 §7` T4's ordering: when a replayed approval and a duplicate effect both apply,
    the approval is checked first and its error is what the caller sees."""
    title = approval_checked_first.title
    store = _clocked(backend, lambda: T0)
    action = an_action(payment_id="txn_order")
    approval_id = granted_approval(store, action)

    store.consume_approval_and_reserve(
        approval_id, action.action_hash, "refund:order", "act_m", LEASE
    )
    store.begin_execution("refund:order", "act_m")
    store.commit_effect("refund:order", "act_m", {"ok": True})

    problem = expect(
        "approval-checked-first",
        title,
        lambda: store.consume_approval_and_reserve(
            approval_id, action.action_hash, "refund:order", "act_n", LEASE
        ),
        ApprovalMismatch,
        reason="consumed",
    )
    return problem or passed("approval-checked-first", title)


# --- resolution (v0.1 §7 T10; §5.2) ---------------------------------------------------------


@case("only-ambiguous", "only an AMBIGUOUS record resolves")
def resolution_only_ambiguous(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = resolution_only_ambiguous.title
    store = backend.open()

    store.reserve_effect("refund:res1", "act_o", LEASE)
    store.begin_execution("refund:res1", "act_o")
    store.commit_effect("refund:res1", "act_o", {"ok": True})
    problem = expect(
        "only-ambiguous",
        title,
        lambda: store.resolve_effect("refund:res1", EffectState.FAILED, "cli:human"),
        InvalidArgument,
    )
    if problem:
        return problem

    store.reserve_effect("refund:res2", "act_p", LEASE)
    problem = expect(
        "only-ambiguous",
        title,
        lambda: store.resolve_effect("refund:res2", EffectState.COMMITTED, "cli:human"),
        InvalidArgument,
    )
    if problem:
        return problem

    problem = expect(
        "only-ambiguous",
        title,
        lambda: store.resolve_effect("refund:absent", EffectState.COMMITTED, "cli:human"),
        InvalidArgument,
    )
    if problem:
        return problem
    # The refusal is not the whole assertion. A store that raised and moved the record anyway
    # would pass every line above, and a `resolve` that could overwrite a live record is a way
    # to release a reservation `v0.1 §5.3` says there is none of.
    for key, want in (
        ("refund:res1", EffectState.COMMITTED),
        ("refund:res2", EffectState.RESERVED),
    ):
        record = store.get_effect(key)
        if record is None or record.state is not want:
            got = record.state if record else "gone"
            return failed(
                "only-ambiguous",
                title,
                f"a refused resolve moved {key!r} to {got}; it must leave the record alone",
            )
    return passed("only-ambiguous", title)


@case("two-resolutions", "an AMBIGUOUS record resolves to COMMITTED or FAILED, and nothing else")
def resolution_two_targets(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = resolution_two_targets.title
    store = backend.open()

    def ambiguate(key: str, action_id: str) -> None:
        store.reserve_effect(key, action_id, LEASE)
        store.begin_execution(key, action_id)
        store.mark_ambiguous(key, action_id, "the response was lost")

    ambiguate("refund:res3", "act_q")
    for target in (EffectState.RESERVED, EffectState.EXECUTING, EffectState.AMBIGUOUS):
        problem = expect(
            "two-resolutions",
            title,
            partial(store.resolve_effect, "refund:res3", target, "cli:human"),
            InvalidArgument,
        )
        if problem:
            return problem

    resolved = store.resolve_effect("refund:res3", EffectState.COMMITTED, "cli:human")
    if resolved.state is not EffectState.COMMITTED:
        return failed("two-resolutions", title, f"resolved to {resolved.state}, expected committed")

    ambiguate("refund:res4", "act_r")
    resolved = store.resolve_effect("refund:res4", EffectState.FAILED, "cli:human")
    if resolved.state is not EffectState.FAILED:
        return failed("two-resolutions", title, f"resolved to {resolved.state}, expected failed")
    # Read back, not merely returned. A store that fabricated the returned record and wrote
    # nothing passed every line above -- found by review.
    for key, want in (("refund:res3", EffectState.COMMITTED), ("refund:res4", EffectState.FAILED)):
        record = store.get_effect(key)
        if record is None or record.state is not want:
            got = record.state if record else "gone"
            return failed(
                "two-resolutions",
                title,
                f"resolve_effect returned {want} for {key!r} but the record says {got}",
            )
    return passed("two-resolutions", title)


# --- outcome (v0.1 §5.5, one layer down; SPEC-v0.6 §2.5) ------------------------------------


def _every_refusal(store: StateStore) -> list[tuple[str | None, Callable[[], Any]]]:
    """Every refusal path the protocol has, as (effect_key, call) pairs.

    Built once and used by both `outcome` cases, so neither can drift into driving fewer
    refusals than the other.
    """
    store.reserve_effect("refund:o1", "act_s", LEASE)
    store.begin_execution("refund:o1", "act_s")
    store.commit_effect("refund:o1", "act_s", {"ok": True})

    store.reserve_effect("refund:o2", "act_t", LEASE)

    store.reserve_effect("refund:o3", "act_u", LEASE)
    store.begin_execution("refund:o3", "act_u")
    store.mark_ambiguous("refund:o3", "act_u", "lost")

    action = an_action(payment_id="txn_o")
    approval_id = granted_approval(store, action)
    store.consume_approval(approval_id, action.action_hash)

    return [
        ("refund:o1", lambda: store.reserve_effect("refund:o1", "act_v", LEASE)),
        ("refund:o2", lambda: store.reserve_effect("refund:o2", "act_w", LEASE)),
        ("refund:o3", lambda: store.reserve_effect("refund:o3", "act_x", LEASE)),
        ("refund:o2", lambda: store.begin_execution("refund:o2", "act_wrong")),
        ("refund:o2", lambda: store.commit_effect("refund:o2", "act_wrong", None)),
        ("refund:o2", lambda: store.fail_effect("refund:o2", "act_wrong", "no")),
        ("refund:o1", lambda: store.begin_execution("refund:o1", "act_s")),
        ("refund:o1", lambda: store.extend_lease("refund:o1", "act_s", T0 + timedelta(hours=1))),
        ("refund:o1", lambda: store.resolve_effect("refund:o1", EffectState.FAILED, "cli:h")),
        ("refund:absent", lambda: store.begin_execution("refund:absent", "act_y")),
        # These three touch no effect record, so pairing them with one made their per-row
        # assertion vacuous. `None` says so, and the caller checks every record instead.
        (None, lambda: store.consume_approval(approval_id, action.action_hash)),
        (None, lambda: store.take_continuation("not-a-real-continuation")),
        (None, lambda: store.revoke_delegation("dlg_" + "0" * 32, by=None, at=T0)),
    ]


@case("no-failed-on-refusal", "no refusal writes FAILED")
def outcome_no_failed_on_refusal(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """SPEC-v0.6 §2.5.

    `fail_effect` and `resolve_effect` write `FAILED` as their **purpose** and neither is a
    refusal path. What this drives is every refusal, and after each the record must not have
    become `FAILED` -- a store that guessed there would turn "I could not confirm the write"
    into "it definitely did not happen", which is the failure this library exists to prevent.
    """
    title = outcome_no_failed_on_refusal.title
    store = _clocked(backend, lambda: T0)
    watched = ("refund:o1", "refund:o2", "refund:o3")
    for _row_key, call in _every_refusal(store):
        # Every watched record, not only the one this row names: a refusal that moved a
        # *different* key to FAILED is the same defect and a per-row check cannot see it.
        snapshot = {key: store.get_effect(key) for key in watched}
        with contextlib.suppress(Exception):
            call()  # a refusal is expected here; the record it leaves is the subject
        for key in watched:
            before, after = snapshot[key], store.get_effect(key)
            if after is None:
                if before is not None:
                    return failed(
                        "no-failed-on-refusal",
                        title,
                        f"a refusal deleted the record for {key!r}; nothing in this protocol "
                        "removes an effect record",
                    )
                continue
            was_failed = before is not None and before.state is EffectState.FAILED
            if after.state is EffectState.FAILED and not was_failed:
                return failed(
                    "no-failed-on-refusal",
                    title,
                    f"a refusal moved {key!r} to FAILED; only fail_effect and resolve_effect "
                    "may write it, and neither is a refusal path (§2.5)",
                )
    return passed("no-failed-on-refusal", title)


@case("no-not-executed", "no store method raises NotExecuted")
def outcome_no_not_executed(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`NotExecuted` is the executor's opt-in to `FAILED` and the one exception an agent may
    read as permission to retry. A store raising it would be asserting something about a remote
    it has never spoken to."""
    title = outcome_no_not_executed.title
    store = _clocked(backend, lambda: T0)
    for _key, call in _every_refusal(store):
        try:
            call()
        except NotExecuted as wrong:
            return failed(
                "no-not-executed",
                title,
                f"a store method raised NotExecuted: {wrong}. That is the executor's opt-in to "
                "FAILED, and a store has never spoken to the remote (§2.5)",
            )
        except BaseException:
            pass
    return passed("no-not-executed", title)


# --- durability (v0.1 §5.2, which no test drove) --------------------------------------------


@case("ambiguous-survives", "AMBIGUOUS survives a reopen and still refuses a blind retry")
def durability_ambiguous(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = durability_ambiguous.title
    store = backend.open()
    store.reserve_effect("refund:dur1", "act_z", LEASE)
    store.begin_execution("refund:dur1", "act_z")
    store.mark_ambiguous("refund:dur1", "act_z", "the response was lost")

    other = backend.reopen()
    if other is None:
        if not storage_is_confined(backend):
            return dishonest("ambiguous-survives", title, "reopen()")
        return na(
            "ambiguous-survives",
            title,
            "this backend's storage does not outlive the object that holds it",
        )
    record = other.get_effect("refund:dur1")
    if record is None:
        return failed("ambiguous-survives", title, "the record did not survive the reopen")
    if record.state is not EffectState.AMBIGUOUS:
        return failed("ambiguous-survives", title, f"the record came back {record.state}")
    problem = expect(
        "ambiguous-survives",
        title,
        lambda: other.reserve_effect("refund:dur1", "act_zz", LEASE),
        AmbiguousEffect,
    )
    return problem or passed("ambiguous-survives", title)


@case("terminal-states-survive", "every terminal record survives a reopen")
def durability_terminal(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = durability_terminal.title
    store = backend.open()
    finishers: tuple[tuple[str, str, Callable[[StateStore, str, str], None]], ...] = (
        ("refund:dur2", "act_c1", lambda s, k, a: s.commit_effect(k, a, {"ok": True})),
        ("refund:dur3", "act_c2", lambda s, k, a: s.fail_effect(k, a, "refused before acting")),
    )
    for key, action_id, finish in finishers:
        store.reserve_effect(key, action_id, LEASE)
        store.begin_execution(key, action_id)
        finish(store, key, action_id)

    other = backend.reopen()
    if other is None:
        if not storage_is_confined(backend):
            return dishonest("terminal-states-survive", title, "reopen()")
        return na(
            "terminal-states-survive",
            title,
            "this backend's storage does not outlive the object that holds it",
        )
    for key, want in (("refund:dur2", EffectState.COMMITTED), ("refund:dur3", EffectState.FAILED)):
        record = other.get_effect(key)
        if record is None or record.state is not want:
            got = record.state if record else "gone"
            return failed(
                "terminal-states-survive", title, f"{key} came back {got}, expected {want}"
            )
    return passed("terminal-states-survive", title)


# --- evidence (v0.1 §7 T7; v0.3 §10 T60c) ---------------------------------------------------


@case("action-round-trip", "an Action round-trips without coercion")
def evidence_action_round_trip(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """The store holds the Action a human approved. A round trip that coerced an `int` to a
    `float`, truncated a string, or dropped a claim would change the `action_hash` -- which is
    the whole of `v0.1 §4.2 A1`, reached through storage."""
    title = evidence_action_round_trip.title
    store = backend.open()
    action = Action(
        name="stripe.refund",
        arguments={
            "payment_id": "txn_rt",
            "amount": 2000,
            "flagged": True,
            "note": "café " + "x" * 300,
            "tags": ["a", "b"],
            "nested": {"z": 1, "a": {"deep": None}},
        },
        # `v0.3 §10` T60c is *about* claims, issuer and expiry, and all three are deliberately
        # outside the canonical form (`v0.3 §2.2`) -- so the `action_hash` comparison below
        # cannot see them, and a store that dropped all three passed this case until a review
        # built one. They are asserted separately, which is the only way they can be.
        principal=Principal(
            agent="conformance-agent",
            user="ada",
            claims={"dept": "finance", "level": 3},
            issuer="https://issuer.example",
            expires_at=T0 + timedelta(hours=1),
        ),
        resource="payment:txn_rt",
    )
    request = build_request(action, timedelta(minutes=15), T0)
    store.put_approval_request(request)

    record = store.get_approval(request.request_id)
    if record is None:
        return failed("action-round-trip", title, "the request did not come back")
    back = record.request.action
    if back.action_hash != action.action_hash:
        return failed(
            "action-round-trip",
            title,
            f"the action hash changed across the store: {action.action_hash} -> {back.action_hash}",
        )
    for name, value in action.canonical_arguments.items():
        got = back.canonical_arguments.get(name)
        if type(got) is not type(value) or got != value:
            return failed(
                "action-round-trip",
                title,
                f"argument {name!r} came back as {type(got).__name__} {got!r}, "
                f"expected {type(value).__name__} {value!r}",
            )
    if back.principal != action.principal:
        return failed(
            "action-round-trip",
            title,
            f"the principal changed across the store: {back.principal!r}",
        )
    for name in ("claims", "issuer", "expires_at"):
        got, want = getattr(back.principal, name), getattr(action.principal, name)
        if got != want:
            return failed(
                "action-round-trip",
                title,
                f"principal.{name} came back {got!r}, expected {want!r}. It is outside the "
                "action hash by design (v0.3 §2.2), so nothing else here can see it",
            )
    if back.resource != action.resource or back.environment != action.environment:
        return failed("action-round-trip", title, "resource or environment changed")
    return passed("action-round-trip", title)


@case("event-ids", "append_event returns the event as stored")
def evidence_event_ids(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.2 §4.1`: `Control` hands every event to its sinks with the id the store assigned, and
    the store is the only thing that knows it. A sink that could not join its export back to the
    record would be exporting something else."""
    title = evidence_event_ids.title
    store = backend.open()
    stored = [
        store.append_event(
            Event(
                event_id=0,
                ts=T0,
                type=EventType.ACTION_PROPOSED,
                action_id=f"act_e{i}",
                effect_key=f"refund:e{i}",
                data={"reason": "rule[1]", "amount": 2000, "nested": {"a": [1, 2]}},
            )
        )
        for i in range(4)
    ]
    ids = [event.event_id for event in stored]
    if any(event_id is None for event_id in ids):
        return failed(
            "event-ids",
            title,
            "append_event returned an event with no id; the store is the only thing that knows "
            "it, and a sink that could not join its export back to the record would be "
            "exporting something else (v0.2 §4.1)",
        )
    if len(set(ids)) != len(ids):
        return failed("event-ids", title, f"append_event returned duplicate ids: {ids}")
    if ids != sorted(ids):  # type: ignore[type-var]
        return failed("event-ids", title, f"append_event returned ids out of order: {ids}")
    if any(event_id == 0 for event_id in ids):
        return failed(
            "event-ids",
            title,
            "append_event returned the id it was handed, not the one it stored",
        )
    by_id = {event.event_id: event for event in store.events()}
    held = set(by_id)
    if not set(ids) <= held:
        return failed(
            "event-ids",
            title,
            f"append_event returned ids {ids} the store does not hold: {sorted(held)}",  # type: ignore[type-var]
        )
    # The payload too, not only the id. A store that replaced every event's `data` passed this
    # case while the ids matched, and an event log whose data is not the data is not evidence.
    for event in stored:
        differs = _differing_field(event, by_id[event.event_id])
        if differs is not None:
            name, mine, theirs = differs
            return failed(
                "event-ids", title, f"event.{name} came back {theirs!r}, expected {mine!r}"
            )
    return passed("event-ids", title)


@case("receipt-round-trip", "a receipt round-trips")
def evidence_receipt(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = evidence_receipt.title
    store = backend.open()
    action = an_action(payment_id="txn_rcp")
    receipt = Receipt(
        receipt_id="ctr_" + "1" * 12,
        action_id=action.action_id,
        action=action.name,
        action_hash=action.action_hash,
        principal=action.principal,
        resource=action.resource,
        arguments=action.canonical_arguments,
        environment=action.environment,
        decision=Decision.ALLOW,
        decision_reason="rule[0]",
        effect_key="refund:txn_rcp",
        attempt=1,
        result=ReceiptResult.COMMITTED,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=1),
    )
    store.put_receipt(receipt)
    back = [held for held in store.receipts() if held.receipt_id == receipt.receipt_id]
    if not back:
        return failed("receipt-round-trip", title, "the receipt did not come back")
    # Every field, not two of seventeen. A store that mangled `decision`, `approver`,
    # `arguments`, `attempt` or the timestamps passed this case while the two it compared
    # survived -- and receipt fidelity is what §6's chain rests on.
    differs = _differing_field(receipt, back[0])
    if differs is not None:
        name, mine, theirs = differs
        return failed(
            "receipt-round-trip",
            title,
            f"receipt.{name} came back {theirs!r}, expected {mine!r}",
        )
    return passed("receipt-round-trip", title)


# --- continuation (v0.2 §10 T26, T26b) ------------------------------------------------------


@case("one-resumption", "one suspension admits exactly one resumption")
def continuation_one_resumption(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = continuation_one_resumption.title
    store = _clocked(backend, lambda: T0)
    action = an_action(payment_id="txn_cont")
    store.reserve_effect("refund:cont", action.action_id, LEASE)
    store.begin_execution("refund:cont", action.action_id)
    rounds = store.hold_continuation(action, "refund:cont", "cont-token-1", T0 + timedelta(hours=1))
    if rounds != 1:
        return failed(
            "one-resumption", title, f"the first hold reported round {rounds}, expected 1"
        )

    held = store.take_continuation("cont-token-1")
    if held.action.action_hash != action.action_hash:
        return failed("one-resumption", title, "the rehydrated action is not the one suspended")
    problem = expect(
        "one-resumption",
        title,
        lambda: store.take_continuation("cont-token-1"),
        InvalidArgument,
    )
    return problem or passed("one-resumption", title)


@case("extend-lease-refusals", "extend_lease refuses what it must")
def continuation_extend_lease(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.2 §10` T26b. An expired lease is extendable by nothing, and an expired reservation is
    still released by nobody."""
    title = continuation_extend_lease.title
    now = T0
    store = _clocked(backend, lambda: now)

    # A record that is RESERVED and not yet EXECUTING, held by this very action.
    store.reserve_effect("refund:ext1", "act_x1", LEASE)
    problem = expect(
        "extend-lease-refusals",
        title,
        lambda: store.extend_lease("refund:ext1", "act_x1", now + timedelta(hours=1)),
        DuplicateEffect,
    )
    if problem:
        return problem

    # EXECUTING, but under another action.
    store.begin_execution("refund:ext1", "act_x1")
    problem = expect(
        "extend-lease-refusals",
        title,
        lambda: store.extend_lease("refund:ext1", "act_other", now + timedelta(hours=1)),
        DuplicateEffect,
    )
    if problem:
        return problem

    # A lease that has already lapsed. `AmbiguousEffect` and not `DuplicateEffect`: an expired
    # lease is extendable by nothing and an expired reservation is released by nobody, so the
    # refusal names the unknown rather than a live holder (`v0.2 §6.9.4`).
    store.reserve_effect("refund:ext2", "act_x2", timedelta(seconds=1))
    store.begin_execution("refund:ext2", "act_x2")
    now = T0 + timedelta(minutes=1)
    problem = expect(
        "extend-lease-refusals",
        title,
        lambda: store.extend_lease("refund:ext2", "act_x2", now + timedelta(hours=1)),
        AmbiguousEffect,
    )
    return problem or passed("extend-lease-refusals", title)


# --- delegation (v0.3 §5.2, §10 T75b, T78) --------------------------------------------------


def _delegation(delegation_id: str, *, revoked: bool = False) -> DelegationRecord:
    return DelegationRecord(
        delegation_id=delegation_id,
        parent_id="head-of-finance",
        depth=1,
        grant_json='{"actions":["stripe.*"],"delegable":false}',
        created_by_agent="conformance-agent",
        created_by_user="ada",
        created_via="api",
        created_at=T0,
        revoked_at=T0 if revoked else None,
        revoked_by="cli:human" if revoked else None,
    )


@case("insert-not-upsert", "put_delegation inserts and never upserts")
def delegation_insert(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.3 §5.2`: an upsert on an existing id would clear `revoked_at`, which is `unrevoke` by
    another door in a release that says there is no such thing."""
    title = delegation_insert.title
    store = backend.open()
    identifier = "dlg_" + "a" * 32
    store.put_delegation(_delegation(identifier))
    store.revoke_delegation(identifier, by="cli:human", at=T0)

    try:
        store.put_delegation(_delegation(identifier))
    except BaseException:
        record = store.get_delegation(identifier)
        if record is None or record.revoked_at is None:
            return failed(
                "insert-not-upsert",
                title,
                "the duplicate insert raised but cleared revoked_at anyway",
            )
        return passed("insert-not-upsert", title)
    record = store.get_delegation(identifier)
    if record is not None and record.revoked_at is None:
        return failed(
            "insert-not-upsert",
            title,
            "put_delegation upserted on a duplicate id and cleared revoked_at -- unrevoking by "
            "another door (v0.3 §5.2)",
        )
    return failed("insert-not-upsert", title, "put_delegation accepted a duplicate delegation id")


@case("revoke-atomic-idempotent", "revoke_delegation is atomic, idempotent and strict")
def delegation_revoke(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    title = delegation_revoke.title
    store = backend.open()
    identifier = "dlg_" + "b" * 32
    store.put_delegation(_delegation(identifier))

    if store.revoke_delegation(identifier, by="cli:human", at=T0) is not True:
        return failed("revoke-atomic-idempotent", title, "the first revoke did not report True")
    if store.revoke_delegation(identifier, by="cli:human", at=T0) is not False:
        return failed(
            "revoke-atomic-idempotent",
            title,
            "the second revoke did not report False; an already-revoked delegation is "
            "idempotent, not an error",
        )
    problem = expect(
        "revoke-atomic-idempotent",
        title,
        lambda: store.revoke_delegation("dlg_" + "c" * 32, by="cli:human", at=T0),
        InvalidArgument,
    )
    if problem:
        return problem
    # `True` is not the assertion; the row is. A store reporting a successful revoke while the
    # delegation stays live is a `v0.3` authorization hole, and this case graded it `pass` until
    # a review built one.
    record = store.get_delegation(identifier)
    if record is None:
        return failed("revoke-atomic-idempotent", title, "the delegation vanished")
    if record.revoked_at is None:
        return failed(
            "revoke-atomic-idempotent",
            title,
            "revoke_delegation reported True and the row is still live; a revoke that only "
            "reports success is an authorization hole",
        )
    if record.revoked_by != "cli:human":
        return failed(
            "revoke-atomic-idempotent",
            title,
            f"the row records revoked_by={record.revoked_by!r}, not the revoker",
        )
    return passed("revoke-atomic-idempotent", title)


@case("grant-json-round-trip", "grant_json round-trips with its offset retained")
def delegation_grant_json(backend: StoreBackend, processes: int = CONTENDERS) -> CaseResult:
    """`v0.3 §5.2`: `expires_at` retains the offset it was written with, not normalized to UTC,
    so a grant round-trips to an equal `Grant`."""
    title = delegation_grant_json.title
    store = backend.open()
    identifier = "dlg_" + "d" * 32
    grant_json = (
        '{"actions":["stripe.*"],"delegable":false,'
        '"expires_at":"2026-06-01T12:00:00+05:30","resources":null}'
    )
    record = _delegation(identifier)
    store.put_delegation(
        DelegationRecord(
            delegation_id=record.delegation_id,
            parent_id=record.parent_id,
            depth=record.depth,
            grant_json=grant_json,
            created_by_agent=record.created_by_agent,
            created_by_user=record.created_by_user,
            created_via=record.created_via,
            created_at=record.created_at,
        )
    )
    back = store.get_delegation(identifier)
    if back is None:
        return failed("grant-json-round-trip", title, "the delegation did not come back")
    if back.grant_json != grant_json:
        return failed(
            "grant-json-round-trip",
            title,
            f"grant_json changed across the store:\n  wrote {grant_json}\n  read  "
            f"{back.grant_json}",
        )
    if back.created_by_user != "ada" or back.created_via != "api":
        return failed("grant-json-round-trip", title, "the creator's identity changed")
    return passed("grant-json-round-trip", title)


# --- clock plumbing -------------------------------------------------------------------------


def _clocked(backend: StoreBackend, clock: Callable[[], datetime]) -> StateStore:
    return backend.open_with_clock(clock)


# --- the registry ---------------------------------------------------------------------------

SUITES: Mapping[str, tuple[Case, ...]] = {
    "reservation": (
        reservation_e1_in_process,
        reservation_e1_cross_process,
        reservation_retry_table,
        reservation_lease_expiry,
    ),
    "approval": (
        approval_binding,
        approval_single_use,
        approval_expiry,
        approval_atomic,
        approval_checked_first,
    ),
    "resolution": (resolution_only_ambiguous, resolution_two_targets),
    "outcome": (outcome_no_failed_on_refusal, outcome_no_not_executed),
    "durability": (durability_ambiguous, durability_terminal),
    "evidence": (evidence_action_round_trip, evidence_event_ids, evidence_receipt),
    "continuation": (continuation_one_resumption, continuation_extend_lease),
    "delegation": (delegation_insert, delegation_revoke, delegation_grant_json),
}


def all_case_ids() -> frozenset[str]:
    return frozenset(item.id for cases in SUITES.values() for item in cases)


def selected(only: Sequence[str]) -> frozenset[str]:
    """Which case ids to run. An unknown name raises (§8 T142)."""
    if not only:
        return all_case_ids()
    unknown = sorted(set(only) - all_case_ids())
    if unknown:
        raise InvalidArgument(
            f"no such conformance case: {', '.join(unknown)}. "
            f"Known cases: {', '.join(sorted(all_case_ids()))}"
        )
    return frozenset(only)

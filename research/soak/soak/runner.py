"""Drive real actions through a real `Control` and injure them on purpose. SPEC-v0.6 §8.1.

The soak is not a load test. It exists to answer one question — *does an `AMBIGUOUS` ever appear
that the harness did not cause?* — because that is `ROADMAP.md`'s exit criterion and the one
number nobody can produce by reasoning about the code.

So every failure is **recorded before it is caused**, and everything else about the run is
arranged to make an unexplained ambiguity visible rather than rare: concurrency across threads,
leases short enough to lapse, and a mixture of outcomes so the store is never in one state long
enough to be quiet.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctrlrun import Control, Policy
from ctrlrun.action import Action, Principal
from ctrlrun.effect import EffectState
from ctrlrun.errors import CTRLRunError, NotExecuted

from .ledger import Ambiguity, Injection, Ledger

POLICY = """
schema: ctrlrun.policy/v4
version: "soak"
actions:
  soak.transfer:
    effect: "soak:{reference}"
    rules:
      - when: {amount_gt: 100000}
        decision: deny
      - decision: allow
"""

#: How the harness injures an attempt, and how often. Weighted so most actions commit: a soak in
#: which everything fails exercises the failure paths and never the quiet ones, and an unexplained
#: ambiguity is likeliest to appear where the store is doing ordinary work.
INJECTIONS: tuple[tuple[str, int], ...] = (
    ("", 70),
    ("executor_timeout", 10),
    ("executor_unknown_exception", 10),
    ("not_executed", 8),
    ("lease_expired", 2),
)


@dataclass
class Progress:
    """What the run has done so far. Read by the reporter; written only by `Runner`."""

    actions: int = 0
    committed: int = 0
    failed: int = 0
    ambiguous: int = 0
    denied: int = 0
    refused: int = 0


class Runner:
    """One soak run against one store."""

    def __init__(
        self,
        *,
        open_store: Callable[[], object],
        ledger: Ledger,
        seed: int = 0,
        control: bool = True,
    ) -> None:
        self._open_store = open_store
        self._ledger = ledger
        self._random = random.Random(seed)
        self._control = control
        self._control_fired = False
        self._lock = threading.Lock()
        self.progress = Progress()

    @property
    def control_fired(self) -> bool:
        return self._control_fired

    def run(self, *, until: datetime, workers: int = 4) -> None:
        """Drive actions until `until`, on `workers` threads."""
        threads = [
            threading.Thread(target=self._work, args=(index, until), daemon=True)
            for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            # No timeout: the workers stop at `until` by themselves, and a join that gave up
            # would leave them writing into a store the reporter is reading.
            thread.join()

    def _work(self, worker: int, until: datetime) -> None:
        store = self._open_store()
        control = Control(Policy.from_yaml(POLICY), store, clock=_now)  # type: ignore[arg-type]
        counter = 0
        while datetime.now(UTC) < until:
            counter += 1
            self._one(control, f"w{worker}-{counter}")
        close = getattr(store, "close", None)
        if close is not None:
            close()

    def _one(self, control: Control, reference: str) -> None:
        amount = self._random.choice([500, 2500, 90000, 250000])
        action = Action(
            name="soak.transfer",
            arguments={"reference": reference, "amount": amount},
            principal=Principal(agent="soak-agent"),
        )
        key = f"soak:{reference}"
        injury = self._pick()

        # **Recorded before it is caused** (§8.1). A crash in between leaves an injection with no
        # ambiguity, which is harmless; recording afterwards would leave an ambiguity with no
        # injection, which is a false finding -- and the harness must not manufacture the very
        # thing it is counting.
        if injury in {"executor_timeout", "executor_unknown_exception", "lease_expired"}:
            self._ledger.record(
                Injection(
                    at=datetime.now(UTC),
                    effect_key=key,
                    action_id=action.action_id,
                    cause=_cause_of(injury),
                )
            )
        if injury == "uncontrolled":
            self._control_fired = True  # injected, deliberately unrecorded

        try:
            control.execute(action, _executor(injury), key, lease=_lease(injury))
        except NotExecuted:
            self._tally(failed=1)
        except CTRLRunError:
            self._tally(refused=1)
        except (TimeoutError, RuntimeError):
            self._tally(ambiguous=1)
        else:
            self._tally(committed=1)
        self._tally(actions=1)

    def _pick(self) -> str:
        if self._control and not self._control_fired and self._random.random() < 0.02:
            return "uncontrolled"
        population = [name for name, _ in INJECTIONS]
        weights = [weight for _, weight in INJECTIONS]
        return self._random.choices(population, weights=weights, k=1)[0]

    def _tally(self, **counts: int) -> None:
        with self._lock:
            for name, value in counts.items():
                setattr(self.progress, name, getattr(self.progress, name) + value)


def _cause_of(injury: str) -> str:
    return injury


def _lease(injury: str) -> timedelta:
    # A lease short enough to lapse while the executor is still working, which is how §5.2's
    # expiry path is reached without waiting for one.
    return timedelta(milliseconds=1) if injury == "lease_expired" else timedelta(minutes=5)


def _executor(injury: str) -> Callable[[], object]:
    def run() -> object:
        if injury == "executor_timeout":
            raise TimeoutError("soak: the response was lost")
        if injury == "executor_unknown_exception":
            raise RuntimeError("soak: the remote answered something nobody parsed")
        if injury == "not_executed":
            raise NotExecuted("soak: the remote refused before acting")
        if injury == "uncontrolled":
            # The positive control: an ambiguity with no ledger entry behind it.
            raise TimeoutError("soak: an ambiguity the harness did not record")
        if injury == "lease_expired":
            time.sleep(0.01)
        return {"ok": True}

    return run


def _now() -> datetime:
    return datetime.now(UTC)


def ambiguities_in(store: object) -> list[Ambiguity]:
    """Every `AMBIGUOUS` record the store holds, with what it says about itself."""
    records = store.list_effects(EffectState.AMBIGUOUS)  # type: ignore[attr-defined]
    return [
        Ambiguity(
            effect_key=record.effect_key,
            action_id=record.action_id,
            error=record.error,
            resolved_by=record.resolved_by,
        )
        for record in records
    ]


def sqlite_store(path: Path) -> Callable[[], object]:
    from ctrlrun import SQLiteStateStore

    def opened() -> object:
        return SQLiteStateStore(path)

    return opened


def postgres_store(url: str, schema: str) -> Callable[[], object]:
    from ctrlrun.postgres import PostgresStateStore

    def opened() -> object:
        return PostgresStateStore(url, schema=schema)

    return opened

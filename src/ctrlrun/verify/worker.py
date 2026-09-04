"""G4's process target. SPEC-v0.4 §2.2, §3.6.

A module-level function taking one picklable argument — a JSON string — because a bound method
over a `Control` is not picklable and `spawn` is the default start method on macOS and Windows.
The child rebuilds the whole composition from paths and plain strings: the same policy
document, the same authority document, a `SQLiteStateStore` on the same scratch file.

It is reached as `python -m ctrlrun.verify.worker`, with the payload on stdin, rather than
through `multiprocessing.Process`. `spawn` re-imports the parent's `__main__` module in every
child, so a caller who ran `ctrlrun.verify.run()` from an unguarded script would fork-bomb
itself — and "put your call under `if __name__ == '__main__'`" is not a requirement a
verification tool gets to impose on the program it is verifying. These are the same eight OS
processes either way, which is the whole of what the guarantee is about.

The children run under the **real** clock. §2.2 says why: a callable is not picklable, and
timing is not what G4 is about — atomicity across processes is. Every other scenario uses the
injected clock of §3.6.

Nothing here opens a socket, and nothing here imports the operator's code (§1.2, §3.7).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

#: What a child reports back, in the file it writes. The parent reads these rather than a
#: return value, because a child that died before returning must still be visible as one.
OUTCOME_COMMITTED = "committed"
OUTCOME_REFUSED = "refused"
OUTCOME_ERROR = "error"


def _count(counter_dir: str) -> None:
    """Record one executor call, in a way that survives a process boundary (§2.2).

    `O_CREAT|O_EXCL` on a name no other attempt can produce: the file is the count, and a
    count kept in process memory would be eight separate zeros.
    """
    name = os.path.join(counter_dir, f"{os.getpid()}-{time.monotonic_ns()}")
    handle = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)


def attempt(payload: str) -> None:
    """Execute one action against the scratch store, and write what happened.

    `payload` is a JSON document rather than a tuple of positional arguments so the shape can
    grow without a signature every caller has to match. Everything in it is picklable by
    construction: it is a string.

    Refusals are expected — seven of eight processes must meet one — so they are recorded and
    never raised. An unexpected exception is recorded too, with its type, because a child that
    vanished silently would look exactly like a child that lost the race.
    """
    request: dict[str, Any] = json.loads(payload)
    result: dict[str, Any] = {"pid": os.getpid(), "index": request["index"]}
    try:
        from ..action import Action, Principal
        from ..approval import LocalApprovalProvider
        from ..authority import Authority
        from ..control import Control
        from ..errors import CTRLRunError
        from ..policy import Policy
        from ..state import SQLiteStateStore

        policy = Policy.from_file(request["policy_path"])
        authority = (
            None
            if request["authority_yaml"] is None
            else Authority.from_yaml(
                request["authority_yaml"],
                source=request["authority_source"],
                standalone=request["authority_standalone"],
            )
        )
        store = SQLiteStateStore(request["store_path"])
        control = Control(
            policy,
            store,
            LocalApprovalProvider(store),
            sinks=(),
            authority=authority,
            environment=request["environment"],
        )
        action = Action(
            name=request["action"],
            arguments=request["arguments"],
            principal=Principal(agent=request["agent"], user=request["user"]),
            resource=request["resource"],
            environment=request["environment"],
        )

        def executor() -> str:
            _count(request["counter_dir"])
            return "ctrlrun-verify"

        try:
            if request["approval_id"] is not None:
                from ..control import with_approval

                with with_approval(request["approval_id"]):
                    control.execute(action, executor, request["effect_key"])
            else:
                control.execute(action, executor, request["effect_key"])
            result["outcome"] = OUTCOME_COMMITTED
        except CTRLRunError as refused:
            result["outcome"] = OUTCOME_REFUSED
            result["refusal"] = type(refused).__name__
            result["message"] = str(refused)
        finally:
            store.close()
    except BaseException as exc:  # recorded, never re-raised: a child reports, it does not throw
        result["outcome"] = OUTCOME_ERROR
        result["refusal"] = type(exc).__name__
        result["message"] = str(exc)
    path = os.path.join(request["result_dir"], f"{request['index']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle)


def main() -> int:
    """`python -m ctrlrun.verify.worker`, with one payload on stdin."""
    import sys

    attempt(sys.stdin.read())
    return 0


__all__ = ["OUTCOME_COMMITTED", "OUTCOME_ERROR", "OUTCOME_REFUSED", "attempt", "main"]


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess by G4
    raise SystemExit(main())

"""One contender in `reservation/e1-cross-process`. SPEC-v0.6 §2.4.

Reached as `python -m ctrlrun.conformance.store.worker` with its payload on stdin -- a
subprocess rather than `multiprocessing`, for `v0.4 §12.5`'s reason: `spawn` re-imports the
caller's `__main__`, and requiring an `if __name__ == "__main__"` guard in a backend author's
test file is not a trade a conformance suite gets to make.

The fake remote is an `O_CREAT|O_EXCL` file, so "called exactly once" survives the process
boundary -- a counter in memory would not, which is the whole reason this case is not the
in-process one.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import timedelta
from typing import Any

from .backends import store_from_url


def call_the_remote(marker_dir: str, action_id: str) -> None:
    """The fake remote. Exclusive creation is the count, and it is visible across processes.

    Raises `FileExistsError` if anything already called it, which is the observable a second
    execution would produce.
    """
    path = os.path.join(marker_dir, "called")
    handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, action_id.encode("utf-8"))
    finally:
        os.close(handle)


def rendezvous(marker_dir: str, contenders: int, deadline: float) -> None:
    """Block until every contender has arrived, or give up (SPEC-v0.6 §2.4).

    **This is the barrier, and without it the case proves nothing.** The parent cannot supply
    one by starting the children together: it writes each child's payload and closes each
    child's stdin, and a child blocks on `stdin.read()` until that happens -- so children
    started "at once" still arrive one at a time. An independent review measured the first
    version of this case and found zero overlap between the eight processes: eight *sequential*
    reservations against one file, which the fake-remote count then passed by serialization
    rather than by the store.

    A file per contender, created with `O_CREAT|O_EXCL`, then spin until the count is reached.
    Bounded: a contender that never arrives makes this return rather than hang, and the case
    fails on the outcome rather than on a timeout -- *a timeout is not a test failure*.
    """
    own = os.path.join(marker_dir, f"arrived-{os.getpid()}")
    os.close(os.open(own, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    while time.monotonic() < deadline:
        if sum(1 for name in os.listdir(marker_dir) if name.startswith("arrived-")) >= contenders:
            return
        time.sleep(0.002)


def contend(payload: dict[str, Any]) -> dict[str, Any]:
    """Reserve, call the remote, commit -- or report the refusal that stopped us."""
    store = store_from_url(payload["url"])
    rendezvous(
        payload["rendezvous_dir"],
        int(payload["contenders"]),
        time.monotonic() + float(payload["rendezvous_timeout"]),
    )
    effect_key = payload["effect_key"]
    action_id = payload["action_id"]
    try:
        try:
            store.reserve_effect(effect_key, action_id, timedelta(minutes=5))
        except Exception as refused:
            return {
                "won": False,
                "action_id": action_id,
                "error": type(refused).__name__,
                "message": str(refused),
            }
        try:
            store.begin_execution(effect_key, action_id)
            call_the_remote(payload["marker_dir"], action_id)
            store.commit_effect(effect_key, action_id, {"ok": True})
        except Exception as broke:
            return {
                "won": True,
                "action_id": action_id,
                "error": type(broke).__name__,
                "message": str(broke),
            }
        return {"won": True, "action_id": action_id, "error": None, "message": None}
    finally:
        # On every path, including the refusal one -- the `finally` used to sit on the second
        # `try`, so a refused contender leaked its handle.
        store.close()


def main() -> int:
    payload = json.loads(sys.stdin.read())
    sys.stdout.write(json.dumps(contend(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

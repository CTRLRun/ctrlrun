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


def contend(payload: dict[str, Any]) -> dict[str, Any]:
    """Reserve, call the remote, commit -- or report the refusal that stopped us."""
    store = store_from_url(payload["url"])
    effect_key = payload["effect_key"]
    action_id = payload["action_id"]
    try:
        store.reserve_effect(effect_key, action_id, timedelta(minutes=5))
    except BaseException as refused:
        return {"won": False, "error": type(refused).__name__, "message": str(refused)}
    try:
        store.begin_execution(effect_key, action_id)
        call_the_remote(payload["marker_dir"], action_id)
        store.commit_effect(effect_key, action_id, {"ok": True})
    except BaseException as broke:
        return {"won": True, "error": type(broke).__name__, "message": str(broke)}
    finally:
        store.close()
    return {"won": True, "error": None, "message": None}


def main() -> int:
    payload = json.loads(sys.stdin.read())
    sys.stdout.write(json.dumps(contend(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A merge that landed, a reply that did not, and a job that goes looking for another way.

The merge queue merges pull request #4471. GitHub merges it, and the connection drops before
the response comes back. The job sees an exception, concludes the merge failed, and does the
two things any determined piece of automation does: it tries again, and when that does not
work it reaches for a different way to get the same result.

Both are refused, for two different reasons, and the second one is the interesting one. The
retry is refused because the effect may already have committed. The alternate route — force
pushing the branch to make the target look merged — is refused because the policy does not
list it, and an action the policy does not list is denied rather than allowed. Determination
is the property that makes automation useful and the property that makes it dangerous.

    python examples/without-an-agent/lost-merge/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctrlrun import (
    ActionDenied,
    AmbiguousEffect,
    Control,
    Policy,
    SQLiteStateStore,
    context,
    protect,
)

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "
REPO = "acme/checkout"
NUMBER = 4471


class FakeGitHub:
    """Merges the pull request, then the connection drops. The merge is done."""

    def __init__(self) -> None:
        self.merged: list[int] = []
        self.force_pushed: list[str] = []

    def merge(self, repo: str, number: int) -> dict[str, Any]:
        self.merged.append(number)  # the branch is merged by the time anything else runs
        raise ConnectionResetError("connection reset by peer while reading the response")

    def force_push(self, repo: str, branch: str) -> dict[str, Any]:
        self.force_pushed.append(branch)
        return {"repo": repo, "branch": branch, "forced": True}


def state_dir(root: Path) -> Path:
    """A store of this example's own, emptied so the example repeats.

    An example that reserved effect keys in a live store would block real work. Only the
    files this script writes are removed, and only by name: never a directory.
    """
    evidence = root / ".ctrlrun" / "examples" / "without-an-agent" / HERE.name
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("state.db", "state.db-wal", "state.db-shm", "receipts.jsonl", "events.jsonl"):
        (evidence / name).unlink(missing_ok=True)
    return evidence


def main(root: Path) -> None:
    store = SQLiteStateStore(state_dir(root) / "state.db")
    github = FakeGitHub()
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)

    @protect("github.merge_pull_request", effect="merge:{repo}:{number}", control=control)
    def merge(repo: str, number: int) -> dict[str, Any]:
        return github.merge(repo, number)

    @protect("git.force_push", effect="branch:{repo}:{branch}", control=control)
    def force_push(repo: str, branch: str) -> dict[str, Any]:
        return github.force_push(repo, branch)

    print("A merge that landed and a reply that did not")
    print(f"   merge {REPO}#{NUMBER}  →  merged at the remote  →  response lost")
    try:
        with context(agent="merge-queue"):
            try:
                merge(repo=REPO, number=NUMBER)
            except ConnectionResetError as lost:
                print(f"   the job saw:         {lost}")

            print("   the job retries the merge")
            try:
                merge(repo=REPO, number=NUMBER)
            except AmbiguousEffect:
                print(f"{BLOCKED}effect may already have committed; blind retry refused")
            except ConnectionResetError as reached:
                # Not the remote's fault and not a lucky escape: the retry was supposed to be
                # refused here, before the call went out. Reaching the remote at all is the
                # failure, whatever the remote then said.
                raise SystemExit(f"the retry reached the remote: {reached}") from reached
            else:
                raise SystemExit("the retry was permitted; the job merged twice")

            print("   the job looks for another way to the same end: force push the branch")
            try:
                force_push(repo=REPO, branch="release")
            except ActionDenied as denied:
                print(f"{BLOCKED}the policy does not list git.force_push ({denied.reason})")
            else:
                raise SystemExit("the alternate route was permitted; the policy was not a list")

        for record in store.list_effects():
            print(f"   effect record:       {record.effect_key}  {record.state}")
        print(f"   remote merges:       {github.merged.count(NUMBER)}")
        print(f"   remote force pushes: {len(github.force_pushed)}")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())

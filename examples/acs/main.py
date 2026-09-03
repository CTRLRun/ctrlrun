#!/usr/bin/env python3
"""The double-refund scenario, through ACS hooks, with CTRLRun behind them.

ACS is advisory: a Guardian returns a decision and the *platform* runs the tool. So one tool
call fires two hooks — `steps/toolCallRequest` before, `steps/toolCallResult` after — and
CTRLRun holds the reservation between them. That is what makes the second attempt refusable:
the first call's effect record is still open when the second arrives.

The remote commits the refund and then the response goes missing. ACS reports that as
`exit_status: "timeout"`, and ACS says nothing about what a timeout means for the side
effect. CTRLRun records it as AMBIGUOUS — the fail-closed reading — and refuses the retry.

    python examples/acs/main.py

No network. Its own state directory, under `.ctrlrun/examples/acs/`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.acs import ACS_VERSION, TOOL_CALL_REQUEST, TOOL_CALL_RESULT, AcsControlHook

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "


class FakeAcsRuntime:
    """The smallest thing that behaves like an ACS host: it fires hooks and obeys verdicts."""

    def __init__(self, hook: AcsControlHook) -> None:
        self._hook = hook
        self.session_id = str(uuid.uuid4())
        self.tool_calls = 0

    def call_tool(self, tool: str, arguments: dict[str, Any], *, lose_response: bool) -> str:
        request_id = str(uuid.uuid4())
        verdict = self._hook.handle(
            self._envelope(
                TOOL_CALL_REQUEST,
                request_id,
                {
                    "tool": {"name": tool, "provider": "stripe"},
                    "arguments": {name: {"value": value} for name, value in arguments.items()},
                },
            )
        )
        decision = verdict.get("result", {}).get("decision")
        if decision != "allow":
            return f"{decision}: {verdict['result'].get('reasoning', '')}"

        # The platform runs the tool. CTRLRun did not, and does not know what happened until
        # the result hook tells it.
        self.tool_calls += 1
        exit_status = "timeout" if lose_response else "success"

        self._hook.handle(
            self._envelope(
                TOOL_CALL_RESULT,
                str(uuid.uuid4()),
                {
                    "tool": {"name": tool, "provider": "stripe"},
                    "exit_status": exit_status,
                    "outputs": [{"value": {"id": f"re_{arguments['payment_id']}"}}],
                    "request_id_ref": request_id,
                },
            )
        )
        return f"allow → the platform ran it → exit_status={exit_status}"

    def _envelope(self, method: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "id": self.tool_calls + 1,
            "params": {
                "acs_version": ACS_VERSION,
                "request_id": request_id,
                "timestamp": "2026-09-04T10:00:00Z",
                "metadata": {
                    "agent_id": "refund-agent",
                    "session_id": self.session_id,
                    "environment": "production",
                    "user_context": {"user_id": "alice", "roles": ["support"]},
                },
                "payload": payload,
            },
        }


def state_dir(root: Path) -> Path:
    """A store of this example's own, emptied so the example repeats."""
    evidence = root / ".ctrlrun" / "examples" / HERE.name
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("state.db", "state.db-wal", "state.db-shm", "receipts.jsonl", "events.jsonl"):
        (evidence / name).unlink(missing_ok=True)
    return evidence


def main(root: Path) -> None:
    store = SQLiteStateStore(state_dir(root) / "state.db")
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
    runtime = FakeAcsRuntime(AcsControlHook(control, prefix="acs"))

    print("Duplicate effect after a lost response — through ACS hooks")
    print("   ACS is advisory: the Guardian decides, the platform executes.")
    try:
        arguments = {"payment_id": "txn_1", "amount": 200}

        first = runtime.call_tool("create_refund", arguments, lose_response=True)
        print(f"   steps/toolCallRequest  →  {first}")
        print("   ACS says nothing about what a timeout means for the side effect.")

        second = runtime.call_tool("create_refund", arguments, lose_response=False)
        if not second.startswith("deny"):
            raise SystemExit("the retry was permitted; this example proved nothing")
        print("   agent retries the identical call  →")
        print(f"{BLOCKED}{second}")

        for record in store.list_effects():
            print(f"   effect record:       {record.effect_key}  {record.state}")
        print(f"   tool calls the platform actually ran: {runtime.tool_calls}")
        print("   only a human moves it on:  ctrlrun resolve refund:txn_1 --committed|--failed")
        print("")
        print("   The decision the Guardian returned, in ACS's own shape:")
        refused = runtime._hook.handle(
            runtime._envelope(
                TOOL_CALL_REQUEST,
                str(uuid.uuid4()),
                {
                    "tool": {"name": "create_refund", "provider": "stripe"},
                    "arguments": {name: {"value": value} for name, value in arguments.items()},
                },
            )
        )
        print("  ", json.dumps(refused["result"], indent=2).replace("\n", "\n   "))
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())

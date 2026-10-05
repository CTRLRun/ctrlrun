#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""A pull request's head moves after a human approved the merge. What merges?

    python research/github-merge-head-race/run.py \\
        --github-mcp-server ./github-mcp-server \\
        --out research/github-merge-head-race/results/$(date +%F).json \\
        --markdown research/github-merge-head-race/results/$(date +%F).md

The stack is real except for GitHub:

    agent ──▶ ctrlrun gateway ──▶ github-mcp-server (http) ──▶ fake GitHub REST (fake_github.py)

The gateway and `ctrlrun approve` are the ones installed beside the interpreter running this
script; github-mcp-server is the binary named on the command line. Both versions are read at
runtime. Every scenario gets a fresh pull request, and every gateway scenario a fresh gateway
process and a fresh SQLite store. Counts come from the fake GitHub and nowhere else.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ctrlrun import SQLiteStateStore

HERE = Path(__file__).resolve().parent
CTRLRUN = Path(sys.executable).parent / "ctrlrun"

APPROVED_HEAD = "a" * 40
PUSHED_HEAD = "b" * 40
PULL = {"owner": "acme", "repo": "checkout", "pullNumber": 4471, "merge_method": "squash"}
EFFECT_KEY = "merge:acme/checkout:4471"
REVISION = "2025-06-18"

PLAIN = """\
schema: ctrlrun.policy/v2
actions:
  mcp.github.merge_pull_request:
    effect: "merge:{owner}/{repo}:{pullNumber}"
    decision: approve
"""

REQUIRE_SHA = """\
schema: ctrlrun.policy/v2
actions:
  mcp.github.merge_pull_request:
    effect: "merge:{owner}/{repo}:{pullNumber}"
    rules:
      - when: { expectedHeadSha_neq: "" }
        decision: approve
      - decision: deny
"""

CLAIM_NOT_EXECUTED = PLAIN + "    mcp: { not_executed_on_error: true }\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"nothing listening on {port} after {timeout}s")


@contextmanager
def process(argv: list[str], port: int, cwd: Path | None = None) -> Iterator[None]:
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_for(port)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=10)


class GitHub:
    """The fake's test endpoints."""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def reset(self) -> None:
        httpx.post(self.base + "/_test/reset")

    def push(self, sha: str) -> None:
        httpx.post(self.base + "/_test/push", json={"sha": sha})

    def drop_next_response(self) -> None:
        httpx.post(self.base + "/_test/drop_next_response")

    def state(self) -> dict[str, Any]:
        return dict(httpx.get(self.base + "/_test/state").json())


class Agent:
    """An MCP client that does what the gateway's documentation says: on `-41002` it waits for
    a human and re-sends the identical `tools/call`."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.next_id = 0
        self.headers = {
            # github-mcp-server refuses a token that is not shaped like one; this one is invented.
            "Authorization": "Bearer ghp_" + "0" * 36,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": REVISION,
        }
        self._post(
            "initialize",
            {
                "protocolVersion": REVISION,
                "capabilities": {},
                "clientInfo": {"name": "merge-agent", "version": "0"},
            },
        )

    def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        body = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        response = httpx.post(self.url, json=body, headers=self.headers, timeout=60)
        text = response.text
        if not text.strip():
            raise RuntimeError(f"{method}: HTTP {response.status_code} with an empty body")
        if "text/event-stream" in response.headers.get("content-type", ""):
            text = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")][-1]
        return dict(json.loads(text))

    def merge(self, **extra: str) -> dict[str, Any]:
        arguments = {**PULL, **extra}
        return self._post("tools/call", {"name": "merge_pull_request", "arguments": arguments})


def answer(message: dict[str, Any]) -> str:
    """What the agent got back, in one line."""
    if "error" in message:
        error = message["error"]
        data = error.get("data") or {}
        return f"{error['code']} {data.get('error', '')}".strip()
    result = message["result"]
    text = result["content"][0]["text"] if result.get("content") else ""
    if result.get("isError"):
        return "isError: " + text.rsplit(": ", 1)[-1].removesuffix(" []")
    return "merged" if '"merged":true' in text else "result: " + text[:60]


class Run:
    """One scenario: a fresh pull request, and a fresh gateway and store where there is one."""

    def __init__(self, github: GitHub, upstream: str, policy: str | None) -> None:
        self.github = github
        self.upstream = upstream
        self.policy = policy
        self.steps: list[str] = []
        self.directory: Path | None = None
        self.tool_calls = 0
        self.evidence: dict[str, Any] = {}
        github.reset()

    @contextmanager
    def agent(self) -> Iterator[Agent]:
        if self.policy is None:
            yield Agent(self.upstream)
            return
        with tempfile.TemporaryDirectory(prefix="ctrlrun-merge-") as directory:
            self.directory = Path(directory)
            (self.directory / "ctrlrun.yaml").write_text(self.policy)
            (self.directory / ".ctrlrun").mkdir()
            port = free_port()
            argv = [
                str(CTRLRUN), "gateway",
                "--upstream", self.upstream,
                "--alias", "github",
                "--principal", "merge-agent",
                "--listen", f"127.0.0.1:{port}",
            ]  # fmt: skip
            with process(argv, port, cwd=self.directory):
                yield Agent(f"http://127.0.0.1:{port}/mcp")
                self.evidence = self._evidence()

    def call(self, label: str, message: dict[str, Any]) -> dict[str, Any]:
        self.tool_calls += 1
        self.steps.append(f"{label} → {answer(message)}")
        return message

    def approve(self, message: dict[str, Any]) -> None:
        request_id = message["error"]["data"]["request_id"]
        subprocess.run(
            [str(CTRLRUN), "approve", request_id],
            cwd=self.directory,
            check=True,
            capture_output=True,
        )
        self.steps.append("a human runs `ctrlrun approve`")

    def resolve_failed(self) -> None:
        subprocess.run(
            [str(CTRLRUN), "resolve", EFFECT_KEY, "--failed"],
            cwd=self.directory,
            check=True,
            capture_output=True,
        )
        self.steps.append(f"a human reads the 409 and runs `ctrlrun resolve {EFFECT_KEY} --failed`")

    def push(self) -> None:
        self.github.push(PUSHED_HEAD)
        self.steps.append(f"the head moves {APPROVED_HEAD[:7]} → {PUSHED_HEAD[:7]}")

    def drop_next_response(self) -> None:
        self.github.drop_next_response()
        self.steps.append("GitHub will merge, then drop the connection")

    def _evidence(self) -> dict[str, Any]:
        """What the gateway's store holds: the effect record, how many actions it proposed, and
        how many receipts it wrote, so a refusal that left nothing behind is counted."""
        assert self.directory is not None
        store = SQLiteStateStore(self.directory / ".ctrlrun" / "state.db")
        try:
            record = store.get_effect(EFFECT_KEY)
            proposed = [e for e in store.events() if e.type.value == "ACTION_PROPOSED"]
            return {
                "effect_record": "none" if record is None else record.state.value,
                "actions_proposed": len(proposed),
                "receipts": len(store.receipts()),
            }
        finally:
            store.close()

    def result(self, name: str, title: str) -> dict[str, Any]:
        state = self.github.state()
        if state["unknown"]:
            raise RuntimeError(f"{name}: the stack asked the fake for {state['unknown']}")
        return {
            "name": name,
            "title": title,
            "path": "direct" if self.policy is None else "gateway",
            "provider_calls": state["provider_calls"],
            "mutations": len(state["mutations"]),
            "merged_at": [sha[:7] for sha in state["mutations"]],
            "tool_calls": self.tool_calls,
            "effect_record": self.evidence.get("effect_record"),
            "actions_proposed": self.evidence.get("actions_proposed"),
            "receipts": self.evidence.get("receipts"),
            "steps": self.steps,
        }


# --- the scenarios ------------------------------------------------------------------------------
#
# The three `direct_*` rows are the control: the same agent talking to github-mcp-server with no
# gateway, so a reader can tell what GitHub does on its own from what the gateway adds.


def direct_head_moves(run: Run) -> None:
    with run.agent() as agent:
        run.push()
        run.call("merge, no expectedHeadSha", agent.merge())


def direct_sha_head_moves(run: Run) -> None:
    with run.agent() as agent:
        run.push()
        run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD))


def direct_lost_response(run: Run) -> None:
    with run.agent() as agent:
        run.drop_next_response()
        run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD))
        run.call("retry", agent.merge(expectedHeadSha=APPROVED_HEAD))


def gateway_head_unchanged(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge, no expectedHeadSha", agent.merge()))
        run.call("the identical call", agent.merge())


def gateway_head_moves(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge, no expectedHeadSha", agent.merge()))
        run.push()
        run.call("the identical call", agent.merge())


def gateway_sha_head_moves(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.push()
        run.call("the identical call", agent.merge(expectedHeadSha=APPROVED_HEAD))
        run.call("the identical call again", agent.merge(expectedHeadSha=APPROVED_HEAD))
        run.call("retarget to @b", agent.merge(expectedHeadSha=PUSHED_HEAD))


def gateway_after_409(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.push()
        run.call("the identical call", agent.merge(expectedHeadSha=APPROVED_HEAD))
        run.resolve_failed()
        run.approve(run.call("merge @b", agent.merge(expectedHeadSha=PUSHED_HEAD)))
        run.call("the identical call", agent.merge(expectedHeadSha=PUSHED_HEAD))


def gateway_sha_changed(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.push()
        run.call("merge @b, which nobody approved", agent.merge(expectedHeadSha=PUSHED_HEAD))


def gateway_lost_response(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.drop_next_response()
        run.call("the identical call", agent.merge(expectedHeadSha=APPROVED_HEAD))
        run.call("retry", agent.merge(expectedHeadSha=APPROVED_HEAD))


def gateway_lost_response_claimed(run: Run) -> None:
    with run.agent() as agent:
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.drop_next_response()
        run.call("the identical call", agent.merge(expectedHeadSha=APPROVED_HEAD))
        retry = run.call("retry", agent.merge(expectedHeadSha=APPROVED_HEAD))
        if retry.get("error", {}).get("code") == -41002:
            run.approve(retry)
            run.call("retry, approved again", agent.merge(expectedHeadSha=APPROVED_HEAD))


def gateway_require_sha(run: Run) -> None:
    with run.agent() as agent:
        run.call("merge, no expectedHeadSha", agent.merge())
        run.approve(run.call("merge @a", agent.merge(expectedHeadSha=APPROVED_HEAD)))
        run.push()
        run.call("the identical call", agent.merge(expectedHeadSha=APPROVED_HEAD))


Scenario = tuple[str, str, str | None, Callable[[Run], None]]

SCENARIOS: list[Scenario] = [
    ("direct-head-moves", "no expectedHeadSha; head moves first", None, direct_head_moves),
    ("direct-sha-head-moves", "expectedHeadSha @a; head moves first", None, direct_sha_head_moves),
    ("direct-lost-response", "merge lands, reply lost; agent retries", None, direct_lost_response),
    ("head-unchanged", "no expectedHeadSha; head unchanged", PLAIN, gateway_head_unchanged),
    ("head-moves", "no expectedHeadSha; head moves after approval", PLAIN, gateway_head_moves),
    (
        "sha-head-moves",
        "expectedHeadSha @a; head moves after approval",
        PLAIN,
        gateway_sha_head_moves,
    ),
    (
        "after-409",
        "after the 409: resolve, approve the new head, merge",
        PLAIN,
        gateway_after_409,
    ),
    ("sha-changed", "agent changes expectedHeadSha after approval", PLAIN, gateway_sha_changed),
    ("lost-response", "merge lands, reply lost; agent retries", PLAIN, gateway_lost_response),
    (
        "lost-response-claimed",
        "as above, with `mcp: {not_executed_on_error: true}`",
        CLAIM_NOT_EXECUTED,
        gateway_lost_response_claimed,
    ),
    ("require-sha", "policy requires expectedHeadSha", REQUIRE_SHA, gateway_require_sha),
]


def versions(binary: Path) -> dict[str, str]:
    banner = subprocess.run([str(binary), "--version"], capture_output=True, text=True).stdout
    fields = dict(line.split(": ", 1) for line in banner.splitlines() if ": " in line)
    return {
        "ctrlrun": importlib.metadata.version("ctrlrun"),
        "github-mcp-server": fields.get("Version", "unknown"),
        "github-mcp-server commit": fields.get("Commit", "unknown"),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "mcp revision (client)": REVISION,
    }


def markdown(document: dict[str, Any]) -> str:
    lines = [f"# github-merge-head-race, {document['date']}", ""]
    lines += [f"- **{key}**: `{value}`" for key, value in document["versions"].items()]
    lines += [
        "",
        "| Path | Scenario | Provider calls | Mutations | Effect record "
        "| Tool calls / proposed / receipts |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in document["rows"]:
        merged = str(row["mutations"])
        if row["merged_at"]:
            merged += f" ({', '.join(row['merged_at'])})"
        evidence = "—"
        if row["path"] == "gateway":
            evidence = f"{row['tool_calls']} / {row['actions_proposed']} / {row['receipts']}"
        lines.append(
            f"| {row['path']} | {row['title']} | {row['provider_calls']} | {merged} "
            f"| {row['effect_record'] or '—'} | {evidence} |"
        )
    lines.append("")
    for row in document["rows"]:
        lines += [f"## {row['path']}: {row['title']}", ""]
        lines += [f"{n}. {step}" for n, step in enumerate(row["steps"], 1)]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--github-mcp-server", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown", type=Path)
    options = parser.parse_args()

    fake_port, mcp_port = free_port(), free_port()
    github = GitHub(fake_port)
    upstream = f"http://127.0.0.1:{mcp_port}/mcp"
    fake = [sys.executable, str(HERE / "fake_github.py"), str(fake_port)]
    server = [
        str(options.github_mcp_server), "http",
        "--gh-host", f"http://127.0.0.1:{fake_port}",
        "--port", str(mcp_port),
        "--listen-host", "127.0.0.1",
        "--toolsets", "pull_requests",
    ]  # fmt: skip
    rows = []
    with ExitStack() as stack:
        stack.enter_context(process(fake, fake_port))
        stack.enter_context(process(server, mcp_port))
        for name, title, policy, scenario in SCENARIOS:
            run = Run(github, upstream, policy)
            scenario(run)
            rows.append(run.result(name, title))

    document = {
        "date": datetime.now(UTC).date().isoformat(),
        "versions": versions(options.github_mcp_server),
        "rows": rows,
    }
    rendered = markdown(document)
    if options.out:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(json.dumps(document, indent=2) + "\n")
    if options.markdown:
        options.markdown.parent.mkdir(parents=True, exist_ok=True)
        options.markdown.write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    main()

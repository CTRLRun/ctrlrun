"""Run every fenced block marked `runnable`, offline, and fail on the first sentence that lies.

A code sample a reader pastes and watches fail is the most expensive sentence in the
documentation, so a sample is either runnable and run here, or it carries no marker and makes
no promise. The marker is a word on the fence's info string:

    ```python runnable
    ```bash runnable
    ```yaml runnable

Rules, stated once here and in `docs/STYLE.md`:

- Every runnable block in one document runs in **one temporary directory**, in document order,
  so a `yaml runnable` policy written early is the `ctrlrun.yaml` a later block reads.
- A `python runnable` block is its own script. `python runnable continue` is appended to the
  previous runnable Python blocks of the same document and the whole is run again, which is
  how a page defines a function in one block and calls it in the next without repeating it.
- A `bash runnable` block runs under `bash -euo pipefail`, with this interpreter's `bin/` on
  `PATH` so `ctrlrun …` is the checkout's CLI.
- A `yaml runnable` block is loaded with `Policy.from_yaml`, and with `Authority.from_yaml`
  when it carries an `authority:` section, and is then written to `ctrlrun.yaml` — or to the
  name given by a `file=<name>` token, which any block may carry.
- **No network.** Every subprocess gets a `sitecustomize` that refuses sockets, name resolution
  and connections, the same guard `tests/test_examples.py` puts under the examples. A snippet
  that reaches for the network fails here rather than in a reader's terminal, where it would
  fail differently.

Exit status is the number of failures, capped at 1.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from _files import DOCUMENT_PATTERNS, Fence, documents, fences, relative

RUNNABLE = "runnable"
LANGUAGES = frozenset({"python", "bash", "yaml"})
TIMEOUT_SECONDS = 120

#: Installed as `sitecustomize` on the subprocess's `PYTHONPATH`. Replacing the socket *type*
#: with a function breaks anything that subclasses it — `ssl` does — so the refusal goes on
#: the operations, exactly as `tests/test_examples.py` does it.
NO_NETWORK = """\
import socket

_real = socket.socket


class _Refusing(_real):
    def connect(self, *args, **kwargs):
        raise RuntimeError("a documentation snippet tried to connect; snippets run offline")

    def connect_ex(self, *args, **kwargs):
        raise RuntimeError("a documentation snippet tried to connect; snippets run offline")


def _refuse(*args, **kwargs):
    raise RuntimeError("a documentation snippet tried to resolve a name; snippets run offline")


socket.socket = _Refusing
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
"""


@dataclass(frozen=True)
class Failure:
    location: str
    language: str
    message: str

    def __str__(self) -> str:
        return f"{self.location} ({self.language}): {self.message}"


@dataclass(frozen=True)
class Outcome:
    ran: int
    failures: tuple[Failure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def runnable_fences(path: Path) -> list[Fence]:
    text = path.read_text(encoding="utf-8")
    return [
        fence
        for fence in fences(text, path)
        if RUNNABLE in fence.tokens and fence.language in LANGUAGES
    ]


def run_document(path: Path, *, guard: Path | None = None) -> Outcome:
    """Run every runnable block of one document, in order, in one temporary directory."""
    blocks = runnable_fences(path)
    if not blocks:
        return Outcome(0, ())
    with tempfile.TemporaryDirectory(prefix="docs-snippet-") as scratch:
        workdir = Path(scratch) / "work"
        workdir.mkdir()
        if guard is None:
            guard = Path(scratch) / "guard"
            guard.mkdir()
            (guard / "sitecustomize.py").write_text(NO_NETWORK, encoding="utf-8")
        environment = _environment(guard)
        failures: list[Failure] = []
        python_so_far: list[str] = []
        for fence in blocks:
            if fence.language == "python":
                if "continue" in fence.tokens:
                    source = "\n".join([*python_so_far, fence.body])
                else:
                    python_so_far = []
                    source = fence.body
                python_so_far.append(fence.body)
                failure = _run_python(fence, source, workdir, environment)
            elif fence.language == "bash":
                failure = _run_bash(fence, workdir, environment)
            else:
                failure = _load_yaml(fence, workdir)
            if failure is not None:
                failures.append(failure)
        return Outcome(len(blocks), tuple(failures))


def run_documents(paths: Iterable[Path]) -> Outcome:
    ran = 0
    failures: list[Failure] = []
    with tempfile.TemporaryDirectory(prefix="docs-snippet-guard-") as scratch:
        guard = Path(scratch)
        (guard / "sitecustomize.py").write_text(NO_NETWORK, encoding="utf-8")
        for path in paths:
            outcome = run_document(path, guard=guard)
            ran += outcome.ran
            failures.extend(outcome.failures)
    return Outcome(ran, tuple(failures))


def _environment(guard: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(guard), environment.get("PYTHONPATH", "")) if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), environment.get("PATH", "")]
    )
    # A snippet decides its own policy and store; the operator's must not leak in.
    for name in ("CTRLRUN_CONFIG", "CTRLRUN_STATE", "CTRLRUN_STORE_URL"):
        environment.pop(name, None)
    return environment


def _file_token(fence: Fence, default: str) -> str:
    for token in fence.tokens:
        if token.startswith("file="):
            return token[len("file=") :]
    return default


def _run_python(
    fence: Fence, source: str, workdir: Path, environment: Mapping[str, str]
) -> Failure | None:
    script = workdir / _file_token(fence, f"snippet_{fence.line}.py")
    script.write_text(source, encoding="utf-8")
    return _run([sys.executable, str(script)], fence, workdir, environment)


def _run_bash(fence: Fence, workdir: Path, environment: Mapping[str, str]) -> Failure | None:
    script = workdir / _file_token(fence, f"snippet_{fence.line}.sh")
    script.write_text(fence.body, encoding="utf-8")
    return _run(["bash", "-euo", "pipefail", str(script)], fence, workdir, environment)


def _run(
    command: list[str], fence: Fence, workdir: Path, environment: Mapping[str, str]
) -> Failure | None:
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=dict(environment),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Failure(fence.location, fence.language, f"did not finish in {TIMEOUT_SECONDS}s")
    except OSError as exc:
        return Failure(fence.location, fence.language, f"could not start: {exc}")
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-8:]
        return Failure(
            fence.location,
            fence.language,
            f"exit {completed.returncode}\n    " + "\n    ".join(tail),
        )
    return None


def _load_yaml(fence: Fence, workdir: Path) -> Failure | None:
    import yaml

    from ctrlrun import Authority, Policy
    from ctrlrun.errors import CTRLRunError

    try:
        document = yaml.safe_load(fence.body)
    except yaml.YAMLError as exc:
        return Failure(fence.location, fence.language, f"not valid YAML: {exc}")
    if not isinstance(document, Mapping):
        return Failure(fence.location, fence.language, "a runnable YAML block is a document")
    try:
        if "actions" in document:
            Policy.from_yaml(fence.body, source=fence.location)
        if "authority" in document:
            Authority.from_yaml(
                fence.body, source=fence.location, standalone="actions" not in document
            )
        if "actions" not in document and "authority" not in document:
            return Failure(
                fence.location,
                fence.language,
                "neither a policy (`actions:`) nor an authority document (`authority:`)",
            )
    except CTRLRunError as exc:
        return Failure(fence.location, fence.language, f"{type(exc).__name__}: {exc}")
    (workdir / _file_token(fence, "ctrlrun.yaml")).write_text(fence.body, encoding="utf-8")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="documents to run; default: all")
    parser.add_argument("--list", action="store_true", help="list runnable blocks and exit")
    arguments = parser.parse_args(argv)
    paths = [p.resolve() for p in arguments.paths] or documents(patterns=DOCUMENT_PATTERNS)
    if arguments.list:
        for path in paths:
            for fence in runnable_fences(path):
                print(f"{fence.location} {fence.language} {' '.join(fence.tokens)}")
        return 0
    outcome = run_documents(paths)
    for failure in outcome.failures:
        print(f"FAIL {failure}")
    scope = ", ".join(relative(p) for p in paths) if len(paths) <= 3 else f"{len(paths)} documents"
    print(f"snippets: {outcome.ran} runnable block(s) in {scope}, {len(outcome.failures)} failed")
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())

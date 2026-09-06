"""What the audit reads, and how it reads a fenced block.

The set of documents is taken from **git**, not from the filesystem, for the reason the
packaging tests give: a file that exists on disk and is not tracked is not in a fresh clone,
and a check that passed against it has checked nothing a reader will see. Outside a checkout
the audit falls back to a glob, so the same scripts run from an sdist.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The documents a reader is sent to. Specs, the roadmap and the changelog are deliberately
#: not here: they are the historical record, and the audit is about the pages that describe
#: the shipped version to a stranger. Each check may narrow this further.
DOCUMENT_PATTERNS: tuple[str, ...] = (
    "README.md",
    "docs/*.md",
    "docs/**/*.md",
    "docs/**/*.mdx",
    "adapters/*/README.md",
    "examples/**/*.md",
)


def tracked_files(root: Path = REPO_ROOT) -> list[Path] | None:
    """Every path git tracks under `root`, or `None` outside a checkout."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if listed.returncode != 0:
        return None
    return [root / name for name in listed.stdout.decode("utf-8").split("\0") if name]


def documents(
    root: Path = REPO_ROOT,
    patterns: Iterable[str] = DOCUMENT_PATTERNS,
    exclude: Iterable[str] = (),
) -> list[Path]:
    """The documents matching `patterns` relative to `root`, tracked by git where possible."""
    patterns = tuple(patterns)
    exclude = tuple(exclude)
    candidates = tracked_files(root)
    if candidates is None:
        candidates = sorted({path for pattern in patterns for path in root.glob(pattern)})
    chosen: list[Path] = []
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if not any(_match(relative, pattern) for pattern in patterns):
            continue
        if any(_match(relative, pattern) for pattern in exclude):
            continue
        if path.is_file():
            chosen.append(path)
    return sorted(set(chosen))


def _match(relative: str, pattern: str) -> bool:
    """`fnmatch` with `**` meaning any depth, which `fnmatch` alone does not give."""
    if "**" not in pattern:
        return fnmatch(relative, pattern)
    head, _, tail = pattern.partition("**/")
    if not relative.startswith(head):
        return False
    remainder = relative[len(head) :]
    return any(fnmatch(part, tail) for part in _suffixes(remainder))


def _suffixes(relative: str) -> Iterator[str]:
    parts = relative.split("/")
    for index in range(len(parts)):
        yield "/".join(parts[index:])


@dataclass(frozen=True)
class Fence:
    """One fenced code block: where it is, what its info string says, and its body."""

    path: Path
    line: int
    language: str
    tokens: tuple[str, ...]
    body: str

    @property
    def location(self) -> str:
        return f"{relative(self.path)}:{self.line}"


_OPEN = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>[^`]*)$")


def fences(text: str, path: Path) -> Iterator[Fence]:
    """Every fenced block in `text`, with 1-based line numbers for its opening fence.

    An opening fence closes at the first line consisting of the same fence character, at least
    as long, and nothing else — which is CommonMark's rule and also what GitHub renders.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        opened = _OPEN.match(lines[index])
        if opened is None:
            index += 1
            continue
        fence = opened.group("fence")
        indent = len(opened.group("indent"))
        info = opened.group("info").strip().split()
        language = info[0].lower() if info else ""
        tokens = tuple(info[1:])
        start = index
        index += 1
        body: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                break
            body.append(_dedent(lines[index], indent))
            index += 1
        yield Fence(path, start + 1, language, tokens, "\n".join(body) + "\n")
        index += 1


def _dedent(line: str, indent: int) -> str:
    """Strip up to `indent` leading spaces: a fence opened inside a list item or a component
    is indented, and CommonMark removes that indentation from its content lines."""
    removable = len(line) - len(line.lstrip(" "))
    return line[min(removable, indent) :]


def outside_fences(text: str) -> Iterator[tuple[int, str]]:
    """Every (1-based line number, line) that is not inside a fenced block."""
    lines = text.splitlines()
    fence: str | None = None
    for number, line in enumerate(lines, start=1):
        if fence is None:
            opened = _OPEN.match(line)
            if opened is not None:
                fence = opened.group("fence")
                continue
            yield number, line
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else str(path)

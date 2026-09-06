"""Render the badge row, and write the one badge this repository produces itself.

A stranger reads the badges before the first sentence, so the row is a claim like any other and
is generated rather than typed. One list, two renders: the README's centred HTML and the plain
Markdown the docs site uses. A badge added in one place and forgotten in the other is the
drift this removes.

    python tools/docs_audit/render_badges.py readme     # print one render
    python tools/docs_audit/render_badges.py --write    # refresh docs/generated/
    python tools/docs_audit/render_badges.py --check    # CI

Six of the seven badges are rendered by somebody else — PyPI, GitHub Actions, OpenSSF. Two are
this repository's own, published to the orphan `badges` branch by a job that runs only on a
push to `main`:

- `verify-badge.json`, written by the composite action from a real `ctrlrun verify` run;
- `tests-badge.json`, written by `--write-count` **after** the suite has passed, so the number
  is the size of a suite that went green and not of one that merely collected.

`--write-count` is what CI calls. It counts what `pytest` collects, which equals what it ran
because the step is ordered after the run; the badge says a count and never says "passing",
because a count is what was measured.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from _files import REPO_ROOT, documents, relative
from render_readiness import collected

GENERATED = REPO_ROOT / "docs" / "generated"
FILENAMES = {"readme": "badges.readme.md", "mdx": "badges.mdx"}
FORMATS = tuple(FILENAMES)
BADGES_BRANCH = "https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges"
_OPEN = re.compile(r"generated from tools/docs_audit/render_badges\.py \((?P<format>[a-z]+)\)")
_CLOSE = re.compile(r"end generated")


@dataclass(frozen=True)
class Badge:
    """One badge: what it shows, where the image comes from, and what it links to."""

    alt: str
    image: str
    href: str


#: The row, in reading order: what it is, that it builds, that its own suite is this big, that
#: its guarantees were checked, how its supply chain scores, and the licence.
BADGES: tuple[Badge, ...] = (
    Badge(
        "PyPI",
        "https://img.shields.io/pypi/v/ctrlrun?color=B8730A&label=pypi",
        "https://pypi.org/project/ctrlrun/",
    ),
    Badge(
        "Python versions",
        "https://img.shields.io/pypi/pyversions/ctrlrun?color=B8730A",
        "https://pypi.org/project/ctrlrun/",
    ),
    Badge(
        "CI",
        "https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml/badge.svg?branch=main",
        "https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml",
    ),
    Badge(
        "Tests",
        f"https://img.shields.io/endpoint?url={BADGES_BRANCH}/tests-badge.json",
        "https://docs.ctrlrun.dev/how-this-is-built",
    ),
    Badge(
        "CTRLRun verified",
        f"https://img.shields.io/endpoint?url={BADGES_BRANCH}/verify-badge.json",
        "https://docs.ctrlrun.dev/security/verify-guarantees",
    ),
    Badge(
        "OpenSSF Scorecard",
        "https://api.scorecard.dev/projects/github.com/CTRLRun/ctrlrun/badge",
        "https://scorecard.dev/viewer/?uri=github.com/CTRLRun/ctrlrun",
    ),
    Badge(
        "License",
        "https://img.shields.io/pypi/l/ctrlrun?color=B8730A",
        "https://github.com/CTRLRun/ctrlrun/blob/main/LICENSE",
    ),
)


def tests_badge(count: int) -> dict[str, object]:
    """The shields.io endpoint document for the test-count badge."""
    return {
        "schemaVersion": 1,
        "label": "tests",
        "message": f"{count:,}",
        "color": "B8730A",
    }


def _readme() -> list[str]:
    lines = ['<p align="center">']
    for badge in BADGES:
        lines.append(f'  <a href="{badge.href}"><img src="{badge.image}" alt="{badge.alt}"></a>')
    lines.append("</p>")
    return lines


def _mdx() -> list[str]:
    return [" ".join(f"[![{b.alt}]({b.image})]({b.href})" for b in BADGES)]


def render(fmt: str) -> str:
    body = _readme() if fmt == "readme" else _mdx()
    comment = f"generated from tools/docs_audit/render_badges.py ({fmt}) — edit the list, not this"
    if fmt == "readme":
        return "\n".join([f"<!-- {comment} -->", *body, "<!-- end generated -->"]) + "\n"
    return "\n".join(["{/* " + comment + " */}", *body, "{/* end generated */}"]) + "\n"


def marker_blocks(text: str) -> list[tuple[int, str, str]]:
    lines = text.splitlines()
    blocks: list[tuple[int, str, str]] = []
    index = 0
    while index < len(lines):
        opened = _OPEN.search(lines[index])
        if opened is None:
            index += 1
            continue
        start, fmt = index, opened.group("format")
        index += 1
        while index < len(lines) and not _CLOSE.search(lines[index]):
            index += 1
        if index >= len(lines):
            blocks.append((start + 1, fmt, ""))
            break
        blocks.append((start + 1, fmt, "\n".join(lines[start : index + 1]) + "\n"))
        index += 1
    return blocks


def check(pages: list[Path] | None = None) -> list[str]:
    drift: list[str] = []
    for fmt in FORMATS:
        target = GENERATED / FILENAMES[fmt]
        if not target.exists() or target.read_text(encoding="utf-8") != render(fmt):
            drift.append(f"{relative(target)} differs from the generator; run --write")
    if pages is None:
        pages = documents(patterns=("README.md", "docs/**/*.md", "docs/**/*.mdx"))
    for page in pages:
        if page.parent == GENERATED:
            continue
        for line, fmt, embedded in marker_blocks(page.read_text(encoding="utf-8")):
            if fmt not in FORMATS:
                drift.append(f"{relative(page)}:{line}: unknown badge format {fmt!r}")
            elif embedded != render(fmt):
                drift.append(f"{relative(page)}:{line}: badge row differs; paste it fresh")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("format", nargs="?", choices=FORMATS)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--write-count",
        metavar="PATH",
        help="write the shields.io endpoint document for the test-count badge",
    )
    arguments = parser.parse_args(argv)
    if arguments.write_count:
        count = collected()
        Path(arguments.write_count).write_text(
            json.dumps(tests_badge(count), indent=2) + "\n", encoding="utf-8"
        )
        print(f"tests badge: {count:,}")
        return 0
    if arguments.format:
        sys.stdout.write(render(arguments.format))
        return 0
    if arguments.write:
        GENERATED.mkdir(parents=True, exist_ok=True)
        for fmt in FORMATS:
            (GENERATED / FILENAMES[fmt]).write_text(render(fmt), encoding="utf-8")
        print(f"badges: {len(BADGES)} written")
    if arguments.check or not arguments.write:
        drift = check()
        for item in drift:
            print(f"DRIFT {item}")
        print(f"badges: {len(drift)} drifted")
        return 0 if not drift else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

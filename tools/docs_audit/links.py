"""Internal links and anchors resolve.

Checked, with no network:

- `[text](relative/path.md)` and `[text](relative/path.md#anchor)`, resolved against the
  linking file's directory;
- `[text](#anchor)`, against the linking file's own headings;
- `[text](/path)` and `href="/path"` — the docs site's own root-relative form, resolved against
  `docs/` with `.mdx` then `.md` appended, and against the repository root as a fallback;
- `https://github.com/CTRLRun/ctrlrun/blob/<ref>/<path>` and `/tree/<ref>/<path>`, which are
  internal links wearing an absolute URL, resolved against the checkout.

Every other absolute URL is skipped: an external link is somebody else's to keep, and a check
that opened sockets would be a check that failed in CI for reasons nobody here can fix.

Anchors use GitHub's slug rule — lowercase, formatting stripped, punctuation removed, spaces to
hyphens, duplicates suffixed `-1`, `-2` — which is also close enough to Mintlify's for the
headings this repository writes. An explicit `{#id}` on a heading and an `id="…"` attribute
inside the page are accepted as well.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from _files import DOCUMENT_PATTERNS, REPO_ROOT, documents, outside_fences, relative

#: A render under `docs/generated/` is a fragment, embedded into a page by a later session and
#: never published on its own. Its links are checked on the page that embeds it, where they
#: either resolve or fail with that page — and until a page embeds it, a link to a page not
#: yet written is a plan, not a broken link.
EXCLUDED: tuple[str, ...] = ("docs/generated/*",)


def documents_to_check() -> list[Path]:
    return documents(patterns=DOCUMENT_PATTERNS, exclude=EXCLUDED)


_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HREF = re.compile(r"""href=["']([^"']+)["']""")
_GITHUB = re.compile(r"^https://github\.com/CTRLRun/ctrlrun/(?:blob|tree)/[^/]+/(.*)$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_EXPLICIT_ID = re.compile(r"\{#([A-Za-z0-9_-]+)\}\s*$")
_ID_ATTRIBUTE = re.compile(r"""\bid=["']([A-Za-z0-9_-]+)["']""")


_IA = REPO_ROOT / "docs" / "IA.md"


def planned_pages() -> frozenset[str]:
    """Every site path `docs/IA.md` lists. A link to one that does not exist yet is a plan the
    next session owes, reported as such and not as broken; the launch audit has to drive the
    count to zero, and a link to a path the IA never named is broken today."""
    if not _IA.exists():
        return frozenset()
    text = _IA.read_text(encoding="utf-8")
    return frozenset(
        re.findall(r"(?m)^\s*[├└│─\s]*[^`\n]*?\s{2,}([a-z0-9][a-z0-9/-]*)\s*(?:\(.*\))?\s*$", text)
    ) | frozenset(re.findall(r"`([a-z0-9][a-z0-9/-]*)`", text))


@dataclass(frozen=True)
class Broken:
    path: str
    line: int
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.target} — {self.reason}"


def slug(heading: str) -> str:
    """GitHub's heading slug: what `#anchor` has to match."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = _EXPLICIT_ID.sub("", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    seen: dict[str, int] = {}
    found: set[str] = set()
    for _, line in outside_fences(text):
        matched = _HEADING.match(line)
        if matched is not None:
            explicit = _EXPLICIT_ID.search(matched.group(2))
            if explicit is not None:
                found.add(explicit.group(1))
            base = slug(matched.group(2))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f"{base}-{count}")
        for attribute in _ID_ATTRIBUTE.findall(line):
            found.add(attribute)
    return found


def links(text: str) -> Iterator[tuple[int, str]]:
    for number, line in outside_fences(text):
        for target in _MARKDOWN_LINK.findall(line):
            yield number, target
        for target in _HREF.findall(line):
            yield number, target


def _resolve(target: str, source: Path) -> tuple[Path | None, str | None] | None:
    """The file a target names and its anchor, or `None` when it is not ours to check."""
    if target.startswith(("mailto:", "tel:")):
        return None
    path_part, _, anchor = target.partition("#")
    anchor = unquote(anchor) or None
    # A query string is not part of the path. `/try?situation=uncertain` is the same page as
    # `/try`, and the site serves it that way; a checker that kept the query looked for a file
    # named after the whole string and reported a working link as broken.
    path_part = path_part.partition("?")[0]
    if path_part.startswith(("http://", "https://")):
        matched = _GITHUB.match(path_part)
        if matched is None:
            return None
        return REPO_ROOT / unquote(matched.group(1)), anchor
    if not path_part:
        return source, anchor
    path_part = unquote(path_part)
    if path_part.startswith("/"):
        for candidate in (
            REPO_ROOT / "docs" / (path_part.lstrip("/") + ".mdx"),
            REPO_ROOT / "docs" / (path_part.lstrip("/") + ".md"),
            REPO_ROOT / "docs" / path_part.lstrip("/"),
            REPO_ROOT / path_part.lstrip("/"),
        ):
            if candidate.exists():
                return candidate, anchor
        return REPO_ROOT / "docs" / (path_part.lstrip("/") + ".mdx"), anchor
    return (source.parent / path_part).resolve(), anchor


PLANNED: list[Broken] = []


def check_text(text: str, source: Path) -> list[Broken]:
    """Broken links in one page. Links to planned pages are collected in `PLANNED` instead."""
    broken: list[Broken] = []
    planned = PLANNED
    name = relative(source)
    own_anchors: set[str] | None = None
    for number, target in links(text):
        resolved = _resolve(target, source)
        if resolved is None:
            continue
        path, anchor = resolved
        if path is None:
            continue
        if not path.exists():
            site_path = target.partition("#")[0].partition("?")[0].lstrip("/")
            if target.startswith("/") and site_path in planned_pages():
                planned.append(
                    Broken(name, number, target, "planned in docs/IA.md, not written yet")
                )
                continue
            broken.append(Broken(name, number, target, f"{relative(path)} does not exist"))
            continue
        if anchor is None or path.is_dir():
            continue
        if path.suffix.lower() not in {".md", ".mdx"}:
            continue
        if path == source:
            if own_anchors is None:
                own_anchors = anchors(text)
            available = own_anchors
        else:
            available = anchors(path.read_text(encoding="utf-8"))
        if anchor not in available:
            broken.append(Broken(name, number, target, f"no heading #{anchor} in {relative(path)}"))
    return broken


def check_paths(paths: Iterable[Path]) -> list[Broken]:
    broken: list[Broken] = []
    for path in paths:
        broken.extend(check_text(path.read_text(encoding="utf-8"), path))
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="documents to check; default: all")
    arguments = parser.parse_args(argv)
    paths = [p.resolve() for p in arguments.paths] or documents_to_check()
    PLANNED.clear()
    broken = check_paths(paths)
    for item in broken:
        print(item)
    for item in PLANNED:
        print(f"PLANNED {item}")
    print(f"links: {len(paths)} document(s), {len(broken)} broken, {len(PLANNED)} planned")
    return 0 if not broken else 1


if __name__ == "__main__":
    sys.exit(main())

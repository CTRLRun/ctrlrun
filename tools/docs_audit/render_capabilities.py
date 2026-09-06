"""Render `docs/capabilities.yaml` three ways, and refuse a rendered copy that drifted.

One source, several renders. The README's capability matrix, the docs home page's capability
grid and the plain-text list PyPI and directory listings carry are never edited by hand: each
is the output of this script for one `format`, and `--check` fails when any of them differs.

    python tools/docs_audit/render_capabilities.py readme      # print one render
    python tools/docs_audit/render_capabilities.py --write     # refresh docs/generated/
    python tools/docs_audit/render_capabilities.py --check     # CI

`--check` compares two things:

1. the three files under `docs/generated/`, byte for byte;
2. every **marker block** in `README.md` and under `docs/`, which is how a page embeds a render
   in place. A block opens with the generated-comment line this script emits — it names the
   format — and closes with an `end generated` comment. What lies between must equal the render.

    <!-- generated from docs/capabilities.yaml (readme) — edit the YAML, never this table -->
    | Guarantee | … |
    <!-- end generated -->

MDX pages use `{/* … */}` for both lines.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from _files import REPO_ROOT, documents, relative

SOURCE = REPO_ROOT / "docs" / "capabilities.yaml"
GENERATED = REPO_ROOT / "docs" / "generated"
FORMATS: tuple[str, ...] = ("readme", "mdx", "text")
FILENAMES: Mapping[str, str] = {
    "readme": "capabilities.readme.md",
    "mdx": "capabilities.mdx",
    "text": "capabilities.txt",
}
WAYS_IN: tuple[str, ...] = ("decorator", "gateway", "adapter")
WAY_LABELS: Mapping[str, str] = {
    "decorator": "`@protect`",
    "gateway": "Gateway",
    "adapter": "Adapter",
}
VERSIONS: frozenset[str] = frozenset({"v0.1", "v0.2", "v0.3", "v0.4", "v0.5", "v0.6"})
MAX_DESCRIPTION_WORDS = 15

_OPEN_COMMENT = (
    "generated from docs/capabilities.yaml ({format}) — edit the YAML, never this {what}"
)
_MARKER_OPEN = re.compile(r"generated from docs/capabilities\.yaml \((?P<format>[a-z]+)\)")
_MARKER_CLOSE = re.compile(r"end generated")


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    description: str
    guarantee: bool
    ways_in: Mapping[str, bool | str]
    since: str
    page: str
    claim: str | None
    claim_note: str | None


class CapabilitiesError(ValueError):
    """`docs/capabilities.yaml` says something the generator refuses to render."""


def load(path: Path = SOURCE) -> tuple[Capability, ...]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or not isinstance(document.get("capabilities"), list):
        raise CapabilitiesError(f"{path}: expected a mapping with a `capabilities:` list")
    entries: list[Capability] = []
    seen: set[str] = set()
    for index, raw in enumerate(document["capabilities"]):
        where = f"{path}: capabilities[{index}]"
        entries.append(_parse(raw, where, seen))
    guarantees = [entry for entry in entries if entry.guarantee]
    if len(guarantees) != 6:
        raise CapabilitiesError(
            f"{path}: {len(guarantees)} entries carry `guarantee: true`; the README matrix has "
            "exactly six rows, one per group of the verify catalogue"
        )
    return tuple(entries)


def _parse(raw: object, where: str, seen: set[str]) -> Capability:
    if not isinstance(raw, Mapping):
        raise CapabilitiesError(f"{where}: not a mapping")
    required = {"id", "name", "description", "guarantee", "ways_in", "since", "page", "claim"}
    allowed = required | {"claim_note"}
    missing = required - set(raw)
    unknown = set(raw) - allowed
    if missing or unknown:
        raise CapabilitiesError(f"{where}: missing {sorted(missing)}, unknown {sorted(unknown)}")
    identifier = raw["id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", identifier):
        raise CapabilitiesError(f"{where}: id must be kebab-case, got {identifier!r}")
    if identifier in seen:
        raise CapabilitiesError(f"{where}: duplicate id {identifier!r}")
    seen.add(identifier)
    description = raw["description"]
    if not isinstance(description, str) or not description.strip():
        raise CapabilitiesError(f"{where}: description must be a non-empty string")
    words = len(description.split())
    if words > MAX_DESCRIPTION_WORDS:
        raise CapabilitiesError(
            f"{where}: description is {words} words; the limit is {MAX_DESCRIPTION_WORDS}"
        )
    if description.rstrip().endswith("!"):
        raise CapabilitiesError(f"{where}: no exclamation marks (docs/STYLE.md)")
    ways = raw["ways_in"]
    if not isinstance(ways, Mapping) or set(ways) != set(WAYS_IN):
        raise CapabilitiesError(f"{where}: ways_in must name exactly {list(WAYS_IN)}")
    for way, value in ways.items():
        if not isinstance(value, bool | str) or (isinstance(value, str) and not value.strip()):
            raise CapabilitiesError(f"{where}: ways_in.{way} must be true, false or a short note")
    if raw["since"] not in VERSIONS:
        raise CapabilitiesError(f"{where}: since must be one of {sorted(VERSIONS)}")
    page = raw["page"]
    if not isinstance(page, str) or not re.fullmatch(r"[a-z0-9-]+(/[a-z0-9-]+)*", page):
        raise CapabilitiesError(f"{where}: page must be a docs-site path like concepts/effect-keys")
    claim = raw["claim"]
    note = raw.get("claim_note")
    if claim is None and not (isinstance(note, str) and note.strip()):
        raise CapabilitiesError(f"{where}: a null claim needs a claim_note saying who adds the row")
    if claim is not None and (not isinstance(claim, str) or not claim.strip()):
        raise CapabilitiesError(f"{where}: claim must be a CLAIMS.md row's quoted text, or null")
    if not isinstance(raw["guarantee"], bool):
        raise CapabilitiesError(f"{where}: guarantee must be true or false")
    if not isinstance(raw["name"], str) or not raw["name"].strip():
        raise CapabilitiesError(f"{where}: name must be a non-empty string")
    return Capability(
        id=identifier,
        name=raw["name"].strip(),
        description=description.strip(),
        guarantee=raw["guarantee"],
        ways_in=dict(ways),
        since=raw["since"],
        page=page,
        claim=claim,
        claim_note=note,
    )


def _cell(value: bool | str) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "—"
    return value


def render(fmt: str, capabilities: Sequence[Capability]) -> str:
    if fmt == "readme":
        return _render_readme(capabilities)
    if fmt == "mdx":
        return _render_mdx(capabilities)
    if fmt == "text":
        return _render_text(capabilities)
    raise CapabilitiesError(f"unknown format {fmt!r}; one of {FORMATS}")


def _render_readme(capabilities: Sequence[Capability]) -> str:
    lines = [
        f"<!-- {_OPEN_COMMENT.format(format='readme', what='table')} -->",
        "| Guarantee | " + " | ".join(WAY_LABELS[way] for way in WAYS_IN) + " |",
        "|---|" + "---|" * len(WAYS_IN),
    ]
    for entry in capabilities:
        if not entry.guarantee:
            continue
        cells = " | ".join(_cell(entry.ways_in[way]) for way in WAYS_IN)
        lines.append(f"| **{entry.name}** — {entry.description} | {cells} |")
    lines.append("<!-- end generated -->")
    return "\n".join(lines) + "\n"


def _render_mdx(capabilities: Sequence[Capability]) -> str:
    """The six guarantees as cards a reader sees; everything else folded under one accordion.

    A front door with twenty-six cards is an inventory, and a stranger reads none of it. The
    six that are guarantees are what the README matrix shows; the rest stay one click away."""
    lines = ["{/* " + _OPEN_COMMENT.format(format="mdx", what="grid") + " */}"]
    guarantees = [entry for entry in capabilities if entry.guarantee]
    others = [entry for entry in capabilities if not entry.guarantee]
    lines.extend(_mdx_cards(guarantees, indent="  "))
    lines.append(f'<Accordion title="Everything else it does ({len(others)} more)">')
    lines.extend(_mdx_cards(others, indent="    "))
    lines.append("</Accordion>")
    lines.append("{/* end generated */}")
    return "\n".join(lines) + "\n"


def _mdx_cards(entries: Sequence[Capability], *, indent: str) -> list[str]:
    outer = indent[:-2]
    lines = [f"{outer}<Columns cols={{2}}>"]
    for entry in entries:
        lines.append(f'{indent}<Card title="{_attribute(entry.name)}" href="/{entry.page}">')
        lines.append(f"{indent}  {entry.description} Since {entry.since}.")
        lines.append(f"{indent}</Card>")
    lines.append(f"{outer}</Columns>")
    return lines


def _render_text(capabilities: Sequence[Capability]) -> str:
    lines = [_OPEN_COMMENT.format(format="text", what="list")]
    for entry in capabilities:
        lines.append(f"- {entry.name}: {entry.description}")
    lines.append("end generated")
    return "\n".join(lines) + "\n"


def _attribute(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def write(capabilities: Sequence[Capability], directory: Path = GENERATED) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in FORMATS:
        target = directory / FILENAMES[fmt]
        target.write_text(render(fmt, capabilities), encoding="utf-8")
        written.append(target)
    return written


@dataclass(frozen=True)
class Drift:
    path: str
    line: int | None
    reason: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{where}: {self.reason}"


def marker_blocks(text: str) -> list[tuple[int, str, str]]:
    """Every (opening line number, format, embedded text) marker block in a page."""
    lines = text.splitlines()
    blocks: list[tuple[int, str, str]] = []
    index = 0
    while index < len(lines):
        opened = _MARKER_OPEN.search(lines[index])
        if opened is None:
            index += 1
            continue
        start = index
        fmt = opened.group("format")
        index += 1
        while index < len(lines) and not _MARKER_CLOSE.search(lines[index]):
            index += 1
        if index >= len(lines):
            blocks.append((start + 1, fmt, ""))
            break
        blocks.append((start + 1, fmt, "\n".join(lines[start : index + 1]) + "\n"))
        index += 1
    return blocks


def check(
    capabilities: Sequence[Capability],
    *,
    directory: Path = GENERATED,
    pages: Sequence[Path] | None = None,
) -> list[Drift]:
    drift: list[Drift] = []
    for fmt in FORMATS:
        target = directory / FILENAMES[fmt]
        expected = render(fmt, capabilities)
        if not target.exists():
            drift.append(Drift(relative(target), None, "missing; run --write"))
        elif target.read_text(encoding="utf-8") != expected:
            drift.append(Drift(relative(target), None, "differs from the generator; run --write"))
    if pages is None:
        pages = documents(patterns=("README.md", "docs/**/*.md", "docs/**/*.mdx"))
    for page in pages:
        if page.parent == directory and page.name in FILENAMES.values():
            continue
        for line, fmt, embedded in marker_blocks(page.read_text(encoding="utf-8")):
            if fmt not in FORMATS:
                drift.append(Drift(relative(page), line, f"unknown format {fmt!r}"))
            elif not embedded:
                drift.append(Drift(relative(page), line, "marker block never closes"))
            elif embedded != render(fmt, capabilities):
                drift.append(
                    Drift(
                        relative(page),
                        line,
                        f"embedded {fmt} render differs from the generator; paste it fresh",
                    )
                )
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("format", nargs="?", choices=FORMATS, help="print one render")
    parser.add_argument("--write", action="store_true", help="refresh docs/generated/")
    parser.add_argument("--check", action="store_true", help="fail if a rendered copy drifted")
    arguments = parser.parse_args(argv)
    try:
        capabilities = load()
    except CapabilitiesError as exc:
        print(f"capabilities: {exc}")
        return 1
    if arguments.format:
        sys.stdout.write(render(arguments.format, capabilities))
        return 0
    if arguments.write:
        for path in write(capabilities):
            print(f"wrote {relative(path)}")
    if arguments.check or not arguments.write:
        drift = check(capabilities)
        for item in drift:
            print(f"DRIFT {item}")
        print(f"capabilities: {len(capabilities)} entries, {len(drift)} drifted copy(ies)")
        return 0 if not drift else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

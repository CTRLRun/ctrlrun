"""Extract every cookbook recipe's files from its page into `examples/cookbook/<name>/`.

A recipe page under `docs/docs/cookbook/` is the single source: its `yaml runnable` block is the
recipe's `ctrlrun.yaml` and its `python runnable file=main.py` block is the script. This
script writes those into `examples/cookbook/<name>/`, so the directory a reader clones runs
exactly what the page shows, and `--check` refuses a copy that drifted either way.

    python tools/docs_audit/render_cookbook.py --write
    python tools/docs_audit/render_cookbook.py --check

`tests/test_cookbook.py` runs every extracted directory offline, twice, and asserts exit 0.
"""

from __future__ import annotations

import argparse
import sys

from _files import REPO_ROOT, fences, relative

PAGES = REPO_ROOT / "docs" / "docs" / "cookbook"
EXAMPLES = REPO_ROOT / "examples" / "cookbook"
HEADER = (
    "# Extracted by tools/docs_audit/render_cookbook.py from\n"
    "# docs/docs/cookbook/{name}.mdx — edit the page, never this file.\n"
)


def recipes() -> dict[str, dict[str, str]]:
    """`{recipe name: {file name: content}}` for every page under `docs/docs/cookbook/`."""
    out: dict[str, dict[str, str]] = {}
    for page in sorted(PAGES.glob("*.mdx")):
        if page.stem == "index":
            continue
        files: dict[str, str] = {}
        for fence in fences(page.read_text(encoding="utf-8"), page):
            if "runnable" not in fence.tokens or fence.language not in {"python", "yaml", "bash"}:
                continue
            name = next((t[5:] for t in fence.tokens if t.startswith("file=")), None)
            if name is None:
                if fence.language != "yaml":
                    continue
                name = "ctrlrun.yaml"
            header = HEADER.format(name=page.stem)
            files[name] = header + fence.body
        # A recipe is a directory only where the page carries something to run. The two adapter
        # recipes show a framework's own code, which the adapters CI job runs against a real
        # install; they extract nothing here rather than a directory that cannot run offline.
        if "main.py" in files or "run.sh" in files:
            out[page.stem] = files
    return out


def check(extracted: dict[str, dict[str, str]]) -> list[str]:
    drift: list[str] = []
    for name, files in extracted.items():
        for filename, content in files.items():
            target = EXAMPLES / name / filename
            if not target.exists():
                drift.append(f"{relative(target)} missing; run --write")
            elif target.read_text(encoding="utf-8") != content:
                drift.append(
                    f"{relative(target)} differs from docs/docs/cookbook/{name}.mdx; run --write"
                )
    if EXAMPLES.exists():
        for existing in EXAMPLES.iterdir():
            if (
                existing.is_dir()
                and existing.name not in extracted
                and existing.name != "__pycache__"
            ):
                drift.append(
                    f"{relative(existing)} has no page under docs/docs/cookbook/; remove it"
                )
    return drift


def write(extracted: dict[str, dict[str, str]]) -> int:
    count = 0
    for name, files in extracted.items():
        directory = EXAMPLES / name
        directory.mkdir(parents=True, exist_ok=True)
        for filename, content in files.items():
            (directory / filename).write_text(content, encoding="utf-8")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    extracted = recipes()
    if arguments.write:
        print(
            f"wrote {write(extracted)} files for {len(extracted)} recipes under {relative(EXAMPLES)}"
        )
    if arguments.check or not arguments.write:
        drift = check(extracted)
        for item in drift:
            print(f"DRIFT {item}")
        print(f"cookbook: {len(extracted)} recipes, {len(drift)} drifted")
        return 0 if not drift else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

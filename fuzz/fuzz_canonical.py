#!/usr/bin/env python3
"""Atheris entry point for the canonical property. The invariants live in `properties.py`.

Two ways to run it:

    python fuzz/fuzz_canonical.py --corpus          # the seed corpus, no Atheris needed
    python fuzz/fuzz_canonical.py -max_total_time=60 fuzz/corpus/canonical

The first is what `tests/test_fuzzing.py` and CI's quick gate run, so the target is executed
on every commit rather than only wherever a fuzzing toolchain happens to exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

FUZZ = Path(__file__).resolve().parent
if str(FUZZ) not in sys.path:
    sys.path.insert(0, str(FUZZ))

import properties  # noqa: E402


def one_input(data: bytes) -> None:
    properties.check_canonical(properties.document_from_bytes(data))


def _run_corpus() -> int:
    seeds = sorted(p for p in (FUZZ / "corpus" / "canonical").iterdir() if p.is_file())
    findings: dict[str, int] = {}
    for path in seeds:
        known = properties.check_canonical(properties.document_from_bytes(path.read_bytes()))
        if known is not None:
            findings[known] = findings.get(known, 0) + 1
    print(f"canonical: {len(seeds)} inputs, {len(findings)} known finding(s) reproduced")
    for name, count in sorted(findings.items()):
        print(f"  {name}: {count}")
    return 0


def main() -> int:
    if "--corpus" in sys.argv:
        return _run_corpus()

    import atheris

    with atheris.instrument_imports():
        import properties as instrumented  # noqa: F401

    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

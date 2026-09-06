#!/usr/bin/env python3
"""Atheris entry point for the policy property. The invariants live in `properties.py`.

Two ways to run it:

    python fuzz/fuzz_policy.py --corpus          # the seed corpus, no Atheris needed
    python fuzz/fuzz_policy.py -max_total_time=60 fuzz/corpus/policy

The first is what `tests/test_fuzzing.py` and CI's quick gate run, so the target is executed
on every commit rather than only wherever a fuzzing toolchain happens to exist.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

FUZZ = Path(__file__).resolve().parent
if str(FUZZ) not in sys.path:
    sys.path.insert(0, str(FUZZ))

# **`properties` is deliberately not imported here.** `atheris.instrument_imports()` hooks the
# import system and instruments what is imported *inside* it, transitively -- so `properties`,
# and through it `ctrlrun.action`, has to reach the interpreter for the first time in that
# block. An import at module scope puts it in `sys.modules` first and makes the instrumented
# re-import a silent no-op: the campaign still runs, at full speed, guided by nothing at all.
# `stat::new_units_added: 0` after ten million executions is what that looks like in a CI log,
# and it is what the first version of this file did.
properties: Any = None


def one_input(data: bytes) -> None:
    properties.check_policy(properties.policy_text_from_bytes(data))


def _run_corpus() -> int:
    seeds = sorted(p for p in (FUZZ / "corpus" / "policy").iterdir() if p.is_file())
    findings: dict[str, int] = {}
    for path in seeds:
        known = properties.check_policy(properties.policy_text_from_bytes(path.read_bytes()))
        if known is not None:
            findings[known] = findings.get(known, 0) + 1
    print(f"policy: {len(seeds)} inputs, {len(findings)} known finding(s) reproduced")
    for name, count in sorted(findings.items()):
        print(f"  {name}: {count}")
    return 0


def main() -> int:
    if "--corpus" in sys.argv:
        import properties as module

        globals()["properties"] = module
        return _run_corpus()

    import atheris

    # The first import of `properties` in this process, so the instrumentation actually
    # applies to it and to `ctrlrun` underneath it.
    with atheris.instrument_imports():
        import properties as module

    globals()["properties"] = module

    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

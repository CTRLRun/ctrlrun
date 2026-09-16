# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Build one receipt chain with five RELEASED ctrlrun wheels, then verify it with this build.

`SPEC-v0.11.md` §6.1. A store kept since v0.6 holds five receipt schema versions: `v3` (0.6),
`v4` (0.7), `v5` (0.8), `v6` (0.9), `v7` (0.10). Nothing proved that `verify` walks such a chain
end to end, hash by hash, **each row hashed by the rule its own version wrote**. v0.10's release
pass proved the `v6`/`v7` boundary against the released 0.9.0 and stopped there.

**The chain is written by the released wheels and not by fixtures this build produces**, because
a fixture is this build's opinion of what 0.6 wrote and the wheel is what it actually wrote.
v0.10's upgrade check did this and it is the reason it found anything.

This needs a network and five virtual environments, so it is a script an operator or a release
pass runs rather than a test the ordinary suite runs. `tests/test_five_schema_versions.py` keeps
the invariant checkable without a network, and `G31` grades the walk.

    python scripts/five_schema_chain.py            # build and verify, print the transcript
    python scripts/five_schema_chain.py --keep DIR # leave the store behind to poke at

The transcript it printed on 2026-09-14, against this build, is in that test file's docstring.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

#: One release per receipt schema version a store kept since v0.6 can hold. 0.6.1 rather than
#: 0.6.0 because it is the last 0.6, and `v3` is what both wrote.
RELEASES: tuple[tuple[str, str], ...] = (
    ("0.6.1", "ctrlrun.receipt/v3"),
    ("0.7.0", "ctrlrun.receipt/v4"),
    ("0.8.0", "ctrlrun.receipt/v5"),
    ("0.9.0", "ctrlrun.receipt/v6"),
    ("0.10.0", "ctrlrun.receipt/v7"),
)

#: `ctrlrun.policy/v1`, which every release from 0.6 on still reads: the rule since
#: `SPEC-v0.3.md` §12.2 is that every reader upgrades before any writer switches, and a document
#: only the newest binary parses would make this script test the policy loader instead of the
#: chain.
POLICY = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

#: Run by each wheel inside its own environment. It must use only what 0.6.1 already had, so
#: nothing here touches `Receipt.schema`: that attribute does not exist in 0.6.1, and an earlier
#: version of this script raised `AttributeError` out of its own reporting line **after** the
#: writes had landed, which reads as a failed write and is not one.
WRITER = """
import sys
from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal

database, tag, policy = sys.argv[1], sys.argv[2], sys.argv[3]
store = SQLiteStateStore(database)
control = Control(Policy.from_yaml(policy), store)
for index in range(2):
    control.execute(
        Action(
            name="stripe.refund",
            arguments={"payment_id": "%s-%d" % (tag, index), "amount": 2000},
            principal=Principal(agent="chain-agent"),
        ),
        lambda: {"ok": True},
        "refund:%s-%d" % (tag, index),
    )
print(len(store.receipts()))
store.close()
"""


#: The environment every subprocess gets: this one, with anything that could put **this**
#: build's source onto a released wheel's `sys.path` removed.
#:
#: **This is the whole methodology and it was wrong first.** Run as `PYTHONPATH=src python
#: scripts/five_schema_chain.py`, which is how anyone runs a script against an uninstalled
#: checkout, the variable is inherited by every child -- so each "released wheel" imported this
#: build's `src/ctrlrun` instead of the wheel just installed beside it. The script printed a
#: chain of ten receipts that verified perfectly and reported **one** schema version, because
#: all five writers were this binary. A run that checked nothing looked exactly like a pass.
def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for leak in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(leak, None)
    return environment


def _require_wrote_as(python: Path, version: str, schema: str) -> None:
    """Fail loudly if the wheel that just ran was not the released one.

    The positive control for `_clean_environment`, and it is not optional: the only symptom of a
    leaked `sys.path` is a schema count, which is the number this script exists to produce. A
    check that can be fooled by the bug it checks for is not a check.
    """
    seen = subprocess.run(
        [
            str(python),
            "-c",
            "import ctrlrun, ctrlrun.receipt as r;"
            "print(ctrlrun.__file__);print(getattr(r, 'RECEIPT_SCHEMA', 'none'))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_clean_environment(),
    ).stdout.split()
    if f"venv-{version}" not in seen[0]:
        raise SystemExit(
            f"{version} ran from {seen[0]}, which is not its own environment: something put "
            "another ctrlrun on its sys.path, so this run proves nothing"
        )
    if seen[1] != schema:
        raise SystemExit(
            f"{version} writes {seen[1]}, and this script says it writes {schema}. One of the "
            "two is wrong, and RELEASES is the thing to fix"
        )


def _environment(root: Path, version: str) -> Path:
    """A scratch environment with exactly one released ctrlrun in it."""
    target = root / f"venv-{version}"
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            f"ctrlrun=={version}",
        ],
        check=True,
        env=_clean_environment(),
    )
    return python


def build(root: Path) -> Path:
    """Write two receipts with each release, oldest first, into one store."""
    database = root / "chain.db"
    writer = root / "writer.py"
    writer.write_text(WRITER, encoding="utf-8")
    for version, schema in RELEASES:
        python = _environment(root, version)
        done = subprocess.run(
            [str(python), str(writer), str(database), f"v{version}", POLICY],
            check=True,
            capture_output=True,
            text=True,
            env=_clean_environment(),
        )
        _require_wrote_as(python, version, schema)
        print(
            f"  {version:<7} wrote 2 receipts under {schema}; store now holds {done.stdout.strip()}"
        )
    return database


def verify(database: Path) -> int:
    """Verify the whole chain with THIS build, and report what it walked."""
    from ctrlrun.receipt import verify_chain
    from ctrlrun.state import SQLiteStateStore

    store = SQLiteStateStore(str(database))
    try:
        rows = store.receipts()
        schemas = sorted({row.schema for row in rows})
        report = verify_chain(store)
    finally:
        store.close()

    print()
    print(f"  receipts:              {len(rows)}")
    print(f"  schemas in ONE chain:  {len(schemas)}")
    for schema in schemas:
        at = [row.seq for row in rows if row.schema == schema]
        print(f"    {schema:<22} at seq {at}")
    print(
        f"  verify_chain:          ok={report.ok} verified={report.verified} "
        f"chained={report.chained} unchained={report.unchained}"
    )
    print(f"  breaks:                {[(b.name, b.seq) for b in report.breaks] or 'none'}")

    expected = {schema for _, schema in RELEASES}
    if set(schemas) != expected:
        print(f"\nFAIL: expected {sorted(expected)}, walked {schemas}")
        return 1
    if not report.ok or report.verified != len(rows):
        print("\nFAIL: the chain did not verify end to end")
        return 1
    print(f"\nOK: one chain, {len(schemas)} receipt schema versions, verified end to end")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=Path, help="build in this directory and leave it behind")
    arguments = parser.parse_args()

    print(f"Building one chain with {len(RELEASES)} released wheels from PyPI:")
    if arguments.keep:
        arguments.keep.mkdir(parents=True, exist_ok=True)
        return verify(build(arguments.keep))
    with tempfile.TemporaryDirectory() as scratch:
        return verify(build(Path(scratch)))


if __name__ == "__main__":
    sys.exit(main())

# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Hold the coverage floors CONTRIBUTING.md states, from the JSON `scripts/check.sh` writes.

    python scripts/coverage_floor.py coverage.json --statements 90 --branches 80

Two numbers rather than coverage.py's single blended one, because the blend hides which of the
two slipped and the floors are stated separately. Exit 1 names the one that did.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def measure(report: Path) -> tuple[float, float]:
    totals = json.loads(report.read_text(encoding="utf-8"))["totals"]
    statements = 100.0 * totals["covered_lines"] / totals["num_statements"]
    branches = 100.0 * totals["covered_branches"] / totals["num_branches"]
    return statements, branches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("--statements", type=float, required=True, help="floor, percent")
    parser.add_argument("--branches", type=float, required=True, help="floor, percent")
    arguments = parser.parse_args(argv)
    statements, branches = measure(arguments.report)
    print(f"statements {statements:6.2f}%  floor {arguments.statements:g}%")
    print(f"branches   {branches:6.2f}%  floor {arguments.branches:g}%")
    below = []
    if statements < arguments.statements:
        below.append("statements")
    if branches < arguments.branches:
        below.append("branches")
    if below:
        print(f"coverage_floor: below the floor: {', '.join(below)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

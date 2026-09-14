#!/bin/sh
# SPDX-FileCopyrightText: 2026 The CTRLRun contributors
# SPDX-License-Identifier: Apache-2.0
# An SBOM of the distribution this repository ships, measured from the built wheel.
#
# Usage: scripts/sbom.sh <wheel> <output.cdx.json>
#
# **From the artifact, not from the manifest.** `pyproject.toml` says what the package
# *declares*; this installs the wheel into an empty environment and records what actually
# resolves, which is the same discipline release verification follows for everything else here:
# verify the thing you ship. A manifest-derived SBOM would be this repository's opinion of its
# own dependencies, and the whole point of the document is to be checkable against reality.
#
# **`pip` is uninstalled before the scan, and that is not cosmetic.** `python -m venv` puts pip
# in the environment, and a scanner reading the environment cannot tell the difference between
# "ctrlrun needs this" and "the venv came with this". An SBOM that lists pip as a dependency of
# ctrlrun is wrong in the direction that matters: it overstates the attack surface a consumer is
# taking on, and an SBOM nobody can trust is worse than none. `test_sbom.py` asserts the
# component list is exactly the two declared runtime dependencies, so a future Python that seeds
# a venv with something else fails the suite rather than shipping a wrong document.
set -eu

wheel="$1"
out="$2"
root="$(cd "$(dirname "$0")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

"${PYTHON:-python3}" -m venv "$scratch/venv"
"$scratch/venv/bin/pip" install --quiet --no-cache-dir "$wheel"
"$scratch/venv/bin/pip" uninstall --yes --quiet pip

cyclonedx-py environment "$scratch/venv" \
    --of JSON \
    --output-reproducible \
    --pyproject "$root/pyproject.toml" \
    --mc-type library \
    -o "$out"

printf 'sbom: %s\n' "$out"

#!/bin/sh
# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
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
# **The seed packages are uninstalled before the scan, and that is not cosmetic.**
# `python -m venv` puts pip in the environment, and on some versions setuptools and wheel too. A
# scanner reading that environment cannot tell the difference between "ctrlrun needs this" and
# "the venv came with this". An SBOM listing them as dependencies of ctrlrun is wrong in the
# direction that matters: it overstates what a consumer is taking on, and nothing about a padded
# SBOM looks broken.
#
# The list is three because a measurement said so, not because three felt safe. The first
# version removed only pip, which is all Python 3.12 seeds, and it passed locally and went red in
# CI on 3.11 with `['PyYAML', 'click', 'setuptools']`. That is the guard working: the assertion
# in `test_sbom.py` and in the `package` job compares by **equality**, so a future interpreter
# that seeds something else fails loudly rather than shipping a document nobody can trust.
set -eu

wheel="$1"
out="$2"
root="$(cd "$(dirname "$0")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

"${PYTHON:-python3}" -m venv "$scratch/venv"
"$scratch/venv/bin/pip" install --quiet --no-cache-dir "$wheel"
# `pip` last: it cannot uninstall the others once it has removed itself.
"$scratch/venv/bin/pip" uninstall --yes --quiet setuptools wheel 2>/dev/null || true
"$scratch/venv/bin/pip" uninstall --yes --quiet pip

cyclonedx-py environment "$scratch/venv" \
    --of JSON \
    --output-reproducible \
    --pyproject "$root/pyproject.toml" \
    --mc-type library \
    -o "$out"

printf 'sbom: %s\n' "$out"

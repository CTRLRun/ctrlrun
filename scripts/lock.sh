#!/bin/sh
# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
# Regenerates every hash-pinned requirements file CI installs from.
#
# The version floors in pyproject.toml are deliberate minimums and stay where they are; what
# is pinned here is what CI *installs*, so a workflow run resolves to bytes somebody has seen
# rather than to whatever PyPI serves that morning. `--universal` makes one file serve every
# interpreter in the matrix, `--generate-hashes` is what `pip install --require-hashes` checks
# against. Dependabot moves the pins (`.github/dependabot.yml`); this is how a person does.
#
# `requirements/in/backend.in` rides in every lock: see the comment there for why.
#
# The inputs live in `requirements/in/`, one directory down, on purpose: Dependabot treats an
# `x.in` beside an `x.txt` as a pip-tools pair and would regenerate the lock with pip-compile,
# which knows nothing of `--universal` or of the extras taken from pyproject.toml. With no
# `.in` in its directory it edits the pins and hashes in place, which is all it should do.
set -eu
cd "$(dirname "$0")/.."

compile() {
    out="$1"; shift
    uv pip compile --quiet --universal --generate-hashes --python-version 3.11 \
        --output-file "$out" "$@"
}

extras="--extra dev --extra gateway --extra otel --extra identity"
# `ci.txt` and `docs.txt` both take `--extra postgres`, and for different reasons. The `check`
# job needs psycopg to run the Postgres tests; the `docs` job needs it to *collect* them, because
# the readiness block records what `pytest --collect-only` finds and 76 tests exist only when
# psycopg is importable. A docs lock with fewer extras counts a smaller suite than the one that
# ran and fails the audit against a number that was right.
# shellcheck disable=SC2086
compile requirements/ci.txt        pyproject.toml $extras --extra postgres requirements/in/backend.in
# shellcheck disable=SC2086
compile requirements/adapters.txt  pyproject.toml $extras requirements/in/adapters.in requirements/in/backend.in
# shellcheck disable=SC2086
compile requirements/docs.txt      pyproject.toml $extras --extra postgres requirements/in/docs.in requirements/in/backend.in
compile requirements/fuzz.txt      pyproject.toml requirements/in/backend.in
compile requirements/build.txt     requirements/in/build.in requirements/in/backend.in
compile requirements/sbom.txt      requirements/in/sbom.in requirements/in/backend.in
compile requirements/atheris.txt   requirements/in/atheris.in requirements/in/backend.in

#!/bin/sh
# Regenerates every hash-pinned requirements file CI installs from.
#
# The version floors in pyproject.toml are deliberate minimums and stay where they are; what
# is pinned here is what CI *installs*, so a workflow run resolves to bytes somebody has seen
# rather than to whatever PyPI serves that morning. `--universal` makes one file serve every
# interpreter in the matrix, `--generate-hashes` is what `pip install --require-hashes` checks
# against. Dependabot moves the pins (`.github/dependabot.yml`); this is how a person does.
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
# shellcheck disable=SC2086
compile requirements/ci.txt        pyproject.toml $extras --extra postgres
# shellcheck disable=SC2086
compile requirements/adapters.txt  pyproject.toml $extras requirements/in/adapters.in
# shellcheck disable=SC2086
compile requirements/docs.txt      pyproject.toml $extras requirements/in/docs.in
compile requirements/fuzz.txt      pyproject.toml
compile requirements/build.txt     requirements/in/build.in
compile requirements/atheris.txt   requirements/in/atheris.in

#!/bin/sh
# Everything CI's `check` job runs, in one place, cheapest first.
#
# CI calls this file rather than naming the tools itself, so "it passed locally" and "it
# passed in CI" cannot come to mean different things. They did once: `ruff format` reads
# Python code blocks inside Markdown and `ruff check` does not, so a spec whose blocks
# happened to parse went red on a check that had never fired before, found by CI rather than
# by the person who wrote it. The formatter now leaves Markdown alone; this file is what
# keeps the next such asymmetry from being discovered the same way.
#
# `test_ci_runs_the_check_script` fails if CI stops calling it.
#
# Cheapest first, so a formatting slip costs a second rather than a full test run. The tools
# run through `python -m`, so they come from the same environment as the `ctrlrun` they are
# checking. Set PYTHON to pick a different interpreter.
set -eu

PYTHON="${PYTHON:-python3}"

run() {
    printf '\n=== %s ===\n' "$*"
    "$PYTHON" -m "$@"
}

run ruff format --check
run ruff check
run mypy --strict src
# `-n auto` across cores, `--dist loadfile` so a file's tests stay on one worker: a suite
# whose fixtures are per-file (the Postgres schema fixtures especially) pays fewer setups that
# way, and an ordering assumption inside a file still holds. PYTEST_ARGS overrides for a single
# test or a serial reproduction.
# shellcheck disable=SC2086
run pytest -n auto --dist loadfile ${PYTEST_ARGS:-}

printf '\nall checks passed\n'

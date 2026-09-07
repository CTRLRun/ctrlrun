"""How the read commands open a store, and what they say when they cannot.

`_store`'s docstring states the invariant: *"It creates nothing, and it migrates nothing -- a
command an operator runs to read evidence must not have a side effect on the database it
reads."* The explicit `sqlite://` branch held to it; the default branch did not, so
`ctrlrun receipts` in any directory without a `ctrlrun.yaml` created a state database, migrated
it, and answered "no receipts yet" -- an operator looking for evidence of an agent action was
told there was none, from a store they had just created one directory away.

And five commands called `_store` outside the `try` that turns a kernel refusal into a clean
message, so every refusal it raises reached the terminal as a traceback. `ctrlrun effects`,
which calls it inside, printed one clean line for the identical input.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun.cli import main as cli

READ_COMMANDS = ("receipts", "effects", "inspect", "stats")


def _run(args, cwd: Path, env: dict[str, str] | None = None):
    """Invoke the CLI in an empty directory, and hand back what it left there.

    `tmp_path` and an explicit chdir rather than `CliRunner.isolated_filesystem`, which is
    deprecated and goes away in Click 9.
    """
    here = cwd / "empty"
    here.mkdir(exist_ok=True)
    previous = os.getcwd()
    os.chdir(here)
    try:
        result = CliRunner().invoke(cli.main, args, env=env or {}, catch_exceptions=True)
    finally:
        os.chdir(previous)
    return result, here


@pytest.mark.parametrize("command", READ_COMMANDS)
def test_a_read_command_never_reaches_the_terminal_as_a_traceback(command, tmp_path):
    """The CLI's error contract: `Error: <message>`, non-zero, and never a stack trace."""
    args = [command, "act_1"] if command == "inspect" else [command]
    result, _ = _run(args, tmp_path)

    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"ctrlrun {command} raised {result.exception!r}"
    )
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command", READ_COMMANDS)
def test_a_read_command_creates_no_store(command, tmp_path):
    """The invariant `_store` documents, applied to the branch that did not hold it."""
    args = [command, "act_1"] if command == "inspect" else [command]
    _, here = _run(args, tmp_path)

    assert list(here.iterdir()) == [], (
        f"ctrlrun {command} left {[p.name for p in here.iterdir()]} behind"
    )


@pytest.mark.parametrize("command", ("receipts", "effects", "inspect", "approve", "deny"))
def test_an_empty_CTRLRUN_STATE_is_one_clean_line_from_every_command(command, tmp_path):
    """`InvalidArgument` out of `state_path()`. `effects` reported it cleanly and the rest
    dumped a traceback, so the CLI contradicted itself command to command on one input."""
    args = [command, "req_1"] if command in ("inspect", "approve", "deny") else [command]
    result, _ = _run(args, tmp_path, env={"CTRLRUN_STATE": "  "})

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "CTRLRUN_STATE" in result.output


@pytest.mark.parametrize("command", ("receipts", "effects"))
def test_an_unknown_store_scheme_is_one_clean_line(command, tmp_path):
    result, _ = _run([command, "--store-url", "mysql://nope/db"], tmp_path)

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "no store backend" in result.output


def test_a_missing_sqlite_database_still_says_so(tmp_path):
    """The branch that was already right, pinned so the fix does not regress it."""
    result, _ = _run(["receipts", "--store-url", f"sqlite://{tmp_path}/nope.db"], tmp_path)

    assert result.exit_code != 0
    assert "no database at" in result.output


def test_a_read_command_reads_a_store_that_exists(tmp_path):
    """The positive control: refusing when the store is absent must not refuse when it is
    present, or the tests above would pass on a command that never works."""
    from ctrlrun import SQLiteStateStore

    database = tmp_path / "state.db"
    SQLiteStateStore(database).close()

    result, _ = _run(["receipts", "--store-url", f"sqlite://{database}"], tmp_path)

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output

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

REPO_ROOT = Path(__file__).resolve().parents[1]

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


# --- `delegate` and `revoke` write where the operator says (SPEC-v0.6 §4) -----------------
#
# Both called `Control.from_file()`, which always opens `.ctrlrun/state.db` beside the policy.
# Neither took `--store-url`, so on the backend this milestone exists for, `ctrlrun delegate`
# wrote a delegation into a local SQLite file no agent reads, and `ctrlrun revoke` reported
# "revoked" while the delegation stayed live in Postgres. `_store`'s docstring names exactly
# this failure for the read commands -- "on the backend this milestone exists for, `ctrlrun
# resolve` could not reach a record" -- and the two write commands were left out of the fix.
#
# Revoke is the one that matters: an operator cuts a chain in a hurry, is told it is cut, and
# it is not.

WRITE_COMMANDS = ("delegate", "revoke")

#: `^dlg_[0-9a-f]{32}$`, which `revoke` checks before it reaches the store.
DELEGATION_ID = "dlg_" + "a" * 32


@pytest.mark.parametrize("command", WRITE_COMMANDS)
def test_the_authority_write_commands_accept_a_store_url(command):
    """The option exists at all. Without it there is no way to name the store, and the
    command silently uses a different one."""
    from click.testing import CliRunner

    result = CliRunner().invoke(cli.main, [command, "--help"])

    assert "--store-url" in result.output, f"ctrlrun {command} cannot be pointed at a store"


@pytest.mark.authority
def test_revoke_acts_on_the_named_store_and_not_the_default_one(tmp_path):
    """The routing itself, proved positively.

    Asserting only "the default store was not written" passes for the wrong reason before the
    fix: without the option click rejects the command outright, so nothing is written either
    way. This puts a real delegation in a named store, revokes it through the CLI, and reads
    the revocation back out of that same store -- which cannot happen unless the command
    opened it.

    The delegation is created through `Control._delegate`, the path `ctrlrun delegate` itself
    uses, rather than by hand-writing a row: a hand-built `grant_json` drifts from what the
    code writes, and the loader rightly refuses what it cannot read.
    """
    from ctrlrun import Control, Policy, SQLiteStateStore
    from ctrlrun.authority import grant_from_yaml
    from ctrlrun.control import _optional_authority

    here = tmp_path / "empty"
    here.mkdir(exist_ok=True)
    policy_file = here / "ctrlrun.yaml"
    policy_file.write_text(
        (REPO_ROOT / "examples" / "authority" / "payments.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    named = tmp_path / "named.db"

    store = SQLiteStateStore(named)
    control = Control(
        Policy.from_file(policy_file), store, authority=_optional_authority(str(policy_file))
    )
    parent = next(
        gid for gid in sorted(control.authority.grants)
        if control.authority.grants[gid].delegable
    )
    child = grant_from_yaml(
        _delegable_child(control.authority.grants[parent]), source="<test>"
    )
    created = control._delegate(parent, child, by=_subject_principal(control, parent), via="cli")
    store.close()

    result, ran_in = _run(["revoke", created.delegation_id, "--store-url", f"sqlite://{named}"],
                          tmp_path, env={})

    reopened = SQLiteStateStore(named)
    try:
        record = reopened.get_delegation(created.delegation_id)
    finally:
        reopened.close()

    assert result.exit_code == 0, result.output
    assert record.revoked_at is not None, "the CLI did not revoke in the store it was given"
    assert not (ran_in / ".ctrlrun" / "state.db").exists(), (
        "the CLI created the default store while pointed at another"
    )


def _delegable_child(parent) -> str:
    """A one-grant document the parent admits.

    Every dimension the parent constrains is restated, because SPEC-v0.3 §5.4's containment is
    structural: *omission never means unlimited*, so a child that simply left `resources:` out
    would be an escalation, not a narrowing.
    """
    import yaml as _yaml

    subject = {"agent": parent.subject.agent}
    if parent.subject.user is not None:
        subject["user"] = parent.subject.user
    document: dict = {"subject": subject, "actions": list(parent.actions)}
    if parent.resources is not None:
        document["resources"] = list(parent.resources)
    if parent.environments is not None:
        document["environments"] = list(parent.environments)
    if parent.constraints:
        # `constraints` maps the condition key to a `Condition`; the document form is the
        # operand it was written with.
        document["constraints"] = {
            key: condition.operand for key, condition in parent.constraints.items()
        }
    if parent.expires_at is not None:
        document["expires_at"] = parent.expires_at.isoformat()
    return _yaml.safe_dump(document)


def _subject_principal(control, parent_id):
    from ctrlrun import Principal

    subject = control.authority.grants[parent_id].subject
    return Principal(agent=subject.agent, user=subject.user)


# --- a driver's own exception is not the CLI's error contract ----------------------------


def test_a_state_path_that_is_not_a_database_is_one_clean_line(tmp_path):
    """`sqlite3.DatabaseError: file is not a database` names neither the path nor the remedy,
    and is not a `CTRLRunError`, so nothing in the CLI or in an application catches it."""
    here = tmp_path / "empty"
    here.mkdir(exist_ok=True)
    decoy = tmp_path / "notadb.db"
    decoy.write_text("this is not a database\n", encoding="utf-8")

    result, _ = _run(["receipts"], tmp_path, env={"CTRLRUN_STATE": str(decoy)})

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert str(decoy) in result.output


def test_an_unreachable_postgres_host_is_one_clean_line(tmp_path):
    """A mistyped `--store-url` is a typo, and `psycopg.OperationalError` with a resolver
    stack under it reads as a broken package."""
    result, _ = _run(
        ["receipts", "--store-url", "postgresql://nosuchhost.invalid/db"], tmp_path
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output

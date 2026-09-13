"""T272 to T280: `ctrlrun revoke --created-by` and `--under` (SPEC-v0.8 §7).

The selector is a query over rows that already exist. Every match is revoked exactly as one id
is revoked today, one at a time, so a run that stops halfway leaves the rows it reached revoked
and the rest untouched (§7.3, §7.4).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from click.testing import CliRunner

from ctrlrun.cli.main import main
from ctrlrun.state import SQLiteStateStore

#: Every test here builds an `authority:` section and writes `DELEGATION_*` events, which
#: `conftest` requires a test to declare (SPEC-v0.3 §4.1).
pytestmark = pytest.mark.authority

# --- the workspace ----------------------------------------------------------------------------

DOCUMENT = """schema: ctrlrun.policy/v3
environment: production
authority:
  max_delegation_depth: 4
  grants:
    - id: head-of-finance
      subject: { agent: "human-cfo" }
      actions: ["stripe.**"]
      resources: ["payment:EU-*"]
      constraints: { amount_gte: 0, amount_lte: 10000000 }
      environments: ["production"]
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
    - id: ops-lead
      subject: { agent: "human-ops" }
      actions: ["deploy.**"]
      resources: ["service:EU-*"]
      constraints: { replicas_gte: 0, replicas_lte: 100 }
      environments: ["production"]
      expires_at: "2027-01-01T00:00:00+00:00"
      delegable: true
actions:
  stripe.refund:
    decision: allow
  deploy.rollout:
    decision: allow
"""

FINANCE_CHILD = """subject: { agent: "finance-agent", user: "cfo@example.com" }
actions: ["stripe.refund"]
resources: ["payment:EU-4*"]
constraints: { amount_gte: 0, amount_lte: 2500000 }
environments: ["production"]
expires_at: "2026-12-01T00:00:00+00:00"
delegable: true
"""

FINANCE_GRANDCHILD = """subject: { agent: "finance-agent", user: "cfo@example.com" }
actions: ["stripe.refund"]
resources: ["payment:EU-42"]
constraints: { amount_gte: 0, amount_lte: 1000 }
environments: ["production"]
expires_at: "2026-12-01T00:00:00+00:00"
delegable: true
"""

OPS_CHILD = """subject: { agent: "deploy-agent", user: "ops@example.com" }
actions: ["deploy.rollout"]
resources: ["service:EU-4*"]
constraints: { replicas_gte: 0, replicas_lte: 10 }
environments: ["production"]
expires_at: "2026-12-01T00:00:00+00:00"
delegable: true
"""

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not POSTGRES_URL,
                reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against",
            ),
        ),
    ]
)
def workspace(request, tmp_path, monkeypatch):
    """A working tree with a policy and the grant files, on either backend (T280).

    The Postgres run gets a scratch schema of its own, migrated by opening a store once and
    dropped afterwards, because `ctrlrun` refuses a schema that is not already at HEAD.
    """
    (tmp_path / "ctrlrun.yaml").write_text(DOCUMENT, encoding="utf-8")
    (tmp_path / "finance-child.yaml").write_text(FINANCE_CHILD, encoding="utf-8")
    (tmp_path / "finance-grandchild.yaml").write_text(FINANCE_GRANDCHILD, encoding="utf-8")
    (tmp_path / "ops-child.yaml").write_text(OPS_CHILD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)

    if request.param == "sqlite":
        yield _Workspace(tmp_path, [])
        return

    from ctrlrun.postgres import PostgresStateStore

    schema = f"revsel_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    migrated = PostgresStateStore(POSTGRES_URL, schema=schema)
    migrated.close()
    try:
        yield _Workspace(tmp_path, ["--store-url", _with_schema(POSTGRES_URL, schema)], schema)
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _with_schema(url: str, schema: str) -> str:
    """`url` with CTRLRun's schema parameter added, keeping every parameter it already has.

    A naive f-string appends a second '?' to a URL carrying `?sslmode=require`, and
    `_peel_schema` then reads `ctrlrun_schema` as part of the `sslmode` value and falls back to
    `public`. The test would pass while the command wrote outside its scratch schema, into the
    database every other test shares.
    """
    parts = urlsplit(url)
    pairs = [*parse_qsl(parts.query), ("ctrlrun_schema", schema)]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


class _Workspace:
    def __init__(self, path, store_args, schema=None):
        self.path = path
        self.store_args = store_args
        self.schema = schema

    def cli(self, *arguments):
        return CliRunner().invoke(main, [*arguments, *self.store_args])

    def delegate(self, parent: str, as_who: str, grant: str = "finance-child.yaml") -> str:
        result = self.cli("delegate", "--parent", parent, "--file", grant, "--as", as_who, "--json")
        assert result.exit_code == 0, result.output
        return str(json.loads(result.output)["delegation_id"])

    def records(self):
        """Every delegation row, revoked or not, keyed by id."""
        store = self._open()
        try:
            return {row.delegation_id: row for row in store.delegations(include_revoked=True)}
        finally:
            store.close()

    def revoked_events(self):
        store = self._open()
        try:
            return [event for event in store.events() if event.type == "DELEGATION_REVOKED"]
        finally:
            store.close()

    def _open(self):
        if not self.store_args:
            return SQLiteStateStore(self.path / ".ctrlrun" / "state.db")
        from ctrlrun.postgres import PostgresStateStore

        # The base URL and the schema, kept apart rather than re-parsed out of the CLI's own
        # argument: a test that reads a different schema from the one the command wrote to is a
        # test that asserts nothing.
        return PostgresStateStore(POSTGRES_URL, schema=self.schema)


@pytest.fixture
def tree(workspace):
    """Six delegations: five beneath `head-of-finance` at three depths, one beneath `ops-lead`.

    `alice`, `bob` and a user-less `human-cfo` create the three top rows, so T273 can tell
    `--created-by AGENT` from `--created-by AGENT/USER` on rows that differ only in the user.
    """
    alice = workspace.delegate("head-of-finance", "human-cfo/alice@example.com")
    bob = workspace.delegate("head-of-finance", "human-cfo/bob@example.com")
    bare = workspace.delegate("head-of-finance", "human-cfo")
    ops = workspace.delegate("ops-lead", "human-ops/carol@example.com", "ops-child.yaml")
    child = workspace.delegate(alice, "finance-agent/cfo@example.com", "finance-grandchild.yaml")
    grandchild = workspace.delegate(
        child, "finance-agent/cfo@example.com", "finance-grandchild.yaml"
    )
    return {
        "alice": alice,
        "bob": bob,
        "bare": bare,
        "ops": ops,
        "child": child,
        "grandchild": grandchild,
    }


def _revoked(workspace, ids):
    rows = workspace.records()
    return {name for name, identifier in ids.items() if rows[identifier].is_revoked}


# --- T272: --created-by matches the creator, and nothing else ---------------------------------


def test_T272_created_by_revokes_that_creators_rows_and_leaves_every_other(workspace, tree):
    """§7.3 — three creators in one store, and the selector reaches exactly one of them."""
    result = workspace.cli("revoke", "--created-by", "human-ops")

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"ops"}


def test_T272_the_other_direction_is_asserted_too(workspace, tree):
    """A selector that revoked everything would pass the assertion above on its own."""
    result = workspace.cli("revoke", "--created-by", "finance-agent/cfo@example.com")

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"child", "grandchild"}


# --- T273: AGENT and AGENT/USER are different selectors ---------------------------------------


def test_T273_created_by_agent_matches_every_user(workspace, tree):
    """§7.3 — `--created-by AGENT` matches rows created by that agent with any user, or none."""
    result = workspace.cli("revoke", "--created-by", "human-cfo")

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"alice", "bob", "bare"}


def test_T273_created_by_agent_slash_user_matches_both_fields(workspace, tree):
    """The same rows, told apart by the user: only `alice`'s row is reached."""
    result = workspace.cli("revoke", "--created-by", "human-cfo/alice@example.com")

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"alice"}


# --- T274: --under reaches the subtree at every depth -----------------------------------------


def test_T274_under_revokes_the_subtree_including_a_grandchild(workspace, tree):
    """§7.3 — the subtree of a policy grant with several children, at every depth."""
    result = workspace.cli("revoke", "--under", "head-of-finance")

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"alice", "bob", "bare", "child", "grandchild"}


def test_T274_under_a_delegation_reaches_its_own_subtree_only(workspace, tree):
    """`--under` takes a delegation id as readily as a grant id, and stops where the tree does."""
    result = workspace.cli("revoke", "--under", tree["child"])

    assert result.exit_code == 0, result.output
    assert _revoked(workspace, tree) == {"grandchild"}


# --- T275: one event per match, and a second run is idempotent --------------------------------


def test_T275_each_match_produces_one_event_and_a_second_run_produces_none(workspace, tree):
    """§7.4 — one revocation, one record, per row, exactly as one id is revoked today."""
    first = workspace.cli("revoke", "--created-by", "human-cfo")
    assert first.exit_code == 0, first.output

    after_first = workspace.revoked_events()
    assert len(after_first) == 3
    assert {event.data["delegation_id"] for event in after_first} == {
        tree["alice"],
        tree["bob"],
        tree["bare"],
    }

    second = workspace.cli("revoke", "--created-by", "human-cfo")

    assert second.exit_code == 0, second.output
    assert len(workspace.revoked_events()) == 3
    assert "3 already revoked" in second.output
    # **And it says nothing it did not do.** `Control.revoke` is idempotent, so a second pass
    # over revoked rows appends no event whatever the command does, and the event count above is
    # green with the skip deleted. What the skip is actually for is the terminal: without it a
    # rerun prints "revoked <id>" for every row it did not revoke. Asserting the absence is what
    # makes the guard load-bearing (`CONTRIBUTING.md`'s first shape of a false green).
    assert "revoked " + tree["alice"] not in second.output


# --- T276: a run that stops halfway -----------------------------------------------------------


CHILD = """
import sys
from ctrlrun.cli.main import main

sys.exit(main(["revoke", "--created-by", "human-cfo"] + sys.argv[1:], standalone_mode=True))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is not available")
def test_T276_a_killed_run_leaves_what_it_reached_revoked_and_a_second_run_finishes(
    workspace, tmp_path
):
    """§7.4 — a real interruption of a real command, bounded, and the window opened on purpose.

    The rows are created first, the command is spawned as its own process, and it is killed as
    soon as the store shows its first revocation. A test that called an internal function would
    prove nothing about what a `Ctrl-C` at 3am leaves behind.
    """
    for index in range(200):
        workspace.delegate("head-of-finance", f"human-cfo/user-{index}@example.com")

    script = tmp_path / "run_revoke.py"
    script.write_text(CHILD, encoding="utf-8")
    events = workspace.path / ".ctrlrun" / "events.jsonl"
    before = events.read_text(encoding="utf-8").count('"DELEGATION_REVOKED"')

    child = subprocess.Popen(
        [sys.executable, str(script), *workspace.store_args],
        cwd=str(workspace.path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # The event file, not the store: opening a store to poll costs more than the run it is
        # watching, and the kill then lands after the child has finished, which opens no window.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if events.read_text(encoding="utf-8").count('"DELEGATION_REVOKED"') > before:
                break
            if child.poll() is not None:
                break
            time.sleep(0.001)
        child.send_signal(signal.SIGKILL)
    finally:
        child.wait(timeout=30)

    rows = workspace.records()
    revoked = [row for row in rows.values() if row.is_revoked]
    assert revoked, "the child revoked nothing, so the run never started"
    assert len(revoked) < len(rows), (
        "the child finished before it could be killed, so no window was opened: "
        f"{len(revoked)} of {len(rows)}"
    )
    # **At most one row can lack its event, and the reason is worth stating.** `Control.revoke`
    # writes the row and then appends the event: two writes, no transaction over the pair, and
    # `StateStore` is frozen (SPEC-v0.6 §9.2), so a kill between them leaves a revoked row whose
    # event was never written. That is v0.3 behaviour for a single `ctrlrun revoke` as much as
    # for a selector; a selector only makes the window easier to land in. Asserting equality
    # here would be asserting something the code does not promise.
    events = len(workspace.revoked_events())
    assert len(revoked) - 1 <= events <= len(revoked), (
        f"{events} events for {len(revoked)} revoked rows: at most one row may be mid-write"
    )

    second = workspace.cli("revoke", "--created-by", "human-cfo")

    assert second.exit_code == 0, second.output
    after = workspace.records()
    assert all(row.is_revoked for row in after.values() if row.created_by_agent == "human-cfo")


# --- T277: an empty selector is an error ------------------------------------------------------


def test_T277_a_selector_matching_nothing_exits_non_zero_and_names_what_it_searched_for(
    workspace, tree
):
    """§7.5 — the one place in v0.8 where an empty result is a failure."""
    missing = workspace.cli("revoke", "--created-by", "human-cfo-typo")

    assert missing.exit_code != 0
    assert "human-cfo-typo" in missing.output
    assert _revoked(workspace, tree) == set()

    nowhere = workspace.cli("revoke", "--under", "no-such-grant")

    assert nowhere.exit_code != 0
    assert "no-such-grant" in nowhere.output


# --- T278: the usage errors -------------------------------------------------------------------


def test_T278_the_selectors_are_mutually_exclusive_and_refuse_a_positional_id(workspace, tree):
    """§7.2 — each combination is a usage error with its own message."""
    both = workspace.cli("revoke", "--created-by", "human-cfo", "--under", "head-of-finance")
    assert both.exit_code != 0
    assert "--created-by" in both.output and "--under" in both.output

    with_id = workspace.cli("revoke", tree["alice"], "--created-by", "human-cfo")
    assert with_id.exit_code != 0
    assert "--created-by" in with_id.output

    under_with_id = workspace.cli("revoke", tree["alice"], "--under", "head-of-finance")
    assert under_with_id.exit_code != 0
    assert "--under" in under_with_id.output

    neither = workspace.cli("revoke")
    assert neither.exit_code != 0

    assert _revoked(workspace, tree) == set()


def test_T278_an_agent_name_containing_a_slash_cannot_be_written(workspace, tree):
    """The rule `delegate --as` already states, on the selector that reads what it wrote.

    **The message is asserted, not only the exit code**, and the mutation table is why: with
    the two-separator refusal removed, `a/b/c` parses as the agent `a` and the user `b/c`,
    matches nothing, and exits non-zero through §7.5's empty-selector error instead. A test
    that asserted only the exit code was green with the guard deleted, which is a subsumed
    guard (`CONTRIBUTING.md`'s first shape of a false green).
    """
    result = workspace.cli("revoke", "--created-by", "a/b/c")

    assert result.exit_code != 0
    assert "more than one '/'" in result.output
    assert "no delegation matched" not in result.output
    assert _revoked(workspace, tree) == set()


def test_T278_a_trailing_separator_is_a_typed_and_lost_user(workspace, tree):
    """`--created-by human-cfo/` is refused rather than read as "every user".

    The widest reading of an ambiguous command is the wrong default during an incident, and
    without the guard this exits non-zero anyway through §7.5's empty-selector error, which is
    the subsumed shape the mutation table caught twice already. So the message is asserted.
    """
    result = workspace.cli("revoke", "--created-by", "human-cfo/")

    assert result.exit_code != 0
    assert "ends with '/'" in result.output
    assert "no delegation matched" not in result.output
    assert _revoked(workspace, tree) == set()


# --- T279: --by keeps its 0.7.0 meaning -------------------------------------------------------


def test_T279_by_still_names_who_performed_the_revocation(workspace, tree):
    """§7.2 — two meanings on one option is the defect; `--by` keeps the one it had."""
    selector = workspace.cli("revoke", "--created-by", "human-ops", "--by", "incident-4412")
    assert selector.exit_code == 0, selector.output

    single = workspace.cli("revoke", tree["alice"], "--by", "incident-4412")
    assert single.exit_code == 0, single.output

    rows = workspace.records()
    assert rows[tree["ops"]].revoked_by == "incident-4412"
    assert rows[tree["alice"]].revoked_by == "incident-4412"
    assert all(event.data["revoked_by"] == "incident-4412" for event in workspace.revoked_events())


def test_T279_by_defaults_to_the_cli_approver_on_a_selector_run(workspace, tree):
    """The default is the one `ctrlrun revoke <id>` has always had."""
    result = workspace.cli("revoke", "--created-by", "human-ops")

    assert result.exit_code == 0, result.output
    assert workspace.records()[tree["ops"]].revoked_by == "cli:local"


# --- T280 is the `workspace` fixture: every test above runs on both backends -------------------


def test_T280_the_backend_under_test_is_the_one_the_command_wrote_to(workspace, tree):
    """The positive control for the fixture itself: a vacuous backend parameter proves nothing."""
    rows = workspace.records()

    assert set(tree.values()) <= set(rows)
    assert all(row.created_at.tzinfo is not None for row in rows.values())
    assert datetime.now(UTC) >= max(row.created_at for row in rows.values())

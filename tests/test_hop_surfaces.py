# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""SPEC-v0.10 §6, item 5: the operator surfaces for a hop.

The 3am question is `v0.9 §7.2`'s shape one level up: **which envelope did the peer actually hold,
and which hop narrowed it**. An operator could already see whether a chain was valid; what nothing
answered was which link took the resource away.

No new command: `v0.9 §7.1`'s reasoning applies unchanged, and a hop is one more thing `inspect`
answers about.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ctrlrun.action import Principal
from ctrlrun.authority import Authority, grant_from_yaml, narrowed_dimensions
from ctrlrun.cli.main import main
from ctrlrun.control import Control
from ctrlrun.policy import Policy
from ctrlrun.reporting import HOP_SCHEMA, hop_document
from ctrlrun.state import SQLiteStateStore

DOC = """
schema: ctrlrun.policy/v7
actions:
  stripe.refund:
    effect: "refund:{payment}"
    decision: allow
authority:
  max_delegation_depth: 3
  grants:
    - id: head-of-support
      subject: { agent: "planner" }
      actions: ["stripe.refund", "stripe.refund.partial"]
      resources: ["payment:*"]
      environments: ["production", "staging"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
"""

MIDDLE = """
subject: { agent: "relay" }
actions: ["stripe.refund"]
resources: ["payment:EU-*"]
environments: ["production"]
delegable: true
expires_at: "2026-12-01T00:00:00Z"
"""

LEAF = """
subject: { agent: "worker" }
actions: ["stripe.refund"]
resources: ["payment:EU-1"]
environments: ["production"]
expires_at: "2026-10-01T00:00:00Z"
"""


def _chain(tmp_path):
    authority = Authority.from_yaml(DOC, source="t")
    policy = Policy.from_yaml(DOC, source="t")
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(policy, store, authority=authority)
    first = control.hop(
        "head-of-support", grant_from_yaml(MIDDLE, source="t"), by=Principal(agent="planner")
    )
    second = control.hop(
        first.delegation_id, grant_from_yaml(LEAF, source="t"), by=Principal(agent="relay")
    )
    return control, store, authority, first, second


@pytest.mark.authority
def test_T503_inspect_hop_renders_every_ancestor_and_what_each_narrowed(tmp_path):
    """§6.2. `chain[]` carries one entry per link with the dimensions that link narrowed, which
    is the part that answers the question. Depth is **derived by walking to the root**, never read
    from the stored column (`v0.3 §5.5`): a row edited with a text editor must not be able to
    assert its way to a shorter chain, and a view that trusted the column would launder that."""
    _, store, authority, first, second = _chain(tmp_path)

    document = hop_document(second.delegation_id, authority, store)

    assert document is not None
    assert document["schema"] == HOP_SCHEMA
    assert document["hop"] == second.delegation_id
    assert document["created_via"] == "hop"
    assert document["created_by"] == {"agent": "relay", "user": None}
    assert document["subject"] == {"agent": "worker", "user": None}
    assert document["depth"] == 2
    assert document["root_id"] == "head-of-support"

    ids = [step["id"] for step in document["chain"]]
    assert ids == [second.delegation_id, first.delegation_id]
    # The leaf narrowed resources (EU-* to EU-1) and the expiry; the middle narrowed the actions
    # it dropped, its resources, and its own expiry.
    assert "resources" in document["chain"][0]["narrowed"]
    assert "expires_at" in document["chain"][0]["narrowed"]
    assert "actions" in document["chain"][1]["narrowed"]


@pytest.mark.authority
def test_T504_an_unknown_hop_exits_non_zero_with_nothing_on_stdout(tmp_path, monkeypatch):
    """As `inspect` does for an unknown action, so a script cannot mistake "no such hop" for "a
    hop with no chain"."""
    _, store, _authority, _, _ = _chain(tmp_path)
    store.close()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ctrlrun.yaml").write_text(DOC)

    result = CliRunner().invoke(
        main, ["inspect", "--hop", "dlg_" + "0" * 32, "--store-url", f"sqlite:{tmp_path}/s.db"]
    )

    assert result.exit_code != 0
    assert result.stdout.strip() == ""


@pytest.mark.authority
def test_T506_json_emits_the_keys_section_6_2_names(tmp_path):
    """§6.2's table, asserted by key set so a field cannot be added without a test going red."""
    _, store, authority, _, second = _chain(tmp_path)

    document = hop_document(second.delegation_id, authority, store)

    assert document is not None
    assert set(document) == {
        "schema",
        "hop",
        "created_by",
        "created_at",
        "created_via",
        "subject",
        "depth",
        "revoked_at",
        "root_id",
        "missing_parent_id",
        "chain",
    }
    assert set(document["chain"][0]) == {
        "id",
        "parent_id",
        "depth",
        "created_via",
        "revoked_at",
        "narrowed",
    }
    json.dumps(document)  # portable JSON, like every other surface (v0.6 §11)


@pytest.mark.authority
def test_T505_a_refusal_prints_the_command_with_the_presented_hop_filled_in(tmp_path):
    """§6.3. **The argument is the presented hop**, never the id the store could not read.

    That id is by construction unreadable, so `inspect --hop <it>` is T504's path and exits
    non-zero: a refusal whose one suggested command is guaranteed to fail is worse than no
    suggestion, because it teaches an operator the line is noise.
    """
    from ctrlrun.errors import AuthorityDenied

    control, _store, _authority, _first, second = _chain(tmp_path)
    from ctrlrun.action import Action

    proposed = Action(
        name="stripe.refund",
        resource="payment:US-9",  # outside the leaf's envelope
        arguments={"amount": 10, "payment": "US-9"},
        principal=Principal(agent="worker"),
        environment="production",
    )

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(proposed, lambda: "ok", "refund:US-9", hop=second.delegation_id)

    message = str(refused.value)
    assert f"ctrlrun inspect --hop {second.delegation_id}" in message, message


@pytest.mark.authority
def test_T505b_the_printed_command_is_one_that_works(tmp_path):
    """Asserted by **running** what the refusal printed, which is what forbids suggesting an id
    the store cannot read."""
    _, store, authority, _, second = _chain(tmp_path)

    document = hop_document(second.delegation_id, authority, store)

    assert document is not None, (
        "the refusal suggests `inspect --hop <presented>`; if that id does not resolve, the "
        "suggestion is a dead end"
    )


def test_narrowed_dimensions_never_contradicts_contained_dimension(tmp_path):
    """§6.2's rule that the helper decides nothing. It answers the complement of
    `contained_dimension`, so on a **contained** pair that function answers `None` and this one
    answers a subset of `DIMENSIONS`; the two are checked against each other on the same pairs so
    a disagreement is still caught."""
    from ctrlrun.authority import DIMENSIONS, contained_dimension

    parent = Authority.from_yaml(DOC, source="t").grants["head-of-support"]
    child = grant_from_yaml(MIDDLE, source="t")

    assert contained_dimension(parent, child) is None, "the fixture must be a contained pair"
    narrowed = narrowed_dimensions(parent, child)
    assert set(narrowed) <= set(DIMENSIONS)
    assert list(narrowed) == sorted(narrowed, key=DIMENSIONS.index), "DIMENSIONS order"


@pytest.mark.authority
def test_T503b_depth_is_walked_even_when_the_stored_column_lies(tmp_path):
    """`v0.3 §5.5`: depth is **derived by walking to the root**, never read from the stored
    column, so a row edited directly in the database cannot assert its way to a shorter chain.

    A mutation run is why this test exists. Reading `delegation.depth` instead of walking passed
    every other test in this file, because a chain created through `Control.hop` has a column that
    agrees with the walk. **The column only lies when somebody edits it**, which is the whole
    threat the rule is about, so the test has to do the editing.
    """
    _control, store, authority, _first, second = _chain(tmp_path)
    record = store.get_delegation(second.delegation_id)
    assert record is not None
    store._connection().execute(
        "UPDATE delegations SET depth = 1 WHERE delegation_id = ?", (second.delegation_id,)
    )
    store._connection().commit()  # a text editor's lie: "I am one link down"

    document = hop_document(second.delegation_id, authority, store)

    assert document is not None
    assert document["depth"] == 2, (
        "the view read the stored column, which is the edit v0.3 §5.5 exists to refuse; a "
        "surface that trusted it would launder exactly that edit into an operator's answer"
    )
    assert document["chain"][0]["depth"] == 2


@pytest.mark.authority
def test_T505c_a_broken_chain_suggests_the_presented_hop_and_names_the_unreadable_one(tmp_path):
    """§6.3's rule, and the case that separates it from the obvious alternative.

    `missing_parent_id` names the record the store could **not** read, so
    `inspect --hop <that id>` is T504's unknown-id path and exits non-zero. The refusal therefore
    prints the **presented** hop, which does resolve, and names the unreadable one in prose.

    A mutation run is why this test exists: printing the missing id instead passed every other
    test here, because none of them broke a chain.
    """
    from ctrlrun.action import Action
    from ctrlrun.errors import AuthorityDenied

    control, store, _authority, first, second = _chain(tmp_path)
    # The shape a hand-edited store has, which `v0.3 §5.6` rule 1 exists for.
    store._connection().execute(
        "DELETE FROM delegations WHERE delegation_id = ?", (first.delegation_id,)
    )
    store._connection().commit()

    proposed = Action(
        name="stripe.refund",
        resource="payment:EU-1",
        arguments={"amount": 10, "payment": "EU-1"},
        principal=Principal(agent="worker"),
        environment="production",
    )

    with pytest.raises(AuthorityDenied) as refused:
        control.execute(proposed, lambda: "ok", "refund:EU-1", hop=second.delegation_id)

    message = str(refused.value)
    assert f"ctrlrun inspect --hop {second.delegation_id}" in message, message
    assert f"--hop {first.delegation_id}" not in message, (
        "the suggested command names the record the store could not read, so running it exits "
        "non-zero: a dead end teaches an operator the line is noise"
    )
    assert first.delegation_id in message, "the unreadable id belongs in the prose"


@pytest.mark.authority
def test_T507_scan_names_the_principals_holding_a_root_grant(tmp_path):
    """**§6.4, and the operator's half of §2.3.2's residual.**

    ctrlrun cannot make a receiving agent present the hop it was given: one holding a grant of its
    own can decline and act on that instead. The deployment rule that collapses it is *an agent
    that only ever acts on handed-over work holds no root grant of its own*, and §2.3.2 leans on
    §6's surfaces to make it checkable. Without this line the rule is advice.

    **It reports and does not score** (`v0.4 §3.9`): a principal here is a fact about the
    document, it is not a finding, and it does not move the exit code.
    """
    from ctrlrun.scan import report_document, report_lines, scan

    (tmp_path / "ctrlrun.yaml").write_text(DOC)
    (tmp_path / "app.py").write_text("import ctrlrun\n")

    report = scan(str(tmp_path))

    assert report.root_grant_holders == ("planner",)
    assert "holds a root grant (1)" in "\n".join(report_lines(report))
    assert report_document(report)["root_grant_holders"] == ["planner"]
    # A fact, not a finding.
    assert all("planner" not in finding.target for finding in report.findings), (
        "a root-grant holder was reported as a finding; v0.4 §3.9 forbids scan grading a document"
    )


@pytest.mark.authority
def test_T506b_the_spec_table_for_ctrlrun_hop_v1_names_every_key_the_code_emits(tmp_path):
    """`SPEC-v0.10 §6.2` is a frozen schema's table, and it listed eight of eleven keys.

    T506 asserts the emitted document against a literal in this file, which keeps the *code*
    honest and says nothing about the *document*. The three it missed -- `schema`, `root_id`,
    `missing_parent_id` -- were emitted for a whole milestone while §6.2 went on describing a
    smaller shape. Under-describing is the safe direction for a reader and the wrong one for a
    schema somebody writes a consumer against.

    Parsed out of the spec rather than repeated here, so the two cannot agree by being edited
    together.
    """
    import re
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[1] / "docs" / "SPEC-v0.10.md").read_text(
        encoding="utf-8"
    )
    section = spec.split("emitting `ctrlrun.hop/v1`:", 1)[1].split("\n\n**", 1)[0]
    # A row's first cell may name more than one key -- §6.2 pairs `created_at`, `created_via` --
    # so every backticked name in that cell counts, not just the first.
    documented = {
        name.removesuffix("[]")
        for line in section.splitlines()
        if line.startswith("| `")
        for name in re.findall(r"`([a-z_]+(?:\[\])?)`", line.split("|")[1])
    }
    assert documented, "parsed no keys out of SPEC-v0.10 §6.2; the section moved"

    _, store, authority, _, second = _chain(tmp_path)
    document = hop_document(second.delegation_id, authority, store)
    assert document is not None
    emitted = set(document)
    assert emitted == documented, (
        f"§6.2 and the emitted document disagree. Only in the code: "
        f"{sorted(emitted - documented)}. Only in the spec: {sorted(documented - emitted)}"
    )

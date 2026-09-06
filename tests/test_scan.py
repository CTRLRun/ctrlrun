"""`ctrlrun scan` — the coverage finder. `docs/SPEC-scan.md`, T194-T206.

**This suite is written before the implementation and is expected to be red.** It is the
specification in executable form: `docs/SPEC-scan.md` says what the command does, and every
test here names the section it enforces. Making it green is the item; nothing in it should be
weakened to get there.

Each test imports `ctrlrun.scan` inside its own body rather than at module scope, so that a
missing module reads as thirteen unmet requirements rather than one collection error.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun.cli.main import main


def _scan():
    """The module under specification. Imported here so each test fails on its own."""
    import ctrlrun.scan as module

    return module


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return root


def _kinds(report, kind: str) -> list:
    return [finding for finding in report.findings if finding.kind == kind]


# --- T194: it reads, and never runs, the tree ------------------------------------------


def test_T194_scan_never_imports_the_tree_it_reads(tmp_path):
    """§2.1. The one failure that would make this tool worse than nothing."""
    sentinel = tmp_path / "imported"
    _tree(
        tmp_path / "src",
        {
            "danger.py": f"""
            from pathlib import Path

            Path({str(sentinel)!r}).write_text("the scanner executed me")

            def refund(payment_id):
                return stripe.refunds.create(payment_id)
            """,
        },
    )

    _scan().scan(path=tmp_path / "src")

    assert not sentinel.exists(), "scan imported the tree it was asked to read"


# --- T195-T197: what is and is not a finding -------------------------------------------


def test_T195_a_protected_call_and_its_caller_are_not_findings(tmp_path):
    """§3.3 (1) and (2)."""
    _tree(
        tmp_path,
        {
            "billing.py": """
            import ctrlrun

            @ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
            def refund(payment_id, amount):
                return stripe.refunds.create(payment_id, amount)

            def handle(payment_id):
                return refund(payment_id=payment_id, amount=100)
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    assert _kinds(report, "unprotected_call") == []


def test_T196_a_context_block_is_not_coverage(tmp_path):
    """§3.3, last paragraph: the false negative most likely to be written by accident."""
    _tree(
        tmp_path,
        {
            "worker.py": """
            import ctrlrun

            def run(payment_id):
                with ctrlrun.context(agent="worker"):
                    return stripe.refunds.create(payment_id)
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    found = _kinds(report, "unprotected_call")
    assert [finding.target for finding in found] == ["stripe.refunds.create"]


def test_T197_the_vocabulary_matches_whole_words_in_segments(tmp_path):
    """§3.2. `deleted_at` is not a deletion."""
    _tree(
        tmp_path,
        {
            "mixed.py": """
            def run(client, row):
                client.create_refund(1)
                client.delete_namespace("checkout")
                client.send_email("a@example.com")
                _ = row.deleted_at
                client.undeleted(1)
                client.refunds_report()
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    assert sorted(finding.target for finding in _kinds(report, "unprotected_call")) == [
        "client.create_refund",
        "client.delete_namespace",
        "client.send_email",
    ]


def test_T198_a_call_with_no_target_path_is_undetermined(tmp_path):
    """§4.1. Not a finding, and above all not silently dropped."""
    _tree(
        tmp_path,
        {
            "dynamic.py": """
            def run(client, verb, handlers, key):
                getattr(client, verb)()
                handlers[key]()
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    assert sorted(call.line for call in report.undetermined) == [2, 3]
    assert _kinds(report, "unprotected_call") == []


# --- T199-T200: fail loud --------------------------------------------------------------


def test_T199_a_file_that_will_not_parse_is_a_finding_not_a_skip(tmp_path):
    """§7 first row, §6 last line: the scan ran, so it is exit 1 and not exit 2."""
    _tree(
        tmp_path,
        {
            "broken.py": "def refund(:\n",
            "fine.py": "def run(client):\n    client.delete_account(1)\n",
        },
    )

    report = _scan().scan(path=tmp_path)

    assert [finding.file for finding in _kinds(report, "unparseable")] == ["broken.py"]
    assert _kinds(report, "unprotected_call"), "the other file was not scanned"
    assert report.exit_code == 1


def test_T200_an_empty_tree_is_exit_two(tmp_path):
    """§7 penultimate row: `0 findings` for a scan of nothing is the false green.

    The first draft of this test asserted the exit code alone and **passed against a tree with
    no `scan` command at all**, because click answers an unknown subcommand with exit 2. It is
    the harness lying in the way this project keeps finding: a green that means the opposite of
    what it reads. So it also requires the message §7 asks for, which only the command can
    produce.
    """
    (tmp_path / "README.md").write_text("no python here\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["scan", "--path", str(tmp_path)])

    assert "No such command" not in result.output, "there is no scan command yet"
    assert result.exit_code == 2, result.output
    assert "no Python file" in result.output
    assert "0 findings" not in result.output


# --- T201: suppression is attribution --------------------------------------------------


def test_T201_a_suppression_needs_a_reason_and_stays_in_the_report(tmp_path):
    """§3.6."""
    _tree(
        tmp_path,
        {
            "app.py": """
            def run(client, cache):
                client.delete_account(1)
                cache.delete_key("k")  # ctrlrun: not-consequential — a local cache entry
                cache.delete_stale()  # ctrlrun: not-consequential
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    assert sorted(finding.line for finding in _kinds(report, "unprotected_call")) == [2, 4]
    assert [suppression.line for suppression in report.suppressions] == [3]
    assert "a local cache entry" in report.suppressions[0].reason
    assert report.exit_code == 1


# --- T202: the policy-side kinds -------------------------------------------------------


def test_T202_both_policy_side_kinds_are_found_and_neither_is_an_unprotected_call(tmp_path):
    """§3.4 and §3.5."""
    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text(
        textwrap.dedent(
            """
            schema: ctrlrun.policy/v2

            actions:
              refunds.create:
                decision: allow
            """
        ).lstrip("\n"),
        encoding="utf-8",
    )
    _tree(
        tmp_path,
        {
            "app.py": """
            import ctrlrun

            @ctrlrun.protect("nothing.listed", effect="thing:{id}")
            def act(id):
                return client.delete_account(id)
            """,
        },
    )

    report = _scan().scan(path=tmp_path, policy=policy)

    assert [f.name for f in _kinds(report, "action_without_effect")] == ["refunds.create"]
    assert [f.name for f in _kinds(report, "protected_action_not_in_policy")] == ["nothing.listed"]
    assert _kinds(report, "unprotected_call") == []


# --- T203-T204: the report tells the truth about itself ---------------------------------


def test_T203_the_limits_sentence_is_in_every_run_including_a_clean_one(tmp_path):
    """§4.5. The run with zero findings is the run where it matters."""
    module = _scan()
    _tree(tmp_path, {"quiet.py": "def run(x):\n    return x + 1\n"})

    report = module.scan(path=tmp_path)
    lines = "\n".join(module.report_lines(report))
    document = module.report_document(report)

    assert not report.findings
    assert "This is a finder, not a proof." in lines
    assert "A clean scan means nothing was found where it looked." in lines
    assert "finder, not a proof" in " ".join(str(v) for v in document["limits"].values())
    assert document["schema"] == module.SCAN_SCHEMA


def test_T204_the_human_output_and_the_json_come_from_one_producer(tmp_path):
    """§5.3."""
    module = _scan()
    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text("schema: ctrlrun.policy/v2\nactions:\n  a.b:\n    decision: allow\n")
    _tree(
        tmp_path,
        {
            "broken.py": "def refund(:\n",
            "app.py": """
            import ctrlrun

            @ctrlrun.protect("nothing.listed", effect="thing:{id}")
            def act(id, client, verb):
                getattr(client, verb)()
                return client.delete_account(id)

            def raw(client):
                client.send_payout(1)
            """,
        },
    )

    report = module.scan(path=tmp_path, policy=policy)
    lines = "\n".join(module.report_lines(report))
    document = module.report_document(report)

    assert {finding["kind"] for finding in document["findings"]} == {
        "unprotected_call",
        "protected_action_not_in_policy",
        "action_without_effect",
        "unparseable",
    }
    for finding in document["findings"]:
        assert finding["kind"] in lines
        assert str(finding.get("target") or finding.get("name") or finding["file"]) in lines


# --- T207-T208: found by running it -----------------------------------------------------


def test_T207_a_decorator_that_supplies_the_effect_is_not_a_missing_effect(tmp_path):
    """§3.4. The policy's `effect:` is the gateway's; a decorator carries its own.

    Reporting the policy alone flagged `examples/double-refund`, whose decorator passes
    `effect="refund:{payment_id}"` — a finding that would have taught a reader to add a key
    they already had. Found by running the command against this repository's own examples.
    """
    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text(
        "schema: ctrlrun.policy/v1\n\nactions:\n  stripe.refund:\n    rules:\n"
        "      - decision: allow\n",
        encoding="utf-8",
    )
    _tree(
        tmp_path,
        {
            "app.py": """
            import ctrlrun

            @ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
            def refund(payment_id):
                return stripe.refunds.create(payment_id)
            """,
        },
    )

    report = _scan().scan(path=tmp_path, policy=policy)

    assert _kinds(report, "action_without_effect") == []
    assert report.exit_code == 0


def test_T208_a_call_on_an_expression_is_a_finding_and_not_undetermined(tmp_path):
    """§4.1. Undetermined is for a callee with no name at all, not for an unnamed receiver.

    Treating every unresolved base as undetermined produced 216 entries against this
    repository's own `src/`, of which two were the dynamic dispatch the category exists for —
    `hashlib.sha256(text).hexdigest()` and `"\\n".join(parts)` filled the rest. A list that
    long is a list nobody reads, and `.delete()` on an expression is still a delete.
    """
    _tree(
        tmp_path,
        {
            "chained.py": """
            def run(factory, key, handlers):
                factory(key).delete_account(1)
                handlers[key]()
            """,
        },
    )

    report = _scan().scan(path=tmp_path)

    found = _kinds(report, "unprotected_call")
    assert [finding.target for finding in found] == ["delete_account"]
    assert found[0].detail == "called on an expression, not a name"
    assert [call.line for call in report.undetermined] == [3]


# --- T205-T206: it is not an entry point, and it is not imported ------------------------


def test_T205_scan_resolves_no_principal_evaluates_no_policy_and_opens_no_store(
    tmp_path, monkeypatch
):
    """§9.2, made executable: break every entry point and require the scan to finish."""
    import ctrlrun.control as control_module
    import ctrlrun.state as state_module

    def _refuse(*args, **kwargs):
        raise AssertionError("scan reached an entry point")

    monkeypatch.setattr(control_module.Control, "execute", _refuse)
    monkeypatch.setattr(control_module.Control, "evaluate", _refuse)
    monkeypatch.setattr(control_module.Control, "resolve_principal", _refuse)
    monkeypatch.setattr(state_module.SQLiteStateStore, "__init__", _refuse)

    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text("schema: ctrlrun.policy/v2\nactions:\n  a.b:\n    decision: allow\n")
    _tree(tmp_path, {"app.py": "def run(client):\n    client.delete_account(1)\n"})

    report = _scan().scan(path=tmp_path, policy=policy)

    assert _kinds(report, "unprotected_call")


def test_T206_importing_ctrlrun_does_not_import_the_scanner():
    """The subprocess assertion T30, T92, T125b and T134 make, plus one module name.

    It asserts the module **exists** first: `find_spec` returning `None` would otherwise make
    this pass on a tree where `ctrlrun.scan` was never written, which is a green for the one
    reason that means nothing.
    """
    assert importlib.util.find_spec("ctrlrun.scan") is not None, "ctrlrun.scan does not exist"

    finished = subprocess.run(
        [sys.executable, "-c", "import ctrlrun, sys; print('ctrlrun.scan' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "False"


# --- the CLI surface §9.4 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        ["scan", "--help"],
        ["scan", "--vocabulary"],
    ],
)
def test_the_cli_carries_the_flags_the_spec_freezes(arguments):
    """§9.4. `--vocabulary` with no argument prints the list in force and exits 0."""
    result = CliRunner().invoke(main, arguments)

    assert result.exit_code == 0, result.output

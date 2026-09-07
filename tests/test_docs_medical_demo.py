"""The medical affairs demo page, held to the module it claims to run.

The page tells a reader to press four buttons and promises what each one does. The promise is
cheap to write and expensive to get wrong, so the module the browser runs is run here too --
natively, with no Node and no network -- through that exact sequence. A page describing a
refusal nobody reproduced is the false green this repository keeps finding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

if not (DOCS / "demos" / "medical-affairs.mdx").exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

PAGE = (DOCS / "demos" / "medical-affairs.mdx").read_text(encoding="utf-8")
SCRIPT = (DOCS / "medical-demo.js").read_text(encoding="utf-8")


def _array(name: str) -> str:
    """The JSON array of Python lines the script embeds, joined the way the script joins it."""
    body = SCRIPT.split(f"var {name} = [", 1)[1].split("].join", 1)[0]
    return "\n".join(json.loads("[" + body + "]"))


MODULE = _array("MODULE")


@pytest.fixture
def harness():
    """The module, executed the way Pyodide executes it, with `step` exposed."""
    namespace: dict[str, object] = {}
    exec(compile(MODULE, "<medical-demo>", "exec"), namespace)
    step = namespace["step"]

    def call(op: str, **request: object) -> dict:
        return json.loads(step(json.dumps({"op": op, **request})))

    return call


# --- the module ----------------------------------------------------------------------------


def test_the_module_is_valid_python_and_defines_step():
    namespace: dict[str, object] = {}
    exec(compile(MODULE, "<medical-demo>", "exec"), namespace)
    assert callable(namespace["step"])


def test_the_module_runs_the_sequence_the_page_tells_the_reader_to_press(harness):
    """Draft, send, approve, let a newer study land, send again. The last one is the page."""
    drafted = harness("draft")
    assert drafted["outcome"] == "executed"
    assert drafted["revision"] == "A"

    pending = harness("send")
    assert pending["outcome"] == "approval_required"
    assert pending["action_hash"].startswith("sha256:")

    approved = harness("approve", request_id=pending["request_id"])
    assert approved["outcome"] == "approved"
    assert approved["action_hash"] == pending["action_hash"], "the reviewer signed another action"

    refreshed = harness("refresh")
    assert refreshed["outcome"] == "refreshed"
    assert refreshed["revision"] == "B"
    assert refreshed["recommendation"] != drafted["recommendation"], "the letter did not change"

    refused = harness("send", approval_id=approved["approval_id"])
    assert refused["outcome"] == "approval_mismatch"
    assert refused["reason"] == "mismatch"
    assert refused["letters"] == 0, "the letter reached the physician"


def test_the_approval_that_matches_is_spent_once(harness):
    """The positive control for the test above: the same approval, on the letter it covers,
    sends -- and a second send under a fresh approval is refused as a duplicate. Without this,
    a module that refused everything would pass."""
    pending = harness("send")
    approved = harness("approve", request_id=pending["request_id"])
    sent = harness("send", approval_id=approved["approval_id"])
    assert sent["outcome"] == "executed"
    assert sent["letters"] == 1

    again = harness("send")
    assert again["outcome"] == "approval_required"
    granted = harness("approve", request_id=again["request_id"])
    duplicate = harness("send", approval_id=granted["approval_id"])
    assert duplicate["outcome"] == "duplicate"
    assert duplicate["reason"] == "committed"
    assert duplicate["letters"] == 1, "the same letter went out twice"


def test_a_lost_reply_from_the_safety_database_is_ambiguous_and_not_failed(harness):
    lost = harness("event", lose_reply=True)
    assert lost["outcome"] == "reply_lost"
    assert lost["cases"] == 1

    retried = harness("event")
    assert retried["outcome"] == "ambiguous_retry"
    assert retried["cases"] == 1, "the case was filed twice"


def test_the_policy_refuses_an_unapproved_use_and_an_action_nobody_wrote_down(harness):
    denied = harness("unapproved")
    assert denied["outcome"] == "denied"
    assert denied["letters"] == 0

    unknown = harness("unreviewed")
    assert unknown["outcome"] == "denied"
    assert unknown["reason"] == "unknown_action"
    assert unknown["letters"] == 0


def test_the_module_has_no_way_to_grant_but_the_reviewer_button(harness):
    """`grant_approval` appears once, under the `approve` op. An auto-approve or a dry run in
    here would make every refusal on the page a decoration."""
    assert MODULE.count("grant_approval") == 1
    for forbidden in ("auto_approve", "dry_run", "deny_approval", "reserve_effect"):
        assert forbidden not in MODULE, forbidden


def test_the_policy_on_the_page_is_the_policy_in_the_module():
    """A reader reads the YAML on the page and believes it produced what they just saw."""
    shown = PAGE.split("```yaml", 1)[1].split("```", 1)[0].strip()
    embedded = MODULE.split('POLICY = """', 1)[1].split('"""', 1)[0].strip()
    assert shown == embedded


# --- the script ----------------------------------------------------------------------------


def test_the_script_does_nothing_unless_the_page_mounts_it():
    """Mintlify includes every .js file on every page and cannot scope one, so the script must
    find its container before it touches anything."""
    assert 'var CONTAINER = "ctrlrun-medical-demo";' in SCRIPT
    assert "if (!root) return;" in SCRIPT
    assert 'id="ctrlrun-medical-demo"' in PAGE


def test_the_script_wires_itself_after_the_page_renders():
    """Mintlify is a single-page application: wiring only on DOMContentLoaded misses every
    client-side navigation, which is how a reader reaches this page from the sidebar."""
    assert "document.readyState" in SCRIPT
    assert "MutationObserver" in SCRIPT, "a sidebar navigation would leave the buttons dead"
    assert "data-wired" in SCRIPT, "wiring twice would double every click"


def test_the_script_does_not_load_sqlite3_as_a_package():
    """sqlite3 is built into Pyodide and asking micropip for it fails the whole boot."""
    packages = SCRIPT.split("var PACKAGES = ", 1)[1].split(";", 1)[0]
    assert "sqlite3" not in packages


def test_the_page_and_the_script_name_one_pyodide_build():
    versions = set(re.findall(r"v?(\d+\.\d+\.\d+)", SCRIPT.split("PYODIDE =")[1].split("\n")[0]))
    assert len(versions) == 1, versions
    version = versions.pop()
    assert version in PAGE, "the page does not name the build the script loads"


def test_the_reference_the_panel_marks_is_the_one_the_newer_study_brings_in():
    """The panel marks the line that moved. A module that renamed the reference without the
    script following would draw revision B with nothing marked, which is the page's whole
    point going quietly missing."""
    marked = SCRIPT.split('var NEW_REFERENCE = "', 1)[1].split('"', 1)[0]
    revisions = re.findall(r'"references": \[([^\]]+)\]', MODULE)
    assert len(revisions) == 2, "the module no longer holds two revisions of the letter"
    assert marked not in revisions[0], f"{marked} is already in revision A"
    assert marked in revisions[1], f"{marked} is not in revision B"


def test_the_script_builds_the_letter_from_nodes_and_not_from_markup():
    """`try-it.js` hands nothing to an HTML parser and neither does this. Not because a string
    here is attacker-controlled -- every one comes from the module in the same file -- but
    because a page about a boundary should not be the page that makes an exception."""
    assert "innerHTML" not in SCRIPT
    assert "insertAdjacentHTML" not in SCRIPT


def test_the_script_names_every_outcome_the_module_can_return():
    """A new branch in `step` with no entry in OUTCOMES would print a raw enum at a reader."""
    outcomes = set(re.findall(r'result\["outcome"\] = "(\w+)"', MODULE))
    mapped = set(re.findall(r"^    (\w+): \[", SCRIPT, re.M))
    assert outcomes - mapped == set(), f"the script does not name: {sorted(outcomes - mapped)}"


def test_the_script_wires_every_control_the_page_mounts():
    """A button the page shows and the script never binds is a control that does nothing."""
    mounted = set(re.findall(r'<button[^>]*name="(\w+)"', PAGE))
    assert mounted, "the page mounts no buttons"
    for name in mounted:
        assert f"buttons.{name}.addEventListener" in SCRIPT or f'find("{name}")' in SCRIPT, name


def test_the_page_tells_a_reader_what_to_do_when_it_does_not_run():
    assert "pip install ctrlrun" in SCRIPT


def test_the_page_says_the_product_and_the_references_are_invented():
    """The one sentence that keeps an illustration from reading as medical information."""
    flowed = " ".join(PAGE.lower().split())
    assert "are invented" in flowed
    assert "nothing on this page is medical information about a real medicine" in flowed

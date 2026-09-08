"""Exercise the actual Python used by the Medical Affairs browser workbench."""

import copy
import json
import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/medical_workbench.py"


@pytest.fixture
def workbench():
    module = runpy.run_path(str(SOURCE))
    snapshot = {
        "inquiry": "DEMO-001",
        "version": 1,
        "validationVersion": 1,
        "destination": "Internal medical review archive",
        "sources": copy.deepcopy(module["SOURCES"]),
        "claims": [
            {"id": key, "source": item[0], "span": item[1], "text": item[2][0]}
            for key, item in module["CLAIMS"].items()
        ],
    }

    def call(op, **extra):
        return json.loads(module["step"](json.dumps({"op": op, "snapshot": snapshot, **extra})))

    return snapshot, call


def test_browser_and_local_example_execute_identical_python():
    script = (ROOT / "docs/medical-workbench.js").read_text()
    encoded = re.search(r'var MODULE = (\[.*?\])\.join\("\\n"\);', script, re.S)
    assert encoded
    assert "\n".join(json.loads(encoded[1])) == SOURCE.read_text().rstrip("\n")


def test_reviewed_document_releases_and_produces_real_receipts(workbench):
    _, call = workbench
    assert call("validate")["validation"]["passed"]
    assert call("release")["outcome"] == "approval_required"
    assert call("approve")["outcome"] == "review_required"
    approved = call("approve", reviewed=True)
    assert approved["outcome"] == "approved"
    assert approved["action_hash"].startswith("sha256:")
    released = call("release")
    assert released["outcome"] == "committed"
    assert released["writes"] == 1
    assert released["effect_state"] == "COMMITTED"
    assert released["receipts"][-1]["approval_id"] == approved["approval_id"]
    assert released["receipts"][-1]["action_hash"] == approved["action_hash"]
    assert released["receipts"][-1]["approver"] == "human:demo-medical-reviewer"


def test_edited_document_cannot_use_original_approval(workbench):
    snapshot, call = workbench
    call("approve", reviewed=True)
    snapshot["version"] = snapshot["validationVersion"] = 2
    result = call("release")
    assert result["outcome"] == "approval_mismatch"
    assert result["reason"] == "mismatch"
    assert result["writes"] == 0
    assert call("approve", reviewed=True)["outcome"] == "approved"
    assert call("release")["writes"] == 1


@pytest.mark.parametrize("change", ["unsupported", "numbers", "source", "version", "destination"])
def test_invalid_evidence_is_denied_even_with_a_client_pass_flag(workbench, change):
    snapshot, call = workbench
    call("approve", reviewed=True)
    if change == "unsupported":
        snapshot["claims"].append({"id": "C4", "text": "Prevents disease progression."})
    elif change == "numbers":
        snapshot["claims"][0]["text"] = snapshot["claims"][0]["text"].replace("60%", "90%")
    elif change == "source":
        snapshot["sources"][0]["version"] = "Fixture 2.0"
    elif change == "version":
        snapshot["validationVersion"] = 0
    else:
        snapshot["destination"] = "External destination"
    snapshot["validation_ok"] = True
    result = call("release")
    assert result["outcome"] == "validation_blocked"
    assert result["validation"]["issues"]
    assert result["writes"] == 0
    assert result["receipts"][-1]["decision"] == "deny"


def test_approval_reuse_and_duplicate_effect_never_write_twice(workbench):
    _, call = workbench
    call("approve", reviewed=True)
    call("release")
    reused = call("release")
    assert reused["outcome"] == "approval_mismatch"
    assert reused["reason"] == "consumed"
    assert reused["writes"] == 1
    call("approve", reviewed=True)
    duplicate = call("release")
    assert duplicate["outcome"] == "duplicate"
    assert duplicate["writes"] == 1


def test_unknown_delivery_remains_ambiguous_until_destination_confirmation(workbench):
    _, call = workbench
    call("approve", reviewed=True)
    lost = call("release", lose_reply=True)
    assert lost["outcome"] == "reply_lost"
    assert lost["effect_state"] == "AMBIGUOUS"
    assert lost["writes"] == 1
    call("approve", reviewed=True)
    retry = call("release")
    assert retry["outcome"] == "ambiguous_retry"
    assert retry["effect_state"] == "AMBIGUOUS"
    assert retry["writes"] == 1
    resolved = call("reconcile")
    assert resolved["outcome"] == "reconciled"
    assert resolved["effect_state"] == "COMMITTED"
    assert resolved["writes"] == 1


def test_reconciliation_never_invents_a_delivery(workbench):
    _, call = workbench
    result = call("reconcile")
    assert result["outcome"] == "nothing_to_reconcile"
    assert result["writes"] == 0

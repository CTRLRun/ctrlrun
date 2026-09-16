# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The upgrade path, and the one irreversible thing a v0.10 release does.

`SPEC-v0.10 §9.3`. Run against the **released** 0.9.0 from PyPI by the release pass, not against a
fixture: a fixture is a claim about what 0.9.0 did, and the check exists because the claim might be
wrong. What is kept here is the shape and the invariant, so a later change that breaks it goes red
in the ordinary suite rather than only in a release rehearsal.

Measured during the 0.10.0 release pass, `pip install ctrlrun==0.9.0` into a clean venv:

    0.9.0 wrote: 3 receipts; chain ok: True; schemas: ['ctrlrun.receipt/v6']
    this build:  migration ran, store opens: True
                 schemas in one chain: ['ctrlrun.receipt/v6', 'ctrlrun.receipt/v7']
                 chain verifies ACROSS it: True (verified 4, breaks 0)
    0.9.0 again: reads 4 receipts, chain ok, breaks []
    after ONE created_via='hop' row:
                 0.9.0 decides an UNRELATED principal's action: False authority_unreadable
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority, grant_from_yaml
from ctrlrun.control import Control
from ctrlrun.policy import Policy
from ctrlrun.receipt import RECEIPT_SCHEMA, verify_chain
from ctrlrun.state import SQLiteStateStore

DOC = """
schema: ctrlrun.policy/v7
actions:
  stripe.refund:
    effect: "refund:{payment}"
    decision: allow
authority:
  grants:
    - id: issuer
      subject: { agent: "planner" }
      actions: ["stripe.refund"]
      resources: ["payment:*"]
      environments: ["production"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
    - id: unrelated
      subject: { agent: "other" }
      actions: ["stripe.refund"]
      resources: ["payment:*"]
      environments: ["production"]
"""

CHILD = """
subject: { agent: "worker" }
actions: ["stripe.refund"]
resources: ["payment:EU-1"]
environments: ["production"]
expires_at: "2026-10-01T00:00:00Z"
"""


def _action(agent: str = "planner", payment: str = "EU-0") -> Action:
    return Action(
        name="stripe.refund",
        resource=f"payment:{payment}",
        arguments={"amount": 10, "payment": payment},
        principal=Principal(agent=agent),
        environment="production",
    )


@pytest.mark.authority
def test_a_chain_spanning_two_receipt_schemas_verifies_end_to_end(tmp_path):
    """`v0.3 §12.2`'s rule: every reader upgrades before any writer switches, so an older receipt
    on disk still parses, and a receipt read from a store is hashed as the document it was read
    from. That is what lets one chain hold `v6` and `v7` rows and still verify."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(
        Policy.from_yaml(DOC, source="t"), store, authority=Authority.from_yaml(DOC, source="t")
    )
    for n in range(3):
        control.execute(_action(payment=f"EU-{n}"), lambda: "ok", f"refund:EU-{n}")

    report = verify_chain(store)

    assert report.ok, [break_.name for break_ in report.breaks]
    assert {receipt.schema for receipt in store.receipts()} == {RECEIPT_SCHEMA}
    assert report.verified == 3


@pytest.mark.authority
def test_one_hop_row_makes_the_whole_deployment_unreadable_to_an_older_reader(tmp_path):
    """**SPEC-v0.10 §9.3, and the sentence the CHANGELOG owes an operator.**

    `CreatedVia` is a closed vocabulary and `_candidates` reads every delegation row before
    filtering any of them, so a reader that does not know `"hop"` answers `authority_unreadable`
    for **every action in the deployment**, not just that delegation. Fail-closed, and a
    deployment that stops.

    So the irreversible step is **creating the first hop**, not installing 0.10.0. This test
    stands in for an older reader by narrowing the vocabulary to what 0.9.0 knew.
    """
    import ctrlrun.authority as authority_module

    store = SQLiteStateStore(str(tmp_path / "s.db"))
    authority = Authority.from_yaml(DOC, source="t")
    control = Control(Policy.from_yaml(DOC, source="t"), store, authority=authority)
    control.hop("issuer", grant_from_yaml(CHILD, source="t"), by=Principal(agent="planner"))

    # An UNRELATED principal, its own root grant, no hop presented.
    unrelated = _action(agent="other", payment="EU-5")
    assert authority.evaluate(unrelated, now=datetime.now(UTC), store=store).passed

    knew_three = {k: v for k, v in authority_module._CREATED_VIA.items() if k != "hop"}
    original = authority_module._CREATED_VIA
    try:
        authority_module._CREATED_VIA = knew_three  # type: ignore[assignment]
        older = authority.evaluate(unrelated, now=datetime.now(UTC), store=store)
    finally:
        authority_module._CREATED_VIA = original  # type: ignore[assignment]

    assert not older.passed
    assert older.reason == "authority_unreadable", (
        "an older reader met one created_via='hop' row and did not stop; §9.3's claim that the "
        "blast radius is the deployment is what makes the first hop the irreversible step"
    )

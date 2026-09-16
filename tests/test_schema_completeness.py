# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The release pass's D27 rule: a schema is complete when something actually writes every field.

`SPEC-v0.7.md` §12 D27, run by v0.8 for three items without incident and by SPEC-v0.9 §10.1 here:
"item 7 asserts every one of them is written by something before the release PR opens."

**The key existing is not the assertion.** `tests/test_demo.py` and `tests/test_protect.py` already
pin the full field list of `ctrlrun.receipt/v6`, which catches a field that was never added. What
they cannot catch is a field that is present on every receipt and populated by nothing: a schema
bumped for a feature whose write path was never wired, which reads as shipped and is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.control import Control
from ctrlrun.policy import Policy
from ctrlrun.receipt import RECEIPT_SCHEMA
from ctrlrun.state import InMemoryStateStore
from ctrlrun.verify.guarantees import CATALOGUE, GUARANTEES

pytestmark = pytest.mark.authority

DOC = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    effect: "refund:{id}"
    resource: "payment:{id}"
    decision: allow
authority:
  grants:
    - id: payer
      subject: {agent: "payer"}
      actions: ["payments.*"]
      resources: ["payment:*"]
      tasks: ["refund-run:*"]
      budgets:
        - {metric: amount, limit: 1000, window: PT24H}
"""


class _Clock:
    now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_every_field_v6_froze_is_written_by_something() -> None:
    """One action carrying all three, because a field written only by a test double is a field
    nothing writes. §10.1 froze `task`, `scope_hash` and `budget_charges` before any item started;
    this is the assertion that they arrived."""
    clock = _Clock()
    store = InMemoryStateStore(clock=clock)
    control = Control(
        policy=Policy.from_yaml(DOC, source="<d>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(DOC, source="<d>"),
    )
    action = Action(
        name="payments.refund",
        arguments={"amount": 100, "id": "1"},
        principal=Principal(agent="payer", user="ada"),
        resource="payment:1",
        environment="prod",
    )

    receipt = control.execute(
        action,
        lambda: {"ok": True},
        "refund:1",
        task="refund-run:march",
        scope=lambda _action: {"resources": ["payment:1"]},
    )

    assert receipt.schema == RECEIPT_SCHEMA
    # SPEC-v0.9 §6: the task the action was bound to.
    assert receipt.task == "refund-run:march", receipt.task
    # §5.5: the hash of what the provider returned, never its content.
    assert receipt.scope_hash and receipt.scope_hash.startswith("sha256:"), receipt.scope_hash
    # §10.1: which grants were charged, which metrics, how much.
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        receipt.budget_charges
    )

    # And the same three survive the round trip an evidence consumer actually makes.
    document = receipt.to_dict()
    for field in ("task", "scope_hash", "budget_charges"):
        assert document[field], f"{field} is empty in the serialized receipt"


#: SPEC-v0.11 §8's table, which assigns ids **in item order** so that splitting the milestone
#: renumbers nothing. The items land one at a time, so between them the catalogue legitimately
#: has holes: item 4 lands `G31` while `G28` (item 2), `G29`, `G30` and `G32` (item 3) are still
#: unbuilt. A hole is allowed **only** if it is one of these, and the release item asserts the
#: set is complete before 0.11.0 ships.
_V0_11_GUARANTEES: Final = {
    "G28": "item 2, the anchor",
    "G29": "item 3, the prune",
    "G30": "item 3, the hold",
    "G31": "item 4, five receipt schemas",
    "G32": "item 3, an honestly pruned chain leaves a clean anchor report",
}


def test_the_guarantee_catalogue_has_no_hole_it_cannot_account_for() -> None:
    """`ctrlrun.guarantees/v7`. A catalogue with a stub row reads as a shipped guarantee, which is
    why the schema moves once per milestone rather than several branches racing it.

    **This asserted contiguity from `G1` until v0.11**, and contiguity is not what the check was
    ever for: it was for *a number nobody built*. `SPEC-v0.11.md` §8 assigns ids in **item**
    order rather than landing order, deliberately, so that cutting the milestone in two
    renumbers nothing, and item 4's `G31` therefore lands while `G28` to `G30` do not exist yet.
    Relaxing to "increasing and unique" would have given up the check entirely, so instead a gap
    must be a number §8 named and nothing else, and every id present must be one this project
    assigned.
    """
    assert CATALOGUE == "ctrlrun.guarantees/v7", CATALOGUE

    ids = [entry.id for entry in GUARANTEES]
    numbers = [int(entry.id[1:]) for entry in GUARANTEES]
    assert numbers == sorted(set(numbers)), f"the catalogue is out of order or repeats: {ids}"

    missing = [f"G{number}" for number in range(1, max(numbers) + 1) if f"G{number}" not in ids]
    unexplained = [name for name in missing if name not in _V0_11_GUARANTEES]
    assert not unexplained, (
        f"the catalogue skips {unexplained}, and SPEC-v0.11 §8 does not assign those numbers to "
        "an item that has not landed. A gap here is a guarantee nobody built"
    )
    if missing:
        # Not an error, but it must be a number §8 named, and it says which item owes it. The
        # release item is where this list is required to be empty.
        assert all(name in _V0_11_GUARANTEES for name in missing), missing
    for entry in GUARANTEES:
        # A row whose title is a placeholder reads as a shipped guarantee in every report that
        # prints the catalogue, which is the failure this assertion is actually for.
        assert entry.title.strip(), entry.id
        assert not entry.title.upper().startswith("TODO"), entry.id


def test_the_policy_schema_accepts_v7_keys_and_an_older_reader_refuses_them() -> None:
    """`ctrlrun.policy/v7`. `tasks:` and `budgets:` are **refused** in a `v6` document rather than
    ignored, because an older reader would grant the action on every task and against no limit."""
    from ctrlrun.errors import PolicyError

    assert Authority.from_yaml(DOC, source="<v7>").grants["payer"].tasks == ("refund-run:*",)
    assert Authority.from_yaml(DOC, source="<v7>").grants["payer"].budgets[0].limit == 1000

    older = DOC.replace("ctrlrun.policy/v7", "ctrlrun.policy/v6")
    with pytest.raises(PolicyError):
        Authority.from_yaml(older, source="<v6>")


def test_the_window_is_seconds_everywhere_it_is_stored() -> None:
    """§10's `Budget` row: a `timedelta` in Python, ISO-8601 in the document, integer seconds in
    `_canonical_grant`, because a `timedelta` is not a `PlainValue` and cannot render through
    `canonical_bytes`."""
    from ctrlrun.authority import grant_to_json

    grant = Authority.from_yaml(DOC, source="<d>").grants["payer"]
    assert grant.budgets[0].window == timedelta(hours=24)

    stored: Any = grant_to_json(grant)
    assert '"window": 86400' in stored or '"window":86400' in stored, stored

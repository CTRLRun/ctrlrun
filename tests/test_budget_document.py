"""T399 to T407: the budget in the document (SPEC-v0.9 §2).

Parse, validate, canonicalise into the policy hash, and contain. **Nothing counts anything in
this item**: no ledger, no reservation, no execution. Item 4 builds the counter and item 5
spends it.

The window axis is the one to get right. A draft of §2.6 had it inverted, and the rule as written
would have accepted a child spending 24x its parent's authority while rejecting the child that was
genuinely narrower. T401 and T402 are written in both directions for that reason.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ctrlrun.authority import (
    Authority,
    Budget,
    Grant,
    Subject,
    canonical_grants,
    contained_dimension,
)
from ctrlrun.errors import InvalidArgument, PolicyError
from ctrlrun.policy import Policy

pytestmark = pytest.mark.authority

DAY = timedelta(hours=24)
HOUR = timedelta(hours=1)
WEEK = timedelta(days=7)
MONTH = timedelta(days=30)

DOCUMENT = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    decision: allow
authority:
  grants:
    - id: payments-agent
      subject: {agent: "payer"}
      actions: ["payments.*"]
      budgets:
        - metric: amount
          limit: 100000
          window: PT24H
"""


def _grant(*budgets: Budget, **overrides) -> Grant:
    fields = dict(
        id=overrides.pop("id", "g"),
        subject=Subject(agent="payer", user="ada"),
        actions=("payments.refund",),
        budgets=budgets or None,
    )
    fields.update(overrides)
    return Grant(**fields)


def test_T399_a_well_formed_budget_loads_and_renders() -> None:
    authority = Authority.from_yaml(DOCUMENT, source="<d>")
    grant = authority.grants["payments-agent"]
    assert grant.budgets is not None
    budget = grant.budgets[0]
    assert (budget.metric, budget.limit, budget.window) == ("amount", 100000, DAY)


def test_T400_a_child_limit_above_its_parents_is_rejected() -> None:
    parent = _grant(Budget("amount", 100000, DAY))
    child = _grant(Budget("amount", 100001, DAY))
    assert contained_dimension(parent, child) == "budgets"


def test_T401_a_child_window_SHORTER_than_its_parents_is_rejected() -> None:
    """§2.6's axis that reads backwards, with the 24x case on the page beside the assertion.

    Parent: 100,000 per rolling day. Child: 100,000 per rolling **hour**, which is 2,400,000 a
    day. A draft of the rule accepted exactly this.
    """
    parent = _grant(Budget("amount", 100000, DAY))
    child = _grant(Budget("amount", 100000, HOUR))
    assert contained_dimension(parent, child) == "budgets"


def test_T402_a_child_window_LONGER_than_its_parents_is_accepted() -> None:
    """The same limit over a week is one seventh the rate: narrower, and contained."""
    parent = _grant(Budget("amount", 100000, DAY))
    child = _grant(Budget("amount", 100000, WEEK))
    assert contained_dimension(parent, child) is None


def test_T402a_the_pairing_rule_over_the_two_budget_parent_SS2_2_exists_for() -> None:
    """§2.6.1's four worked rows. A mapping keyed by metric could not express this parent."""
    parent = _grant(Budget("amount", 100000, DAY), Budget("amount", 500000, MONTH))
    # Row 1: each parent budget discharged by its own child budget.
    both = _grant(Budget("amount", 50000, DAY), Budget("amount", 100000, MONTH))
    assert contained_dimension(parent, both) is None
    # Row 2: ONE child budget discharges both, being under each limit and at least as long as
    # each window. A 30-day cap of 50,000 implies a 24-hour cap of 50,000.
    one = _grant(Budget("amount", 50000, MONTH))
    assert contained_dimension(parent, one) is None
    # Row 3: the monthly parent budget is discharged by nothing. 50,000/day x 30 is 1,500,000.
    daily_only = _grant(Budget("amount", 50000, DAY))
    assert contained_dimension(parent, daily_only) == "budgets"
    # Row 4: shorter window, higher rate.
    hourly = _grant(Budget("amount", 100000, HOUR))
    assert contained_dimension(parent, hourly) == "budgets"


def test_T403_a_child_omitting_a_budget_its_parent_carries_is_rejected() -> None:
    """`v0.3 §5.4`, unchanged and with no exception for budgets."""
    parent = _grant(Budget("amount", 100000, DAY))
    assert contained_dimension(parent, _grant()) == "budgets"


def test_T404_a_child_budget_on_a_metric_the_parent_does_not_budget_is_an_addition() -> None:
    parent = _grant(Budget("amount", 100000, DAY))
    child = _grant(Budget("amount", 50000, DAY), Budget("count", 10, DAY))
    assert contained_dimension(parent, child) is None


def test_T405_a_budget_changed_in_the_document_moves_the_policy_hash() -> None:
    one = canonical_grants(Authority.from_yaml(DOCUMENT, source="<a>"))
    widened = canonical_grants(
        Authority.from_yaml(DOCUMENT.replace("limit: 100000", "limit: 10000000"), source="<b>")
    )
    assert one != widened, "a budget outside the canonical render is one outside the hash"


def test_T405a_a_budget_in_a_break_glass_envelope_moves_the_hash_too() -> None:
    """`canonical_grants` walks envelopes through `_canonical_grant`; §2.8's reason for both."""
    envelope = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    decision: allow
authority:
  grants:
    - id: everyday
      subject: {agent: "ops"}
      actions: ["payments.read"]
  break_glass:
    incident:
      subject: {agent: "oncall-*"}
      actions: ["payments.*"]
      max_ttl: PT4H
      budgets:
        - {metric: amount, limit: 50000, window: PT24H}
"""
    one = canonical_grants(Authority.from_yaml(envelope, source="<a>"))
    two = canonical_grants(
        Authority.from_yaml(envelope.replace("limit: 50000", "limit: 5000000"), source="<b>")
    )
    assert one != two


def test_T406_two_budgets_on_one_metric_load_in_document_order() -> None:
    text = DOCUMENT.replace(
        "        - metric: amount\n          limit: 100000\n          window: PT24H\n",
        "        - {metric: amount, limit: 100000, window: PT24H}\n"
        "        - {metric: amount, limit: 500000, window: P30D}\n",
    )
    grant = Authority.from_yaml(text, source="<d>").grants["payments-agent"]
    assert grant.budgets is not None
    assert [b.window for b in grant.budgets] == [DAY, MONTH], "list order is the document's"


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ("limit: 1.5", "a float limit"),
        ('limit: "100.50"', "a decimal string limit"),
        ("limit: -1", "a negative limit"),
        ("limit: true", "a bool limit, which is an int in Python"),
        ("window: PT0S", "a zero window"),
        ("window: -PT1H", "a negative window"),
        ("metric: 7", "a non-string metric"),
    ],
)
def test_T407_the_loader_and_the_constructor_refuse_the_same_shapes(bad: str, why: str) -> None:
    """§2.2's rule: the model refuses exactly what the loader refuses.

    The decimal **string** is the one to write first: YAML hands a quoted number back as a `str`,
    so `"100.50"` is the shape an operator actually produces, and coercing it would put the drift
    back through the door `v0.1 §2.3` closed.
    """
    key = bad.split(":")[0]
    text = DOCUMENT.replace(f"{key}: 100000" if key == "limit" else f"{key}: PT24H", bad)
    if key == "metric":
        text = DOCUMENT.replace("metric: amount", bad)
    with pytest.raises(PolicyError):
        Policy.from_yaml(text, source="<bad>")


@pytest.mark.parametrize(
    ("limit", "window"),
    [(1.5, DAY), (-1, DAY), (True, DAY), (100, timedelta(0)), (100, -HOUR)],
)
def test_T407a_the_constructor_refuses_what_the_loader_refuses(limit, window) -> None:
    with pytest.raises(InvalidArgument):
        Budget("amount", limit, window)


def test_T407b_a_budget_is_refused_in_a_v6_document() -> None:
    older = DOCUMENT.replace("ctrlrun.policy/v7", "ctrlrun.policy/v6")
    with pytest.raises(PolicyError) as caught:
        Policy.from_yaml(older, source="<old>")
    assert "budgets" in str(caught.value)

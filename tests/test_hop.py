# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""SPEC-v0.10 §2, item 1: the hop, and the envelope that crosses it.

T470 to T477 and T488a. The centre of the file is T472: §2.3.1 measured that a receiving agent
holding a grant of its own is authorised by that one, the hop never consulted and the issuer's
budget charged nothing, and that which way it went turned on how two identifiers sort. Everything
else here is the containment relation v0.3 already shipped, exercised across a boundary.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import (
    AUTHORITY_HOP,
    CONTAINMENT,
    DIMENSIONS,
    Authority,
    AuthorityEscalation,
    grant_from_yaml,
)
from ctrlrun.state import SQLiteStateStore

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

DOC = """
schema: ctrlrun.policy/v7
authority:
  max_delegation_depth: {depth}
  grants:
    - id: {issuer}
      subject: {{ agent: "planner" }}
      actions: ["stripe.refund", "stripe.refund.partial"]
      resources: ["payment:*"]
      environments: ["production"]
      constraints: {{ amount_lte: 100000 }}
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
      tasks: ["refund-run:*"]
      budgets:
        - {{ metric: amount, limit: 100000, window: PT24H }}
{extra}"""

OWN_GRANT = """    - id: {own}
      subject: {{ agent: "worker" }}
      actions: ["stripe.refund"]
      resources: ["payment:*"]
      environments: ["production"]
"""

NARROW = """
subject: { agent: "worker" }
actions: ["stripe.refund"]
resources: ["payment:EU-1"]
environments: ["production"]
constraints: { amount_lte: 5000 }
expires_at: "2026-10-01T00:00:00Z"
tasks: ["refund-run:7"]
budgets:
  - { metric: amount, limit: 20000, window: P30D }
"""


def _authority(*, issuer: str = "issuer", own: str | None = None, depth: int = 3) -> Authority:
    extra = OWN_GRANT.format(own=own) if own else ""
    return Authority.from_yaml(
        DOC.format(issuer=issuer, depth=depth, extra=extra), source="test_hop"
    )


def _store(tmp_path: Any, name: str = "s.db") -> SQLiteStateStore:
    return SQLiteStateStore(str(tmp_path / name))


def _action(**kw: Any) -> Action:
    base: dict[str, Any] = {
        "name": "stripe.refund",
        "resource": "payment:EU-1",
        "arguments": {"amount": 1000, "payment": "EU-1"},
        "principal": Principal(agent="worker"),
        "environment": "production",
    }
    base.update(kw)
    return Action(**base)


def _hop(authority: Authority, store: SQLiteStateStore, parent: str = "issuer", **over: Any):
    """One hop beneath `parent`, narrowed on every dimension the parent constrains."""
    child = grant_from_yaml(NARROW, source="test_hop")
    from dataclasses import replace

    if over:
        child = replace(child, **over)
    planned = authority.plan_delegation(
        parent, child, by=Principal(agent="planner"), store=store, now=datetime.now(UTC)
    )
    store.put_delegation(planned.to_record())
    return planned


# --- T470 ---------------------------------------------------------------------------------


def test_T470_a_hop_that_narrows_is_admitted_and_its_action_runs(tmp_path):
    """The negative control for every row below (SPEC-v0.10 §2.4).

    Without it a kernel that refused every hop whatever would pass T471 on all eight dimensions,
    which is what `v0.4 §2.2` means by a guarantee that could not have failed.
    """
    authority, store = _authority(), _store(tmp_path)
    hop = _hop(authority, store)

    result = authority.evaluate(
        _action(),
        now=datetime.now(UTC),
        store=store,
        task="refund-run:7",
        hop=hop.delegation_id,
    )

    assert result.passed, result.reason
    assert result.grant_id == hop.delegation_id
    assert result.hop == hop.delegation_id


# --- T471 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_T471_a_hop_that_widens_on_any_dimension_is_refused(dimension, tmp_path):
    """One row of `v0.3 §5.4` at a time, so each breaks alone, as that section's T76 does.

    Asserted **by reason and by dimension**, never by exception type: `AuthorityEscalation` is
    raised for six different rules, and a test that asserted only the type could not tell which
    guard ran (SPEC-v0.10 §2.7).
    """

    authority, store = _authority(), _store(tmp_path)
    parent = authority.grants["issuer"]
    child = grant_from_yaml(NARROW, source="test_hop")
    widened = _widen_one(child, parent, dimension)
    if widened is None:
        pytest.skip(f"{dimension} cannot be widened beyond this parent")

    with pytest.raises(AuthorityEscalation) as raised:
        authority.plan_delegation(
            "issuer",
            widened,
            by=Principal(agent="planner"),
            store=store,
            now=datetime.now(UTC),
        )

    assert raised.value.reason == CONTAINMENT
    assert raised.value.dimension == dimension, (
        f"widening {dimension} was refused on {raised.value.dimension!r}"
    )
    assert list(store.delegations()) == [], "a refused hop wrote a record"


def _widen_one(child, parent, dimension):
    """Widen exactly one dimension past the parent, holding the rest contained."""
    from dataclasses import replace

    from ctrlrun.authority import Budget, Subject
    from ctrlrun.policy import Condition

    if dimension == "subject":
        return replace(child, subject=Subject(agent="*"))
    if dimension == "actions":
        return replace(child, actions=("stripe.**",))
    if dimension == "resources":
        return replace(child, resources=("payment:**",))
    if dimension == "constraints":
        return replace(
            child, constraints={"amount_lte": Condition("amount_lte", "amount", "lte", 999999)}
        )
    if dimension == "environments":
        return replace(child, environments=("production", "staging"))
    if dimension == "expires_at":
        return replace(child, expires_at=parent.expires_at + timedelta(days=1))
    if dimension == "tasks":
        return replace(child, tasks=("**",))
    if dimension == "budgets":
        # §2.6's window axis, which reads backwards: the same limit over a SHORTER window is a
        # higher rate, and therefore more authority.
        return replace(
            child, budgets=(Budget(metric="amount", limit=20000, window=timedelta(hours=1)),)
        )
    return None


# --- T472 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("own", "note"),
    [("aaa-own", "the receiver's own grant sorts BEFORE dlg_"), ("zzz-own", "and AFTER it")],
)
def test_T472_the_hop_decides_whichever_way_the_two_ids_sort(own, note, tmp_path):
    """**P1's document, as a test** (SPEC-v0.10 §2.3.1, §2.3.2).

    The receiving principal holds a grant of its own that admits more than the hop does. Before
    §2.3's rule, `Authority.evaluate` returned `min(passed, key=_by_grant_id)` over both, so the
    receiver's own grant was named whenever it sorted first, the hop was never consulted, and
    `_charges_for` returned `()` so the issuer's budget paid nothing.

    **Parametrized over both codepoint orders**, because with the rule absent exactly one of the
    two orders goes green, and a test that ran only that one would report `PASS` on a build with
    no rule at all. §2.3.1 measured both directions and this is them.
    """
    authority, store = _authority(own=own), _store(tmp_path)
    hop = _hop(authority, store)
    now = datetime.now(UTC)

    # Inside the hop's envelope: the hop decides, and the issuer is charged.
    inside = authority.evaluate(
        _action(), now=now, store=store, task="refund-run:7", hop=hop.delegation_id
    )
    assert inside.passed, inside.reason
    assert inside.grant_id == hop.delegation_id, (
        f"{note}, and the decision named {inside.grant_id!r} rather than the hop"
    )
    charged = {charge.grant_id for charge in authority._charges_for(_action(), inside, store=store)}
    assert charged == {hop.delegation_id, "issuer"}, (
        "rule 2: the issuing agent's budget is what a hop spends, and every ancestor is charged"
    )

    # Outside it, on a resource the hop does not name but the receiver's own grant does.
    outside = authority.evaluate(
        _action(resource="payment:US-9"),
        now=now,
        store=store,
        task="refund-run:7",
        hop=hop.delegation_id,
    )
    assert not outside.passed, (
        f"{note}, and the receiver's own grant authorised an action the hop does not cover"
    )
    assert outside.reason == AUTHORITY_HOP
    assert outside.dimension == "resources"
    assert outside.hop == hop.delegation_id


def test_T472b_the_same_document_with_no_hop_presented_is_decided_as_0_9_0_decided_it(tmp_path):
    """The other half of §2.3.2: `hop=None` changes nothing, which is why every existing
    deployment upgrades untouched. The receiver's own grant authorises the wider action, exactly
    as it did at 0.9.0, and nothing about presenting no hop is a refusal."""
    authority, store = _authority(own="aaa-own"), _store(tmp_path)
    _hop(authority, store)

    result = authority.evaluate(
        _action(resource="payment:US-9"), now=datetime.now(UTC), store=store
    )

    assert result.passed
    assert result.grant_id == "aaa-own"
    assert result.hop is None


# --- T473, T474 ---------------------------------------------------------------------------


def test_T473_an_action_with_no_hop_is_unchanged_over_a_document_carrying_hops(tmp_path):
    """Opt in, then fail closed (`v0.3 §1.2`). A store full of hops changes nothing for a
    caller that presents none, which is what makes this milestone safe to ship."""
    authority, store = _authority(), _store(tmp_path)
    for _ in range(3):
        _hop(authority, store)

    planner = authority.evaluate(
        _action(principal=Principal(agent="planner"), arguments={"amount": 900}),
        now=datetime.now(UTC),
        store=store,
        task="refund-run:1",
    )

    assert planner.passed
    assert planner.grant_id == "issuer"
    assert planner.hop is None


def test_T474_each_hop_refusal_carries_its_own_reason_and_never_anothers(tmp_path):
    """§2.3.2 rule 3, and §2.3.3's correction to it.

    `authority_hop` is not a bucket. A revoked chain keeps `authority_revoked`, an expired hop
    keeps `authority_expired`, and only the two facts about the hop itself -- an id naming
    nothing, and a grant that does not reach the action -- are `authority_hop`. An earlier draft
    of rule 2 demanded `authority_hop` for a hop that is not "live", which contradicted rule 3
    for the same input.
    """
    authority, store = _authority(), _store(tmp_path)
    now = datetime.now(UTC)
    live = _hop(authority, store)

    unknown = authority.evaluate(
        _action(), now=now, store=store, task="refund-run:7", hop="dlg_" + "0" * 32
    )
    assert unknown.reason == AUTHORITY_HOP
    assert unknown.hop == "dlg_" + "0" * 32
    assert unknown.grant_id is None, "an id naming nothing implicates no grant"
    assert unknown.dimension is None, "and has no grant whose rows could have failed"

    not_mine = authority.evaluate(
        _action(principal=Principal(agent="someone-else")),
        now=now,
        store=store,
        task="refund-run:7",
        hop=live.delegation_id,
    )
    assert not_mine.reason == AUTHORITY_HOP
    assert not_mine.dimension == "subject"

    # Every row of `unmatched_shape`, not just the two the prose names. A mutation run found
    # `environments` and `actions` covered by nothing: removing either row left the suite green,
    # which is the shape SPEC-v0.9 §13.0 records shipping three times in one milestone.
    for dimension, over in (
        ("actions", {"name": "stripe.refund.partial"}),
        ("resources", {"resource": "payment:US-9"}),
        ("environments", {"environment": "staging"}),
    ):
        off = authority.evaluate(
            _action(**over), now=now, store=store, task="refund-run:7", hop=live.delegation_id
        )
        assert off.reason == AUTHORITY_HOP, f"{dimension}: {off.reason}"
        assert off.dimension == dimension, f"{dimension}: named {off.dimension!r}"
        assert off.hop == live.delegation_id

    store.revoke_delegation(live.delegation_id, by="operator", at=now)
    revoked = authority.evaluate(
        _action(), now=now, store=store, task="refund-run:7", hop=live.delegation_id
    )
    assert revoked.reason == "authority_revoked", (
        "a revoked hop keeps the chain's reason; authority_hop is not a bucket for it"
    )


def test_T474b_a_hop_off_its_task_or_past_its_constraint_keeps_the_grants_own_reason(tmp_path):
    """The other half of rule 3: a hop that DID reach the action and then failed on one of the
    grant's own dimensions reports that dimension's reason, because those refusals already name
    the grant and folding them into `authority_hop` would lose which one failed (§2.3.3)."""
    authority, store = _authority(), _store(tmp_path)
    now = datetime.now(UTC)
    hop = _hop(authority, store)

    off_task = authority.evaluate(
        _action(), now=now, store=store, task="refund-run:9", hop=hop.delegation_id
    )
    assert off_task.reason == "authority_task"
    assert off_task.grant_id == hop.delegation_id

    too_much = authority.evaluate(
        _action(arguments={"amount": 99999}),
        now=now,
        store=store,
        task="refund-run:7",
        hop=hop.delegation_id,
    )
    assert too_much.reason == "authority_constraint"
    assert too_much.grant_id == hop.delegation_id


# --- T475, T477 ---------------------------------------------------------------------------


def test_T475_two_hops_hold_no_more_than_the_root_granted(tmp_path):
    """§2.6, as a test. A narrowed root narrows everything two hops beneath it, on the next
    evaluation, with no writes and without finding the children.

    This is `v0.3 §5.6`'s stated purpose holding across a boundary, and it is the property §3.2
    refuses to trade away when it settles that a hop is a record rather than a token.
    """

    wide, store = _authority(), _store(tmp_path)
    now = datetime.now(UTC)
    first = _hop(wide, store, delegable=True)
    second_child = grant_from_yaml(
        NARROW.replace('agent: "worker"', 'agent: "worker-b"'), source="test_hop"
    )
    planned = wide.plan_delegation(
        first.delegation_id, second_child, by=Principal(agent="worker"), store=store, now=now
    )
    store.put_delegation(planned.to_record())
    assert (first.depth, planned.depth) == (1, 2)

    action = _action(principal=Principal(agent="worker-b"))
    under_wide = wide.evaluate(
        action, now=now, store=store, task="refund-run:7", hop=planned.delegation_id
    )
    assert under_wide.passed, under_wide.reason

    # The same store, the same chain, a root the operator has narrowed in the document.
    narrowed = Authority.from_yaml(
        DOC.format(issuer="issuer", depth=3, extra="").replace(
            "amount_lte: 100000", "amount_lte: 100"
        ),
        source="test_hop",
    )
    after = narrowed.evaluate(
        action, now=now, store=store, task="refund-run:7", hop=planned.delegation_id
    )

    assert not after.passed
    assert after.reason == "authority_escalation"
    assert after.dimension == "constraints"


def test_T477_max_delegation_depth_counts_hops_and_delegations_in_one_chain(tmp_path):
    """§2.5, and O3. A hop is a link in the same chain, not a second counter, so a third link is
    refused `max_depth` whatever the mix of the first two."""
    from dataclasses import replace

    authority, store = _authority(depth=2), _store(tmp_path)
    now = datetime.now(UTC)
    first = _hop(authority, store, delegable=True)
    second = replace(
        grant_from_yaml(NARROW.replace('agent: "worker"', 'agent: "worker-b"'), source="t"),
        delegable=True,
    )
    planned = authority.plan_delegation(
        first.delegation_id, second, by=Principal(agent="worker"), store=store, now=now
    )
    store.put_delegation(planned.to_record())

    third = grant_from_yaml(NARROW.replace('agent: "worker"', 'agent: "worker-c"'), source="t")
    with pytest.raises(AuthorityEscalation) as raised:
        authority.plan_delegation(
            planned.delegation_id,
            third,
            by=Principal(agent="worker-b"),
            store=store,
            now=now,
        )

    assert raised.value.reason == "max_depth"


# --- T488a --------------------------------------------------------------------------------


def test_T488a_the_lease_extension_takes_the_hop_from_its_caller_and_not_the_context(tmp_path):
    """**SPEC-v0.10 §3.4.3, and the defect a second review round found in the first round's
    answer.**

    `Control._suspend` re-decides authority (`control.py:2218`) and raises, which is what makes
    `v0.3 §5.7`'s "a chain of any depth is cut by one write" true of actions in flight. The first
    answer had it read the hop from a context variable, reasoning that `_suspend` runs inside the
    `execute` call that was given one.

    It does not, on any round after the first. `_outcome`'s own docstring says "`execute` and
    `resume` both come through here", and `_suspend` is called from inside `_outcome`, so round
    two onward reaches it from `Control.resume` -- whose ambient context belongs to the resuming
    process, and may hold an unrelated hop or none.

    So the hop is a **parameter**, threaded `execute` -> `_outcome` -> `_suspend`, and this test
    pins the shape rather than the reasoning: the decision path reads no context variable, and
    every caller that can supply a hop declares it.
    """
    import inspect

    from ctrlrun.control import Control

    body = inspect.getsource(Control._suspend)
    assert "_HOP.get" not in body, (
        "the lease extension must take its hop from the caller: `_outcome`'s docstring says "
        "`execute` and `resume` both come through it, so the ambient value is the resuming "
        "process's on every round after the first"
    )
    assert "hop" in inspect.signature(Control._suspend).parameters
    assert "hop" in inspect.signature(Control._outcome).parameters
    assert "hop" in inspect.signature(Control.execute).parameters
    assert "hop" in inspect.signature(Control.evaluate).parameters


def test_T488b_the_context_variable_is_evidence_only(tmp_path):
    """`_HOP` is `_TASK`'s twin and carries the hop to the receipt, which is item 2's field. It
    is deliberately not a decision input anywhere: a grep is the test, because the failure mode
    is a future edit reaching for the ambient value again."""
    from pathlib import Path as _Path

    source = _Path("src/ctrlrun/control.py").read_text()
    reads = [line.strip() for line in source.splitlines() if "_HOP.get" in line]

    # The receipt writer reads it, which is the point: `_HOP` is `_TASK`'s twin and carries the
    # hop to the evidence. What must never happen is a DECISION reading it, and the shape that
    # would take is passing it to `_authority_result`, which is what `_suspend` did once.
    decisions = [line for line in reads if "_authority_result" in line or "evaluate(" in line]
    assert decisions == [], f"a decision reads the hop from the context: {decisions}"
    assert any("hop=_HOP.get" in line for line in reads), (
        "the receipt should still carry the hop; if this fails the evidence path lost it"
    )


# --- T476 ---------------------------------------------------------------------------------

_HOP_WORKERS = 8
_HOP_AMOUNT = 3000
#: The issuer's budget. Eight workers at 3,000 against 12,000 is four spends and four refusals,
#: so the test fails in both directions: an implementation that charged nobody would let all
#: eight through, and one that refused everything would spend nothing.
_HOP_LIMIT = 12000


RACE_DOC = """
schema: ctrlrun.policy/v7
authority:
  max_delegation_depth: 3
  grants:
    - id: issuer
      subject: {{ agent: "planner" }}
      actions: ["stripe.refund"]
      resources: ["payment:*"]
      environments: ["production"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
      budgets:
        - {{ metric: amount, limit: {limit}, window: PT24H }}
"""

RACE_CHILD = """
subject: {{ agent: "worker-{index}" }}
actions: ["stripe.refund"]
resources: ["payment:EU-{index}"]
environments: ["production"]
expires_at: "2026-10-01T00:00:00Z"
budgets:
  # Contained on both axes (§2.6): the same limit over the same window. A child whose limit
  # exceeded its parent's is refused at creation, which is what an earlier draft of this
  # fixture discovered.
  - {{ metric: amount, limit: {limit}, window: PT24H }}
"""


def _hop_race_worker(args):
    """One process, racing the others for the ISSUER's budget through its own hop.

    **The charges come from `_charges_for` over a real chain**, not hand-written: a worker that
    built its own `Charge` tuple would be testing v0.9's store, which v0.9 already tested, and
    would pass on a build where §2.7's walk was never reached.
    """
    index, url, schema, barrier = args
    from ctrlrun.postgres import PostgresStateStore
    from ctrlrun.state import BudgetExhaustedError

    made = PostgresStateStore(url, schema=schema)
    try:
        authority = Authority.from_yaml(RACE_DOC.format(limit=_HOP_LIMIT), source="race")
        now = datetime.now(UTC)
        hop = authority.plan_delegation(
            "issuer",
            grant_from_yaml(RACE_CHILD.format(index=index, limit=_HOP_LIMIT), source="race"),
            by=Principal(agent="planner"),
            store=made,
            now=now,
        )
        made.put_delegation(hop.to_record())
        action = Action(
            name="stripe.refund",
            resource=f"payment:EU-{index}",
            arguments={"amount": _HOP_AMOUNT},
            principal=Principal(agent=f"worker-{index}"),
            environment="production",
        )
        result = authority.evaluate(action, now=now, store=made, hop=hop.delegation_id)
        if not result.passed:
            return f"error:not-authorised:{result.reason}"
        charges = authority._charges_for(action, result, store=made)
        if {charge.grant_id for charge in charges} != {hop.delegation_id, "issuer"}:
            return f"error:charges:{sorted(c.grant_id for c in charges)}"
        barrier.wait()
        made.reserve_effect(f"hop-k{index}", "a", timedelta(minutes=5), charges)
        return "spent"
    except BudgetExhaustedError:
        return "refused"
    except Exception as exc:
        return f"error:{type(exc).__name__}: {exc}"
    finally:
        made.close()


@pytest.mark.serial
@pytest.mark.skipif(POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set")
@pytest.mark.parametrize("run", range(3))
def test_T476_eight_agents_hopping_one_issuer_spend_at_most_the_issuers_budget(run: int) -> None:
    """**The quantitative half of rule 2, under the `v0.6` multi-process standard.**

    Eight distinct receiving agents, eight distinct hops, eight distinct effect keys, all
    charging one issuer. Without §2.7's charge-every-ancestor the total is eight times what
    anybody granted; the point of doing it across processes is that a counter correct in one
    process is not a claim about anything an operator runs.

    Distinct effect keys on purpose, so the effects table's own uniqueness does not serialise
    the workers and hide the defect. `manager.Barrier` so they contend: `SPEC-v0.10`'s build plan
    records that v0.9 shipped a race test holding 4/4 against a deliberately unlocked
    implementation until a barrier was added.

    Run three times, because a broken implementation is occasionally right (`v0.9 §3.6.2`).
    """
    from ctrlrun.postgres import PostgresStateStore

    schema = f"hoprace_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        with mp.Manager() as manager:
            barrier = manager.Barrier(_HOP_WORKERS)
            with mp.Pool(_HOP_WORKERS) as pool:
                results = pool.map(
                    _hop_race_worker,
                    [(i, POSTGRES_URL, schema, barrier) for i in range(_HOP_WORKERS)],
                )
        reader = PostgresStateStore(POSTGRES_URL, schema=schema)
        try:
            spent = sum(
                row.amount
                for row in reader.consumptions(grant_id="issuer")
                if row.released_at is None
            )
            hop_rows = sum(
                1
                for row in reader.consumptions()
                if row.grant_id.startswith("dlg_") and row.released_at is None
            )
        finally:
            reader.close()
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)

    errors = [result for result in results if result.startswith("error")]
    assert not errors, f"a worker failed for a reason that is not the budget: {errors[:3]}"
    assert spent <= _HOP_LIMIT, (
        f"{_HOP_WORKERS} agents hopping one issuer spent {spent} against a limit of {_HOP_LIMIT}"
    )
    assert results.count("spent") == _HOP_LIMIT // _HOP_AMOUNT, (
        "the issuer's limit must be spent exactly, not under-spent: an implementation that "
        "refused everything would satisfy the bound above and grade green"
    )
    assert hop_rows == results.count("spent"), (
        "§2.7 writes one row per ancestor, so every spend that charged the issuer charged its "
        "own hop too"
    )


# --- item 2: §3, the receipt and the resumed leg -------------------------------------------


@pytest.mark.authority
def test_T478_both_ends_of_a_hop_name_it(tmp_path):
    """§1.2 rule 3. The receipt of an action run **under** a hop names it, and the
    `DELEGATION_CREATED` event that created it names it too, linked to the action that created
    it where the creator supplied one (§3.4.4)."""
    from ctrlrun.control import Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml(
        # An `effect:` template, because the hop carries a budget and `v0.9 §2.4.1` refuses a
        # budgeted grant whose action resolves no effect key: nothing could be charged.
        "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n"
        '    effect: "refund:{payment}"\n    decision: allow\n',
        source="t",
    )
    control = Control(policy, store, authority=authority)
    # Through `Control.hop`, because the event is half of what this test asserts: the helper
    # above writes the record straight to the store and appends nothing.
    hop = control.hop("issuer", grant_from_yaml(NARROW, source="t"), by=Principal(agent="planner"))

    # `Control.execute` takes the effect key; the policy's `effect:` template is `@protect`'s to
    # resolve (`v0.9 §2.4.1`), and a budgeted grant refuses an action that resolves none.
    receipt = control.execute(
        _action(), lambda: "ok", "refund:EU-1", task="refund-run:7", hop=hop.delegation_id
    )

    assert receipt.hop == hop.delegation_id
    assert receipt.schema == "ctrlrun.receipt/v7"
    created = [
        e
        for e in store.events()
        if e.type is EventType.DELEGATION_CREATED
        and e.data.get("delegation_id") == hop.delegation_id
    ]
    assert created and created[0].data["created_via"] == "hop"


@pytest.mark.authority
def test_T479_a_hop_created_outside_an_action_carries_no_action_id(tmp_path):
    """§3.4.4. `ctrlrun delegate` from a shell creates an authority record outside any action's
    life, and the event stays exactly as `v0.3 §7` has it."""
    from ctrlrun.control import Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml("schema: ctrlrun.policy/v7\nactions: {}\n", source="t")
    control = Control(policy, store, authority=authority)

    control.hop("issuer", grant_from_yaml(NARROW, source="t"), by=Principal(agent="planner"))

    created = [e for e in store.events() if e.type is EventType.DELEGATION_CREATED]
    assert len(created) == 1
    assert created[0].action_id is None


@pytest.mark.authority
def test_T485_a_leg_this_build_suspended_is_evaluated_on_both_dimensions(tmp_path):
    """§3.4.2. `EXECUTION_STARTED` carries the task and the hop, and a resumed leg is decided on
    both rather than skipping either. `v0.9 §6.3.2` named this change and the milestone that
    would want it."""
    from ctrlrun.control import Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml(
        # An `effect:` template, because the hop carries a budget and `v0.9 §2.4.1` refuses a
        # budgeted grant whose action resolves no effect key: nothing could be charged.
        "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n"
        '    effect: "refund:{payment}"\n    decision: allow\n',
        source="t",
    )
    control = Control(policy, store, authority=authority)
    hop = _hop(authority, store)
    control.execute(
        _action(), lambda: "ok", "refund:EU-1", task="refund-run:7", hop=hop.delegation_id
    )

    started = [e for e in store.events() if e.type is EventType.EXECUTION_STARTED]
    assert started, "no EXECUTION_STARTED was written"
    assert started[0].data["task"] == "refund-run:7"
    assert started[0].data["hop"] == hop.delegation_id


@pytest.mark.authority
def test_T487_the_resumed_leg_keys_on_the_key_and_not_the_value(tmp_path):
    """**The upgrade case, against the real `_resumed_context`** (SPEC-v0.10 §3.4.2).

    A 0.9.0 `EXECUTION_STARTED` carries `{}`. Evaluating the task dimension against an absent
    value hits `v0.9 §6.4` and denies every in-flight action across the upgrade, on the only
    receipt an MCP multi round-trip ever gets. So `{}` means `evaluate_task=False` and no hop.

    **And a present key with a `None` value is a value this build wrote.** A 0.10 build running
    under a hop with no task writes `{"hop": "dlg_...", "task": None}`; a reader keying on the
    VALUE reads that as 0.9.0's silence and drops a hop the event is carrying. A mutation run
    found the first version of this test could not tell the two apart, because it asserted over
    a dict literal instead of driving the function.
    """
    from ctrlrun.control import Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import Event, EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml("schema: ctrlrun.policy/v7\nactions: {}\n", source="t")
    control = Control(policy, store, authority=authority)
    action = _action()
    store.append_event(
        Event(
            type=EventType.EXECUTION_STARTED,
            action_id=action.action_id,
            ts=datetime.now(UTC),
            data={"task": None, "hop": "dlg_" + "1" * 32},
        )
    )

    _, _, _, bound = control._resumed_context(action, datetime.now(UTC))

    assert bound.recorded is True, (
        "a present key with a None value is a value this build wrote, not 0.9.0's silence"
    )
    assert bound.hop == "dlg_" + "1" * 32, "the resumed leg dropped a hop the event carried"
    assert bound.task is None

    # **The case that actually separates the two readings**, and a mutation run is what found
    # that the block above does not: with a hop present, keying on the value happens to agree.
    # A 0.10 build running with neither writes `{"task": None, "hop": None}`, and a value-keyed
    # reader calls that 0.9.0's silence, skips the task dimension, and lets a leg through that a
    # task-bound grant refuses. That is the fail-open direction.
    neither, neither_store = _authority(), _store(tmp_path, "s2.db")
    neither_control = Control(policy, neither_store, authority=neither)
    plain = _action()
    neither_store.append_event(
        Event(
            type=EventType.EXECUTION_STARTED,
            action_id=plain.action_id,
            ts=datetime.now(UTC),
            data={"task": None, "hop": None},
        )
    )

    _, _, _, both_none = neither_control._resumed_context(plain, datetime.now(UTC))

    assert both_none.recorded is True, (
        "both keys present with None values is a leg THIS build suspended under no hop and no "
        "task; read as 0.9.0's silence it skips the task dimension and admits a leg a "
        "task-bound grant refuses"
    )


@pytest.mark.authority
def test_T487b_a_leg_0_9_0_suspended_is_evaluated_as_0_9_0_evaluated_it(tmp_path):
    """The other row of §3.4.2's table: `{}` is the only shape that means the fields predate
    this build, and it must not be read as "the caller named neither"."""
    from ctrlrun.control import Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import Event, EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml("schema: ctrlrun.policy/v7\nactions: {}\n", source="t")
    control = Control(policy, store, authority=authority)
    action = _action()
    store.append_event(
        Event(
            type=EventType.EXECUTION_STARTED,
            action_id=action.action_id,
            ts=datetime.now(UTC),
            data={},
        )
    )

    _, _, _, bound = control._resumed_context(action, datetime.now(UTC))

    assert bound.recorded is False, (
        "evaluating the task dimension on a 0.9.0 leg denies every action in flight across the "
        "upgrade (v0.9 §6.4), on the only receipt an MCP multi round-trip ever gets"
    )
    assert bound.hop is None and bound.task is None


@pytest.mark.authority
def test_T488c_revoking_a_hop_cuts_an_in_flight_action_on_every_round(tmp_path):
    """**SPEC-v0.10 §3.4.3, and the test that section said must exist.**

    `Control._suspend` re-decides authority on every round of a suspended action, which is what
    makes `v0.3 §5.7`'s "a chain of any depth is cut by one write" true of actions in flight.
    `_outcome` is reached from `execute` on round one and from `resume` on every round after, so
    the hop has to travel both ways or round two falls back to the receiver's whole candidate set.

    **This suspends twice on purpose.** §3.4.3 says so in as many words: a test that suspends once
    exercises only round one, which is the round the first design was right about. An independent
    review found the gap with exactly this shape, and its control is the second assertion below:
    without a grant of its own the receiver is cut on round two either way, so a test that omitted
    the competing grant would pass over the defect.
    """
    from ctrlrun.control import Control
    from ctrlrun.errors import AuthorityDenied, Suspended
    from ctrlrun.policy import Policy

    policy = Policy.from_yaml(
        "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n"
        '    effect: "refund:{payment}"\n    decision: allow\n',
        source="t",
    )

    def run(*, receiver_holds_its_own: bool) -> str:
        authority = _authority(own="aaa-own" if receiver_holds_its_own else None)
        store = _store(tmp_path, f"s-{receiver_holds_its_own}.db")
        control = Control(policy, store, authority=authority)
        hop = control.hop(
            "issuer", grant_from_yaml(NARROW, source="t"), by=Principal(agent="planner")
        )
        rounds: list[str] = []

        def suspends() -> Any:
            raise Suspended(f"round-{len(rounds)}")

        try:
            control.execute(
                _action(), suspends, "refund:EU-1", task="refund-run:7", hop=hop.delegation_id
            )
        except Suspended:
            rounds.append("suspended")
        # Round two: the operator cuts the hop while the remote is still holding the exchange.
        control.revoke(hop.delegation_id, by="operator")
        try:
            control.resume("round-0", suspends)
        except Suspended:
            rounds.append("suspended")
            return "held across the revocation"
        except AuthorityDenied as denied:
            return f"cut: {denied.reason}"
        return "ran"

    assert run(receiver_holds_its_own=True) == "cut: authority_revoked", (
        "a receiver holding a grant of its own kept its reservation across the round trip after "
        "the hop was cut, which is the fallback SPEC-v0.10 §2.3 forbids"
    )
    # The control: without a competing grant the revocation cuts round two either way, so a test
    # that omitted the grant above would pass over the defect.
    assert run(receiver_holds_its_own=False) == "cut: authority_revoked"


@pytest.mark.authority
def test_T474c_a_malformed_hop_id_is_refused_without_reaching_the_log_verbatim(tmp_path):
    """§3.3, and an independent review's finding.

    The refusal was always right. The **evidence row** was not: a hop reaches the kernel from the
    caller, through `@protect`'s template and so from the action's arguments, and every refused
    action writes one `AUTHORITY_DENIED` into an append-only log an operator reads on a terminal.
    A megabyte of `A`, or NULs and ANSI escapes, went straight through.

    An id this kernel could not have minted is recorded as its length and nothing else.
    """
    from ctrlrun.receipt import EventType

    authority, store = _authority(), _store(tmp_path)
    now = datetime.now(UTC)
    for presented in ("A" * 4096, "dlg_x\n\ninjected\x00\x1b[31m", "issuer"):
        result = authority.evaluate(_action(), now=now, store=store, hop=presented)
        assert result.reason == AUTHORITY_HOP, presented
        assert result.hop == presented, "the result carries what was presented"

    from ctrlrun.control import Control
    from ctrlrun.policy import Policy

    control = Control(
        Policy.from_yaml(
            "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n    decision: allow\n",
            source="t",
        ),
        store,
        authority=authority,
    )
    from ctrlrun.errors import AuthorityDenied

    with pytest.raises(AuthorityDenied):
        control.execute(_action(), lambda: "ok", hop="A" * 4096)

    logged = [
        event.data.get("hop")
        for event in store.events()
        if event.type is EventType.AUTHORITY_DENIED and "hop" in event.data
    ]
    assert logged, "no AUTHORITY_DENIED carried a hop"
    assert all(len(value) < 64 for value in logged), (
        f"a caller-supplied id reached the evidence log at full length: {len(logged[-1])}"
    )
    assert "malformed" in logged[-1] and "4096" in logged[-1]


@pytest.mark.authority
def test_T483_a_hop_refusals_event_carries_the_exact_key_set_and_nothing_about_the_envelope(
    tmp_path,
):
    """§3.3's table, asserted **as a set**, both rows.

    The point is the negative half. A refusal may carry the id the caller already holds and the
    name of the row that stopped it; it may never carry the patterns, the limits, the subject or
    the expiry, because that turns each refusal into one question against the envelope and a peer
    enumerates it one key at a time. `v0.9 §5.5` refuses the same thing when it lets a scope hash
    reach a receipt while the scope never does.

    A set and not a `in` check: `assert "actions" not in data` names one leak and passes over
    every other. `==` is what makes a field added later go red here rather than ship.

    §11 records this test asserting a key set that matched neither the prose nor the code, so the
    two rows below are read off §3.3's table rather than off `_authority_data`.
    """
    from ctrlrun.control import Control
    from ctrlrun.errors import AuthorityDenied
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import EventType

    authority, store = _authority(), _store(tmp_path)
    control = Control(
        Policy.from_yaml(
            "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n    decision: allow\n",
            source="t",
        ),
        store,
        authority=authority,
    )

    def refusal_data(**kwargs: Any) -> dict[str, Any]:
        before = len(list(store.events()))
        with pytest.raises(AuthorityDenied):
            control.execute(
                _action(**{k: v for k, v in kwargs.items() if k != "hop"}),
                lambda: "ok",
                hop=kwargs["hop"],
            )
        rows = [
            event.data
            for event in list(store.events())[before:]
            if event.type is EventType.AUTHORITY_DENIED
        ]
        assert len(rows) == 1, rows
        return rows[0]

    # Row one: the id names no delegation. No grant is implicated, so no grant may be described.
    unknown = refusal_data(hop="dlg_" + "0" * 32)
    assert set(unknown) == {"reason", "hop"}, unknown
    assert unknown["reason"] == AUTHORITY_HOP
    assert unknown["hop"] == "dlg_" + "0" * 32

    # Row two: the delegation exists and its grant does not reach the action. One more key, the
    # *name* of the row that stopped it, and still nothing about what the row contains.
    live = _hop(authority, store)
    reached = refusal_data(hop=live.delegation_id, principal=Principal(agent="someone-else"))
    assert set(reached) == {"reason", "hop", "grant_id", "dimension"}, reached
    assert reached["dimension"] == "subject"
    assert reached["grant_id"] == live.delegation_id

    # And the envelope itself is nowhere in either payload, by value rather than by key name.
    envelope = json.dumps(
        {
            "actions": list(live.grant.actions),
            "resources": list(live.grant.resources),
            "environments": list(live.grant.environments),
            "subject": live.grant.subject.agent,
        }
    )
    for data in (unknown, reached):
        rendered = json.dumps(data)
        for fragment in ("worker", "payment:", "*"):
            assert fragment not in rendered or fragment in str(data.get("hop", "")), (
                f"{fragment!r} from {envelope} reached a refusal payload: {data}"
            )


@pytest.mark.authority
def test_T486_a_resume_inside_an_unrelated_ambient_task_and_hop_reads_neither(tmp_path):
    """`v0.9 §6.3.2`'s ambient-context hazard, at the hop (SPEC-v0.10 §3.4.2).

    A resumed leg is decided on what its `EXECUTION_STARTED` recorded, not on whatever the
    process happens to be inside when the resume runs. The hazard is concrete: a relay serving
    several agents resumes a suspended leg from inside its own `Control.hop(...)` block, and a
    resume that read the ambient value would decide agent A's suspended action against agent B's
    envelope. It is the fail-**open** direction whenever the ambient hop is wider than the
    recorded one.

    The ambient values here are deliberately *different* from the recorded ones rather than
    absent, so a reader that took the ambient value would produce a visibly wrong answer instead
    of the same answer by luck. That is mutation pattern 3: a test whose two sources agree proves
    nothing about which one was read.
    """
    from ctrlrun.control import _HOP, _TASK, Control
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import Event, EventType

    authority, store = _authority(), _store(tmp_path)
    policy = Policy.from_yaml("schema: ctrlrun.policy/v7\nactions: {}\n", source="t")
    control = Control(policy, store, authority=authority)
    action = _action()
    recorded_hop = "dlg_" + "a" * 32
    store.append_event(
        Event(
            type=EventType.EXECUTION_STARTED,
            action_id=action.action_id,
            ts=datetime.now(UTC),
            data={"task": "refund-run:7", "hop": recorded_hop},
        )
    )

    ambient_hop = "dlg_" + "b" * 32
    task_token, hop_token = _TASK.set("some-other-run:99"), _HOP.set(ambient_hop)
    try:
        _, _, _, bound = control._resumed_context(action, datetime.now(UTC))
    finally:
        _TASK.reset(task_token)
        _HOP.reset(hop_token)

    assert bound.hop == recorded_hop, (
        f"the resumed leg took the ambient hop {ambient_hop} over the one its "
        f"EXECUTION_STARTED recorded; that decides one agent's action against another's envelope"
    )
    assert bound.task == "refund-run:7", "and the same for the task dimension"
    assert bound.recorded is True

    # **The case that separates a fallback from a read**, and a mutation run is what found the
    # block above does not. A 0.10 build running with neither writes `{"task": null, "hop":
    # null}`. Both branches above take the event's value because it is a string, so a reader
    # that falls back to the ambient value *only when the event's is absent* passes everything
    # so far. Here the event recorded neither, and the answer must still be neither: the leg ran
    # under no hop, and the ambient one belongs to whoever is resuming it.
    empty, empty_store = _authority(), _store(tmp_path, "s3.db")
    empty_control = Control(policy, empty_store, authority=empty)
    plain = _action()
    empty_store.append_event(
        Event(
            type=EventType.EXECUTION_STARTED,
            action_id=plain.action_id,
            ts=datetime.now(UTC),
            data={"task": None, "hop": None},
        )
    )

    task_token, hop_token = _TASK.set("some-other-run:99"), _HOP.set(ambient_hop)
    try:
        _, _, _, neither = empty_control._resumed_context(plain, datetime.now(UTC))
    finally:
        _TASK.reset(task_token)
        _HOP.reset(hop_token)

    assert neither.hop is None, (
        f"a leg that recorded no hop was resumed under the resumer's own {ambient_hop}"
    )
    assert neither.task is None, "and the same for the task dimension"
    assert neither.recorded is True, "the keys were present, so this is not 0.9.0's silence"

"""Policy versioning and the control registry. Item 7; SPEC-v0.6 §7, §8 T171-T177d.

Two things in one item because both answer *"what decided this, and can I still tell?"* — the
policy hash on every receipt, and the registry that says which written expectation a rule serves.

The sharpest rule in this file is §7.3's second: **a control is attribution, not prevention.**
Citing `maker-checker-refunds` on a rule does not cause an approval; the rule's `decision:
approve` does. Any test whose name or docstring implies otherwise would be a false green in prose,
so the tests are written to say what a control *records* and never what it enforces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import Policy, SQLiteStateStore
from ctrlrun.errors import PolicyError

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)

TIDY = """
schema: ctrlrun.policy/v4
version: "2026.1"
environment: production
actions:
  stripe.refund:
    rules:
      - when: {amount_gt: 50000}
        decision: approve
      - decision: allow
  customer.read:
    decision: allow
"""

#: The same policy, reformatted the way a document drifts in a repository: comments, a different
#: key order inside each mapping, different quoting, more whitespace. Every *decision input* is
#: identical.
UNTIDY = """
# House policy. Reviewed 2026-01-04.
schema: 'ctrlrun.policy/v4'

environment:   production          # unchanged
version: "2026.1"

actions:

  stripe.refund:

    rules:

      -   decision: approve
          when:
            amount_gt: 50000

      -   decision: allow

  customer.read:
      decision: allow
"""


def policy_hash(text: str) -> str:
    return Policy.from_yaml(text).policy_hash


# --- T171: the hash is over the rules, not the bytes ------------------------------------------


def test_T171_comments_key_order_and_whitespace_do_not_change_the_hash() -> None:
    """SPEC-v0.6 §7.1.

    The decision is a function of the rules, not of the formatting. A hash over the file's bytes
    would move on a reformat and make every receipt's provenance field noise -- an operator who
    ran `yamlfmt` would find that nothing before that commit could be compared with anything
    after it.
    """
    assert policy_hash(TIDY) == policy_hash(UNTIDY)
    assert policy_hash(TIDY).startswith("sha256:")
    assert len(policy_hash(TIDY)) == len("sha256:") + 64

    # The control: these really are different documents, so the equality above is not trivial.
    assert TIDY != UNTIDY
    assert TIDY.replace(" ", "") != UNTIDY.replace(" ", "")


#: A v4 document exercising the three inputs `TIDY` does not carry: `data:`, per-action
#: `controls:` and per-rule `controls:`. Each parametrized case below removes or edits exactly
#: one of them, so a case that passes says the hash saw that input and nothing else.
LABELLED_FOR_HASH = """
schema: ctrlrun.policy/v4
version: "2026.1"
environment: production
controls:
  c-one: {title: "One"}
  c-two: {title: "Two"}
actions:
  stripe.refund:
    controls: [c-one]
    data:
      payment_id: internal
      amount: internal
    rules:
      - when: {amount_gt: 50000}
        controls: [c-two]
        decision: approve
      - decision: allow
"""


def _hash_differs(edited: str, base: str) -> bool:
    return Policy.from_yaml(edited).policy_hash != Policy.from_yaml(base).policy_hash


@pytest.mark.parametrize(
    ("what", "edited"),
    [
        ("a rule's threshold", TIDY.replace("amount_gt: 50000", "amount_gt: 10000")),
        ("a rule's decision", TIDY.replace("decision: approve", "decision: deny")),
        (
            "the rule order",
            TIDY.replace(
                "      - when: {amount_gt: 50000}\n        decision: approve\n"
                "      - decision: allow",
                "      - decision: allow\n"
                "      - when: {amount_gt: 50000}\n        decision: approve",
            ),
        ),
        ("an action name", TIDY.replace("customer.read", "customer.write")),
        ("a whole action", TIDY.replace("  customer.read:\n    decision: allow\n", "")),
        ("the environment", TIDY.replace("environment: production", "environment: staging")),
        ("the mode", TIDY.replace('version: "2026.1"', 'version: "2026.1"\nmode: observe')),
        # The three §7.1 inputs a mutation run found unguarded. Dropping `data:`, the per-action
        # citations or the per-rule citations from `_canonical_policy` left the whole suite
        # green, so the hash's coverage of them was a sentence in §7.1 and nothing else.
        (
            "an action's data labels",
            LABELLED_FOR_HASH.replace("      amount: internal\n", ""),
        ),
        (
            "a data label's value",
            LABELLED_FOR_HASH.replace("amount: internal", "amount: phi"),
        ),
        (
            "an action's control citations",
            LABELLED_FOR_HASH.replace("    controls: [c-one]\n", ""),
        ),
        (
            "a rule's control citations",
            LABELLED_FOR_HASH.replace("        controls: [c-two]\n", ""),
        ),
        (
            "a control's title",
            LABELLED_FOR_HASH.replace('c-two: {title: "Two"}', 'c-two: {title: "Rewritten"}'),
        ),
    ],
)
def test_T171_any_decision_input_changes_the_hash(what: str, edited: str) -> None:
    """Each of §7.1's named inputs, one at a time. A hash that moved for some of them and not
    others would be worse than none: an operator would read "the policy did not change" from a
    field that only sometimes notices."""
    base = LABELLED_FOR_HASH if "control" in what or "data" in what else TIDY
    assert edited != base, f"the edit for {what} did not change the document"
    assert _hash_differs(edited, base), f"changing {what} left the policy hash unchanged"


def test_T171_the_declared_version_alone_does_not_change_the_hash() -> None:
    """§7.1: `version:` is recorded and **never authoritative**.

    Two documents with the same `version:` and different hashes are two different policies, and
    the hash is what says so. The converse has to hold too, or the field would be an input to the
    thing it is supposed to be independent of.
    """
    relabelled = TIDY.replace('version: "2026.1"', 'version: "2026.2-hotfix"')
    assert relabelled != TIDY
    assert policy_hash(relabelled) == policy_hash(TIDY)
    assert Policy.from_yaml(relabelled).version == "2026.2-hotfix"
    assert Policy.from_yaml(TIDY).version == "2026.1"


def test_T171_the_authority_document_is_part_of_the_hash() -> None:
    """§7.1: *"Authority is included"*, and `v0.3 §4.6` makes it half of what decided an action.

    A receipt whose `policy_hash` moved when a rule changed and stayed still when a **grant**
    changed would answer "what decided this" with half the answer.
    """
    without = """
schema: ctrlrun.policy/v4
actions:
  stripe.refund:
    decision: allow
"""
    with_grant = """
schema: ctrlrun.policy/v4
authority:
  grants:
    - id: support
      subject: {agent: refund-agent}
      actions: [stripe.refund]
actions:
  stripe.refund:
    decision: allow
"""
    wider = with_grant.replace("actions: [stripe.refund]", "actions: [stripe.*]")
    assert policy_hash(without) != policy_hash(with_grant)
    assert policy_hash(with_grant) != policy_hash(wider)


def test_T171_the_hash_is_stable_across_processes_and_runs() -> None:
    """Provenance that changed between two runs of the same binary over the same file would
    record nothing. `canonical_bytes` sorts recursively, so this is a property of the
    canonicalizer -- and asserting it here is what would catch somebody hashing a `repr`."""
    import subprocess
    import sys
    import textwrap

    once = policy_hash(TIDY)
    assert once == policy_hash(TIDY)

    probe = textwrap.dedent(f"""
        from ctrlrun import Policy
        print(Policy.from_yaml({TIDY!r}).policy_hash)
    """)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == once, (
        "the policy hash differs between two processes, so it is derived from something that is "
        "not the document"
    )


def test_T171_the_schema_version_is_part_of_the_hash() -> None:
    """Two documents whose rules read the same under different schema versions are not the same
    policy: the schema is what says how the rules are to be read."""
    as_v3 = TIDY.replace("ctrlrun.policy/v4", "ctrlrun.policy/v3").replace(
        'version: "2026.1"\n', ""
    )
    as_v4 = TIDY.replace('version: "2026.1"\n', "")
    assert Policy.from_yaml(as_v3).schema == "ctrlrun.policy/v3"
    assert Policy.from_yaml(as_v4).schema == "ctrlrun.policy/v4"
    assert policy_hash(as_v3) != policy_hash(as_v4)


# --- `version:` needs v4, and the older schemas still load ------------------------------------


def test_the_version_key_needs_v4_and_every_older_schema_still_loads() -> None:
    """§7.1: *"`v1`, `v2` and `v3` documents load unchanged and get a `policy_hash` like any
    other; only `version:` needs `v4`."*"""
    for older in ("ctrlrun.policy/v1", "ctrlrun.policy/v2", "ctrlrun.policy/v3"):
        document = f"schema: {older}\nactions:\n  customer.read:\n    decision: allow\n"
        loaded = Policy.from_yaml(document)
        assert loaded.schema == older
        assert loaded.version is None
        assert loaded.policy_hash.startswith("sha256:")

        with pytest.raises(PolicyError) as refused:
            Policy.from_yaml(document.replace("actions:", 'version: "1"\nactions:'))
        assert "version" in str(refused.value)
        assert "v4" in str(refused.value), (
            f"the refusal does not say which schema `version:` needs: {refused.value}"
        )


def test_the_top_level_key_set_is_still_closed() -> None:
    """§7.1 grows the set by one and it *stays closed*: a typo is a load error, not a silent
    permissive policy (`v0.1 §3.1`)."""
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(TIDY.replace("version:", "verison:"))
    assert "verison" in str(refused.value)


# --- T172: receipts carry both, and the hash is authoritative ----------------------------------


def test_T172_every_receipt_carries_the_hash_and_the_declared_version(tmp_path) -> None:
    """SPEC-v0.6 §7.1, §9.5's `ctrlrun.receipt/v3`."""
    from ctrlrun import Control
    from ctrlrun.action import Action, Principal

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    policy = Policy.from_yaml(TIDY)
    control = Control(policy, store, clock=lambda: T0)
    receipt = control.execute(
        Action(
            name="customer.read",
            arguments={"customer_id": "c1"},
            principal=Principal(agent="a"),
        ),
        lambda: {"ok": True},
        "read:c1",
        lease=LEASE,
    )

    assert receipt.policy_hash == policy.policy_hash
    assert receipt.policy_version == "2026.1"
    document = receipt.to_dict()
    assert document["policy_hash"] == policy.policy_hash
    assert document["policy_version"] == "2026.1"
    store.close()


def test_T172_two_policies_sharing_a_version_string_are_told_apart_by_the_hash(tmp_path) -> None:
    """§7.1: the declared version is for humans and the hash is what decides.

    An operator who edits a rule and forgets to bump `version:` -- which is the ordinary case,
    because nothing enforces the bump and §7.1 says nothing should -- still gets two
    distinguishable receipts.
    """
    from ctrlrun import Control
    from ctrlrun.action import Action, Principal

    edited = TIDY.replace("amount_gt: 50000", "amount_gt: 10000")
    hashes = []
    for index, text in enumerate((TIDY, edited)):
        store = SQLiteStateStore(tmp_path / f"s{index}.db", clock=lambda: T0)
        control = Control(Policy.from_yaml(text), store, clock=lambda: T0)
        receipt = control.execute(
            Action(
                name="customer.read",
                arguments={"customer_id": "c1"},
                principal=Principal(agent="a"),
            ),
            lambda: {"ok": True},
            "read:c1",
            lease=LEASE,
        )
        assert receipt.policy_version == "2026.1"
        hashes.append(receipt.policy_hash)
        store.close()

    assert hashes[0] != hashes[1], (
        "two policies with the same declared version and different rules produced the same "
        "provenance; the hash is the field that is supposed to tell them apart"
    )


# --- T175: the registry loads, cites, and refuses a dangling id --------------------------------


REGISTERED = """
schema: ctrlrun.policy/v4
controls:
  maker-checker-refunds:
    title: "A refund over the desk limit is approved by a second person"
    source: "House policy FIN-4.2"
  card-data-handling:
    title: "Cardholder data is not written to evidence"
    source: "PCI DSS v4.0 §3.3.1"
  cited-by-nobody:
    title: "A control the operator has written down and not yet wired up"
actions:
  stripe.refund:
    controls: [card-data-handling]
    rules:
      - when: {amount_gt: 50000}
        decision: approve
        controls: [maker-checker-refunds]
      - decision: allow
  customer.read:
    decision: allow
"""


def test_T175_a_control_cited_by_no_rule_still_loads() -> None:
    """SPEC-v0.6 §7.3. A registry is a list of what the operator has written down; a control
    nothing cites yet is an ordinary state of a document being filled in, not an error."""
    policy = Policy.from_yaml(REGISTERED)
    assert set(policy.controls) == {
        "maker-checker-refunds",
        "card-data-handling",
        "cited-by-nobody",
    }
    assert policy.controls["cited-by-nobody"].title.startswith("A control the operator")
    assert policy.controls["cited-by-nobody"].source is None


def test_T175_a_dangling_id_is_a_load_error_naming_it() -> None:
    """§7.3's third rule: *"an unknown control id is a load error, naming it. A registry whose
    citations can dangle is a registry that quietly stops meaning anything."*"""
    for where, edited in (
        (
            "an action",
            REGISTERED.replace("controls: [card-data-handling]", "controls: [typo-here]"),
        ),
        (
            "a rule",
            REGISTERED.replace("controls: [maker-checker-refunds]", "controls: [typo-here]"),
        ),
    ):
        with pytest.raises(PolicyError) as refused:
            Policy.from_yaml(edited)
        assert "typo-here" in str(refused.value), (
            f"a dangling id cited by {where} was refused without naming it: {refused.value}"
        )


def test_T175_the_receipt_carries_the_union_of_the_action_and_the_matched_rule(tmp_path) -> None:
    """§7.3: *"the ids the **matched rule** cited, unioned with the action's."*

    The union, and not the rule's alone: an action-level control governs every rule under it, and
    a receipt that dropped it would answer "under what" with only half of what the operator wrote.
    """
    from ctrlrun import Control
    from ctrlrun.action import Action, Principal

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    control = Control(
        Policy.from_yaml(REGISTERED),
        store,
        clock=lambda: T0,
    )

    # The second rule matches: the action's control only.
    small = control.execute(
        Action(
            name="stripe.refund",
            arguments={"payment_id": "p1", "amount": 100},
            principal=Principal(agent="a"),
        ),
        lambda: {"ok": True},
        "refund:p1",
        lease=LEASE,
    )
    assert small.controls == ("card-data-handling",)

    # The first rule matches: the union, **in registry order** — which is `maker-checker-refunds`
    # first, because that is the order `REGISTERED` declares them in, and not the order the
    # action and the rule cite them in.
    #
    # This assertion used to read `("card-data-handling", "maker-checker-refunds")`, which is
    # citation order, while §7.3, the `Evaluation.controls` docstring and the comment beside
    # this very line all said registry order. An independent review found all four disagreeing.
    # Registry order is the one kept, because it is the property worth having: two receipts
    # citing the same set list it the same way whichever rule matched, so a human reading them
    # against the document is not comparing two orderings of one answer.
    #
    # Asserted on the evaluation, because an `approve` decision needs a human and the union is a
    # property of the decision, not of what happens after it.
    over = Policy.from_yaml(REGISTERED).evaluate(
        Action(
            name="stripe.refund",
            arguments={"payment_id": "p2", "amount": 90000},
            principal=Principal(agent="a"),
        )
    )
    assert over.decision.value == "approve"
    assert over.controls == ("maker-checker-refunds", "card-data-handling")
    assert list(Policy.from_yaml(REGISTERED).controls)[:2] == [
        "maker-checker-refunds",
        "card-data-handling",
    ], "the fixture stopped demonstrating registry order, so the assertion above proves nothing"

    # An action citing none carries none. Without this, a control list that was simply always
    # the whole registry would pass every assertion above.
    none = control.execute(
        Action(
            name="customer.read",
            arguments={"customer_id": "c1"},
            principal=Principal(agent="a"),
        ),
        lambda: {"ok": True},
        "read:c1",
        lease=LEASE,
    )
    assert none.controls == ()
    store.close()


def test_T175_a_control_is_attribution_and_changes_no_decision() -> None:
    """§7.3's second rule, and this project's sharpest: **a control is attribution, not
    prevention.**

    Citing `maker-checker-refunds` on a rule does not cause an approval; the rule's `decision:
    approve` does. So the same document with every `controls:` line removed reaches the *same
    decision for every action* -- and if it did not, a control would be enforcing something, and
    every sentence in §7.3 would be a false green in prose.
    """
    from ctrlrun.action import Action, Principal

    stripped = "\n".join(
        line
        for line in REGISTERED.splitlines()
        if "controls:" not in line
        and not line.strip().startswith(
            ("maker-checker", "card-data", "cited-by", "title:", "source:")
        )
    )
    with_controls = Policy.from_yaml(REGISTERED)
    without = Policy.from_yaml(stripped)

    for name, arguments in (
        ("stripe.refund", {"payment_id": "p1", "amount": 100}),
        ("stripe.refund", {"payment_id": "p2", "amount": 90000}),
        ("customer.read", {"customer_id": "c1"}),
        ("unknown.action", {}),
    ):
        action = Action(name=name, arguments=arguments, principal=Principal(agent="a"))
        one, two = with_controls.evaluate(action), without.evaluate(action)
        assert one.decision is two.decision, (
            f"{name} with {arguments} decided {one.decision} with controls and {two.decision} "
            "without them; a control that changes a decision is enforcing something"
        )
        assert one.reason == two.reason


def test_T175_controls_need_v4_and_the_registry_key_set_is_closed() -> None:
    """§7.1's gate, and §3.1's closed key sets applied to the new mapping."""
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(REGISTERED.replace("ctrlrun.policy/v4", "ctrlrun.policy/v3"))
    assert "controls" in str(refused.value)
    assert "v4" in str(refused.value)

    with pytest.raises(PolicyError) as typo:
        Policy.from_yaml(REGISTERED.replace('    source: "House policy FIN-4.2"', '    sauce: "x"'))
    assert "sauce" in str(typo.value)


def test_T175_the_source_is_cited_and_never_interpreted() -> None:
    """§7.3's first rule. `source:` is a string the operator wrote: the kernel does not know what
    PCI DSS is, does not check the clause exists, and makes **no compliance, conformance or
    alignment claim** on the strength of one.

    Asserted as a property of the loader: any string loads, including one naming a standard that
    does not exist, because validating it would be the beginning of interpreting it.
    """
    invented = REGISTERED.replace(
        '"PCI DSS v4.0 §3.3.1"', '"Entirely Fictional Standard 9000 §1.1"'
    )
    policy = Policy.from_yaml(invented)
    assert policy.controls["card-data-handling"].source == "Entirely Fictional Standard 9000 §1.1"


# --- §7.2: the policy changed between the grant and its consumption ----------------------------


APPROVE_OVER = """
schema: ctrlrun.policy/v4
version: "before"
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: {amount_gt: 1000}
        decision: approve
      - decision: allow
"""

DENY_OVER = APPROVE_OVER.replace('version: "before"', 'version: "after"').replace(
    "        decision: approve", "        decision: deny"
)
ALLOW_ALL = """
schema: ctrlrun.policy/v4
version: "after"
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    decision: allow
"""


def a_refund(payment_id: str = "p1", amount: int = 90000):
    from ctrlrun.action import Action, Principal

    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": amount},
        principal=Principal(agent="refund-agent"),
    )


def granted(store, action, *, at=T0, ttl=timedelta(hours=1)) -> str:
    """A real granted approval for `action`, as `ctrlrun approve` would leave one.

    Through `build_request`, so it picks up whatever `policy_in_force` is carrying -- which is
    how every shipped provider builds one (§7.1).
    """
    from dataclasses import replace as _replace

    from ctrlrun.approval import build_request

    request = _replace(build_request(action, ttl, at), request_id=f"req_{action.action_id[-8:]}")
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:ada")
    return request.request_id


def status_of(store, request_id: str) -> str:
    record = store.get_approval(request_id)
    assert record is not None
    return str(record.status)


def test_T173_the_APPROVE_row_is_unchanged(tmp_path) -> None:
    """SPEC-v0.6 §7.2, first row. The approval is consumed with the reservation
    (`v0.1 §4.2 A4`), and the receipt records both policy hashes."""
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    policy = Policy.from_yaml(APPROVE_OVER)
    control = Control(policy, store, clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    with with_approval(request_id):
        receipt = control.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)

    assert receipt.decision.value == "approve"
    assert receipt.approval_id == request_id
    assert receipt.approver == "cli:ada"
    assert status_of(store, request_id) == "consumed"
    assert receipt.policy_hash == policy.policy_hash
    store.close()


def test_T173_the_DENY_row_refuses_and_leaves_the_approval_granted(tmp_path) -> None:
    """§7.2's second row, and §7.2.1's argument for it.

    A human's answer is not spent on an action that did not run. The token authorizes nothing on
    its own -- `v0.1 §4.2 A1` binds it to one `action_hash`, and the policy is re-evaluated on
    every presentation -- so while the policy says `DENY` the approval opens nothing, and if the
    policy is corrected it opens exactly the action it was granted for.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval
    from ctrlrun.errors import ActionDenied

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    # Granted under the old policy; presented under one that now denies.
    control = Control(Policy.from_yaml(DENY_OVER), store, clock=lambda: T0)
    with pytest.raises(ActionDenied), with_approval(request_id):
        control.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)

    assert status_of(store, request_id) == "granted", (
        "a policy edit spent a human's answer on an action that did not run"
    )

    # And the correction restores exactly what was approved -- the whole of §7.2.1's second
    # bullet, which would be an assertion about nothing if the grant had been consumed above.
    fixed = Control(Policy.from_yaml(APPROVE_OVER), store, clock=lambda: T0)
    with with_approval(request_id):
        receipt = fixed.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)
    assert receipt.decision.value == "approve"
    assert status_of(store, request_id) == "consumed"
    store.close()


def test_T173_the_ALLOW_row_invalidates_the_approval_it_did_not_need(tmp_path) -> None:
    """§7.2's third row. **This is a change to shipped behaviour and the reason §7.2 exists.**

    Today a re-evaluation that returns `ALLOW` leaves `approval_id` unset, so the presented
    approval is never consumed: it stays `granted` for its full TTL, for a hash that a later
    policy edit could make `APPROVE`-requiring again -- a live bearer token for an action a human
    already answered. `v0.1 §4.1` calls a request id a bearer token in as many words.

    The old behaviour is asserted to be gone, not merely the new one to be present.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    control = Control(Policy.from_yaml(ALLOW_ALL), store, clock=lambda: T0)
    with with_approval(request_id):
        receipt = control.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)

    assert receipt.decision.value == "allow"
    assert status_of(store, request_id) != "granted", (
        "an ALLOW re-evaluation left the presented approval live for its full TTL; that is the "
        "bearer token this row exists to close"
    )
    assert status_of(store, request_id) == "consumed"
    # §7.2.2's third step: the evidence says a human answered and the policy did not require it.
    assert receipt.approval_id == request_id
    assert receipt.approver == "cli:ada"
    store.close()


@pytest.mark.parametrize("broken", ["expired", "consumed", "denied"])
def test_T173c_an_ALLOW_action_is_never_refused_by_the_approval_it_did_not_need(
    tmp_path, broken
) -> None:
    """SPEC-v0.6 §7.2.2, and the failure its inversion exists to prevent.

    An earlier draft said *consumed anyway, in the same transaction*, which would have added a
    refusal path to the permissive decision: `consume_approval_and_reserve` checks the approval
    **first** (`v0.1 §4.2 A4`), so an approval that is expired, already consumed or denied raises
    `ApprovalMismatch` -- and **an action the policy allows is refused because of an approval it
    did not need.**

    The reachable case is ordinary: an agent retries inside `with_approval(id)` after the
    operator relaxed the rule, the grant having been spent on the first attempt.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()

    if broken == "expired":
        request_id = granted(store, action, ttl=timedelta(seconds=1))
        clock = lambda: T0 + timedelta(hours=2)  # noqa: E731 - the point is the moved clock
    elif broken == "consumed":
        request_id = granted(store, action)
        store.consume_approval(request_id, action.action_hash)
        clock = lambda: T0  # noqa: E731
    else:
        from ctrlrun.approval import ApprovalRequest

        request = ApprovalRequest(
            request_id="req_denied",
            action_hash=action.action_hash,
            action=action,
            created_at=T0,
            expires_at=T0 + timedelta(hours=1),
        )
        store.put_approval_request(request)
        store.deny_approval(request.request_id, "cli:ada")
        request_id = request.request_id
        clock = lambda: T0  # noqa: E731

    ran: list[str] = []
    control = Control(Policy.from_yaml(ALLOW_ALL), store, clock=clock)
    with with_approval(request_id):
        receipt = control.execute(
            action, lambda: ran.append("ran") or {"ok": True}, "refund:p1", lease=LEASE
        )

    assert ran == ["ran"], (
        f"an ALLOW action was refused because its presented approval was {broken}; the policy "
        "permits this action outright and the approval is not an input to that"
    )
    assert receipt.decision.value == "allow"
    assert receipt.result.value == "committed"
    record = store.get_effect("refund:p1")
    assert record is not None and record.state.value == "committed"
    store.close()


def test_T174_both_policy_hashes_are_recorded_and_differ_when_they_should(tmp_path) -> None:
    """SPEC-v0.6 §7.1's second half, and §8's T174.

    `policy_hash_at_approval` is the hash that was in force when the approval was **granted**,
    carried on the approval record. Where it differs from the receipt's `policy_hash`, the policy
    changed between the grant and its consumption -- which is the thing §7.2's table is about,
    and which no other field can say.
    """
    from ctrlrun import Control
    from ctrlrun.approval import policy_in_force
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    before = Policy.from_yaml(APPROVE_OVER)
    action = a_refund()

    # Granted while `before` was in force. `Control` stamps the hash on the request it creates,
    # which is what `policy_in_force` carries -- the store has no policy and §9.4's argument
    # against giving evidence commands one applies to providers too.
    with policy_in_force(before.policy_hash):
        request_id = granted(store, action)
    recorded = store.get_approval(request_id)
    assert recorded is not None
    assert recorded.policy_hash_at_approval == before.policy_hash

    # Consumed under a policy that still approves, and is a different document.
    after = Policy.from_yaml(APPROVE_OVER.replace("amount_gt: 1000", "amount_gt: 2000"))
    assert after.policy_hash != before.policy_hash
    with with_approval(request_id):
        receipt = Control(after, store, clock=lambda: T0).execute(
            action, lambda: {"ok": True}, "refund:p1", lease=LEASE
        )

    assert receipt.policy_hash == after.policy_hash
    consumed = store.get_approval(request_id)
    assert consumed is not None
    assert consumed.policy_hash_at_approval == before.policy_hash
    assert consumed.policy_hash_at_approval != receipt.policy_hash, (
        "the policy changed between the grant and its consumption and nothing records it"
    )
    store.close()


def test_T174_control_itself_stamps_the_policy_hash_on_the_request_it_creates(tmp_path) -> None:
    """§7.1's mechanism, driven through `Control` rather than set up beside it.

    An independent review deleted `with policy_in_force(self._policy.policy_hash):` from
    `Control._approval_id` and ran the whole suite: **2343 passed.** T174 above sets the context
    variable by hand and builds its request through `granted()`, so its own comment — *"`Control`
    stamps the hash on the request it creates"* — described a line no test executed. §7.1's
    entire mechanism had no end-to-end coverage.

    So: no `policy_in_force` in this test. A real `Control.execute` reaches `APPROVE` with
    nothing presented, raises `ApprovalRequired`, and the stored request must already carry the
    hash of the policy that asked for it.
    """
    from ctrlrun import Control
    from ctrlrun.errors import ApprovalRequired

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    policy = Policy.from_yaml(APPROVE_OVER)
    control = Control(policy, store, clock=lambda: T0)

    with pytest.raises(ApprovalRequired) as raised:
        control.execute(a_refund(), lambda: {"ok": True}, "refund:p1", lease=LEASE)

    record = store.get_approval(raised.value.request_id)
    assert record is not None
    assert record.policy_hash_at_approval == policy.policy_hash, (
        "Control created an approval request without recording the policy that demanded it; "
        "§7.2's whole table needs to know what was in force when the human was asked"
    )
    store.close()


# --- T176: `data_scope` is derived, reserved, and drives a decision ----------------------------


LABELLED = """
schema: ctrlrun.policy/v4
actions:
  patient.record.update:
    data:
      diagnosis: phi
      patient_id: phi
      note: internal
    rules:
      - when: {data_scope_in: [phi]}
        decision: approve
      - decision: allow
"""


def an_update(**arguments):
    from ctrlrun.action import Action, Principal

    return Action(name="patient.record.update", arguments=arguments, principal=Principal(agent="a"))


def test_T176_the_derived_set_is_the_labels_of_the_arguments_actually_supplied() -> None:
    """SPEC-v0.6 §7.4.

    `data_scope` is *"the set of labels present in this action's arguments"*, derived at
    evaluation from the `data:` map and **the arguments actually supplied** -- not the whole
    declared map. An action that carries no PHI is not a PHI action because some other call
    of it would be.
    """
    policy = Policy.from_yaml(LABELLED)

    assert policy.data_scope(an_update(diagnosis="x", note="y")) == frozenset({"phi", "internal"})
    assert policy.data_scope(an_update(note="y")) == frozenset({"internal"})
    assert policy.data_scope(an_update(patient_id="p1")) == frozenset({"phi"})
    assert policy.data_scope(an_update()) == frozenset()
    # An argument the map does not mention carries no label, rather than a default one.
    assert policy.data_scope(an_update(unlabelled="x")) == frozenset()


def test_T176_the_derived_set_drives_a_decision() -> None:
    """§7.4's example, end to end. `data_scope_in: [phi]` means *the derived set intersects this
    list*, which is the membership `_in` already expresses -- **no new operator**."""
    policy = Policy.from_yaml(LABELLED)

    assert policy.evaluate(an_update(diagnosis="x")).decision.value == "approve"
    assert policy.evaluate(an_update(patient_id="p1")).decision.value == "approve"
    assert policy.evaluate(an_update(note="y")).decision.value == "allow"
    assert policy.evaluate(an_update()).decision.value == "allow"


def test_T176_the_operators_behave_as_they_do_everywhere_else() -> None:
    """§7.4: `_eq` and `_neq` compare the whole set; `_in` is intersection.

    **No `contains` and no `not_in` are added**, and the reason is not economy: `_OPERATORS` is
    shared with authority `constraints:` (`v0.3 §4.5` -- one shared implementation, not two), so
    an operator added here would silently become available to grants, and §11 puts *"matching a
    grant on a data label"* out of scope.
    """
    from ctrlrun.policy import _OPERATORS

    assert set(_OPERATORS) == {"eq", "neq", "in", "lt", "lte", "gt", "gte"}, (
        "an operator was added to the shared set; it is now available to authority constraints "
        "too, which §11 excludes"
    )

    equals = Policy.from_yaml(
        LABELLED.replace("data_scope_in: [phi]", "data_scope_eq: [internal, phi]")
    )
    assert equals.evaluate(an_update(diagnosis="x", note="y")).decision.value == "approve"
    assert equals.evaluate(an_update(diagnosis="x")).decision.value == "allow"

    differs = Policy.from_yaml(LABELLED.replace("data_scope_in: [phi]", "data_scope_neq: [phi]"))
    assert differs.evaluate(an_update(note="y")).decision.value == "approve"
    assert differs.evaluate(an_update(diagnosis="x")).decision.value == "allow"


def test_T176_data_scope_is_refused_as_an_argument_in_a_document_of_any_schema() -> None:
    """§7.4's table, first row, and `v0.3 §12.1`'s rule about not gating a reservation.

    **This test had no `pytest.raises` and asserted the four documents *loaded*.** Its own
    comment admitted the problem — *"the refusal has to be driven through something that is
    unambiguously an argument"* — and then did not do it, so an independent review found the
    reservation inert: `RESERVED_ARGUMENTS` is consulted only by the condition splitter, which
    exempts derived subjects, and a `data:` key or an `effect:` placeholder called `data_scope`
    loaded without a murmur while §7.4's table said both were load errors.

    Driven now through the three places an argument is actually named. Not gated on the schema
    version (§12.1): the check runs for every document, because gating it would leave the same
    name meaning two things in two files.
    """
    from ctrlrun.policy import DERIVED_SUBJECTS, RESERVED_ARGUMENTS

    assert "data_scope" in RESERVED_ARGUMENTS
    assert DERIVED_SUBJECTS <= RESERVED_ARGUMENTS

    # `effect:` and `resource:` arrive in v2, so v2 and v3 are where "not gated on v4" is
    # actually observable: a v1 document has no place to name an argument at all, and asserting
    # a refusal there would be asserting against a different error.
    for schema in ("v2", "v3", "v4"):
        head = f"schema: ctrlrun.policy/{schema}\nactions:\n  a.b:\n"
        with pytest.raises(PolicyError, match="data_scope"):
            Policy.from_yaml(head + '    effect: "k:{data_scope}"\n    decision: allow\n')
        with pytest.raises(PolicyError, match="data_scope"):
            Policy.from_yaml(head + '    resource: "r:{data_scope}"\n    decision: allow\n')

    # A `data:` map needs v4, so its refusal is asserted there and the v4 gate is asserted with
    # it -- otherwise a passing `pytest.raises` could be the schema check firing, not this one.
    with pytest.raises(PolicyError, match="data_scope"):
        Policy.from_yaml(
            "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n"
            "    data:\n      data_scope: phi\n    decision: allow\n"
        )

    # And a *condition* on `data_scope` is still permitted: that is the table's second row, and
    # the whole reason `DERIVED_SUBJECTS` is a narrower set than `RESERVED_ARGUMENTS`.
    Policy.from_yaml(
        "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n"
        "    rules:\n      - when: {data_scope_in: [phi]}\n        decision: allow\n"
    )


def test_T176_a_protected_function_may_not_take_a_derived_name_as_a_parameter() -> None:
    """The third place an argument is named, and the one furthest from the policy loader.

    `@protect` builds an `Action` from the call's bound parameters, so a parameter called
    `data_scope` reaches `_ActionPolicy.evaluate` in the same mapping the derived set is merged
    into — two meanings for one name, resolved by merge order in another function. Refused at
    decoration time, on `_reject_variadic`'s precedent: the mistake is in the source.
    """
    from ctrlrun import protect
    from ctrlrun.errors import InvalidArgument

    with pytest.raises(InvalidArgument, match="data_scope"):

        @protect("a.b")
        def _reads_it(data_scope: str) -> int:  # pragma: no cover - never defined
            return 1


def test_T176_a_reserved_name_that_is_not_derived_is_still_a_legal_argument() -> None:
    """The narrowing, asserted, because getting it wrong broke shipped code immediately.

    The first version of the check above used the whole of `RESERVED_ARGUMENTS`. `user` has been
    in that set since v0.3 and protected functions in this repository take a `user` parameter,
    so two shipped tests went red on the spot.

    The two halves of that set are different rules. `agent`, `user`, `claims` and the rest are
    refused as **condition subjects**, because a rule must not read who is acting (`v0.3 §4.5`);
    an *argument* of that name collides with nothing, since policy cannot see the principal at
    all. Only a derived subject is merged into the mapping the arguments are read from.
    """
    from ctrlrun import protect

    Policy.from_yaml(
        "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n"
        '    effect: "k:{user}"\n    data:\n      user: internal\n    decision: allow\n'
    )

    @protect("a.b")
    def _names_a_user(user: str) -> str:
        return user


def test_T176_data_scope_eq_compares_the_set_and_not_its_order() -> None:
    """§7.4: *"`_eq` and `_neq` compare the whole set."* **A set has no order.**

    The derived value is a `sorted(...)` list and `_equal` on lists is order-sensitive, so an
    independent review found `data_scope_eq: [phi, internal]` never matching — silently. The
    key splits, the subject is present, `matches` returns `False`, and the rule falls through
    to whatever is below it. Where that rule was the `deny` or the `approve`, it is fail-open.

    Both orderings are asserted, because one of them passed before the fix and asserting only
    that one would leave this test green against the bug.
    """
    template = (
        "schema: ctrlrun.policy/v4\nversion: order\nactions:\n  care.read:\n"
        "    data:\n      diagnosis: phi\n      note: internal\n"
        "    rules:\n      - when: {%s}\n        decision: approve\n      - decision: allow\n"
    )
    action = _a_care_read()
    for condition in ("data_scope_eq: [phi, internal]", "data_scope_eq: [internal, phi]"):
        decision = Policy.from_yaml(template % condition).evaluate(action).decision
        assert decision.value == "approve", (
            f"{condition!r} did not match a derived set with the same members; a rule written "
            "in the operator's own declaration order silently never fires"
        )
    for condition in ("data_scope_neq: [phi, internal]", "data_scope_neq: [internal, phi]"):
        decision = Policy.from_yaml(template % condition).evaluate(action).decision
        assert decision.value == "allow", condition


def test_T176_an_ordinary_list_argument_still_means_that_exact_list() -> None:
    """The other half of the narrowing above, and the regression it prevents.

    `_in` was widened once to intersect every list-valued subject and broke `value_in: [[1, 2]]`
    against `value = [1, 2]`. `_eq` is one line from the same mistake: an ordinary argument that
    happens to be a list must still compare as a list, order and all.
    """
    from ctrlrun.action import Action, Principal

    document = (
        "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n"
        "    rules:\n      - when: {tags_eq: [1, 2]}\n        decision: approve\n"
        "      - decision: allow\n"
    )
    policy = Policy.from_yaml(document)

    def decide(tags):
        return policy.evaluate(
            Action(name="a.b", arguments={"tags": tags}, principal=Principal(agent="x"))
        ).decision.value

    assert decide([1, 2]) == "approve"
    assert decide([2, 1]) == "allow", (
        "an ordinary list argument was compared as a set; only a derived subject may be"
    )


def _a_care_read():
    from ctrlrun.action import Action, Principal

    return Action(
        name="care.read",
        arguments={"diagnosis": "x", "note": "y"},
        principal=Principal(agent="clinician-agent"),
    )


def test_T176_a_condition_may_address_data_scope_and_a_reserved_name_still_may_not() -> None:
    """§7.4's table, and the distinction that makes the feature implementable at all.

    | May an **argument** be called this? | `data_scope` | **No** |
    | May a **condition key** split to this subject? | `data_scope` | **Yes** |

    Today those were one check: the splitter refuses a condition whose subject is in
    `RESERVED_ARGUMENTS`, which is exactly how `claims_eq:` becomes a load error. Adding
    `data_scope` to that set unchanged would have made `data_scope_in:` a load error too -- **the
    very condition this section asks operators to write**.
    """
    from ctrlrun.policy import DERIVED_SUBJECTS, RESERVED_ARGUMENTS

    assert set(DERIVED_SUBJECTS) == {"data_scope"}
    assert DERIVED_SUBJECTS <= RESERVED_ARGUMENTS, (
        "a derived subject that is not also a reserved argument would let one name mean two "
        "things in one document"
    )

    Policy.from_yaml(LABELLED)  # `data_scope_in:` loads

    for reserved in ("claims", "issuer", "expires_at", "environment", "resource", "agent"):
        with pytest.raises(PolicyError) as refused:
            Policy.from_yaml(
                "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n    rules:\n"
                f"      - when: {{{reserved}_eq: x}}\n        decision: allow\n"
            )
        assert reserved in str(refused.value), (
            f"the allow-list let {reserved!r} through; only `data_scope` is a derived subject"
        )


def test_T176_authority_constraints_cannot_address_data_scope() -> None:
    """§7.4: *"Authority is untouched."* The derived-subject allow-list is the policy
    evaluator's, and a grant that named one is refused as it always was (§11).

    Without this the shared `_OPERATORS` would have quietly extended the authority surface,
    which is the third of the three mistakes §7.4 records in one line of an earlier draft.
    """
    document = """
schema: ctrlrun.policy/v4
authority:
  grants:
    - id: g
      subject: {agent: a}
      actions: [a.b]
      constraints: {data_scope_in: [phi]}
actions:
  a.b:
    decision: allow
"""
    from ctrlrun.authority import Authority

    # Through `Authority`, because that is what parses `constraints:` -- `Policy.from_yaml`
    # never looks inside the authority section (`v0.3 §8.3`: the grants may arrive in a separate
    # document the policy loader never sees).
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(document)
    assert "data_scope" in str(refused.value)
    assert "grant" in str(refused.value).lower(), (
        f"the refusal does not say a grant cannot address a data label: {refused.value}"
    )


def test_T176_the_data_map_needs_v4_and_its_labels_are_checked() -> None:
    """§7.1's gate and §3.1's closed key sets, on the new `data:` mapping."""
    with pytest.raises(PolicyError) as older:
        Policy.from_yaml(LABELLED.replace("ctrlrun.policy/v4", "ctrlrun.policy/v3"))
    assert "data" in str(older.value)
    assert "v4" in str(older.value)

    for broken, fragment in (
        ("      diagnosis: 1\n", "must be"),
        ("      diagnosis: {label: phi, extra: 1}\n", "extra"),
        # `redact:` was described by §7.4 and cut by item 7 (§7.5), so it is refused with the
        # reason rather than reading as a typo -- an operator who writes it and gets no error
        # would believe the value is hidden from the evidence.
        ("      diagnosis: {label: phi, redact: true}\n", "not in v0.6"),
    ):
        with pytest.raises(PolicyError) as refused:
            Policy.from_yaml(LABELLED.replace("      diagnosis: phi\n", broken))
        assert fragment in str(refused.value)


def test_T176_the_intersection_is_the_derived_subjects_and_nothing_else() -> None:
    """The regression control for §7.4's `_in`, which a first version got wrong.

    A **derived** set-valued subject intersects the operand list. An **ordinary** list argument
    does not: `value_in: [[1, 2]]` against `value = [1, 2]` means *this exact list is one of the
    operands*, and has since `v0.1 §3.2`. Making every list-valued subject intersect broke that,
    and the shipped test for it is what said so.

    A rule about a new subject may not quietly re-mean an operator for the old ones -- and
    `_OPERATORS` is shared with authority `constraints:`, so "quietly" would reach further than
    the policy file.
    """
    ordinary = """
schema: ctrlrun.policy/v4
actions:
  a.b:
    rules:
      - when: {tags_in: [[1, 2]]}
        decision: approve
      - decision: allow
"""
    from ctrlrun.action import Action, Principal

    policy = Policy.from_yaml(ordinary)

    def decide(**arguments):
        return policy.evaluate(
            Action(name="a.b", arguments=arguments, principal=Principal(agent="a"))
        ).decision.value

    assert decide(tags=(1, 2)) == "approve", "the whole list must match an operand element"
    assert decide(tags=(1,)) == "allow", (
        "an ordinary list argument intersected the operand list; that is the derived-subject "
        "rule leaking into `_in` for every argument"
    )
    assert decide(tags=(1, 3)) == "allow"

    # And the derived subject does intersect, in the same document shape.
    labelled = Policy.from_yaml(LABELLED)
    assert labelled.evaluate(an_update(diagnosis="x", note="y")).decision.value == "approve"


# --- The independent review's findings, each as the test that was missing ----------------------

ALLOW_NO_EFFECT = """
schema: ctrlrun.policy/v4
version: "after"
actions:
  stripe.refund:
    decision: allow
"""

APPROVE_NO_EFFECT = """
schema: ctrlrun.policy/v4
version: "before"
actions:
  stripe.refund:
    rules:
      - when: {amount_gt: 1000}
        decision: approve
      - decision: allow
"""


def test_T173_the_ALLOW_row_fires_for_an_action_with_no_effect_template(tmp_path) -> None:
    """§7.2's third row, for the action `v0.1 §5.1` documents as the escape hatch.

    An independent review found the row silently not firing when the action has no `effect:`
    template: `_secure` short-circuits on `approval_id is None and effect_key is None` and
    returned before `_spend_unneeded_approval` was ever reached. §7.2's table states the row
    unconditionally; §7.2.2's step 1 quietly assumed a reservation.

    It is reachable by the ordinary route, because the **`APPROVE`** path consumes without a
    reservation too. So: grant, relax the rule to `allow`, present — and the grant outlived the
    answer for its full TTL. That is the bearer token §7.2 exists to close, for a whole class of
    action, and item 7's own throwaway configuration contains one (`patient.record.read`).
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()

    # The APPROVE half, asserted rather than assumed: without it this test would prove only
    # that nothing happens to an approval nothing could have used.
    before = Control(Policy.from_yaml(APPROVE_NO_EFFECT), store, clock=lambda: T0)
    request_id = granted(store, action)
    with with_approval(request_id):
        before.execute(action, lambda: {"ok": True}, None, lease=LEASE)
    assert status_of(store, request_id) == "consumed", (
        "the APPROVE path does not consume without a reservation either, so this test's "
        "premise is wrong and the ALLOW row it checks is unreachable"
    )

    second = granted(store, a_refund("p2"))
    control = Control(Policy.from_yaml(ALLOW_NO_EFFECT), store, clock=lambda: T0)
    with with_approval(second):
        receipt = control.execute(a_refund("p2"), lambda: {"ok": True}, None, lease=LEASE)

    assert receipt.decision.value == "allow"
    assert status_of(store, second) != "granted", (
        "an ALLOW re-evaluation left a presented approval live for its full TTL because the "
        "action had no effect: template"
    )
    assert status_of(store, second) == "consumed"
    assert receipt.approval_id == second
    assert receipt.approver == "cli:ada"
    store.close()


def test_T173c_a_store_failure_closing_the_unneeded_approval_never_refuses_the_action(
    tmp_path,
) -> None:
    """§7.2.2's *"nothing here can refuse the action"*, against the exception class it missed.

    An independent review found `_spend_unneeded_approval` catching `CTRLRunError` only. Any
    other store failure — `sqlite3.OperationalError` ("database is locked"), a dropped `psycopg`
    connection — propagated out of `_secure` **after** the reservation was taken and **before**
    `begin_execution`.

    The observable damage is worse than a refusal. No receipt is written at all (`execute` raises
    before `_record`), and the effect key is left `RESERVED` with nothing holding it until the
    lease lapses: **an ambiguity manufactured by the permissive decision path.** That is also the
    case T154d is about — `Control` never maps a store exception through `v0.1 §5.5` — arriving
    from a direction T154d does not cover.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    class ConsumeExplodes:
        """Refuses exactly what the real store refuses, and breaks on one method.

        A double that could grant, reserve or commit where the real store would not would
        invalidate this test; this one delegates everything and raises a **non-`CTRLRunError`**
        from `consume_approval` alone, which is the one call `_spend_unneeded_approval` makes.
        """

        def __init__(self, inner) -> None:
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def consume_approval(self, *arguments, **keywords):
            raise OSError("connection reset by peer")

    ran: list[str] = []
    control = Control(Policy.from_yaml(ALLOW_ALL), ConsumeExplodes(store), clock=lambda: T0)
    with with_approval(request_id):
        receipt = control.execute(
            action, lambda: ran.append("ran") or {"ok": True}, "refund:p1", lease=LEASE
        )

    assert ran == ["ran"], "a store failure closing an unneeded approval refused an allowed action"
    assert receipt.result.value == "committed"
    record = store.get_effect("refund:p1")
    assert record is not None and record.state.value == "committed", (
        "the effect key was left mid-flight by a failure on the permissive path"
    )
    # And the approval is untouched rather than half-spent: the write did not happen.
    assert status_of(store, request_id) == "granted"
    store.close()


OBSERVED = """
schema: ctrlrun.policy/v4
version: "observed"
mode: observe
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: {amount_gt: 1000}
        decision: approve
      - decision: allow
"""


def test_T173b_an_observed_action_spends_no_grant(tmp_path) -> None:
    """SPEC-v0.6 §7.2.3: *"a counterfactual is not a place to spend a real grant."*

    T173b did not exist, and as §8 specified it, it would have failed: `_observe_secure` read
    `_PRESENTED_APPROVAL` on `APPROVE` and routed it through `_take`, which **consumes**. An
    operator evaluating a policy in observe mode was silently burning their humans' single-use
    answers on actions observe mode was never going to gate.

    The reservation is still taken, and the asymmetry is the point: in observe mode the action
    genuinely executes, so the effect record has to exist or `v0.1 §5.4`'s duplicate refusal has
    nothing to refuse with. Observe mode suppresses CTRLRun's **decisions**, not the record of an
    effect that really happened.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    control = Control(Policy.from_yaml(OBSERVED), store, clock=lambda: T0)
    with with_approval(request_id):
        receipt = control.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)

    assert status_of(store, request_id) == "granted", (
        "observe mode spent a real grant on a counterfactual; §7.2.3 forbids it in those words"
    )
    assert store.get_approval(request_id).consumed_at is None
    # The action ran, and the effect record exists — observe mode gates nothing but records
    # everything that really happened.
    assert receipt.result.value == "observed"
    reserved = store.get_effect("refund:p1")
    assert reserved is not None and reserved.state.value == "committed"
    # And no event claims a consumption that did not occur.
    consumed = [event for event in store.events() if str(event.type) == "APPROVAL_CONSUMED"]
    assert consumed == [], (
        "an APPROVAL_CONSUMED event was appended for an approval nothing consumed, which is the "
        "false-green shape inside the evidence log"
    )
    store.close()


def test_T173b_the_grant_is_still_checked_and_a_mismatch_still_recorded(tmp_path) -> None:
    """The other half, and the reason the fix is *check without spending* rather than *skip*.

    Observe mode's whole job is to report what enforce mode would have done. A presented
    approval bound to a different action must still be found unusable and recorded as
    `APPROVAL_INVALIDATED` — and still not consumed, which it never was on this path.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    request_id = granted(store, a_refund("p9"))  # bound to a different action

    control = Control(Policy.from_yaml(OBSERVED), store, clock=lambda: T0)
    with with_approval(request_id):
        control.execute(a_refund("p1"), lambda: {"ok": True}, "refund:p1", lease=LEASE)

    assert status_of(store, request_id) == "granted"
    kinds = [str(event.type) for event in store.events()]
    assert "APPROVAL_INVALIDATED" in kinds, (
        "observe mode stopped noticing a mismatched approval; checking without spending must "
        "still check"
    )
    store.close()


# --- M1: authority reaches the hash from wherever it was loaded --------------------------------

_GRANTS = """
  grants:
    - id: g1
      subject: {agent: "pay-agent"}
      actions: ["pay"]
      constraints: {amount_lte: %d}
"""
_ACTIONS = """actions:
  pay:
    effect: "pay:{ref}"
    decision: allow
"""
BARE_POLICY = 'schema: ctrlrun.policy/v4\nversion: "p"\n' + _ACTIONS


def _separate_authority(cap: int):
    from ctrlrun.authority import Authority

    return Authority.from_yaml(
        "schema: ctrlrun.policy/v3\nauthority:" + (_GRANTS % cap), standalone=True
    )


def _hash_of(policy_text: str, authority, tmp_path, name: str) -> str:
    from ctrlrun import Control
    from ctrlrun.action import Action, Principal

    store = SQLiteStateStore(tmp_path / name, clock=lambda: T0)
    control = Control(Policy.from_yaml(policy_text), store, authority=authority, clock=lambda: T0)
    action = Action(
        name="pay", arguments={"ref": "r1", "amount": 50}, principal=Principal(agent="pay-agent")
    )
    receipt = control.execute(action, lambda: {"ok": True}, "pay:r1", lease=LEASE)
    store.close()
    return receipt.policy_hash


@pytest.mark.authority
def test_T171_a_separate_authority_document_is_part_of_the_hash(tmp_path) -> None:
    """§7.1: *"where it was loaded from a separate `--authority` document both are folded into
    the one canonical structure before hashing."*

    An independent review found that half unimplemented. `_canonical_policy` read
    `document["authority"]` from the **policy document only**, so a `Control` built with
    `authority=Authority.from_yaml(...)` — the gateway's shape, and `verify --authority`'s —
    hashed nothing of it: two deployments whose grants differed produced byte-identical
    provenance on every receipt, and one with **no** authority at all hashed the same as one
    with grants.

    The existing `test_T171_the_authority_document_is_part_of_the_hash` covers only the inline
    section, which is why the gap was unguarded rather than merely unfixed.
    """
    narrow = _hash_of(BARE_POLICY, _separate_authority(100), tmp_path, "a.db")
    wide = _hash_of(BARE_POLICY, _separate_authority(999999), tmp_path, "b.db")
    none_at_all = _hash_of(BARE_POLICY, None, tmp_path, "c.db")

    assert narrow != wide, (
        "two --authority documents with different limits produced the same provenance; a "
        "receipt's answer to 'what decided this action' is missing half of it"
    )
    assert narrow != none_at_all, (
        "a deployment with grants hashed the same as one with no authority at all"
    )


@pytest.mark.authority
def test_T171_the_same_grants_hash_the_same_from_either_file(tmp_path) -> None:
    """The identity that makes the fix a *fold* rather than a second hash.

    `Control.from_file` reads the policy document again for its `authority:` section and passes
    it in; the gateway passes one loaded from `--authority`. The same grants must reach the same
    hash either way, for the reason `_canonical_policy` gives about `source`: the same rules
    loaded from two paths are one policy, and a receipt saying otherwise would make the field
    noise.
    """
    from ctrlrun.authority import _optional_from_yaml

    inline_text = 'schema: ctrlrun.policy/v4\nversion: "p"\nauthority:' + (_GRANTS % 100) + _ACTIONS
    inline = _hash_of(inline_text, _optional_from_yaml(inline_text), tmp_path, "d.db")
    separate = _hash_of(BARE_POLICY, _separate_authority(100), tmp_path, "e.db")
    assert inline == separate


@pytest.mark.authority
def test_T171_the_hash_records_the_authority_that_actually_decided(tmp_path) -> None:
    """The substitution is a substitution, not a merge, and that is the fail-closed reading.

    A `Control` handed a policy with an inline `authority:` section and no `authority=` argument
    does not enforce that section — `_authority_result` reads `self._authority` and nothing
    else. So the hash records `None`, which is what decided the action. A merge would have put
    grants on the receipt that governed nothing, which is the more comfortable answer and the
    wrong one.
    """
    inline_text = 'schema: ctrlrun.policy/v4\nversion: "p"\nauthority:' + (_GRANTS % 100) + _ACTIONS
    unenforced = _hash_of(inline_text, None, tmp_path, "f.db")
    bare = _hash_of(BARE_POLICY, None, tmp_path, "g.db")
    assert unenforced == bare, (
        "the receipt claimed an authority section that this Control never consulted"
    )


# --- T177c and T177d: the CLI surfaces §9.4 froze, which had no tests --------------------------

CITED = """
schema: ctrlrun.policy/v4
version: "cited"
controls:
  maker-checker-refunds:
    title: "A refund over the desk limit is approved by a second person"
  card-data-handling:
    title: "Cardholder data is not written to evidence"
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    controls: [maker-checker-refunds]
    decision: allow
  customer.read:
    controls: [card-data-handling]
    decision: allow
"""


def test_T177c_receipts_control_filters_on_the_control_a_receipt_cites(tmp_path, monkeypatch):
    """§7.3's last line: *"`ctrlrun receipts --control <id>` filters on it — a flag, not a
    command."* §8's T177c asserts it, and **neither the flag nor the test existed.**

    An independent review found `Receipt.controls` written on every receipt and queryable from
    nowhere, which makes the registry's own argument — *"an operator can go from a control to its
    evidence"* — unreachable from the CLI that holds the evidence.
    """
    from click.testing import CliRunner

    from ctrlrun import Control
    from ctrlrun.action import Action, Principal
    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(CITED), store, clock=lambda: T0)
    control.execute(a_refund(), lambda: {"ok": True}, "refund:p1", lease=LEASE)
    control.execute(
        Action(
            name="customer.read",
            arguments={"id": "c1"},
            principal=Principal(agent="refund-agent"),
        ),
        lambda: {"ok": True},
        None,
        lease=LEASE,
    )
    store.close()
    monkeypatch.setenv("CTRLRUN_STATE", str(database))

    everything = CliRunner().invoke(cli.main, ["receipts"])
    assert everything.exit_code == 0, everything.output
    assert "stripe.refund" in everything.output and "customer.read" in everything.output

    filtered = CliRunner().invoke(cli.main, ["receipts", "--control", "maker-checker-refunds"])
    assert filtered.exit_code == 0, filtered.output
    assert "stripe.refund" in filtered.output
    assert "customer.read" not in filtered.output, (
        "--control returned a receipt that does not cite it; a filter that matches everything "
        "is a filter nobody can use as evidence"
    )

    # An id no receipt cites is empty rather than an error, and says so.
    missing = CliRunner().invoke(cli.main, ["receipts", "--control", "not-a-control"])
    assert missing.exit_code == 0, missing.output
    assert "no receipts cite" in missing.output


def test_T177c_the_command_list_is_exactly_the_one_the_spec_froze():
    """§9.4: v0.6 adds **no command**, and this is the assertion that was missing.

    The review checked it by hand and found the list correct; a milestone whose central claim is
    *"the surface did not grow"* should not be relying on somebody checking by hand.
    """
    from ctrlrun.cli import main as cli

    #: What the CLI offered when v0.6 shipped. §9.4's claim is about *this milestone*, so the
    #: list stays frozen at what v0.6 saw and anything added afterwards is named separately
    #: below -- otherwise a later addition would silently rewrite v0.6's central claim.
    frozen_by_v0_6 = [
        "approve",
        "delegate",
        "demo",
        "deny",
        "effects",
        "gateway",
        "init",
        "inspect",
        "receipts",
        "resolve",
        "revoke",
        "stats",
        "verify",
    ]
    #: Added after v0.6, each on its own version line and each in its own specification.
    after_v0_6 = [
        "mcp-operator",  # SPEC-mcp-operator.md §9.4
        # SPEC-scan.md §9.4. It ships in the same release as the operator server and is no more
        # a v0.6 feature than that one: §9.4's claim is about the surface *this milestone* grew,
        # and a subcommand landing before the tag does not retroactively make it one.
        "scan",
        # SPEC-v0.8 §5.3, §11.1. Opening a break-glass envelope is an act, and acts get
        # commands. It is a v0.8 addition on the v0.8 line, not a v0.6 one.
        "break-glass",
    ]

    assert sorted(cli.main.commands) == sorted(frozen_by_v0_6 + after_v0_6)
    assert not set(frozen_by_v0_6) & set(after_v0_6)


def test_T177d_an_expired_lease_is_displayed_as_expired_and_the_display_transitions_nothing(
    tmp_path, monkeypatch
):
    """§5.2, and §8's T177d, which had no test either.

    Nothing sweeps, so a lease that lapses and is never contended stays `EXECUTING` in the table
    indefinitely — and an operator reading `executing` cannot tell it from live work. The record
    must be **unchanged** afterwards: `list_effects` is a read and reads never move anything.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.reserve_effect("refund:p1", "act_1", timedelta(seconds=1))
    store.begin_execution("refund:p1", "act_1")
    store.close()

    before = SQLiteStateStore(database, clock=lambda: T0)
    assert before.get_effect("refund:p1").state.value == "executing"
    before.close()

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    shown = CliRunner().invoke(cli.main, ["effects"])
    assert shown.exit_code == 0, shown.output
    assert "(lease expired)" in shown.output, shown.output

    after = SQLiteStateStore(database, clock=lambda: T0)
    record = after.get_effect("refund:p1")
    assert record.state.value == "executing", (
        "showing an effect moved it; §5.2 says a read is not a transition"
    )
    after.close()


# --- The minors a review found, each with the test that was missing ---------------------------


@pytest.mark.parametrize(
    ("what", "identifier"),
    [
        ("a newline", "maker\nchecker"),
        ("a carriage return", "maker\rchecker"),
        ("a line separator", "maker\u2028checker"),
        ("a zero-width joiner", "maker\u200dchecker"),
    ],
)
def test_a_control_id_with_a_control_character_is_refused(what: str, identifier: str) -> None:
    """§7.3, on `state.py`'s `_approver` reasoning.

    `ctrlrun receipts` and `ctrlrun receipts --control` print one record per line, so a line
    break inside an id forges a whole row in a listing an operator reads to decide what happened.
    JSON escapes it, so the receipt itself is safe — the CLI table is not, and the id reaches
    both.

    A refusal and not an escape: a stored id that differs from the one the operator wrote is
    worse than a rejected document, because the evidence would then cite something nobody typed.
    """
    import yaml

    document = yaml.safe_dump(
        {
            "schema": "ctrlrun.policy/v4",
            "controls": {identifier: {"title": "T"}},
            "actions": {"a.b": {"decision": "allow"}},
        }
    )
    # Round-tripped through the dumper, so the id really does carry the character rather than a
    # backslash-n that YAML would hand back as two literal characters.
    assert identifier in yaml.safe_load(document)["controls"]
    with pytest.raises(PolicyError, match="control character"):
        Policy.from_yaml(document)


def test_a_control_id_longer_than_the_limit_is_refused() -> None:
    """The other half of the same rule: an id is printed in a table, so it is bounded."""
    from ctrlrun.policy import MAX_CONTROL_ID

    long_id = "x" * (MAX_CONTROL_ID + 1)
    with pytest.raises(PolicyError, match="the limit is"):
        Policy.from_yaml(
            f'schema: ctrlrun.policy/v4\ncontrols:\n  {long_id}: {{title: "T"}}\n'
            "actions:\n  a.b:\n    decision: allow\n"
        )
    # And the limit is not so tight that an ordinary id trips it.
    Policy.from_yaml(
        f'schema: ctrlrun.policy/v4\ncontrols:\n  {"x" * MAX_CONTROL_ID}: {{title: "T"}}\n'
        "actions:\n  a.b:\n    decision: allow\n"
    )


def test_a_receipts_controls_field_is_parsed_and_never_coerced() -> None:
    """`tuple(document.get("controls") or ())` turned the string `"abc"` into three controls.

    Everything else in `Receipt.from_dict` parses into a closed set; this did not, so a
    malformed document produced a receipt citing `a`, `b` and `c` — three controls nobody wrote
    down — in a document a reader would take as evidence.
    """
    from ctrlrun.errors import InvalidArgument
    from ctrlrun.receipt import _controls_of

    assert _controls_of(None) == ()
    assert _controls_of(["a", "b"]) == ("a", "b")
    for malformed in ("abc", 5, {"a": 1}, ["a", 5]):
        with pytest.raises(InvalidArgument):
            _controls_of(malformed)

    # **Through `from_dict`, which is what a reader actually calls.** Asserting only on the
    # helper leaves the call site free to keep coercing -- a mutation run found exactly that,
    # with the old `tuple(... or ())` restored and this test still green.
    from ctrlrun.receipt import Receipt

    document = Receipt.from_json(_a_receipt_json()).to_dict()
    document["controls"] = "abc"
    with pytest.raises(InvalidArgument):
        Receipt.from_dict(document)

    document["controls"] = ["maker-checker-refunds"]
    assert Receipt.from_dict(document).controls == ("maker-checker-refunds",)


def _a_receipt_json() -> str:
    """One real receipt, produced by `Control` rather than hand-built."""
    import tempfile
    from pathlib import Path as _Path

    from ctrlrun import Control

    with tempfile.TemporaryDirectory() as area:
        store = SQLiteStateStore(_Path(area) / "state.db", clock=lambda: T0)
        control = Control(Policy.from_yaml(ALLOW_ALL), store, clock=lambda: T0)
        receipt = control.execute(a_refund(), lambda: {"ok": True}, "refund:p1", lease=LEASE)
        store.close()
        return receipt.to_json()


def test_the_policy_hash_is_injective_over_documents_that_load() -> None:
    """`_plain`'s `str(key)` and its lossy `bytes` decode were two collisions.

    `Policy._from_document` never validates the `authority:` section itself — that is
    `_optional_from_yaml`'s job, on a different call path — so `{1: 2}` and `{"1": 2}` both
    loaded as a `Policy` and hashed identically, and so did two distinct `!!binary` values
    through `decode(errors="replace")`.

    Neither is exploitable today: every such document is refused when the section is actually
    parsed. But `policy_hash` is supposed to be injective over the inputs `Policy.from_yaml`
    accepts, which is the property `canonical_bytes` was promoted to guarantee, and "safe
    because some other loader happens to refuse it" is not that property.
    """
    from ctrlrun.policy import _plain

    # **Belt, and the braces that turned out to be load-bearing first.** Folding the parsed
    # authority into the canonical form — the fix for the `--authority` finding — means
    # `Policy.from_yaml` now parses the section itself, so both collisions are refused before
    # `_plain` sees them. Asserted, because that is why the collision is closed:
    head = (
        "schema: ctrlrun.policy/v4\nactions:\n  a.b:\n    decision: allow\n"
        'authority:\n  grants:\n    - id: g1\n      subject: {agent: "x"}\n'
        '      actions: ["a.b"]\n      constraints:\n'
    )
    for keyed in ("        1: 2\n", '        "1": 2\n'):
        with pytest.raises(PolicyError):
            Policy.from_yaml(head + keyed)

    # And `_plain` refuses them directly, which is what keeps the property true if a future
    # section reaches it by another route. A guard whose only proof is that nothing currently
    # calls it is the shape this suite keeps refusing.
    with pytest.raises(PolicyError, match="must be strings"):
        _plain({1: 2})
    with pytest.raises(PolicyError, match="binary"):
        _plain({"note": b"\xff\xfe"})
    # Two distinct byte strings would have decoded onto one value with `errors="replace"`.
    assert _plain({"a": 1}) == {"a": 1}


def test_a_denial_is_recorded_against_the_approval_that_was_presented(tmp_path) -> None:
    """§7.2.1's third bullet, which was a sentence and not a behaviour.

    The `DENY` row leaves a **live** granted approval for an action the policy currently
    forbids, and §7.2.1 offers three reasons that bound the exposure. One of them is that *"the
    refusal is recorded against the approval so the history shows a grant that met a denial."*
    An independent review found `ACTION_DENIED` appended with `approval_id=None` and the receipt
    carrying neither the id nor the approver: nothing connected the live grant to the refusal.

    And the approval is **untouched**, which is the whole point of the row: it stays `granted`,
    unspent, for the action a human really did answer.
    """
    from ctrlrun import Control
    from ctrlrun.control import with_approval
    from ctrlrun.errors import ActionDenied

    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    action = a_refund()
    request_id = granted(store, action)

    control = Control(Policy.from_yaml(DENY_OVER), store, clock=lambda: T0)
    with pytest.raises(ActionDenied), with_approval(request_id):
        control.execute(action, lambda: {"ok": True}, "refund:p1", lease=LEASE)

    denied = [event for event in store.events() if str(event.type) == "ACTION_DENIED"]
    assert denied, "no ACTION_DENIED was appended at all"
    assert denied[-1].approval_id == request_id, (
        "the denial does not name the live grant it refused; §7.2.1 offers that record as one "
        "of three reasons for leaving the token live"
    )

    receipt = store.receipts()[-1]
    assert receipt.result.value == "denied"
    assert receipt.approval_id == request_id
    assert receipt.approver == "cli:ada"

    # Unspent, which is the row itself.
    assert status_of(store, request_id) == "granted"
    store.close()

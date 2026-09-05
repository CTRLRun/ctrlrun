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
    ],
)
def test_T171_any_decision_input_changes_the_hash(what: str, edited: str) -> None:
    """Each of §7.1's named inputs, one at a time. A hash that moved for some of them and not
    others would be worse than none: an operator would read "the policy did not change" from a
    field that only sometimes notices."""
    assert edited != TIDY, f"the edit for {what} did not change the document"
    assert policy_hash(edited) != policy_hash(TIDY), (
        f"changing {what} left the policy hash unchanged"
    )


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

    # The first rule matches: the union, in registry order. Asserted on the evaluation, because
    # an `approve` decision needs a human and the union is a property of the decision, not of
    # what happens after it.
    over = Policy.from_yaml(REGISTERED).evaluate(
        Action(
            name="stripe.refund",
            arguments={"payment_id": "p2", "amount": 90000},
            principal=Principal(agent="a"),
        )
    )
    assert over.decision.value == "approve"
    assert over.controls == ("card-data-handling", "maker-checker-refunds")

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

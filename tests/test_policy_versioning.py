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

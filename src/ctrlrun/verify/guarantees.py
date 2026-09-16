# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The closed guarantee catalogue, `ctrlrun.guarantees/v1`. SPEC-v0.4 §2.

Ten entries, ordered, versioned, and permanent: a guarantee that is removed leaves its number
retired, and a guarantee that is added takes the next one (§2.3). Nothing here runs anything —
the registry states *what* is claimed, `scenarios.py` states how it is exercised, and
`report.py` states how it is rendered.

Every N/A reason in this module is a statement about the operator's **document**. A scenario
that could not be built for any other cause is an internal error and exits 3 (§3.2, §3.8), so
none of these strings is ever reachable from a failure of verify itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: SPEC-v0.4 §2.3 — the catalogue identifier. It appears in every report and on nothing else.
#: SPEC-v0.6 §9.5 — `v2` adds G11 (§6.6). The version moves because the catalogue is a closed
#: set a report is read against, and a reader that met an id it did not know would have no way
#: to tell a new guarantee from a corrupted line.
#: SPEC-v0.7 §9.4: `v3` is G1 to G16. It moves once, with G13, and the other four join it as
#: their items land; nothing is released in between.
#: SPEC-v0.8 §11.4: `v4` is G1 to G21, and it moves once, here, with G18. G17, G19, G20 and G21
#: join it with their items, and item 8 asserts all five present before the release. No stub
#: rows: a guarantee that reports anything before its check exists is a false green.
#: SPEC-v0.10 §7: `v6` is G1 to G27, and it moves once, with item 1's G25. G26 and G27 join it
#: with their items.
#: SPEC-v0.9 §8: `v5` was G1 to G24, and it moved once, with G24. G22 and G23 joined it with
#: their items. No stub rows: a guarantee that reports anything before its check exists is a
#: false green, which is what 0.6.1 had to fix and what G17 shipped as in v0.8.
#: SPEC-v0.11 §8: `v7` is G1 to G32, and it moves once, here, with item 4's G31. G28 (item 2),
#: G29, G30 and G32 (item 3) join it with their items, and the release item asserts every row
#: present before the release. No stub rows: a guarantee that reports anything before its check
#: exists is a false green.
CATALOGUE: Final = "ctrlrun.guarantees/v7"


@dataclass(frozen=True)
class Guarantee:
    """One entry: a permanent id, the line a report prints, and its ancestry (§2.1).

    `descends_from` is not decoration. §2.3 makes a guarantee "a refusal the specification
    already requires", so every entry names the acceptance tests it is the deployed form of;
    an entry that could not name one would be a feature request wearing a guarantee's clothes.
    """

    id: str
    title: str
    descends_from: tuple[str, ...]


GUARANTEES: Final = (
    Guarantee("G1", "mutated approval refused", ("v0.1 §7 T2",)),
    Guarantee("G2", "replayed approval refused", ("v0.1 §7 T4", "v0.1 §7 T12")),
    Guarantee("G3", "duplicate effect refused", ("v0.1 §5.3 E2", "v0.2 §10 T14")),
    Guarantee("G4", "one winner under concurrency", ("v0.1 §7 T3",)),
    Guarantee("G5", "ambiguous blocks a blind retry", ("v0.1 §7 T1", "v0.1 §7 T8")),
    Guarantee("G6", "unknown action refused", ("v0.1 §7 T6",)),
    Guarantee("G7", "no principal refused", ("v0.1 §2.1", "v0.2 §10 T21", "v0.3 §10 T62")),
    Guarantee("G8", "expired authority refused", ("v0.3 §10 T71",)),
    Guarantee("G9", "delegation cannot escalate", ("v0.3 §10 T76", "v0.3 §10 T81", "v0.3 §10 T75")),
    Guarantee("G10", "unknown exception is ambiguous", ("v0.1 §5.5", "v0.1 §7 T1", "v0.1 §7 T8")),
    Guarantee("G11", "an altered receipt is detected", ("v0.6 §6.5", "v0.6 §8 T164")),
    Guarantee(
        "G12",
        "a byte written is ambiguous",
        ("v0.1 §5.5", "v0.2 §6.8", "v0.7 §8 T220", "v0.7 §8 T221", "v0.7 §8 T223b"),
    ),
    Guarantee(
        "G13",
        "clock divergence is named",
        (
            "v0.1 §5.3 E3",
            "v0.7 §8 T209",
            "v0.7 §8 T210",
            "v0.7 §8 T211",
            "v0.7 §8 T212",
            "v0.7 §8 T213",
        ),
    ),
    Guarantee(
        "G14",
        "token changes across a renewal",
        (
            "v0.1 §5.4",
            "v0.7 §8 T232",
            "v0.7 §8 T233",
            "v0.7 §8 T238",
        ),
    ),
    # SPEC-v0.7 §9.4 — **in id order**, because `BY_ID`'s insertion order is the report's order.
    # Items 3, 4 and 5 each appended after G13 on their own branch, so a textual merge would have
    # produced G13/G15/G14/G16 or a conflict; this is the conflict, resolved the way the
    # catalogue reads.
    Guarantee(
        "G15",
        # Exactly `report._TITLE_WIDTH`. A longer title is the one thing that breaks the CLI
        # table's alignment, and "a renewal past the operator's ceiling is refused" was 48.
        "renewal past the ceiling refused",
        (
            "v0.1 §5.4",
            "v0.7 §8 T240",
            "v0.7 §8 T241",
            "v0.7 §8 T242",
            "v0.7 §8 T245",
            "v0.7 §8 T245b",
        ),
    ),
    Guarantee(
        "G16",
        # Not "a moved precondition is refused": a precondition that moves after the comparison
        # is not refused (§6.7), and a title is the shortest sentence this project writes about
        # a guarantee. What is compared is the fingerprint, and what G16 grades is one that had
        # already moved when the recheck read it.
        "a moved fingerprint is refused",
        ("v0.1 §4.2", "v0.7 §8 T253", "v0.7 §8 T254"),
    ),
    Guarantee(
        "G17",
        # 30 characters against `report._TITLE_WIDTH`'s 32. It says what the refusal is about and
        # not what it prevents: what the kernel refuses is an approval whose **recorded**
        # entitlement does not cover the role, and §3.8 is the paragraph that bounds the claim.
        "an unentitled approver refused",
        ("v0.6 §7.3", "v0.8 §10 T297", "v0.8 §10 T298"),
    ),
    Guarantee(
        "G18",
        # 28 characters, because `report._TITLE_WIDTH` is 32 and a wider title breaks the
        # table's alignment: v0.7 had to shorten G12's for the same reason. "the requester
        # cannot approve" and not "self-approval is refused", because what is compared is the
        # resolved principal on each side and never a string, and "self" invites the reading
        # that two different strings are two different people (SPEC-v0.8 §4.1).
        "the requester cannot approve",
        ("v0.3 §4.2", "v0.8 §10 T285", "v0.8 §10 T286"),
    ),
    Guarantee(
        "G19",
        # 30 characters. "counts once" and not "is refused": a second yes from one principal is
        # recorded and not rejected, and what it does not do is move the count (§4.2).
        "one principal counts once",
        ("v0.3 §4.2", "v0.8 §10 T311", "v0.8 §10 T310"),
    ),
    Guarantee(
        "G20",
        # 28 characters against `report._TITLE_WIDTH`'s 32. "before its exp" is the whole of
        # what is new: a credential *past* its exp was always refused, and a title saying only
        # "a revoked credential refused" would grade v0.3 and claim v0.8.
        "revoked before its exp: no",
        ("v0.3 §3.4", "v0.8 §10 T340", "v0.8 §10 T341"),
    ),
    Guarantee(
        "G21",
        # 30 characters against `report._TITLE_WIDTH`'s 32. "decides nothing" and not "is
        # refused": what a deployment gets under an unapproved policy is not one refusal, it is
        # every action denied, and a title saying "refused" would understate it.
        "unapproved policy decides no",
        ("v0.6 §7.1", "v0.8 §10 T354", "v0.8 §10 T355"),
    ),
    Guarantee(
        "G22",
        # 32 characters exactly, against `report._TITLE_WIDTH`. "held" and not "exhausted": what
        # refuses the next reserve is a budget whose consumption is held by an effect nobody has
        # resolved, and a title saying "exhausted" would describe the ordinary case and miss the
        # one this guarantee is about (SPEC-v0.9 §8).
        "held budget refuses next reserve",
        ("v0.9 §4.1", "v0.9 §9 T436", "v0.9 §9 T437"),
    ),
    Guarantee(
        "G23",
        # 32 characters exactly, against `report._TITLE_WIDTH`. "a failing scope provider" and
        # not "an out-of-scope record": what G23 grades is the **unavailable** half, because
        # that is the one where a kernel could plausibly fail open by treating an unreadable
        # scope as an empty constraint (SPEC-v0.9 §5.6, §8).
        "a failing scope provider refuses",
        ("v0.9 §5.6", "v0.9 §9 T388", "v0.9 §9 T390"),
    ),
    Guarantee(
        "G24",
        # 26 characters against `report._TITLE_WIDTH`'s 32. "grant refused" and not "action
        # refused", because what is compared is the grant's task dimension against the task the
        # caller named, and the refusal reports `authority_task` rather than a bare no_authority
        # so an operator can tell this from having no grant at all (SPEC-v0.9 §6.2).
        "grant refused off its task",
        ("v0.9 §6.2", "v0.9 §9 T380", "v0.9 §9 T381"),
    ),
    Guarantee(
        "G25",
        # 30 characters against `report._TITLE_WIDTH`'s 32. "narrows or it is refused" and not
        # "widening is refused", because the guarantee grades both halves and a title naming only
        # the negative would let the positive control drift out (SPEC-v0.10 §7).
        "a hop narrows or it is refused",
        ("v0.10 §2.4", "v0.10 §2.7 T470", "v0.10 §2.7 T471", "v0.10 §2.7 T472"),
    ),
    Guarantee(
        "G26",
        # 28 characters against `report._TITLE_WIDTH`'s 32. "named on both sides" and not "both
        # receipts name one hop": the two ends of a hop are not always two receipts, because a
        # relay's receipt names the hop it acted UNDER and the one it created is named by its own
        # `DELEGATION_CREATED` event (SPEC-v0.10 §3.4.4, §7).
        "a hop is named on both sides",
        ("v0.10 §3.4", "v0.10 §3.6 T478", "v0.10 §3.6 T488"),
    ),
    Guarantee(
        "G27",
        # 28 characters against `report._TITLE_WIDTH`'s 32. It grades §4.3's **check 2** and only
        # check 2: that is the one producing a DENY, which is what this title promises. Check 3
        # refuses at the handshake and produces `NotExecuted` with the effect `FAILED`, a
        # different outcome under a different name, and a scenario allowed to grade either would
        # report PASS without anybody knowing which (SPEC-v0.10 §7).
        "a swapped upstream is denied",
        ("v0.10 §4.3", "v0.10 §4.7 T490", "v0.10 §4.7 T493"),
    ),
    Guarantee(
        "G28",
        # 30 characters against `report._TITLE_WIDTH`'s 32. **It says truncation and does not say
        # append**, and an earlier draft said both. §2.4's table is what this title has to agree
        # with: an append lands above every anchored `seq`, so no anchored pair stops reproducing
        # and a later anchor freezes the forged chain as readily as an honest one. Correcting the
        # prose that argues a claim and leaving the claim in the registry would be worse than not
        # correcting it: the argument is read once and the registry is read by every operator who
        # runs `verify`.
        "truncation past an anchor fails",
        ("v0.11 §3", "v0.11 §3.6 T530", "v0.11 §3.6 T533"),
    ),
    Guarantee(
        "G29",
        # 31 characters against `report._TITLE_WIDTH`'s 32. **A delta and not "the chain
        # verifies"**: `unchained` is a pre-existing condition on any store migrated from v0.1 to
        # v0.5, survives a prefix prune and can never be inside a prefix, so the absolute version
        # would make retention permanently impossible on the oldest and largest stores.
        "a prune adds no new chain break",
        ("v0.11 §4.1", "v0.11 §4.7 T540", "v0.11 §4.7 T543"),
    ),
    Guarantee(
        "G30",
        # 29 characters. The hold is consulted **inside** the prune's transaction (§4.5): a hold
        # placed between a consult and a delete would be honoured by neither.
        "a held range refuses to prune",
        ("v0.11 §4.3", "v0.11 §4.7 T545"),
    ),
    Guarantee(
        "G31",
        # 27 characters against `report._TITLE_WIDTH`'s 32. It grades **the walk**, not the
        # field: `schema` has existed since v0.3 and `SPEC-v0.11 §6` adds no field. What was
        # never proved is that `verify_chain` walks a chain holding more than one of them,
        # hash by hash, each row hashed by the rule its own version wrote.
        "five receipt schemas verify",
        ("v0.11 §6", "v0.11 §6.1 T521", "v0.11 §6.2 T523"),
    ),
    Guarantee(
        "G32",
        # 31 characters. **§4.6's interaction, which neither G28 nor G29 grades.** G28 grades a
        # truncation against an anchor and G29 grades a prune against the chain; an honest prune
        # leaving the anchor report clean was graded by neither, and that interaction is the one
        # a review found had made items 2 and 3 mutually exclusive. A guarantee for each half and
        # none for the pair is how two correct sections ship cancelling each other.
        "an honest prune keeps anchors",
        ("v0.11 §4.6", "v0.11 §4.7 T547"),
    ),
)

#: By id, for `--only` and for the report. Insertion order is catalogue order.
BY_ID: Final = {guarantee.id: guarantee for guarantee in GUARANTEES}

#: How many OS processes G4 contends with (§2.2, §3.6 — every loop is bounded).
PROCESSES: Final = 8

#: How many candidate argument vectors the synthesizer tries per action (§3.3).
CANDIDATE_BOUND: Final = 64

#: The prefix on every value verify invents, so a value that ever appeared where it should not
#: have is recognizable on sight (§3.3).
SYNTHETIC_PREFIX: Final = "ctrlrun-verify"


# --- N/A reasons: statements about the configuration, never about a failed run (§2.1) ---

NO_APPROVE_RULE: Final = "no action requires approval"

#: SPEC-v0.8 §3.5, §11.7 — G17's own `N/A`, and a statement about the operator's **document**:
#: a control that names no `approver_role` gates nobody, which is the answer §3.5 gives and the
#: opposite of what a missing claim on a principal means.
NO_APPROVER_ROLE: Final = "no cited control names an approver role"

#: SPEC-v0.8 §4.2, §11.7 — G19's own `N/A`, and a statement about the operator's **document**:
#: a document where every action takes one yes has no count to get wrong. Not "M-of-N is not
#: configured", which would be a sentence about a deployment verify cannot see.
NO_M_OF_N: Final = "no action requires more than one approval"

#: SPEC-v0.8 §11.7 — G20's note, printed once beneath the table as `PRECONDITION_NOTE` is, and
#: **not** an `N/A`. Whether a deployment configures a revocation feed is a fact about a
#: constructor call in its application, which no document verify reads can say; so verify
#: supplies one, grades the kernel's behaviour under it, and says so here rather than making a
#: claim about a document that is silent on the subject.
#: SPEC-v0.8 §8.4, §11.7 — G21's note, and a note for the same reason G20's is one: whether a
#: deployment passes `require_approved_policy=True` is a fact about a constructor call in its
#: own code, which no document verify reads can state. Verify sets the flag for its own
#: scenario and says so here rather than claiming anything about a document that is silent.
POLICY_APPROVAL_NOTE: Final = (
    "G21 is graded with require_approved_policy set by verify: whether this deployment sets it "
    "is a fact about its own code, which verify cannot read"
)

REVOCATION_NOTE: Final = (
    "G20 is graded against a revocation feed verify supplies: whether this deployment "
    "configures one is a fact about its own code, which verify cannot read"
)
NO_EFFECT_TEMPLATE: Final = "no action declares an `effect:` template"

#: The sentence that makes G3's N/A actionable rather than mysterious (§2.2). It travels in
#: `detail.note` on every guarantee the missing template takes out, and the human report
#: prints it once, where §4.1's example puts it.
EFFECT_TEMPLATE_NOTE: Final = (
    "in a `ctrlrun.policy/v1` document the template lives in the @protect decorator, "
    "which verify does not read"
)

NO_ACTIONS: Final = (
    "the policy lists no action, so an unknown action is indistinguishable from a known one"
)
EVERY_ACTION_DENIED: Final = "every action in the policy is denied"
NO_AUTHORITY_SECTION: Final = "no authority section"
NO_EXPIRES_AT: Final = "no grant declares an expires_at"
NO_GRANT_MATCHES: Final = "no grant matches any action in the policy"

#: The reason `select()` found nothing on the *authority* axis rather than the policy axis:
#: an action did reach the requested decision, and no grant covered the action verify could
#: synthesize -- typically because the grant constrains `resources:` to a pattern and verify
#: renders the `resource:` template from `SYNTHETIC_PREFIX` values no pattern matches.
#:
#: Without it, every scenario fell back to its own hardcoded sentence about the policy, so
#: `examples/authority/devops.yaml` reported "the policy lists no action" about a document
#: listing five, and exited 0. A false N/A is a false green: it is excluded from the
#: denominator, so the run reports "1/1 pass" for the one guarantee that survived.
NO_GRANT_COVERS_SELECTION: Final = "no grant's `resources:` matches a resource verify can build"

#: Printed once beneath the table, the way `EFFECT_TEMPLATE_NOTE` is: the reason above is
#: short enough to read in a column, and this says what to do about it.
GRANT_RESOURCE_NOTE: Final = (
    "verify renders each `resource:` template from synthetic values, so a grant scoped to "
    "concrete resources matches nothing it can build; scope a grant to a "
    "`ctrlrun-verify-*` resource to make those guarantees applicable"
)
NO_DELEGABLE_GRANT: Final = "no grant is delegable"
#: SPEC-v0.9 §8, G24. A statement about the operator's **document**, like every reason in this
#: module: it says what the document does not declare, not what the kernel is not configured for.
NO_TASKS: Final = "no grant names a task"
#: SPEC-v0.10 §7, G25's second reason: the document has a delegable grant and verify can drive no
#: action under it, so the scenario cannot be built. `NO_DELEGABLE_GRANT` above is G25's first and
#: was already here, because a hop is a delegation; G25 reuses it rather than adding a second
#: spelling. This one is added because a reason that was silently unreachable would be the false
#: `N/A` §7 opens by forbidding.
NO_HOP_ACTION: Final = "the document's delegable grant admits no action verify can drive"
#: SPEC-v0.10 §7, G27. A statement about the operator's **document**: whether any action entry
#: declares an `upstream:` pin at all.
NO_UPSTREAM_PIN: Final = "no action entry pins an upstream"
#: SPEC-v0.9 §8, G22. A statement about the operator's **document**: whether any grant it declares
#: carries a budget at all.
NO_BUDGET: Final = "no grant carries a budget"
#: The action verify can drive carries no value for the metric the budget names, so nothing it
#: could run would spend against it (SPEC-v0.9 §2.3).
NO_BUDGET_METRIC: Final = "no action verify can drive carries the metric the budget names"
#: A document whose one permitted action cannot fit inside its own budget cannot exercise the
#: hold. Legal, and almost always a mistake: the first action of the window exhausts it.
BUDGET_CANNOT_BE_FILLED: Final = "one permitted action does not fit inside the grant's budget"

#: SPEC-v0.9 §2, for `select`. Distinct from `NO_GRANT_COVERS_SELECTION` because they are
#: unrelated facts and the operator's fix differs: one is a `resources:` pattern, the other is a
#: budget smaller than every action in the band the scenario needs. Reported without it, a policy
#: whose approve band starts above its grant's daily budget was told no grant's `resources:`
#: matched, about a document whose patterns matched perfectly.
NO_ACTION_FITS_THE_BUDGET: Final = (
    "every action reaching this decision exceeds a budget on the grant that covers it"
)

#: SPEC-v0.9 §2.3, and a **different fix** from the one above: the grant budgets a metric its
#: actions do not carry, so no vector helps and every action it covers is refused for ever.
#: Reported as "exceeds a budget" it sent an operator to raise a limit that was never the problem.
NO_METRIC_TO_MEASURE: Final = (
    "a budget on the grant that covers it names a metric the action does not carry"
)
BUDGET_MISS_NOTE: Final = (
    "a budget smaller than any single action in the band makes that band unreachable: every "
    "action needing it would exhaust the whole window. Raise the budget, or narrow the rule "
    "that admits actions the budget cannot pay for (SPEC-v0.9 §2)"
)
#: SPEC-v0.9 §8.1, G23. A statement about the **document**, which is what §8.1's argument
#: actually requires: it forbids an `N/A` about whether a *provider* is configured, because that
#: is a fact about an operator's code. Whether any action this configuration admits carries a
#: resource is a fact about their document, and a scope names resources, so an action with none
#: can never be in one.
NO_RESOURCE_TO_SCOPE: Final = "no action this configuration admits carries a resource"

#: SPEC-v0.9 §8.1 — G23's note, printed beneath its row the way `REVOCATION_NOTE` is. Whether a
#: deployment configures a scope provider is a fact about its own code, which verify cannot read,
#: so verify supplies one and says so rather than reporting `N/A` about something it never saw.
SCOPE_PROVIDER_NOTE: Final = (
    "G23 is graded against a scope provider verify supplies: whether this deployment configures "
    "one is a fact about its own code, which verify cannot read. The gateway and the ACS hook "
    "cannot name a provider at all (SPEC-v0.9 §5.2.2)"
)

#: SPEC-v0.7 §8.9, G16's note, printed once beneath the table as `EFFECT_TEMPLATE_NOTE` is. G16
#: is graded against verify's own stand-in for the operator's provider, because a provider is
#: named in code and not in any document verify reads; this says so where a reader looks.
PRECONDITION_NOTE: Final = (
    "verify supplies its own precondition provider; whether your @protect declares one is in "
    "your code, which verify does not read. The gateway and the ACS hook cannot name a "
    "provider at all, and refuse an approval that carries a fingerprint"
)

#: G4's second N/A (§2.2). No backend in v0.4 reaches it — `SQLiteStateStore` refuses
#: `:memory:` precisely so that it cannot — and the row exists so a v0.6 backend that cannot
#: make the guarantee reports N/A rather than a green it did not earn.
PER_CONNECTION_BACKEND: Final = (
    "the configured store backend is per-connection and cannot reserve across processes"
)

#: G4 runs its children under the real clock (§2.2), so a grant that lapsed before this run
#: cannot cover them. A statement about the document plus the wall clock, and reported rather
#: than run into a false red.
GRANT_ALREADY_EXPIRED: Final = (
    "the grant covering this action expired before this run, and G4's processes cannot share "
    "an injected clock"
)

#: G13's one N/A (SPEC-v0.7 §8.9). True of every run it appears on: SQLite has no clock of its
#: own, so there is nothing for the application's to diverge from.
STORE_READS_APPLICATION_CLOCK: Final = (
    "the store verify was given reads only the application's clock, so there is no second clock "
    "to diverge from; pass --store-url postgresql://… to grade this"
)

#: SPEC-v0.7 §8.9 — G15's two N/A reasons, and the bound behind the second. Every loop verify
#: runs is bounded (`v0.4 §3.6`), so a ceiling above what verify will drive is a statement about
#: the document *and* about verify's stated bound, on the precedent of `GRANT_ALREADY_EXPIRED`.
NO_CEILING_DECLARED: Final = (
    "no action verify can drive to allow or approve declares both `effect:` and `max_attempts`"
)
CEILING_BOUND: Final = 100

#: **Scoped to what verify can select**, on `NO_CEILING_DECLARED`'s shape and for its reason. An
#: earlier wording, "every declared max_attempts is above verify's bound", was false of a document
#: whose deny-only action declares `max_attempts: 3`: the fallback selection re-applies the effect
#: and ceiling filters and drops only the bound, so the sentence can only ever describe the actions
#: verify can drive. That is the identical defect §8.9 caught in the sibling sentence, and an N/A
#: reason that is not true of the operator's document is a false green (§8.9's opening MUST).
CEILING_ABOVE_BOUND: Final = (
    "every action verify can drive to allow or approve that declares both `effect:` and "
    f"`max_attempts` declares one above verify's bound of {CEILING_BOUND} attempts"
)

#: SPEC-v0.7 §8.9 — the reason G5 (and G14, when item 3 lands it) reports where the operator's
#: ceiling is the *only* thing that makes a renewal unselectable. Printed only where selecting
#: again without the ceiling filter does find something; otherwise `unselected()`'s reason wins,
#: because a document whose uncapped action is deny-only or ungranted is not a document whose
#: ceilings took the guarantee away.
CEILING_FORBIDS_RENEWAL: Final = (
    "every action with an `effect:` template that verify can select (a decision of allow or "
    "approve under a grant that covers it) declares max_attempts: 1, so no renewal can happen"
)

#: G14's note, printed once beneath the table (SPEC-v0.7 §8.9, §4.6). Not an N/A reason and not
#: a finding: the token is a function of `(effect_key, attempt)` and nothing else, so two stores
#: that share a provider account derive one token wherever their effect-key strings coincide.
#: Verify sees one store and cannot check it, and a guarantee that stayed silent about the one
#: thing it cannot see would be read as having checked it.
EFFECT_KEY_SCOPE_NOTE: Final = (
    "a token is unique only as far as your effect keys are: two stores sharing a provider "
    "account must not produce the same effect-key string for different effects, and nothing "
    "here can check that"
)

#: `--only` (§4.6).
NOT_SELECTED: Final = "not selected"

#: §1.3 — the fourth rule. Never a pass, and never an N/A.
CONTROL_FAILED: Final = "control failed"

__all__ = [
    "BUDGET_CANNOT_BE_FILLED",
    "BUDGET_MISS_NOTE",
    "BY_ID",
    "CANDIDATE_BOUND",
    "CATALOGUE",
    "CEILING_ABOVE_BOUND",
    "CEILING_BOUND",
    "CEILING_FORBIDS_RENEWAL",
    "CONTROL_FAILED",
    "EFFECT_KEY_SCOPE_NOTE",
    "EFFECT_TEMPLATE_NOTE",
    "EVERY_ACTION_DENIED",
    "GRANT_ALREADY_EXPIRED",
    "GRANT_RESOURCE_NOTE",
    "GUARANTEES",
    "NOT_SELECTED",
    "NO_ACTIONS",
    "NO_ACTION_FITS_THE_BUDGET",
    "NO_APPROVER_ROLE",
    "NO_APPROVE_RULE",
    "NO_AUTHORITY_SECTION",
    "NO_BUDGET",
    "NO_BUDGET_METRIC",
    "NO_CEILING_DECLARED",
    "NO_DELEGABLE_GRANT",
    "NO_EFFECT_TEMPLATE",
    "NO_EXPIRES_AT",
    "NO_GRANT_COVERS_SELECTION",
    "NO_GRANT_MATCHES",
    "NO_HOP_ACTION",
    "NO_METRIC_TO_MEASURE",
    "NO_RESOURCE_TO_SCOPE",
    "NO_TASKS",
    "NO_UPSTREAM_PIN",
    "PER_CONNECTION_BACKEND",
    "PROCESSES",
    "SCOPE_PROVIDER_NOTE",
    "STORE_READS_APPLICATION_CLOCK",
    "SYNTHETIC_PREFIX",
    "Guarantee",
]

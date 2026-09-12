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
CATALOGUE: Final = "ctrlrun.guarantees/v3"


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
    "NO_APPROVE_RULE",
    "NO_AUTHORITY_SECTION",
    "NO_CEILING_DECLARED",
    "NO_DELEGABLE_GRANT",
    "NO_EFFECT_TEMPLATE",
    "NO_EXPIRES_AT",
    "NO_GRANT_COVERS_SELECTION",
    "NO_GRANT_MATCHES",
    "PER_CONNECTION_BACKEND",
    "PROCESSES",
    "STORE_READS_APPLICATION_CLOCK",
    "SYNTHETIC_PREFIX",
    "Guarantee",
]

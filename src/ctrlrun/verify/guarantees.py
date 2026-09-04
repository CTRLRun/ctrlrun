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
CATALOGUE: Final = "ctrlrun.guarantees/v1"


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
NO_DELEGABLE_GRANT: Final = "no grant is delegable"

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

#: `--only` (§4.6).
NOT_SELECTED: Final = "not selected"

#: §1.3 — the fourth rule. Never a pass, and never an N/A.
CONTROL_FAILED: Final = "control failed"

__all__ = [
    "BY_ID",
    "CANDIDATE_BOUND",
    "CATALOGUE",
    "CONTROL_FAILED",
    "EFFECT_TEMPLATE_NOTE",
    "EVERY_ACTION_DENIED",
    "GRANT_ALREADY_EXPIRED",
    "GUARANTEES",
    "NOT_SELECTED",
    "NO_ACTIONS",
    "NO_APPROVE_RULE",
    "NO_AUTHORITY_SECTION",
    "NO_DELEGABLE_GRANT",
    "NO_EFFECT_TEMPLATE",
    "NO_EXPIRES_AT",
    "NO_GRANT_MATCHES",
    "PER_CONNECTION_BACKEND",
    "PROCESSES",
    "SYNTHETIC_PREFIX",
    "Guarantee",
]

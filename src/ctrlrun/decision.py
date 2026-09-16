# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The decision vocabulary, below everything that produces or records one.

**Why this module exists rather than these two names living in `policy.py`.** They did, and it
put a cycle in the module map: `state.py` imports `receipt.py`, `receipt.py` imported
`policy.py` for exactly these two names, `policy.py` reaches `authority.py` from inside two
functions, and `authority.py` imports `state.py`. A v0.7 review found it and
`docs/ARCHITECTURE.md` §6 has recorded it since 2026-09-12, as a named item before v1.0 rather
than something to change in a release pass.

Nothing was ever broken at run time, which is why it survived five milestones: the two edges out
of `policy.py` are function-level and run after every module is loaded, so `import ctrlrun`
resolves in one order and the suite passes. What it cost was §6's own rule, **dependencies point
downward only** -- with a cycle in place that sentence describes import order rather than the
module map, and the map is what tells a contributor what a module may know about.

**Why this edge and not one of the other three.** `receipt.py` is the module everything else
records through; the table in §6 lists it as used by *everything else*. An evidence type reaching
**up** into the decider is the edge that most contradicts the map, and what it reached up for was
pure vocabulary: a three-member `StrEnum` and a reason string, no behaviour either way. The other
candidate was moving `policy.py`'s two deferred imports, but `_canonical_authority` and
`hash_with_authority` genuinely need authority's canonicalization, and relocating them decides
who owns the policy hash, which is a real behavioural question rather than a placement one.

**No public name moves.** `policy.py` re-exports both, so `from ctrlrun.policy import Decision`
still resolves, `SPEC-v0.1.md` §8's frozen `__init__` block is literally unchanged, and
`from ctrlrun import Decision` is the same object it always was. The cycle was the only thing
that changed shape.

This module imports nothing from the package, and `test_the_module_graph_has_no_cycle` fails if
it ever does.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Decision(StrEnum):
    """What may happen to an action: exactly three outcomes in v0.1 (SPEC-v0.1 §3.3).

    `StrEnum`, so a member renders as its value in receipts and CLI output (SPEC-v0.1 §6.1).
    """

    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"


#: SPEC-v0.8 §8.4 — the refusal a deployment gets under a policy nobody approved. Its own
#: reason, never folded into `unknown_action` or a generic denial: "this policy was never
#: approved" and "this policy denies this action" are different facts and an operator acts on
#: them differently.
#:
#: It sits beside `Decision` rather than in `policy.py` because `receipt.py` buckets it with the
#: other refusal reasons and was the second half of the import that made the cycle.
POLICY_UNAPPROVED: Final = "policy_unapproved"

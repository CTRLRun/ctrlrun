"""The CTRLRun framework probe. SPEC-v0.4 §7. Research, not part of the package.

This directory lives **outside `src/`**. It is not packaged, never imported by `ctrlrun`, and
its per-framework dependencies are never installed by `ctrlrun` or by any of its extras
(T124b).

It answers a question this project has so far only asserted: *what actually happens when the
response is lost and the framework retries?*

The table it emits reports **behaviour, not quality**. A framework that retries a lost response
is doing what its documentation says it does. The finding is about what an agent stack does
*without* an effect-level guard, and it would be dishonest to present it as anything else.
"""

from __future__ import annotations

SCHEMA = "ctrlrun.framework-probe/v1"

__all__ = ["SCHEMA"]

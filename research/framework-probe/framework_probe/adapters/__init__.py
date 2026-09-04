"""The adapter registry. SPEC-v0.4 §7.

Two stubs and five real rows. The stubs are always available and always run: they are what
keeps the harness honest, because one of them must report `executed_twice` and the other
`executed_once` against the same remote (T122).

An adapter whose framework is not installed is **skipped by name**, and the skip reaches the
results file rather than being a silent absence (T123). A table with four rows where five were
expected has to say which one is missing.
"""

from __future__ import annotations

from .base import Adapter, Attempt, read_version
from .stub import NOT_RETRYING, RETRYING

#: The two that need nothing installed. `run.py` includes them by default and `--no-stubs`
#: leaves them out for a publishable table.
STUBS: tuple[Adapter, ...] = (RETRYING, NOT_RETRYING)


def frameworks() -> tuple[Adapter, ...]:
    """Every third-party adapter, installed or not.

    Imported lazily, one at a time: an adapter whose framework is missing must be *skipped*,
    and a registry that imported them all at module scope would fail before it could say so.
    """
    found: list[Adapter] = []
    for module in ("mcp_client", "langgraph", "crewai", "openai_agents", "autogen"):
        try:
            imported = __import__(f"{__name__}.{module}", fromlist=["ADAPTER"])
        except ImportError:  # pragma: no cover - a broken adapter file, not a missing framework
            continue
        found.append(imported.ADAPTER)
    return tuple(found)


def all_adapters(*, stubs: bool = True) -> tuple[Adapter, ...]:
    return (*(STUBS if stubs else ()), *frameworks())


__all__ = ["STUBS", "Adapter", "Attempt", "all_adapters", "frameworks", "read_version"]

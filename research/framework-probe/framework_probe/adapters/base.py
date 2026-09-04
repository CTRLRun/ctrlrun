"""What an adapter is. SPEC-v0.4 §7.3.

An adapter drives one framework through one scenario against the fake remote and reports
**nothing about the outcome**. The runner derives the outcome from what the remote saw
(`remote.py`), because an adapter that graded its own run would be the framework marking its
own homework.

The fairness rules are the interface, not a note beside it:

1. The same fake remote, with the same behaviour, on the same port discipline.
2. The same scenario text — prompt, tool name, description and schema — byte-identical
   wherever the framework's API admits it, and the diff recorded where it does not.
3. **Framework defaults.** No retry setting changed, no timeout tuned, no guard added.
4. At most **one** configuration change per framework, permitted only where the framework
   cannot run the scenario at all without it, and it appears in `config_deviation` — which
   the results table renders as a column.
5. The version is read at runtime, from the installed distribution, never written down by
   hand (T123).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from typing import Protocol

from ..scenarios import Scenario


@dataclass(frozen=True)
class Attempt:
    """What the adapter itself can say: whether it ran, and what went wrong if it did not.

    Deliberately not an outcome. The outcome is the remote's to report.
    """

    error: str | None = None
    notes: str = ""


class Adapter(Protocol):
    """One framework, driven through one scenario."""

    #: The name that appears in the results table.
    name: str
    #: The installed distribution this adapter reads its version from (§7.3 rule 5).
    distribution: str
    #: §7.3 rule 4 — `None`, or the single change without which the framework cannot run the
    #: scenario at all. It goes in the table.
    config_deviation: str | None

    def available(self) -> bool:
        """Is the framework installed? An absent one is skipped **by name** (T123)."""
        ...

    def run(self, scenario: Scenario, url: str) -> Attempt:
        """Drive the framework once, against the tool endpoint at `url`."""
        ...


def read_version(distribution: str) -> str:
    """The installed version, at runtime (§7.3 rule 5, T123).

    Never a literal in an adapter's source. A version somebody typed is a version that was
    true once.
    """
    try:
        return installed_version(distribution)
    except PackageNotFoundError:
        return ""


def is_installed(distribution: str) -> bool:
    return bool(read_version(distribution))


def tool_endpoint(url: str) -> str:
    return f"{url.rstrip('/')}/tools/issue_refund"


def approve_endpoint(url: str) -> str:
    return f"{url.rstrip('/')}/approve"

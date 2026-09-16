# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""`docs/ARCHITECTURE.md` §6's rule, as a test rather than as a sentence.

§6 says **dependencies point downward only**, and from v0.7 to v0.11 that was false: a review
found the cycle `state -> receipt -> policy -> authority -> state`, §6 was amended to record it,
and it stayed for five milestones. Nothing was broken at run time, because the two edges out of
`policy.py` are function-level and run after every module is loaded, so `import ctrlrun` resolved
in one order and every test passed.

**That is the whole reason this file exists.** The rule had no guard, so the only thing that could
contradict it was a human reading the imports, and the one who did had to write a paragraph
instead of a failing test. A claim about the module map needs a test as much as a claim about
behaviour, which is the rule `SPEC-v0.11.md` §13.3 states for claims about what a feature does
*not* do.

**Module-level imports only, deliberately.** A function-level import is a real edge for layering
and *not* an edge for import order, and conflating them is what let the cycle read as harmless.
This walks the AST, so it sees what a reader of the file sees, without importing anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "ctrlrun"


def _module_name(path: Path) -> str:
    """`src/ctrlrun/gateway/server.py` -> `gateway.server`, and `__init__.py` -> its package."""
    relative = path.relative_to(PACKAGE).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _edges(*, deferred: bool = False) -> dict[str, set[str]]:
    """Every module-level intra-package import, as a graph.

    **Resolving `level` correctly is the whole of this function, and getting it wrong invents
    cycles.** A first version treated `from .. import transport` inside `gateway/outcome.py` as
    `gateway.transport` instead of the top-level `transport`, and reported
    `gateway.outcome -> gateway.transport -> gateway.outcome`, a cycle that does not exist. A
    detector that invents edges is worse than none, because the fix for a phantom cycle is a
    refactor nobody needed.

    `level` counts the dots. Level 1 is the module's own package; each dot above that drops one
    component. `from . import x` and `from .. import x` carry no `module`, so the imported name
    is itself the submodule.
    """
    graph: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        name = _module_name(path)
        parts = name.split(".")[:-1] if "." in name else []
        targets: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # `deferred=False` is the import-order graph: what runs at `import ctrlrun`.
        # `deferred=True` adds function-level imports, which is the **layering** graph -- a
        # deferred import is still one module knowing about another, which is what §6's map says.
        for node in ast.walk(tree) if deferred else tree.body:
            if isinstance(node, ast.ImportFrom) and node.level:
                climb = node.level - 1
                if climb > len(parts):
                    continue  # reaches above the package; not an intra-package edge
                base = parts[: len(parts) - climb]
                if node.module:
                    targets.add(".".join([*base, node.module]))
                else:
                    # `from . import x` / `from .. import x`: each name is a submodule.
                    targets.update(".".join([*base, alias.name]) for alias in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("ctrlrun."):
                        targets.add(alias.name.removeprefix("ctrlrun."))
        graph[name] = {t for t in targets if t and t != name}
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every elementary cycle, by depth-first search, reported as the path that closes it."""
    found: list[list[str]] = []
    seen: set[str] = set()

    def walk(node: str, path: list[str], on_path: set[str]) -> None:
        for target in sorted(graph.get(node, ())):
            if target in on_path:
                found.append([*path[path.index(target) :], target])
            elif target not in seen:
                walk(target, [*path, target], on_path | {target})

    for start in sorted(graph):
        if start not in seen:
            walk(start, [start], {start})
        seen.add(start)
    return found


def test_the_import_order_graph_has_no_cycle() -> None:
    """No cycle among module-level imports: what actually runs at `import ctrlrun`.

    **This is not the test that catches §6's recorded cycle, and saying so matters.** Run against
    the tree as 0.11.0 shipped it, this passes, because the two edges out of `policy.py` are
    function-level. That is the same fact §6 states, and it is why a reviewer had to find the
    cycle by reading. The layering test below is the one that fails there.
    """
    cycles = _cycles(_edges())

    assert cycles == [], "module-level import cycle: " + (
        "; ".join(" -> ".join(cycle) for cycle in cycles)
    )


#: **Empty, and it is meant to stay empty.**
#:
#: It held `policy <-> authority` for one commit. `authority.py` imports the condition evaluator
#: from `policy.py` deliberately, because SPEC-v0.3 §4.5 requires the two axes to share one: a
#: second evaluator would be a second place for `True` to start comparing equal to `1`. The
#: reverse edge is `policy.py` reaching `authority.py` from `_canonical_authority` and
#: `hash_with_authority`, and neither direction could be removed on its own.
#:
#: Moving the **shared** half down to `grammar.py` removed the cycle without touching either.
#: §4.5's requirement is better served than before, because the one evaluator is now owned by
#: neither axis.
#:
#: A new entry here is a decision, not a fix. Add one only with the reason it cannot be
#: relocated, the way that one carried its reason while it stood.
RECORDED_LAYERING_CYCLES: frozenset[frozenset[str]] = frozenset()


def test_the_layering_graph_has_only_the_one_recorded_cycle() -> None:
    """§6's actual rule, including deferred imports, and there is no exception left.

    A function-level import is a real edge for the module map and not one for import order, and
    conflating them is exactly what let `state -> receipt -> policy -> authority -> state` read as
    harmless for five milestones. This is the assertion that fails on 0.11.0's tree.
    """
    found = {frozenset(cycle) for cycle in _cycles(_edges(deferred=True))}

    assert found == RECORDED_LAYERING_CYCLES, (
        "ARCHITECTURE §6 says dependencies point downward only. New: "
        f"{sorted(map(sorted, found - RECORDED_LAYERING_CYCLES))}. "
        f"Gone, so remove it from RECORDED_LAYERING_CYCLES: "
        f"{sorted(map(sorted, RECORDED_LAYERING_CYCLES - found))}"
    )


def test_the_decision_vocabulary_depends_on_nothing_in_the_package() -> None:
    """`decision.py` is the floor the cycle was broken against, and a floor that grows an
    intra-package import is not a floor. `errors.py` holds the same position and is checked
    with it, since it is what `decision.py` would reach for first."""
    graph = _edges()

    assert graph["decision"] == set(), f"decision.py imports {sorted(graph['decision'])}"
    assert graph["errors"] == set(), f"errors.py imports {sorted(graph['errors'])}"


def test_a_receipt_does_not_import_the_decider() -> None:
    """The specific edge that closed the cycle, pinned by name.

    The graph test above fails on *any* cycle, which is the guard that matters. This one names
    the edge that was there, so a change reintroducing it fails with the reason rather than with
    a path a reader has to re-derive.
    """
    graph = _edges()

    assert "policy" not in graph["receipt"], (
        "receipt.py imports policy.py again; that edge is what made "
        "state -> receipt -> policy -> authority -> state"
    )


@pytest.mark.parametrize("cycle", [("a", "b"), ("a", "b", "c")])
def test_the_cycle_detector_finds_a_cycle_it_is_given(cycle: tuple[str, ...]) -> None:
    """The positive control. A detector that returns `[]` on everything passes the test above
    on any codebase, which is the shape of green this project keeps refusing.
    """
    graph = {node: {cycle[(index + 1) % len(cycle)]} for index, node in enumerate(cycle)}

    assert _cycles(graph), f"the detector missed {' -> '.join(cycle)}"


def test_the_cycle_detector_passes_a_graph_that_is_a_dag() -> None:
    """The other half of the control: it must not report a cycle on a diamond, where two paths
    reach one module without any edge pointing back."""
    assert _cycles({"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}) == []

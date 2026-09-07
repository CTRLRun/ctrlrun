"""`ctrlrun scan` — what a project is not covering. `docs/SPEC-scan.md`.

Scan reads text. It reports the consequential call sites and policy entries CTRLRun is not
covering, so that an operator can see the gap between *installed* and *in the path*. It is a
finder and not a proof: §4 of the specification enumerates what it misses by construction, and
`report_lines` prints that on every run, including the run with no findings.

Three rules shape everything here.

**It never imports the tree it reads** (§2.1). `ast.parse` on the file's bytes and nothing
else. Importing a module executes it at module scope, and a tool whose purpose is to find calls
that move money must not be the thing that makes one.

**It adds no entry point** (§9.2). No `Action` is built, no `Principal` resolved, no `Control`
constructed and no store opened. The tempting version of this tool asks the policy what would
happen to each call site it finds, and a principal invented by a tool from a source file is
`--principal-from-client-info` in a fourth costume.

**Where it does not know, it says so** (§7). A file that will not parse and a call with no
resolvable name are counted and printed, never skipped: the file scan cannot read is exactly
where something would hide.

This module sits above `control.py`, beside `verify/` and `cli/`. Nothing in the kernel imports
it and `import ctrlrun` must not reach it (T206).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "SCAN_SCHEMA",
    "VOCABULARY",
    "Finding",
    "FindingKind",
    "ScanError",
    "ScanReport",
    "Suppression",
    "UndeterminedCall",
    "report_document",
    "report_lines",
    "scan",
]

#: The document `report_document` produces. Nothing consumes it inside this package.
SCAN_SCHEMA = "ctrlrun.scan/v1"

#: The consequence vocabulary (§3.2). The first eleven are the README's own sentence, which is
#: the list this project already stands behind; the rest are the verbs a reader of the sector
#: templates would expect beside them. It will be wrong in both directions — `SPEC-scan.md` §11
#: says so — and `--vocabulary` replaces it.
#:
#: `execute` was in the first draft and is not here: `cursor.execute` appears in every project
#: that touches a database, and a verb that matches thousands of lines buries the ones that
#: matter. Measured against this repository's own `src/`, it was 90 of 208 findings.
VOCABULARY: tuple[str, ...] = (
    "approve",
    "cancel",
    "charge",
    "delete",
    "deploy",
    "destroy",
    "disable",
    "drop",
    "grant",
    "issue",
    "merge",
    "pay",
    "payout",
    "publish",
    "purchase",
    "push",
    "refund",
    "remove",
    "revoke",
    "rotate",
    "send",
    "submit",
    "terminate",
    "transfer",
    "truncate",
)

#: Directory names never scanned. Not a judgement about their contents: a finding an operator
#: cannot act on without forking a library is noise, and the honest answer for a dependency is
#: the gateway (§10).
DEFAULT_EXCLUDED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)

#: `# ctrlrun: not-consequential — a reason`. The reason is required (§3.6): a suppression with
#: none does not apply, and the finding stands.
_SUPPRESSION = re.compile(r"#\s*ctrlrun:\s*not-consequential\s*(?:[—-]\s*(?P<reason>\S.*?))?\s*$")

#: §4.5, verbatim. A run that printed `0 unprotected` and nothing else would be the false-green
#: problem in a new costume: a reassuring line, produced by a check that never looked.
_LIMITS = (
    "This is a finder, not a proof. It read {files} files and could not determine {undetermined} "
    "calls; it did not read your dependencies, your other services, or any tool server behind a "
    "gateway. A clean scan means nothing was found where it looked."
)


class ScanError(Exception):
    """The scan could not run at all: exit 2 (§6). Not raised for anything it merely found."""


class FindingKind(StrEnum):
    """What a finding is. An undetermined call is not one of these — see `UndeterminedCall`."""

    UNPROTECTED_CALL = "unprotected_call"
    PROTECTED_ACTION_NOT_IN_POLICY = "protected_action_not_in_policy"
    ACTION_WITHOUT_EFFECT = "action_without_effect"
    UNPARSEABLE = "unparseable"


#: Most severe first (§5.1).
_ORDER: tuple[FindingKind, ...] = (
    FindingKind.UNPROTECTED_CALL,
    FindingKind.PROTECTED_ACTION_NOT_IN_POLICY,
    FindingKind.ACTION_WITHOUT_EFFECT,
    FindingKind.UNPARSEABLE,
)


@dataclass(frozen=True)
class Finding:
    """One thing scan found, and the rule that found it, so a reader can disagree with it."""

    kind: FindingKind
    file: str
    line: int
    rule: str
    target: str | None = None
    name: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Suppression:
    """A finding an operator annotated. It stays in the report, with the reason (§3.6)."""

    file: str
    line: int
    target: str
    reason: str


@dataclass(frozen=True)
class UndeterminedCall:
    """A call with no resolvable name (§4.1). Not a finding: scan does not know what it calls."""

    file: str
    line: int
    expression: str


@dataclass(frozen=True)
class ScanReport:
    """One document, rendered two ways by `report_lines` and `report_document` (§5.3)."""

    root: str
    findings: tuple[Finding, ...]
    suppressions: tuple[Suppression, ...]
    undetermined: tuple[UndeterminedCall, ...]
    files_read: int
    files_excluded: int
    vocabulary: tuple[str, ...]
    policy_path: str | None
    policy_read: bool

    @property
    def exit_code(self) -> int:
        """0 nothing, 1 something — including a suppression or an undetermined call (§6)."""
        if self.findings or self.suppressions or self.undetermined:
            return 1
        return 0

    def of(self, kind: FindingKind) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.kind is kind)


# --- the vocabulary ---------------------------------------------------------------------


def matched_verb(target: str, vocabulary: Sequence[str]) -> str | None:
    """The verb this dotted call path matches, or `None` (§3.2).

    Two shapes, because one rule cannot separate the two things a plural means. A **whole
    segment** matches a verb or its plural: `stripe.refunds.create` is a resource namespace and
    a refund is what it creates. A **word inside a compound segment** matches the verb only in
    the singular: `create_refund` is a refund, and `refunds_report` is a noun phrase about
    refunds and does not move any money. Splitting them was the difference between reporting
    the Stripe SDK's own call shape and reporting every reporting function beside it.
    """
    verbs = frozenset(verb.lower() for verb in vocabulary)
    for segment in target.split("."):
        lowered = segment.lower()
        if lowered in verbs or (lowered.endswith("s") and lowered[:-1] in verbs):
            return lowered.removesuffix("s") if lowered not in verbs else lowered
        if "_" in lowered:
            for word in lowered.split("_"):
                if word in verbs:
                    return word
    return None


def dotted_name(node: ast.expr) -> str | None:
    """`stripe.refunds.create` for an attribute chain over a name, else `None` (§3.1)."""
    target, resolved = call_target(node)
    return target if resolved else None


def call_target(node: ast.expr) -> tuple[str | None, bool]:
    """`(name, is the base resolved)` for a call's function expression.

    A chain bottoming out at a name is fully resolved. A chain over something else --
    `hashlib.sha256(text).hexdigest()`, `"\n".join(parts)` -- yields the trailing attributes
    and `False`: the method being called is known even though what it is called on is not, and
    a `.delete()` on an expression is still a delete.

    Only a call with no attribute at all is undetermined (§4.1) -- `getattr(client, verb)()`,
    `handlers[key]()`. Measured against this repository, treating every unresolved base as
    undetermined produced 216 entries, of which two were the dynamic dispatch the category
    exists for. A list that long is a list nobody reads.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts)), True
    if parts:
        return ".".join(reversed(parts)), False
    return None, False


# --- one file ---------------------------------------------------------------------------


def _is_protect(decorator: ast.expr) -> bool:
    """`protect(...)`, `ctrlrun.protect(...)`, or either without the call (§3.3)."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = dotted_name(target)
    return name is not None and name.split(".")[-1] == "protect"


def _protected_action(decorator: ast.expr) -> tuple[str | None, bool]:
    """`(the action name, it declares an effect)` for a `@protect("name", effect=…)`.

    The name is `None` when it is not a string literal (§7). The second half is why this
    returns a pair: an action with no `effect:` in the policy is a write with no effect key
    **for the gateway**, and not for a decorator that passes `effect=` itself. Reporting the
    policy alone flagged `examples/double-refund`, whose decorator supplies the template --
    a finding that would have taught a reader to add a key they already had.
    """
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return None, False
    first = decorator.args[0]
    declares = any(word.arg == "effect" for word in decorator.keywords)
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value, declares
    return None, declares


_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _covers(node: _Function) -> bool:
    return any(_is_protect(decorator) for decorator in node.decorator_list)


def _calls(node: ast.AST, inside_covered: bool, out: list[tuple[ast.Call, bool]]) -> None:
    """Every call in the tree, each with whether a covered function lexically encloses it."""
    for child in ast.iter_child_nodes(node):
        covered = inside_covered
        if isinstance(child, _Function) and _covers(child):
            covered = True
        if isinstance(child, ast.Call):
            out.append((child, inside_covered))
        _calls(child, covered, out)


def _suppression(line: str) -> tuple[bool, str | None]:
    """`(annotated, reason)`. Annotated with no reason is not a suppression (§3.6)."""
    found = _SUPPRESSION.search(line)
    if found is None:
        return False, None
    return True, found.group("reason")


# --- the scan ----------------------------------------------------------------------------


def _is_excluded(relative: str, exclude: Sequence[str]) -> bool:
    """Whether a pattern excludes this file, matching an ancestor directory as well as the file.

    `--exclude` is documented as *"a glob, relative to the tree, not to read"*, and
    `PurePath.match` compares components from the right -- so `Path("vendor/x.py")` does not
    match `"vendor"`, and `--exclude vendor` silently read every file under it. The built-in
    list one line above already excludes by *directory name*, so the flag an operator types
    behaved differently from the list they cannot change.

    Matching each ancestor as well as the path itself makes `vendor` exclude its whole subtree,
    keeps `gen/*.py` meaning what it meant, and still refuses `ven` for `vendor` -- a glob, not
    a prefix.
    """
    candidate = Path(relative)
    for pattern in exclude:
        if candidate.match(pattern):
            return True
        if any(parent.match(pattern) for parent in candidate.parents if parent != Path(".")):
            return True
    return False


def _python_files(root: Path, exclude: Sequence[str]) -> tuple[list[Path], int]:
    chosen: list[Path] = []
    excluded = 0
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if set(path.relative_to(root).parts) & DEFAULT_EXCLUDED_DIRECTORIES:
            excluded += 1
            continue
        if _is_excluded(relative, exclude):
            excluded += 1
            continue
        chosen.append(path)
    return chosen, excluded


def _scan_file(
    path: Path,
    relative: str,
    vocabulary: Sequence[str],
) -> tuple[
    list[Finding],
    list[Suppression],
    list[UndeterminedCall],
    list[tuple[str | None, bool, int]],
]:
    findings: list[Finding] = []
    suppressions: list[Suppression] = []
    undetermined: list[UndeterminedCall] = []
    protected: list[tuple[str | None, bool, int]] = []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as unreadable:
        # Not a skip. A file scan cannot read is exactly where something would hide (§7).
        findings.append(
            Finding(
                kind=FindingKind.UNPARSEABLE,
                file=relative,
                line=1,
                rule="unreadable",
                detail=str(unreadable),
            )
        )
        return findings, suppressions, undetermined, protected

    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as broken:
        findings.append(
            Finding(
                kind=FindingKind.UNPARSEABLE,
                file=relative,
                line=broken.lineno or 1,
                rule="syntax-error",
                detail=broken.msg,
            )
        )
        return findings, suppressions, undetermined, protected

    lines = source.splitlines()
    covered_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, _Function) and _covers(node):
            covered_names.add(node.name)
            for decorator in node.decorator_list:
                if _is_protect(decorator):
                    declared, has_effect = _protected_action(decorator)
                    protected.append((declared, has_effect, decorator.lineno))

    found: list[tuple[ast.Call, bool]] = []
    _calls(tree, False, found)
    for call, inside_covered in found:
        target, resolved = call_target(call.func)
        if target is None:
            undetermined.append(
                UndeterminedCall(
                    file=relative,
                    line=call.lineno,
                    expression=ast.unparse(call.func),
                )
            )
            continue
        if inside_covered or target in covered_names:
            continue
        verb = matched_verb(target, vocabulary)
        if verb is None:
            continue
        physical = lines[call.lineno - 1] if 0 < call.lineno <= len(lines) else ""
        annotated, reason = _suppression(physical)
        if annotated and reason:
            suppressions.append(
                Suppression(file=relative, line=call.lineno, target=target, reason=reason)
            )
            continue
        findings.append(
            Finding(
                kind=FindingKind.UNPROTECTED_CALL,
                file=relative,
                line=call.lineno,
                rule=f"vocabulary:{verb}",
                target=target,
                detail=None if resolved else "called on an expression, not a name",
            )
        )
    return findings, suppressions, undetermined, protected


def _policy_actions(path: Path) -> dict[str, bool]:
    """`{action name: it can act}` — `allow` or `approve`, at the top level or in any rule.

    The document is loaded by the real loader first (§2.2), so a policy that does not load is
    an error rather than a source scan quietly skipped.
    """
    import yaml

    from .errors import PolicyError
    from .policy import Policy

    try:
        Policy.from_file(path)
    except PolicyError as refused:
        raise ScanError(f"{path}: {refused}") from refused

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    actions = (document or {}).get("actions") or {}
    if not isinstance(actions, dict):
        raise ScanError(f"{path}: `actions:` is not a mapping")

    def acts(body: object) -> bool:
        if not isinstance(body, dict):
            return False
        decisions = [body.get("decision")]
        for rule in body.get("rules") or []:
            if isinstance(rule, dict):
                decisions.append(rule.get("decision"))
        return any(decision in ("allow", "approve") for decision in decisions)

    return {name: acts(body) for name, body in actions.items()}


def scan(
    path: Path | str | None = None,
    policy: Path | str | None = None,
    exclude: Iterable[str] = (),
    vocabulary: Sequence[str] | None = None,
) -> ScanReport:
    """Read a tree and a policy and report what is not covered. The one producer (§5.3)."""
    root = Path(path or Path.cwd()).resolve()
    if not root.is_dir():
        raise ScanError(f"{root} is not a directory")
    words = tuple(vocabulary) if vocabulary is not None else VOCABULARY
    patterns = tuple(exclude)

    files, excluded = _python_files(root, patterns)
    if not files:
        raise ScanError(f"no Python file was found under {root}")

    findings: list[Finding] = []
    suppressions: list[Suppression] = []
    undetermined: list[UndeterminedCall] = []
    protected: list[tuple[str | None, bool, int, str]] = []

    for file in files:
        relative = file.relative_to(root).as_posix()
        one, quiet, unknown, names = _scan_file(file, relative, words)
        findings.extend(one)
        suppressions.extend(quiet)
        undetermined.extend(unknown)
        protected.extend((name, has_effect, line, relative) for name, has_effect, line in names)

    policy_path: Path | None = None
    if policy is not None:
        policy_path = Path(policy)
        if not policy_path.is_file():
            raise ScanError(f"{policy_path} does not exist")
    elif (root / "ctrlrun.yaml").is_file():
        policy_path = root / "ctrlrun.yaml"

    if policy_path is not None:
        actions = _policy_actions(policy_path)
        from .policy import Policy

        loaded = Policy.from_file(policy_path)
        decorated_with_effect = {
            name for name, has_effect, _, _ in protected if name is not None and has_effect
        }
        for name, can_act in sorted(actions.items()):
            # `can_act` is "the policy permits this", which every allowed action satisfies --
            # so asking it alone flagged every read. The question this rule means is "does
            # this action have a consequence to reserve?", and that is `matched_verb` against
            # the same consequence vocabulary the call-site rule already uses. `ctrlrun init`
            # writes `customer.read` and `invoice.read` under the comment *"Reads: autonomous.
            # Declare no effect on these; nothing to reserve"*, and `ctrlrun scan` failed the
            # policy `ctrlrun init` had just written, exit 1, on exactly those two.
            if (
                can_act
                and matched_verb(name, words) is not None
                and loaded.effect_template(name) is None
                and name not in decorated_with_effect
            ):
                findings.append(
                    Finding(
                        kind=FindingKind.ACTION_WITHOUT_EFFECT,
                        file=policy_path.name,
                        line=1,
                        rule="no-effect-template",
                        name=name,
                    )
                )
        for declared, _, at, relative in protected:
            if declared is not None and declared not in actions:
                findings.append(
                    Finding(
                        kind=FindingKind.PROTECTED_ACTION_NOT_IN_POLICY,
                        file=relative,
                        line=at,
                        rule="not-in-policy",
                        name=declared,
                    )
                )

    findings.sort(key=lambda finding: (_ORDER.index(finding.kind), finding.file, finding.line))
    return ScanReport(
        root=str(root),
        findings=tuple(findings),
        suppressions=tuple(suppressions),
        undetermined=tuple(undetermined),
        files_read=len(files),
        files_excluded=excluded,
        vocabulary=words,
        policy_path=str(policy_path) if policy_path else None,
        policy_read=policy_path is not None,
    )


# --- the two renderings ------------------------------------------------------------------


def _limits(report: ScanReport) -> str:
    return _LIMITS.format(files=report.files_read, undetermined=len(report.undetermined))


def _finding_line(finding: Finding) -> str:
    subject = finding.target or finding.name or finding.file
    where = f"{finding.file}:{finding.line}"
    detail = f"  ({finding.detail})" if finding.detail else ""
    return f"  {where}  {subject}  [{finding.kind}: {finding.rule}]{detail}"


def report_lines(report: ScanReport) -> list[str]:
    """The human rendering. Every finding in the document has a line here (§5.3, T204)."""
    lines = [f"ctrlrun scan — {report.root}", ""]
    for kind in _ORDER:
        found = report.of(kind)
        if not found:
            continue
        lines.append(f"{kind} ({len(found)})")
        lines.extend(_finding_line(finding) for finding in found)
        lines.append("")
    if report.undetermined:
        lines.append(f"undetermined ({len(report.undetermined)})")
        lines.extend(
            f"  {call.file}:{call.line}  {call.expression}  [no resolvable name]"
            for call in report.undetermined
        )
        lines.append("")
    if report.suppressions:
        lines.append(f"suppressed ({len(report.suppressions)})")
        lines.extend(
            f"  {one.file}:{one.line}  {one.target}  — {one.reason}" for one in report.suppressions
        )
        lines.append("")
    lines.append(
        f"files read: {report.files_read}   excluded: {report.files_excluded}   "
        f"findings: {len(report.findings)}   suppressed: {len(report.suppressions)}   "
        f"undetermined: {len(report.undetermined)}"
    )
    lines.append(
        f"vocabulary: {len(report.vocabulary)} verbs   "
        f"policy: {report.policy_path or 'not read — the two policy checks did not run'}"
    )
    lines.append("")
    statement = _limits(report)
    head, _, tail = statement.partition("; ")
    lines.append(head + ";")
    lines.append(tail)
    return lines


def report_document(report: ScanReport) -> dict[str, Any]:
    """The JSON rendering. Same document, same producer (§5.2, §5.3)."""
    return {
        "schema": SCAN_SCHEMA,
        "root": report.root,
        "findings": [
            {
                "kind": str(finding.kind),
                "file": finding.file,
                "line": finding.line,
                "rule": finding.rule,
                "target": finding.target,
                "name": finding.name,
                "detail": finding.detail,
            }
            for finding in report.findings
        ],
        "suppressed": [
            {"file": one.file, "line": one.line, "target": one.target, "reason": one.reason}
            for one in report.suppressions
        ],
        "undetermined": [
            {"file": call.file, "line": call.line, "expression": call.expression}
            for call in report.undetermined
        ],
        "totals": {
            "files_read": report.files_read,
            "files_excluded": report.files_excluded,
            "findings": len(report.findings),
            "suppressed": len(report.suppressions),
            "undetermined": len(report.undetermined),
        },
        "vocabulary": list(report.vocabulary),
        "policy": report.policy_path,
        "limits": {
            "statement": _limits(report),
            "not_read": [
                "dependencies",
                "other services",
                "any tool server behind a gateway",
                "anything that is not a .py file under the scanned path",
            ],
        },
        "exit_code": report.exit_code,
    }

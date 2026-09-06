"""The forbidden-words lint.

Two lists, because the rules that produced them are different rules.

**Positioning words** — *runtime control*, *governance*, *guardrails*, *compliant*, *secure* as a
bare adjective, *exactly-once*, *transaction* — never appear where a stranger forms a first
impression: a heading, a page title, a description, a hero line. They are allowed in a body
sentence that explains what CTRLRun is not, or names the thing it is being compared with. So
these are checked in **headline scope**: Markdown headings and the frontmatter fields Mintlify
renders as the page's title, description and social preview.

**Claim words** — *compliance*, *conformant*, *certified*, *aligned with*, *pack*, *sector*, the
named regulations, and social proof that does not exist — are checked **everywhere**, because a
body sentence is where a compliance claim or a sector product gets asserted. Two documents are
exempt by name, because they exist to list what is *not* covered: `docs/OWASP-AGENTIC-TOP10.md`
and `docs/THREAT_MODEL.md`. Everything else negates such a word through the allowlist beside
this file, one regex per legitimate sentence, with the reason.

`lint-allowlist.txt` also names the files the lint does not read, with a reason on each line.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from _files import DOCUMENT_PATTERNS, documents, outside_fences, relative

ALLOWLIST = Path(__file__).with_name("lint-allowlist.txt")

#: The two documents the rules exempt by name. Not in the allowlist file, because an entry
#: there could be deleted by a session that found it inconvenient; these are the rule.
EXEMPT_BY_RULE: tuple[str, ...] = ("docs/OWASP-AGENTIC-TOP10.md", "docs/THREAT_MODEL.md")


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    scope: str  # "headline" or "everywhere"
    why: str


def _rule(id: str, pattern: str, scope: str, why: str) -> Rule:
    return Rule(id, re.compile(pattern, re.IGNORECASE), scope, why)


RULES: tuple[Rule, ...] = (
    _rule("runtime-control", r"\bruntime[ -]control\b", "headline", "a competitor's phrase"),
    _rule("governance", r"\bgovernance\b", "headline", "a competitor's phrase"),
    _rule("guardrails", r"\bguard-?rails?\b", "headline", "a competitor's phrase"),
    _rule("compliant", r"\bcompliant\b", "headline", "a claim this project does not make"),
    _rule(
        "secure",
        r"\bsecure\b(?!\s+(?:by|against|from))",
        "headline",
        "a bare adjective that promises what verify cannot see",
    ),
    _rule("exactly-once", r"\bexactly[ -]once\b", "headline", "what CTRLRun cannot guarantee"),
    _rule(
        "transaction",
        r"\btransactions?\b",
        "headline",
        "allowed once, in the sentence that says CTRLRun is not one",
    ),
    _rule("compliance", r"\bcompliance\b", "everywhere", "no compliance claims"),
    _rule("conformant", r"\bconformant\b", "everywhere", "no standards claims"),
    _rule("certified", r"\bcertif(?:ied|ication)\b", "everywhere", "no standards claims"),
    _rule("aligned-with", r"\baligned with\b", "everywhere", "no standards claims"),
    _rule("pack", r"\bpacks?\b", "everywhere", "0.6 ships no sector packs"),
    _rule("sector", r"\bsectors?\b", "everywhere", "0.6 ships no sector packs"),
    _rule("hipaa", r"\bHIPAA\b", "everywhere", "no regulation is supported"),
    _rule("soc2", r"\bSOC\s?2\b", "everywhere", "no regulation is supported"),
    _rule("eu-ai-act", r"\bEU AI Act\b", "everywhere", "no regulation is supported"),
    _rule("trusted-by", r"\btrusted by\b", "everywhere", "no social proof that does not exist"),
    _rule("testimonial", r"\btestimonials?\b", "everywhere", "no social proof"),
    _rule("excited", r"\bwe(?:'re| are) excited\b", "everywhere", "docs/STYLE.md"),
)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_FRONTMATTER_HEADLINE = re.compile(
    r"""^\s*"?(?:title|sidebarTitle|description|og:title|og:description|twitter:title|twitter:description)"?\s*:"""
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: Rule
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule.id}] {self.text.strip()[:100]}"


@dataclass(frozen=True)
class Allowlist:
    excluded: tuple[str, ...]
    allowed: tuple[tuple[str, re.Pattern[str]], ...]

    def excludes(self, path: str) -> bool:
        return any(fnmatch(path, pattern) for pattern in self.excluded)

    def permits(self, path: str, line: str) -> bool:
        return any(fnmatch(path, glob) and pattern.search(line) for glob, pattern in self.allowed)


def load_allowlist(path: Path = ALLOWLIST) -> Allowlist:
    """`exclude <glob>` and `allow <glob> <regex>` lines; `#` comments; blank lines ignored.

    An `allow` line's regex is everything after the glob, so it may contain spaces.
    """
    excluded: list[str] = []
    allowed: list[tuple[str, re.Pattern[str]]] = []
    if not path.exists():
        return Allowlist((), ())
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("  #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        keyword, _, rest = line.partition(" ")
        if keyword == "exclude":
            excluded.append(rest.strip())
        elif keyword == "allow":
            glob, _, regex = rest.strip().partition(" ")
            if not regex:
                raise ValueError(f"{path}:{number}: `allow` needs a glob and a regex")
            allowed.append((glob, re.compile(regex.strip(), re.IGNORECASE)))
        else:
            raise ValueError(f"{path}:{number}: unknown keyword {keyword!r}")
    return Allowlist(tuple(excluded), tuple(allowed))


def lint_text(text: str, path: str, allowlist: Allowlist) -> list[Finding]:
    """Every finding in one document.

    An `allow` regex is matched against the line **and its two neighbours**, because prose is
    hard-wrapped and a negation like *does not mean secure, safe, compliant, certified or
    audited* breaks across lines wherever the wrap happens to fall. The rules themselves stay
    line-scoped, so widening the allow window can only permit, never miss.
    """
    findings: list[Finding] = []
    lines = list(outside_fences(text))
    in_frontmatter = False
    for position, (number, line) in enumerate(lines):
        window = " ".join(text for _, text in lines[max(position - 1, 0) : position + 2])
        if number == 1 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and line.strip() == "---":
            in_frontmatter = False
            continue
        headline = bool(_HEADING.match(line)) or (
            in_frontmatter and bool(_FRONTMATTER_HEADLINE.match(line))
        )
        for rule in RULES:
            if rule.scope == "headline" and not headline:
                continue
            if not rule.pattern.search(line):
                continue
            if allowlist.permits(path, window):
                continue
            findings.append(Finding(path, number, rule, line))
    return findings


def lint_paths(paths: Iterable[Path], allowlist: Allowlist | None = None) -> list[Finding]:
    allowlist = allowlist if allowlist is not None else load_allowlist()
    findings: list[Finding] = []
    for path in paths:
        name = relative(path)
        if name in EXEMPT_BY_RULE or allowlist.excludes(name):
            continue
        findings.extend(lint_text(path.read_text(encoding="utf-8"), name, allowlist))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="documents to lint; default: all")
    arguments = parser.parse_args(argv)
    paths = [p.resolve() for p in arguments.paths] or documents(patterns=DOCUMENT_PATTERNS)
    findings = lint_paths(paths)
    for finding in findings:
        print(finding)
    print(f"lint: {len(paths)} document(s), {len(findings)} finding(s)")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main())

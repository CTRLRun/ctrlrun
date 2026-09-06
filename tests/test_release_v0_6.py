"""The v0.6 release pass. Item 9; SPEC-v0.6 §8's T180 and T181.

Two claims about the *documents*, made testable because they are the two the release is most
likely to get wrong quietly.

**T180 — alteration is not authorship.** §1.2's third rule, and §6.4's whole argument: a hash
chain says the log was not altered after the fact and says nothing about who wrote it. A README
that blurs the two would be the false-green problem in prose, and prose has no CI of its own.

**T181 — core still installs nothing new.** v0.6 adds `ctrlrun[postgres]`, and an extra is only
an extra while `pip install ctrlrun` does not pull it in.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: SPEC-v0.6 §8's T180. Word boundaries, so `design`, `assign`, `assignment` and `designated`
#: are not hits -- a substring list would flag those, be softened, and stop failing on the thing
#: it exists for.
FORBIDDEN = re.compile(
    r"\b(sign|signs|signed|signing|signature|signatures|authorship|"
    r"non-repudiation|tamper-proof)\b",
    re.IGNORECASE,
)

#: The documents the rule binds. `docs/SPEC-v0.6.md` is deliberately **not** here: it is the
#: place the distinction is argued at length, and scanning it would make the allow-list a copy
#: of §6.
SCANNED = (
    "README.md",
    "CHANGELOG.md",
    "docs/postgres.md",
    "docs/THREAT_MODEL.md",
)


def _lines(name: str) -> list[str]:
    """One scanned document's lines.

    A **missing** file is a failure and not a skip: `docs/postgres.md` is required by §8 and by
    this milestone's definition of done, and skipping on its absence would make the scan
    disappear exactly when somebody deleted the document it covers. The only skip admitted is
    the whole repository being absent, which is the sdist job running this suite from inside a
    distribution that carries `docs/` but not `pyproject.toml`.
    """
    if not (REPO_ROOT / "pyproject.toml").exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    path = REPO_ROOT / name
    assert path.exists(), f"{name} is required by SPEC-v0.6 §8 and is not here"
    return path.read_text(encoding="utf-8").splitlines()


def _load(name: str) -> set[str]:
    """The allow-listed lines of one file, stripped, as a set."""
    return {line.strip() for line in ALLOWED.get(name, ())}


#: **Every line below is allow-listed because somebody wrote it down here on purpose.**
#:
#: §8's T180 describes the list as sentences that *disclaim* one of the words, and most of these
#: are. Some are not, and saying so is the point of splitting the list rather than pretending:
#: a historical changelog entry about JWT signature verification, an HMAC on a webhook, and the
#: Python word for a function's parameters are all `\bsignature\b` and none of them is a claim
#: about receipts. `docs/SPEC-v0.6.md` §8's T180 is amended in this item's PR to say so, on the
#: rule that a contract defect found while implementing it is fixed in the contract.
#:
#: What the list is *for* is unchanged and is the whole design: it fails on a **new** occurrence,
#: which is the event worth failing on, and forces whoever adds one to come here and say what
#: they meant. A plain forbidden-word list would flag §6.4's own "Not authorship", be removed as
#: a false positive, and be gone.
#: Sentences that **disclaim** one of the words. §8's T180 describes the whole
#: list this way, and this half of it is.
DISCLAIMS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "- The receipt chain detects alteration, and alteration is not authorship. Receipts are not",  # noqa: E501
        "signed, the chain is no evidence of who wrote one, and it is not tamper-proof: it does not",  # noqa: E501
    ),
    "CHANGELOG.md": (
        '- **`docs/ROADMAP.md`\'s v0.6 bullet said "receipt integrity (hash chain / signatures)", and the',  # noqa: E501
        "slash was the problem.** A chain detects **alteration**; a signature proves **origin**, and",  # noqa: E501
        "this project verifies what it is handed. Signing is out of scope for v0.6 (`SPEC-v0.6.md` §11).",  # noqa: E501
        "- **`docs/THREAT_MODEL.md`'s \"Receipts are not signed; a database admin can alter history",  # noqa: E501
        "which half it does not: **truncation at the end**, authorship, an adversary who can rewrite",  # noqa: E501
        '- Receipts are not signed. A database administrator can alter history. **This line read "(v0.6)" until v0.6 was built, and that was a promise v0.6 does not keep**: v0.6 adds a hash chain, which detects alteration and is not evidence of authorship, and it does not stop an administrator who can rewrite every row including the chain head. Signing is out of scope (`SPEC-v0.6.md` §11).',  # noqa: E501
    ),
    "docs/postgres.md": (
        "Receipts are not signed, alteration is not authorship, and the chain is not tamper-proof",
    ),
    "docs/THREAT_MODEL.md": (
        "- Receipts are not signed, and they are not signed after v0.6 either. v0.6 adds a **hash chain** (`SPEC-v0.6.md` §6): each receipt carries the hash of the one before it, with `seq` inside the hashed content, so a partial tamper is detected and named — an `UPDATE` on one row, a `DELETE` from the middle, a reordering. What that closes is **alteration that keeps the receipts after it**: changing what receipt *n* says while leaving the rest in place costs a rewrite of all of them plus the head, rather than one statement. **Not a truncation at the end, and not an append.** Two earlier versions of this line claimed the first; a review measured both at **two statements, undetected** — delete the rows and rewind the head, or insert a well-formed row and advance it. The head is a row in the same database as the receipts, so it raises the cost of *forgetting* and not the cost of erasing; an anchor outside the database is what would close that, and v0.6 has none. What it does **not** close is authorship, and it does not close a database admin who can rewrite every row including the chain head: such an adversary recomputes the chain and it verifies. The malicious-administrator line above is unchanged; v0.6 narrows it rather than removing it. Nor does the chain prove that every action wrote a receipt — a receipt whose write failed leaves no gap in `seq` and is invisible to the chain by construction; the events log is where that is reconciled.",  # noqa: E501
    ),
}

#: Sentences about something else entirely, which the word-boundary pattern cannot
#: tell apart: JWT signature verification, an HMAC on a webhook, and the Python word for a
#: function's parameters. §8's T180 did not anticipate these and is amended in item 9's PR.
#: They are allow-listed for the same reason the others are -- so that a **new** occurrence
#: fails and somebody has to come here and say which kind it is.
ANOTHER_SUBJECT: dict[str, tuple[str, ...]] = {
    "CHANGELOG.md": (
        "as widening, expired and revoked authority, token forgery, cross-JWT confusion, and signing",  # noqa: E501
        "token from the same issuer, signed with the same key, carrying the configured `aud`, passes",  # noqa: E501
        "fetch its signing keys in cleartext from wherever it pointed, cache them for the life of",
        "the process, and verify every token the attacker then signed.",
        "- **`Control.evaluate` returns the combined decision**, not the policy axis alone. Its signature",  # noqa: E501
        "one `Control` each. `SPEC-v0.1.md` §8's frozen signature is amended in the same change, as the",  # noqa: E501
        "- **`WebhookApprovalProvider`** — core, over stdlib `urllib.request`. One signed POST on",
        "`APPROVAL_REQUESTED`; the gateway serves the signed inbound grant/deny at",
    ),
    "docs/THREAT_MODEL.md": (
        "| A forged or tampered token | `JWTIdentityProvider` verifies the signature against a JWKS or a pinned key, with the algorithm taken from its own allow-list and never from the token (RFC 8725 §3.1) |",  # noqa: E501
        "| Signing keys fetched from somewhere else | JWKS over HTTPS only, redirects refused outright, a duplicate `kid` refused rather than resolved, a failed fetch never emptying the cache |",  # noqa: E501
        "- **A compromised identity provider.** CTRLRun *consumes* identities: it verifies a token somebody else issued and maps the verified claims onto a `Principal`. It issues nothing, and an issuer that signs a token for the wrong subject has told CTRLRun the truth as far as CTRLRun can tell. Everything downstream — grants, delegation, receipts — is then wrong, correctly and consistently.",  # noqa: E501
    ),
}


#: The two groups, merged. Both are allow-listed; the split is what keeps the reason for each
#: entry visible instead of averaging them into one undifferentiated list.
ALLOWED: dict[str, tuple[str, ...]] = {
    name: DISCLAIMS.get(name, ()) + ANOTHER_SUBJECT.get(name, ())
    for name in set(DISCLAIMS) | set(ANOTHER_SUBJECT)
}


def test_T180_the_release_documents_do_not_blur_alteration_and_authorship() -> None:
    """SPEC-v0.6 §1.2's third rule, as a test rather than an intention."""
    unexpected: list[str] = []
    for name in SCANNED:
        allowed = _load(name)
        for number, line in enumerate(_lines(name), start=1):
            if FORBIDDEN.search(line) and line.strip() not in allowed:
                unexpected.append(f"{name}:{number}  {line.strip()[:110]}")
    assert unexpected == [], (
        "a release document gained a sentence about signing, signatures or authorship that "
        "nobody allow-listed. If it disclaims one of them, add the exact line to ALLOWED with "
        "the reason. If it claims one of them, SPEC-v0.6 §6.4 says it is not true:\n"
        + "\n".join(unexpected)
    )


def test_T180_the_allow_list_is_not_stale() -> None:
    """An allow-list nobody prunes becomes a list of lines that no longer exist, and the next
    person to edit one of these sentences would find the guard silently not covering it.

    So every entry must still resolve to a line in its file **and** still contain a forbidden
    word: an entry that stopped matching is an entry doing nothing.
    """
    orphaned: list[str] = []
    for name, entries in ALLOWED.items():
        present = {line.strip() for line in _lines(name)}
        for entry in entries:
            if entry.strip() not in present:
                orphaned.append(f"{name}: no longer present — {entry.strip()[:90]}")
            elif not FORBIDDEN.search(entry):
                orphaned.append(f"{name}: allow-listed but matches nothing — {entry.strip()[:90]}")
    assert orphaned == [], orphaned


def test_T180_the_scan_would_see_a_new_claim() -> None:
    """The positive control (`v0.4 §1.3`). A guard whose pattern never matched anything would
    pass this file on any content at all, and the two tests above would both stay green."""
    assert FORBIDDEN.search("Receipts are signed, which proves authorship.")
    assert FORBIDDEN.search("The chain is tamper-proof.")
    assert FORBIDDEN.search("CTRLRun gives you non-repudiation.")
    # And the words it must not fire on, which is why it is a word-boundary pattern.
    assert not FORBIDDEN.search("the design of the store")
    assert not FORBIDDEN.search("assign the lease to the caller")
    assert not FORBIDDEN.search("a designated approver")


def test_T181_core_still_installs_pyyaml_and_click_and_nothing_else() -> None:
    """SPEC-v0.6 §8's T181. v0.6 adds `ctrlrun[postgres]`, and `psycopg` is only an extra
    while `pip install ctrlrun` does not pull it in."""
    path = REPO_ROOT / "pyproject.toml"
    if not path.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    names = {re.split(r"[<>=!\[ ]", entry)[0].lower() for entry in project["dependencies"]}
    assert names == {"pyyaml", "click"}, names
    assert "postgres" in project["optional-dependencies"], (
        "v0.6 ships a Postgres backend and it must be an extra"
    )


def test_T181_the_distributions_carry_no_adapter_no_research_and_no_pack() -> None:
    """The other half of T181, and the reason it is built rather than read: `MANIFEST.in`
    resolves against the **working tree** and not the index, so the only way to know what ships
    is to build it. v0.2 shipped four policy files `.gitignore` had swallowed, and every green
    build had already accounted for them.

    `research/` is the new name here. `research/framework-probe/` and `research/soak/` are both
    harnesses whose *results* are published and whose code ships nowhere (`v0.4 §7`, §8.1).

    **This overlaps `test_T136_the_ctrlrun_distributions_contain_no_adapter`, deliberately.**
    T136 grew the `research/` and `packs/` check when item 8's `tests/test_soak.py` needed a
    guarantee behind its skip; §8 asks T181 for all three in one place as the release check, and
    the two are allowed to agree. What is not allowed is neither of them running, which is why
    the skip below is the whole repository being absent and nothing narrower.
    """
    root = REPO_ROOT
    if not (root / "pyproject.toml").exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    with tempfile.TemporaryDirectory() as area:
        built = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", area, str(root)],
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            if "No module named build" in built.stderr:  # pragma: no cover - dev dependency
                pytest.skip("python -m build is not installed")
            raise AssertionError(f"python -m build failed:\n{built.stderr[-2000:]}")

        names: list[str] = []
        for artifact in Path(area).iterdir():
            if artifact.suffix == ".whl":
                names += zipfile.ZipFile(artifact).namelist()
            elif artifact.name.endswith(".tar.gz"):
                with tarfile.open(artifact) as archive:
                    names += archive.getnames()

    assert names, "nothing was built"
    offending = [
        name
        for name in names
        # Anchored on a path **segment**, so a file called `research.py` is not a hit and a
        # directory called `research/` is. The first version matched the bare substring and
        # would have fired on `docs/research.md`.
        if any(part in {"adapters", "research", "packs"} for part in Path(name).parts)
    ]
    assert offending == [], offending

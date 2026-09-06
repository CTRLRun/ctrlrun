#!/usr/bin/env python3
"""Turn the attestation `actions/attest-build-provenance` produced into release assets.

The action signs the distributions and hands back one Sigstore bundle. That bundle lives in
the runner's temp directory and in GitHub's attestation store, neither of which is a file
anybody downloading a release can see. This writes two, beside the distributions, so that
`gh release create dist/*` carries them:

  <name>.intoto.jsonl    the DSSE envelopes, one per line -- the SLSA convention, and what a
                         provenance scanner reads
  <name>.sigstore.json   the bundle verbatim -- what `gh attestation verify --bundle` reads
                         without going back to GitHub

And, before either is written, it checks the thing that actually matters: that the statement
is about **these** bytes. An attestation whose subjects are not the artifacts being uploaded
is worse than no attestation at all, because it passes every check that looks for a file and
none that looks inside it. Mismatch is a non-zero exit and nothing on disk.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# The two this script writes. Excluded when reading what is in `dist/`, so that a re-run sees
# the distributions and not its own output.
DERIVED_SUFFIXES = (".intoto.jsonl", ".sigstore.json")


class Refused(Exception):
    """The attestation does not describe the artifacts about to be uploaded."""


def _distributions(dist: Path) -> dict[str, str]:
    found = {}
    for path in sorted(dist.iterdir()):
        if not path.is_file() or path.name.endswith(DERIVED_SUFFIXES):
            continue
        found[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not found:
        raise Refused(f"{dist} holds no distributions to attest")
    return found


def _bundles(bundle_path: Path) -> list[dict[str, Any]]:
    """The action appends one JSON bundle per line, so the file is JSON Lines even when -- as
    with a single `subject-path` glob covering every artifact -- it holds exactly one."""
    lines = [line for line in bundle_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise Refused(f"{bundle_path} is empty: the attestation step produced nothing")
    return [json.loads(line) for line in lines]


def _envelope(bundle: dict[str, Any]) -> dict[str, Any]:
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, dict):
        raise Refused(
            "bundle carries no dsseEnvelope; a provenance attestation is always DSSE, so this "
            "is not one and there is nothing to publish as in-toto"
        )
    return envelope


def _statement(envelope: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = base64.b64decode(envelope["payload"], validate=True)
    except (KeyError, binascii.Error) as exc:
        raise Refused(f"the DSSE envelope has no decodable payload: {exc}") from exc
    statement: dict[str, Any] = json.loads(payload)
    return statement


def _check(statement: dict[str, Any], expected: dict[str, str]) -> None:
    """Every distribution attested, nothing else attested, and every digest the real one."""
    attested: dict[str, str] = {}
    for subject in statement.get("subject", []):
        digest = subject.get("digest", {}).get("sha256")
        if digest is None:
            raise Refused(f"subject {subject.get('name')!r} carries no sha256 digest")
        attested[subject["name"]] = digest

    missing = sorted(set(expected) - set(attested))
    if missing:
        raise Refused(f"the attestation does not cover {', '.join(missing)}")

    extra = sorted(set(attested) - set(expected))
    if extra:
        raise Refused(f"the attestation covers artifacts that are not being released: {extra}")

    # `.get`, not `[]`: if the missing-subject branch above were ever bypassed this must
    # still refuse in words rather than raise a KeyError. A traceback is a refusal nobody
    # can distinguish from a crash, and the tests assert which message they got.
    wrong = sorted(name for name, digest in expected.items() if attested.get(name) != digest)
    if wrong:
        raise Refused(
            f"digest mismatch on {', '.join(wrong)}: the attestation is about other bytes than "
            f"the ones in the release"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="the action's bundle-path")
    parser.add_argument("--dist", required=True, type=Path, help="the directory being released")
    parser.add_argument("--name", required=True, help="stem for the two assets, e.g. ctrlrun-0.6.0")
    args = parser.parse_args(argv)

    try:
        expected = _distributions(args.dist)
        bundles = _bundles(args.bundle)
        envelopes = [_envelope(bundle) for bundle in bundles]
        for envelope in envelopes:
            _check(_statement(envelope), expected)
    except Refused as refusal:
        print(f"release_provenance: {refusal}", file=sys.stderr)
        return 1

    # Only now, with every statement checked, does anything reach disk.
    jsonl = args.dist / f"{args.name}.intoto.jsonl"
    jsonl.write_text(
        "".join(json.dumps(envelope, separators=(",", ":")) + "\n" for envelope in envelopes),
        encoding="utf-8",
    )

    for index, bundle in enumerate(bundles):
        # One bundle is the ordinary case and gets the plain name. More than one would make a
        # single `.sigstore.json` malformed rather than merely surprising, so they are numbered
        # -- the suffix is what a verifier and a scanner key on, and it survives either way.
        stem = args.name if len(bundles) == 1 else f"{args.name}-{index + 1}"
        (args.dist / f"{stem}.sigstore.json").write_text(
            json.dumps(bundle, separators=(",", ":")), encoding="utf-8"
        )

    print(f"{jsonl.name}: {len(envelopes)} envelope(s) over {len(expected)} distribution(s)")
    for name in sorted(expected):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

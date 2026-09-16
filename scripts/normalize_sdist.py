# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Rewrite an sdist so that two builds of the same commit are the same bytes.

`python -m build` writes the wheel reproducibly when `SOURCE_DATE_EPOCH` is set, and the sdist
not quite: setuptools copies the tree into a scratch directory and the tar records the copy
time, the uid, the gid and the order the filesystem returned. This script keeps every member
and every byte of content and fixes only what the build could not: members sorted by name,
every mtime set to `SOURCE_DATE_EPOCH`, owner `0:0` with empty names, permissions normalised to
`0o644` (files) or `0o755` (directories and files that were executable), and a gzip header with
no name and a zero timestamp. The result is a valid sdist that `pip` and `twine` read as before.

    SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) python scripts/normalize_sdist.py dist/*.tar.gz

The wheel needs no help, and this script refuses one.
"""

from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile
from pathlib import Path


def normalize(path: Path, epoch: int) -> None:
    if not path.name.endswith(".tar.gz"):
        raise SystemExit(f"normalize_sdist: {path} is not a .tar.gz sdist")
    with tarfile.open(path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda m: m.name)
        blobs: dict[str, bytes] = {}
        for member in members:
            if member.isfile():
                extracted = source.extractfile(member)
                assert extracted is not None
                blobs[member.name] = extracted.read()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member in members:
            executable = member.isdir() or (member.mode & 0o111)
            member.mtime = epoch
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mode = 0o755 if executable else 0o644
            member.pax_headers = {}
            if member.isfile():
                target.addfile(member, io.BytesIO(blobs[member.name]))
            else:
                target.addfile(member)
    with (
        open(path, "wb") as out,
        gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as zipped,
    ):
        zipped.write(raw.getvalue())


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: normalize_sdist.py <sdist.tar.gz>...", file=sys.stderr)
        return 2
    epoch_text = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch_text or not epoch_text.isdigit():
        print("normalize_sdist: SOURCE_DATE_EPOCH must be set to an integer", file=sys.stderr)
        return 2
    for name in argv:
        normalize(Path(name), int(epoch_text))
        print(f"normalized {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

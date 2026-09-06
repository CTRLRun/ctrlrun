"""The README's header assets: the demo animation, its tape, and the social preview.

The GIF is a recording of `ctrlrun demo`'s first scenario, and a recording is a claim about
output that can drift the day the demo changes. So the tape is committed, the lines the
recording ends on are committed beside it, and this file asserts that every one of them is a
line the demo prints and a line the README quotes. When the demo's output changes, the README
test fails first, then this one, and the tape is re-run.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "docs" / "assets"
README = REPO_ROOT / "README.md"

if not README.exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

_RUN_VARYING = re.compile(r"(?:apr|dlg)_[0-9a-f]+")


def _expected_lines() -> list[str]:
    text = (ASSETS / "demo.expected.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def test_the_gif_the_tape_and_the_expected_lines_exist():
    assert (ASSETS / "demo.gif").stat().st_size > 10_000
    assert (ASSETS / "demo.tape").exists()
    assert _expected_lines(), "docs/assets/demo.expected.txt is empty"


def test_the_gif_is_a_gif():
    assert (ASSETS / "demo.gif").read_bytes()[:6] in {b"GIF87a", b"GIF89a"}


def test_the_tape_writes_the_gif_the_readme_embeds():
    tape = (ASSETS / "demo.tape").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "Output docs/assets/demo.gif" in tape
    assert 'src="docs/assets/demo.gif"' in readme


def test_the_recording_ends_on_the_line_that_matters():
    """The share unit is a failure and a refusal, so the last line held on screen is the
    count that proves the refund happened once."""
    assert _expected_lines()[-1].strip() == "remote refund calls: 1"
    tape = (ASSETS / "demo.tape").read_text(encoding="utf-8")
    assert "remote refund calls: 1" in tape


@pytest.mark.authority
def test_every_expected_line_is_one_the_demo_prints():
    """Run the demo and compare, with generated ids masked, exactly as the README test does."""
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    with CliRunner().isolated_filesystem():
        result = CliRunner().invoke(main, ["demo"])
    assert result.exit_code == 0, result.output
    printed = {_RUN_VARYING.sub("*", line.rstrip()) for line in result.output.splitlines()}

    missing = [
        line for line in _expected_lines() if _RUN_VARYING.sub("*", line.rstrip()) not in printed
    ]
    assert missing == [], f"the recording ends on lines the demo does not print: {missing}"


def test_every_expected_line_is_one_the_readme_quotes():
    section = README.read_text(encoding="utf-8").split("## What `ctrlrun demo` shows")[1]
    quoted = {_RUN_VARYING.sub("*", line.rstrip()) for line in section.splitlines()}

    missing = [
        line for line in _expected_lines() if _RUN_VARYING.sub("*", line.rstrip()) not in quoted
    ]
    assert missing == [], f"the recording ends on lines the README does not quote: {missing}"


def test_the_social_preview_is_1280_by_640_and_rendered_from_its_svg():
    png = (ASSETS / "social-preview.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (1280, 640)

    svg = (ASSETS / "social-preview.svg").read_text(encoding="utf-8")
    assert "The last check before an AI agent" in svg
    assert "BLOCKED" in svg
    script = (ASSETS / "render-social-preview.sh").read_text(encoding="utf-8")
    assert "social-preview.svg" in script and "social-preview.png" in script


def test_the_header_carries_the_fixed_copy_and_the_five_badges():
    head = README.read_text(encoding="utf-8").split("\n## ", 1)[0]

    assert "The last check before an AI agent does something it can't undo." in head
    assert "Autonomy belongs to the action, not the agent." in head
    assert (
        "Every consequential action happens once, exactly as approved, or not at all — and "
        "leaves a receipt." in head
    )
    for badge in (
        "pypi/v/ctrlrun",
        "pypi/pyversions/ctrlrun",
        "ci.yml/badge.svg",
        "verify-badge.json",
        "pypi/l/ctrlrun",
    ):
        assert badge in head, badge
    assert "generated from docs/capabilities.yaml (readme)" in head
    assert head.count("\n|---|") == 1, "exactly one table above the first H2"

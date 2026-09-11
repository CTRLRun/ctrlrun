"""The README's header assets: the demo animation, its tape, and the social preview.

The GIF is a recording of `ctrlrun demo`'s first two scenarios, and a recording is a claim about
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

#: The README is the PyPI page as well as the GitHub one, so its assets are absolute; see
#: `test_the_readme_carries_no_relative_link_and_no_relative_image`.
RAW = "https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/"
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
    # Absolute, because the README is also the PyPI long description and PyPI resolves a
    # relative src against pypi.org. The file still has to be the one the tape writes.
    assert 'src="' + RAW + 'docs/assets/demo.gif"' in readme


def test_the_recording_ends_on_the_line_that_matters():
    """The share unit is a failure and a refusal. The recording shows two: the count that
    proves the refund happened once, and, held last on screen, the approval that was bound to
    one action and refused for another."""
    lines = [line.strip() for line in _expected_lines()]
    assert "remote refund calls: 1" in lines
    assert lines[-1] == "✗ BLOCKED — approved action ≠ requested action (mismatch)"
    tape = (ASSETS / "demo.tape").read_text(encoding="utf-8")
    assert "sed '/approved action/q'" in tape


def test_the_recording_is_paced_and_hides_nothing_but_the_pacing():
    """The demo prints everything at once; the tape releases it a line at a time so a reader
    can follow. The only off-screen setup is that loop, and the two pipes are typed in the open."""
    tape = (ASSETS / "demo.tape").read_text(encoding="utf-8")
    hidden = tape.split("Hide", 1)[1].split("Show", 1)[0]
    assert "slow()" in hidden and "sleep" in hidden
    assert "ctrlrun" not in hidden, "the demo itself must not run off screen"
    assert "Type \"ctrlrun demo | sed '/approved action/q' | slow\"" in tape


def test_the_wordmark_ships_for_both_themes_and_the_readme_uses_both():
    for name in (
        "wordmark.svg",
        "wordmark-light.svg",
        "wordmark-dark.svg",
        "logo.svg",
        "favicon.svg",
    ):
        assert "#F5A623" in (ASSETS / name).read_text(encoding="utf-8"), f"{name} lacks the accent"
    # That the site's copies are byte-identical to these is checked in CTRLRun/ctrlrun-docs,
    # by `test_the_sites_wordmarks_are_the_librarys`. It is the only checkout that has both,
    # and the check went there whole rather than being weakened to fit here.
    head = README.read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert 'srcset="' + RAW + 'docs/assets/wordmark-dark.svg"' in head
    assert 'src="' + RAW + 'docs/assets/wordmark-light.svg"' in head


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


#: The README carries the transcript inside a collapsed block rather than a section of its own,
#: since the 2026-09-09 rewrite cut the page to what it does, how to use it, and how it works.
#: The guard is unchanged and only its anchor moved: the block is still in the file, and every
#: line the recording ends on still has to be a line the README quotes.
DEMO_BLOCK_ANCHOR = "<summary>What <code>ctrlrun demo</code> shows"


def readme_demo_block() -> str:
    text = README.read_text(encoding="utf-8")
    assert DEMO_BLOCK_ANCHOR in text, "the README no longer carries the demo transcript"
    return text.split(DEMO_BLOCK_ANCHOR, 1)[1].split("</details>", 1)[0]


def test_every_expected_line_is_one_the_readme_quotes():
    quoted = {_RUN_VARYING.sub("*", line.rstrip()) for line in readme_demo_block().splitlines()}

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
    assert "Execution safety" in svg
    assert "for AI agents" in svg
    assert "ctrlrun.dev" in svg
    assert "stripe" not in svg.lower(), "the card should not read as payments-only"
    script = (ASSETS / "render-social-preview.sh").read_text(encoding="utf-8")
    assert "social-preview.svg" in script and "social-preview.png" in script


def test_the_header_carries_the_fixed_copy_and_the_five_badges():
    """The header is the wordmark, the three fixed lines, the badges and the animation.

    It carried the capability matrix too, and this test required it there. That is the front
    door decision the matrix was moved for: a six-by-four table is the right document for
    somebody evaluating CTRLRun and the wrong one for somebody deciding whether to keep
    reading, so the first section after the animation is now the failure itself. The
    requirement is inverted rather than deleted — no table above the first H2, the marker
    still in the file, and the first section named — because a header that quietly grew a
    table again would otherwise pass.

    The copy was rewritten on 2026-09-09 to match ctrlrun.dev's, which leads with the category
    and the claim rather than three slogans, and the page was cut the same day to three
    questions: what it does, how to use it, how it works. What this test requires is unchanged:
    the fixed lines are pinned so the header cannot drift untested, the category noun is still
    asserted, and the first section is still the failure itself.
    """
    text = README.read_text(encoding="utf-8")
    head = text.split("\n## ", 1)[0]

    assert "Execution safety for AI agents." in head
    assert "The model guesses. CTRLRun does not." in head
    assert (
        "A consequential action happens at most once, exactly as approved, and leaves a "
        "receipt." in head
    )
    assert "When the outcome is unknown, CTRLRun says so instead of guessing." in head
    # The category noun, which the hero went without until 0.6: a reader had to reverse-engineer
    # what CTRLRun *is* from three slogans. The documentation root carried it; the README did not.
    assert "A Python library that sits between the decision to act and the call that acts." in head
    # The row was cut from thirteen to ten on 2026-09-11: `pypi/pyversions` is metadata rather
    # than a claim, and `ruff` and `mypy --strict` say how the library is written, which is not
    # what a stranger is deciding on the first screen. `scripts/check.sh` still runs all three
    # and `test_ci_runs_the_check_script` still requires CI to call it, so what the two badges
    # asserted is enforced where it was always enforced. Their absence is required rather than
    # merely untested: a row that grew back would otherwise pass.
    for badge in (
        "pypi/v/ctrlrun",
        "downloads-badge.json",
        "clones-badge.json",
        "ci.yml/badge.svg",
        "fuzz.yml/badge.svg",
        "verify-badge.json",
        "pypi/l/ctrlrun",
    ):
        assert badge in head, badge
    for gone in ("pypi/pyversions/ctrlrun", "astral-sh/ruff", "mypy-strict"):
        assert gone not in head, gone
    # `img.shields.io/pypi/dm` rendered the download count live, which means shields asking
    # pypistats on behalf of every project it serves: it returned `rate limited by upstream
    # service` the day it shipped. `traffic.yml` now asks once a day and publishes the answer.
    # Pinned as an absence because the live form is the obvious thing to reach for again.
    assert "pypi/dm/ctrlrun" not in head, "the live shields form is rate limited; use the endpoint"
    marker = "generated from capabilities.yaml (readme)"
    assert marker not in head, "the capability matrix is not the first screen"
    assert marker in text, "the capability matrix was moved, not dropped"
    assert head.count("\n|---|") == 0, "no table above the first H2"
    assert text.split("\n## ", 2)[1].startswith("What it does")

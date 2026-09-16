# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The README's header assets: the animation, its tape, what it records, and the social preview.

The GIF is a recording of the README's own "Use it in three steps" section, and a recording is a
claim about output that can drift the day the section or the library changes. So the tape is
committed; the three files it records are committed beside it under `docs/assets/recording/`
and asserted byte for byte equal to the README's runnable blocks; the lines the recording ends
on are committed as `docs/assets/demo.expected.txt`; and this file runs those same three files
against the library and asserts that every one of those lines is printed, and that every one is
a line the README quotes. When the output changes, the tests fail first, then the tape is re-run.

Until 2026-09-14 the animation was `ctrlrun demo`'s first two scenarios, piped through `sed` and
a pacing loop. It was a true recording and a hard one to read: a stranger saw a transcript with
two pipes in the command line and no code, and could not tell from it what ctrlrun *is*. The
recording is now the integration itself: the policy file, the agent that runs one refund and is
stopped on the next, the human answering from the shell, and the same approval refused for a
different amount. The demo transcript is still in the README, in the collapsed block that
`tests/test_demo.py` holds to the demo's output.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The README is the PyPI page as well as the GitHub one, so its assets are absolute; see
#: `test_the_readme_carries_no_relative_link_and_no_relative_image`.
RAW = "https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/"
ASSETS = REPO_ROOT / "docs" / "assets"
RECORDING = ASSETS / "recording"
README = REPO_ROOT / "README.md"

if not README.exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

#: What differs between runs of the recorded files: the approval id, the action hash the grant
#: names, and when it lapses. Everything else is printed the same way every time.
_RUN_VARYING = re.compile(r"(?:apr|dlg)_[0-9a-f]+|sha256:[0-9a-f]+|\d{4}-\d{2}-\d{2}T[0-9:.]+Z")

#: The four commands the tape types, in order. The third is also the README's own bash block.
TYPED = (
    "cat ctrlrun.yaml",
    "python agent.py",
    'ctrlrun approve "$(cat request_id.txt)"',
    "python approved.py",
)

#: The README section the recording is of, and the block anchors inside it.
SECTION = "## Use it in three steps"
_FENCE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<body>.*?)^```", re.M | re.S)


def _masked(line: str) -> str:
    return _RUN_VARYING.sub("*", line.rstrip())


def _expected_lines() -> list[str]:
    text = (ASSETS / "demo.expected.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def _section() -> str:
    text = README.read_text(encoding="utf-8")
    assert SECTION in text, "the README no longer has the section the recording is of"
    return text.split(SECTION, 1)[1].split("\n## ", 1)[0]


def _runnable_blocks() -> dict[str, str]:
    """The section's runnable blocks, keyed by the file each one is: the policy, the two
    programs, and the shell line."""
    found: dict[str, str] = {}
    for match in _FENCE.finditer(_section()):
        tokens = match.group("info").split()
        if "runnable" not in tokens:
            continue
        language = tokens[0]
        file = next((t[len("file=") :] for t in tokens if t.startswith("file=")), None)
        if language == "yaml":
            found["ctrlrun.yaml"] = match.group("body")
        elif language == "python":
            assert file, "a recorded Python block names the file the tape runs"
            found[file] = match.group("body")
        elif language == "bash":
            found["approve.sh"] = match.group("body")
    return found


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


def test_the_tape_types_the_four_commands_in_the_open_and_hides_only_the_setup():
    """Everything that produces output is typed on screen. What runs off screen is the copy
    of the committed files into an empty directory and a `clear`, and nothing else: no
    `python`, no `ctrlrun`, so no line of output the reader sees was produced out of sight."""
    tape = (ASSETS / "demo.tape").read_text(encoding="utf-8")

    hidden = re.findall(r"^Hide\n(.*?)^Show", tape, re.M | re.S)
    assert hidden, "the setup runs off screen, so there is a Hide/Show pair"
    assert any("cp -R docs/assets/recording/. " in part for part in hidden), (
        "what the tape records is the committed workspace, copied whole"
    )
    for part in hidden:
        assert "python " not in part and "ctrlrun " not in part, part

    visible = tape
    for part in hidden:
        visible = visible.replace(part, "")
    positions = [visible.find(command) for command in TYPED]
    assert all(p >= 0 for p in positions), dict(zip(TYPED, positions, strict=True))
    assert positions == sorted(positions), "the four commands are typed in the README's order"


def test_the_recorded_files_are_the_readmes_own_runnable_blocks():
    """The tape records `docs/assets/recording/`, and the README shows its blocks. They are the
    same bytes, so the animation is of the section it sits above and not of a cousin of it."""
    blocks = _runnable_blocks()

    for name in ("ctrlrun.yaml", "agent.py", "approved.py"):
        assert name in blocks, f"the section has no runnable block for {name}"
        recorded = (RECORDING / name).read_text(encoding="utf-8")
        assert blocks[name] == recorded, f"README's {name} block differs from the recorded file"
    assert blocks.get("approve.sh", "").strip() == TYPED[2]


def test_the_recording_ends_on_the_refusal_and_the_count():
    """The share unit is a failure and a refusal. The recording ends on the approval spent on a
    different amount being refused, and on the count that proves the mutated call never
    reached the provider."""
    lines = [line.strip() for line in _expected_lines()]
    assert lines[-2] == "€9,000 on that same approval -> refused"
    assert lines[-1] == "calls that reached the provider: 1 (the €9,000 never left)"
    assert "€500   -> succeeded" in lines
    assert any(line.startswith("granted apr_") and "for sha256:" in line for line in lines)


def _run_the_recorded_files(workdir: Path) -> list[str]:
    """Run the three files exactly as the tape does: in an empty directory, in order, with the
    checkout's CLI on `PATH`, and no operator store leaking in. Returns every printed line."""
    shutil.copytree(RECORDING, workdir, dirs_exist_ok=True)
    environment = dict(os.environ)
    for name in ("CTRLRUN_CONFIG", "CTRLRUN_STATE", "CTRLRUN_STORE_URL"):
        environment.pop(name, None)
    environment["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), environment.get("PATH", "")]
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    printed: list[str] = []
    for command in TYPED[1:]:
        argv = [sys.executable, command.split()[1]] if command.startswith("python ") else None
        completed = subprocess.run(
            argv or ["bash", "-euo", "pipefail", "-c", command],
            cwd=workdir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, f"{command}: {completed.stderr or completed.stdout}"
        printed.extend(completed.stdout.splitlines())
    return printed


def test_every_expected_line_is_one_the_recorded_files_print(tmp_path):
    printed = {_masked(line) for line in _run_the_recorded_files(tmp_path / "work")}

    missing = [line for line in _expected_lines() if _masked(line) not in printed]
    assert missing == [], f"the recording ends on lines the files do not print: {missing}"


def test_both_programs_fail_loudly_where_the_refusal_does_not_happen():
    """A recording of a broken build must not look fine. Each program `sys.exit`s on the path
    where the policy or the approval binding was not in force, so the tape fails there."""
    for name in ("agent.py", "approved.py"):
        source = (RECORDING / name).read_text(encoding="utf-8")
        assert "else:\n" in source and "sys.exit(" in source, name


def test_every_expected_line_is_one_the_readme_quotes():
    quoted = {_masked(line) for line in _section().splitlines()}

    missing = [line for line in _expected_lines() if _masked(line) not in quoted]
    assert missing == [], f"the recording ends on lines the README does not quote: {missing}"


def test_the_readme_says_the_animation_is_the_section():
    section = _section()
    assert "The animation above is this section" in section


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


#: What ctrlrun.dev's homepage says first, and therefore what the README says first. The site
#: is the marketing surface and the README is the GitHub and PyPI one, and until 2026-09-14
#: they led with different sentences. The pitch is shared; nothing commercial is, and the
#: README's documentation links go to docs.ctrlrun.dev rather than to the site.
#: `CTRLRun/ctrlrun-docs` asserts the same strings against `index.mdx`, so a change to either
#: surface fails on the other.
HOMEPAGE_H1 = (
    "ctrlrun stops AI agents from taking wrong, restricted, or malicious actions in your workflows."
)
HOMEPAGE_LEDE = (
    "Every action is checked against your rules before it runs. Allowed actions go through. "
    "Sensitive ones wait for a person. Forbidden ones are blocked."
)


def _prose(html: str) -> str:
    """Tags removed and whitespace collapsed: a `<br>` is a space, every other tag is nothing."""
    text = re.sub(r"<br\s*/?>", " ", html)
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def test_the_header_carries_the_fixed_copy_and_the_five_badges():
    """The header is the wordmark, the homepage's own first two sentences, the category and
    the claim, the badges and the animation. (The name predates the row growing past five and
    stays because `CLAIMS.md` cites it; a rename is a cross-repository change.)

    It carried the capability matrix too, and this test required it there. That is the front
    door decision the matrix was moved for: a six-by-four table is the right document for
    somebody evaluating ctrlrun and the wrong one for somebody deciding whether to keep
    reading, so the first section after the animation is the failure itself. The requirement
    is inverted rather than deleted: no table above the first H2, the marker still in the
    file, and the first section named, because a header that quietly grew a table again would
    otherwise pass.

    The fixed lines are pinned so the header cannot drift untested. The first two are the
    homepage's H1 and lede, verbatim; the category noun is still asserted, because a reader
    had to reverse-engineer what ctrlrun *is* from three slogans before 0.6; and the
    at-most-once and unknown sentences are the two claims `CLAIMS.md` maps to their tests.
    """
    text = README.read_text(encoding="utf-8")
    head = text.split("\n## ", 1)[0]
    prose = _prose(head)

    assert HOMEPAGE_H1 in prose
    assert HOMEPAGE_LEDE in prose
    assert "Execution safety for AI agents." in head
    assert "A Python library that sits between the decision to act and the call that acts." in head
    assert (
        "A consequential action happens at most once, exactly as approved, and leaves a "
        "receipt." in head
    )
    assert "When the outcome is unknown, ctrlrun says so instead of guessing." in head
    # The homepage's example heading opens the first section.
    assert "**The model guesses. ctrlrun does not.**" in text
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


def test_how_it_works_names_the_homepage_seven_steps_in_order():
    """The homepage diagram walks seven steps, normalize to record, and names three refusals
    beside them. The README's section is the same walk in prose, in the same order."""
    text = README.read_text(encoding="utf-8")
    section = text.split("## How it works", 1)[1].split("\n## ", 1)[0]
    steps = ["Normalize", "Decide", "Approve", "Reserve", "Execute", "Resolve", "Record"]
    positions = [section.find(f"**{step}:") for step in steps]
    assert all(p >= 0 for p in positions), dict(zip(steps, positions, strict=True))
    assert positions == sorted(positions)
    assert "normalize  →  decide  →  approve  →  reserve  →  execute  →  resolve  →  record" in (
        section
    )

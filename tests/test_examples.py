"""`examples/` and the sector policy templates. Build-list item 2; SPEC-v0.2 §1.1, T31.

Two halves of one promise. The scripts each run the failure they exist to demonstrate and
print the refusal, with no network and a state directory of their own. The templates are
starting points on the v0.1 kernel — which is what lets this item land before §3 changes the
policy schema — so every one declares `ctrlrun.policy/v1` and uses no key §3 adds.

The network is not taken on trust: each script runs in a subprocess whose `sitecustomize`
refuses every socket, so an example that grew a dependency on a live service fails here
rather than on the reader's laptop.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ctrlrun import Policy
from ctrlrun.policy import POLICY_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
TEMPLATES = EXAMPLES / "policies"

#: The four failure scenarios of SPEC-v0.2 §1.1, and the refusal each script must print.
SCENARIOS: dict[str, str] = {
    "double-refund": "blind retry refused",
    "approval-mutation": "approved action ≠ requested action",
    "agent-race": "already reserved",
    "approval-replay": "single-use approval already consumed",
    # SPEC-v0.3 §1.2 — the fifth answers a different question from the other four: not "is
    # this action safe to run" but "is this principal entitled to propose it at all".
    "authority-escalation": "outside the delegated grant",
}

#: Directories under `examples/` that are not one of §1.1's failure scenarios: the sector
#: templates, and item 8's ACS integration example (SPEC-v0.2 §9, `docs/ACS.md`).
NOT_A_SCENARIO = ("policies", "acs", "authority", "__pycache__")

#: The nine sectors of SPEC-v0.2 §1.1.
SECTORS = (
    "devops",
    "e-commerce",
    "government",
    "healthcare",
    "hr",
    "insurance",
    "legal",
    "payments",
    "security",
)

#: SPEC-v0.2 §1.1 — the header every template carries, verbatim.
HEADER = "Starting point on the v0.1 kernel. Adapt before use."

#: The rule the templates are shaped by, stated at the top of each so a reader can apply it
#: to the actions their own system has rather than only to the ones listed. A template whose
#: decisions cannot be re-derived is a list to copy, which is not what a template is for.
DECISION_RULE = (
    "cheap to undo",
    "leaves the building",
    "destroys the evidence",
)

#: Keys SPEC-v0.2 §3 adds. A template using one would not load on v0.1 at all, which is the
#: whole reason §1.1 holds these to v0.1 primitives.
V2_KEYS = ("effect:", "resource:", "mcp:")

#: A template is a starting point, not an attestation. ROADMAP's sector rule holds until the
#: milestone that earns a claim, and no template gets to imply one early.
COMPLIANCE_WORDS = (
    "compliant",
    "compliance",
    "certified",
    "certification",
    "accredited",
    "attestation",
    "conforms to",
    "aligned with",
)

_REFUSE_EVERY_SOCKET = '''\
"""Imported by `site` at startup: nothing under examples/ may open a socket."""

import socket

_real = socket.socket


class _Refusing(_real):
    """A socket that exists but will not connect, which is what a cut cable looks like.

    Replacing the *type* with a function breaks anything that subclasses it — `ssl` does —
    so the refusal goes on the operations instead.
    """

    def connect(self, *args, **kwargs):
        raise RuntimeError("an example tried to connect; examples must run with no network")

    def connect_ex(self, *args, **kwargs):
        raise RuntimeError("an example tried to connect; examples must run with no network")


def _refuse(*args, **kwargs):
    raise RuntimeError("an example tried to resolve a name; examples run with no network")


socket.socket = _Refusing
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
'''


@pytest.fixture(scope="session")
def no_network(tmp_path_factory):
    """A `PYTHONPATH` entry whose `sitecustomize` refuses every socket (SPEC-v0.2 §1.1)."""
    directory = tmp_path_factory.mktemp("no-network")
    (directory / "sitecustomize.py").write_text(_REFUSE_EVERY_SOCKET, encoding="utf-8")
    return directory


def _run(scenario: str, cwd: Path, no_network: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(no_network), environment.get("PYTHONPATH", "")) if part
    )
    return subprocess.run(
        [sys.executable, str(EXAMPLES / scenario / "main.py")],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _templates() -> list[Path]:
    return sorted(TEMPLATES.glob("*.yaml"))


# --- T31: every example runs -----------------------------------------------------------


def test_T31_the_four_scenarios_of_the_spec_are_the_ones_on_disk():
    found = sorted(
        path.name
        for path in EXAMPLES.iterdir()
        if path.is_dir() and path.name not in NOT_A_SCENARIO
    )
    assert found == sorted(SCENARIOS)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_T31_every_example_exits_zero_and_prints_its_refusal(scenario, tmp_path, no_network):
    finished = _run(scenario, tmp_path, no_network)

    assert finished.returncode == 0, f"{scenario} failed:\n{finished.stdout}\n{finished.stderr}"
    assert "BLOCKED" in finished.stdout
    assert SCENARIOS[scenario] in finished.stdout


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_T31_every_example_keeps_its_state_under_its_own_directory(scenario, tmp_path, no_network):
    """An example that reserved effect keys in a live store would block real work (§1.1)."""
    _run(scenario, tmp_path, no_network)

    assert (tmp_path / ".ctrlrun" / "examples" / scenario / "state.db").is_file()
    written = {
        path.relative_to(tmp_path).parts[0] for path in tmp_path.rglob("*") if path.is_file()
    }
    assert written == {".ctrlrun"}


def test_T31_every_file_an_example_needs_is_tracked_by_git():
    """A file `.gitignore` swallows runs locally and is missing from every clone.

    `.gitignore` ignores the operator's own policy at the repo root. The pattern was
    unanchored once, which matched `examples/*/ctrlrun.yaml` too and kept every example's
    policy out of this item's first commit — green locally, red on the first CI run against
    a fresh checkout. This is that failure, caught before the push.
    """
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no repository checkout")

    listed = subprocess.run(
        ["git", "ls-files", "examples"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = set(listed.stdout.split())
    needed = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in EXAMPLES.rglob("*")
        if path.is_file() and path.suffix in (".py", ".yaml")
    }

    assert not needed - tracked, f"untracked: {sorted(needed - tracked)}"


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_T31_every_example_is_repeatable(scenario, tmp_path, no_network):
    """A second run in the same directory must refuse the same thing, not a stale record."""
    first = _run(scenario, tmp_path, no_network)
    second = _run(scenario, tmp_path, no_network)

    assert first.returncode == 0, f"{scenario} failed on its first run:\n{first.stderr}"
    assert second.returncode == 0, f"{scenario} is not repeatable:\n{second.stderr}"
    assert SCENARIOS[scenario] in second.stdout


# --- T31: every template loads ---------------------------------------------------------


def test_T31_the_nine_sectors_of_the_spec_are_the_ones_on_disk():
    assert sorted(path.stem for path in _templates()) == sorted(SECTORS)


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_loads(sector):
    policy = Policy.from_file(TEMPLATES / f"{sector}.yaml")

    assert policy.actions, f"{sector}.yaml declares no actions"


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_declares_schema_v1(sector):
    """v0.1 primitives only, so a template loads on the shipped kernel (SPEC-v0.2 §1.1)."""
    text = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8")

    assert f"schema: {POLICY_SCHEMA}" in text


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_carries_the_adapt_before_use_header(sector):
    text = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8")

    assert HEADER in text.splitlines()[0] or HEADER in "\n".join(text.splitlines()[:3])


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_states_the_rule_its_decisions_follow(sector):
    """The teaching, not the list: a reader has to be able to decide an action not shown.

    Asserted in the preamble — the first dozen lines, above `schema:` — because a rule
    buried under forty lines of YAML is a rule nobody reads before copying the file.
    """
    preamble = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8").split("schema:")[0]
    missing = [clause for clause in DECISION_RULE if clause not in preamble]

    assert not missing, f"{sector}.yaml does not state: {missing}"


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_no_template_uses_a_key_added_by_section_3(sector):
    text = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8")
    used = [key for key in V2_KEYS if key in text]

    assert not used, f"{sector}.yaml uses {used}, which SPEC-v0.2 §3 adds"


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_no_template_claims_compliance(sector):
    text = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8").lower()
    claimed = [word for word in COMPLIANCE_WORDS if word in text]

    assert not claimed, f"{sector}.yaml claims {claimed}; ROADMAP's sector rule forbids it"


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_denies_an_unknown_action(sector):
    """The kernel's floor, asserted per template: there is no default-allow (SPEC-v0.1 §3.3)."""
    from ctrlrun import Action, Decision, Principal

    policy = Policy.from_file(TEMPLATES / f"{sector}.yaml")
    unknown = Action(
        name="nothing.this.template.declares",
        arguments={},
        principal=Principal(agent="some-agent"),
    )

    assert policy.evaluate(unknown).decision is Decision.DENY


@pytest.mark.parametrize("sector", SECTORS)
def test_T31_every_template_has_at_least_one_deny(sector):
    """A template that never says no is a template that taught nothing (SPEC-v0.2 §1.1)."""
    text = (TEMPLATES / f"{sector}.yaml").read_text(encoding="utf-8")

    assert "decision: deny" in text


# --- SPEC-v0.3 §1.2: the two authority configurations ----------------------------------

AUTHORITY_EXAMPLES = EXAMPLES / "authority"


def _authority_documents() -> list[Path]:
    return sorted(AUTHORITY_EXAMPLES.glob("*.yaml"))


def test_the_authority_examples_are_the_two_the_spec_names():
    """§1.2 — a payments delegation chain and a DevOps chain."""
    assert [path.name for path in _authority_documents()] == ["devops.yaml", "payments.yaml"]


@pytest.mark.authority
@pytest.mark.parametrize("path", _authority_documents(), ids=lambda path: path.stem)
def test_each_authority_example_loads_on_both_axes(path):
    """A document that does not load is not an example of anything. Both loaders, because
    `Policy` never reads the `authority:` section and `Authority` never reads `actions:`."""
    from ctrlrun import Authority

    document = path.read_text(encoding="utf-8")

    policy = Policy.from_yaml(document, source=str(path))
    authority = Authority.from_yaml(document, source=str(path))

    assert policy.actions
    assert authority.grants


@pytest.mark.authority
@pytest.mark.parametrize("path", _authority_documents(), ids=lambda path: path.stem)
def test_each_authority_example_declares_v3(path):
    """§12.1 — `authority:` needs `ctrlrun.policy/v3`, and a reader that ignored the section
    would run every action with no authority check at all."""
    from ctrlrun.policy import POLICY_SCHEMA_V3

    assert Policy.from_yaml(path.read_text(encoding="utf-8")).schema == POLICY_SCHEMA_V3


@pytest.mark.authority
@pytest.mark.parametrize("path", _authority_documents(), ids=lambda path: path.stem)
def test_no_authority_example_grants_everything(path):
    """`**` on its own is the whole surface of a system. These are documents a reader copies,
    and a template that hands out `**` teaches the habit it exists to prevent."""
    from ctrlrun import Authority

    for grant in Authority.from_yaml(path.read_text(encoding="utf-8")).grants.values():
        assert grant.actions != ("**",), f"{path.name}: grant {grant.id!r} grants everything"
        assert grant.subject.agent != "**", f"{path.name}: grant {grant.id!r} names any agent"


@pytest.mark.authority
@pytest.mark.parametrize("path", _authority_documents(), ids=lambda path: path.stem)
def test_every_delegable_grant_in_an_example_expires(path):
    """§4.2 — the loader refuses `delegable: true` without `expires_at`, so this cannot fail
    while the loader holds. It is here for what it says to a reader of the *examples*: the
    grants that can mint more authority are the ones with the nearest dates on them."""
    from ctrlrun import Authority

    delegable = [
        grant for grant in Authority.from_yaml(path.read_text()).grants.values() if grant.delegable
    ]
    assert delegable, f"{path.name} shows no delegable grant, so it shows no chain"
    assert all(grant.expires_at is not None for grant in delegable)


def test_the_authority_examples_carry_a_readme_that_says_they_are_not_drop_in():
    """A grant names a real principal in a real organization. Adopting somebody else's is how
    a template becomes an incident, and the file says so on its face."""
    readme = (AUTHORITY_EXAMPLES / "README.md").read_text(encoding="utf-8")

    assert "invented" in readme
    assert "starting points" in readme


@pytest.mark.parametrize("path", _authority_documents(), ids=lambda path: path.stem)
def test_no_authority_example_makes_a_compliance_claim(path):
    """ROADMAP's rule, extended to the v0.3 examples: no badge before the milestone that
    earns it."""
    text = path.read_text(encoding="utf-8").lower()

    assert not [word for word in COMPLIANCE_WORDS if word in text]


@pytest.mark.parametrize("path", _templates(), ids=lambda path: path.stem)
def test_no_sector_template_ships_an_authority_section(path):
    """SPEC-v0.3 §1.2 — the nine templates stay on v0.1 primitives and gain no grants.

    A grant names a real principal in a real organization. A template that shipped plausible
    ones would invite an operator to adopt them, which is the one way a starting point turns
    into somebody else's access-control list. Each says so on its face, so a reader who came
    looking for grants learns why there are none rather than assuming they were forgotten.
    """
    text = path.read_text(encoding="utf-8")
    # The parsed document, not the raw text: the note explaining the absence names the key,
    # and a substring check would be satisfied by a comment while a real section sat below it.
    document = yaml.safe_load(text)

    assert "authority" not in document
    assert "No `authority:` section, deliberately" in text
    assert Policy.from_yaml(text, source=str(path)).schema == POLICY_SCHEMA

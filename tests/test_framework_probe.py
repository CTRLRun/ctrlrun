"""The research harness. SPEC-v0.4 §7; T122-T124b.

`research/framework-probe/` is outside `src/` and is not on the import path, so these tests put
it there themselves. That is the same fact T124b asserts from the other side: after
`pip install .` neither `research` nor `framework_probe` is importable, because neither is
packaged.

T122 needs its **pair**. A stub that retries reporting `executed_twice` proves nothing on its
own: a harness hard-coded to say `executed_twice` would satisfy it, and would say the same
thing about every real framework it ever ran.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_ROOT = REPO_ROOT / "research" / "framework-probe"

if not PROBE_ROOT.exists():  # pragma: no cover - not a checkout
    pytest.skip("research/ is not in the sdist", allow_module_level=True)

if str(PROBE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROBE_ROOT))

from framework_probe import SCHEMA  # noqa: E402
from framework_probe import remote as remote_module  # noqa: E402
from framework_probe import results as results_module  # noqa: E402
from framework_probe import runner as runner_module  # noqa: E402
from framework_probe import scenarios as scenario_module  # noqa: E402
from framework_probe.adapters import STUBS, all_adapters, frameworks  # noqa: E402
from framework_probe.adapters.base import read_version  # noqa: E402
from framework_probe.adapters.stub import NOT_RETRYING, RETRYING  # noqa: E402


def _row(document, framework, scenario):
    return next(
        row
        for row in document["results"]
        if row["framework"] == framework and row["scenario"] == scenario
    )


# --- T122: the harness runs end-to-end, and its stub pair disagrees ------------------------


def test_T122_a_stub_that_retries_a_lost_response_reports_executed_twice():
    document = runner_module.run(
        adapters=[RETRYING], scenarios=[scenario_module.DOUBLE_REFUND_SCENARIO]
    )
    row = _row(document, "stub-retrying", "double-refund")

    assert row["outcome"] == results_module.EXECUTED_TWICE
    assert row["effects_observed"] == 2
    assert row["requests_observed"] == 2


def test_T122_its_pair_a_stub_that_does_not_retry_reports_executed_once():
    """Without this, the harness could report `executed_twice` unconditionally and pass."""
    document = runner_module.run(
        adapters=[NOT_RETRYING], scenarios=[scenario_module.DOUBLE_REFUND_SCENARIO]
    )
    row = _row(document, "stub-not-retrying", "double-refund")

    assert row["outcome"] == results_module.EXECUTED_ONCE
    assert row["effects_observed"] == 1
    assert row["requests_observed"] == 1


def test_T122_the_two_stubs_disagree_against_the_same_remote():
    """One remote, one scenario, one behaviour — and two different answers. That is the whole
    claim the harness makes about itself."""
    document = runner_module.run(
        adapters=list(STUBS), scenarios=[scenario_module.DOUBLE_REFUND_SCENARIO]
    )

    outcomes = {row["framework"]: row["outcome"] for row in document["results"]}

    assert outcomes["stub-retrying"] != outcomes["stub-not-retrying"]
    assert outcomes == {
        "stub-retrying": results_module.EXECUTED_TWICE,
        "stub-not-retrying": results_module.EXECUTED_ONCE,
    }


def test_T122_the_approval_mutation_scenario_reaches_the_remote_unbound():
    """Neither stub has any notion of an approval binding, and that is the finding: the
    mutated action executes, and the remote can see it differs from what was approved."""
    document = runner_module.run(
        adapters=[NOT_RETRYING], scenarios=[scenario_module.APPROVAL_MUTATION_SCENARIO]
    )
    row = _row(document, "stub-not-retrying", "approval-mutation")

    assert row["outcome"] == results_module.EXECUTED_ONCE
    assert row["effects_observed"] == 1
    # The approval is not counted as a request: counting it would make one scenario's
    # `requests_observed` one higher than the other's for no reason a reader could see.
    assert row["requests_observed"] == 1


def test_the_outcome_comes_from_the_remote_and_not_from_the_adapter():
    """An adapter that graded its own run would be the framework marking its own homework."""
    scenario = scenario_module.DOUBLE_REFUND_SCENARIO
    state = remote_module.State(effects=[{"identity": "a"}, {"identity": "a"}])

    assert runner_module.outcome_for(scenario, state, None) == results_module.EXECUTED_TWICE
    # Even when the adapter reported an error: two effects is two effects.
    assert runner_module.outcome_for(scenario, state, "boom") == results_module.EXECUTED_TWICE
    empty = remote_module.State()
    assert runner_module.outcome_for(scenario, empty, None) == results_module.REFUSED
    assert runner_module.outcome_for(scenario, empty, "boom") == results_module.ERROR


def test_the_fake_remote_counts_effects_by_identity_and_not_by_request():
    with remote_module.FakeRemote(behaviour=remote_module.COMMIT_THEN_DROP) as remote:
        assert remote.url.startswith("http://127.0.0.1:")
        RETRYING.run(scenario_module.DOUBLE_REFUND_SCENARIO, remote.url)
        state = remote.snapshot()

    assert len(state.requests) == 2
    assert len(state.effects) == 2
    assert {effect["identity"] for effect in state.effects} == {"probe-payment-1"}


# --- T123: every adapter declares its framework version at runtime -------------------------


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_T123_the_version_is_read_from_the_installed_distribution(adapter):
    """Compared against `importlib.metadata.version`, so a literal in an adapter's source
    fails this: a version somebody typed is a version that was true once."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as metadata_version

    reported = read_version(adapter.distribution)
    try:
        expected = metadata_version(adapter.distribution)
    except PackageNotFoundError:
        assert reported == "", adapter.name
        assert not adapter.available(), adapter.name
        return
    assert reported == expected, adapter.name
    assert adapter.available(), adapter.name


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_T123_no_adapter_hard_codes_a_version(adapter):
    import inspect
    import re

    source = inspect.getsource(type(adapter))

    assert not re.search(r'version\s*[:=]\s*["\']\d', source), adapter.name


def test_T123_an_adapter_whose_framework_is_absent_is_skipped_by_name_in_the_results():
    """A silent absence reads as a framework that had nothing to report. A table with four
    rows where five were expected has to say which one is missing."""

    class Absent:
        name = "not-installed-anywhere"
        distribution = "ctrlrun-probe-no-such-distribution"
        config_deviation = None

        def available(self) -> bool:
            return False

        def run(self, scenario, url):  # pragma: no cover - never reached
            raise AssertionError("an unavailable adapter must not be run")

    document = runner_module.run(
        adapters=[Absent()], scenarios=[scenario_module.DOUBLE_REFUND_SCENARIO]
    )
    row = _row(document, "not-installed-anywhere", "double-refund")

    assert row["outcome"] == results_module.ERROR
    assert "not installed" in row["notes"]
    assert row["version"] == ""
    results_module.validate(document)


def test_T123_every_framework_named_by_the_spec_has_an_adapter():
    named = {adapter.name for adapter in frameworks()}

    assert named == {"langgraph", "crewai", "openai-agents", "autogen", "mcp-client"}


# --- T124: the results document validates --------------------------------------------------


def test_T124_a_full_run_validates_against_the_schema():
    document = runner_module.run()

    results_module.validate(document)
    assert document["schema"] == SCHEMA == "ctrlrun.framework-probe/v1"
    assert document["remote"] == "fake-mcp/1"
    for row in document["results"]:
        assert row["outcome"] in results_module.OUTCOMES
        # Present on **every** row, null or a string: §7.3 rule 4 puts a deviation in the
        # table, and an absent key reads as an absent deviation only to somebody who already
        # knew the rule.
        assert "config_deviation" in row
        assert row["config_deviation"] is None or isinstance(row["config_deviation"], str)
    json.loads(results_module.dumps(document))


@pytest.mark.parametrize(
    "break_it",
    [
        lambda d: d.__setitem__("schema", "something/else"),
        lambda d: d["results"][0].__setitem__("outcome", "worked_fine"),
        lambda d: d["results"][0].pop("config_deviation"),
        lambda d: d.pop("remote"),
    ],
    ids=["schema", "outcome", "deviation", "remote"],
)
def test_T124_the_validator_would_notice(break_it):
    """The control. A validator whose only evidence is a green suite is a validator nothing
    exercises."""
    document = runner_module.run(
        adapters=[NOT_RETRYING], scenarios=[scenario_module.DOUBLE_REFUND_SCENARIO]
    )
    break_it(document)

    with pytest.raises(results_module.InvalidResults):
        results_module.validate(document)


def test_T124_the_markdown_table_has_one_row_per_framework_and_is_rendered_from_the_json():
    document = runner_module.run()
    table = results_module.to_markdown(document)

    rendered = [line for line in table.split("\n") if line.startswith("| ") and "---" not in line]
    header, *rows = rendered
    names = {row.split("|")[1].strip() for row in rows}

    assert "framework" in header and "config_deviation" in header
    assert names == {row["framework"] for row in document["results"]}
    assert "Do not edit by hand" in table
    assert "behaviour, not quality" in table
    # A deviation reaches the table as a column, not a footnote.
    for row in document["results"]:
        if row["config_deviation"]:
            assert row["config_deviation"] in table


def test_T124_no_results_are_checked_in():
    """§7.4 — item 6 ships the harness and no results. A PR carrying findings about other
    projects that nobody had reviewed is not a PR this repository makes."""
    checked_in = subprocess.run(
        ["git", "ls-files", "research/framework-probe/results"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if checked_in.returncode != 0:  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    tracked = [name for name in checked_in.stdout.split() if not name.endswith(".gitkeep")]

    assert tracked == [], tracked


def test_T124_the_runner_writes_both_files(tmp_path):
    document_path = tmp_path / "results.json"
    table_path = tmp_path / "TABLE.md"

    assert (
        runner_module.main(
            ["--out", str(document_path), "--markdown", str(table_path), "--no-stubs"]
        )
        == 0
    )

    document = json.loads(document_path.read_text(encoding="utf-8"))
    results_module.validate(document)
    assert "stub-retrying" not in {row["framework"] for row in document["results"]}
    assert table_path.read_text(encoding="utf-8").startswith("<!-- Rendered from")


# --- T124b: the harness is not part of the package -----------------------------------------


def test_T124b_research_is_not_importable_from_an_installed_ctrlrun():
    """In a subprocess, with the repository root off `sys.path`, so a checkout on the path
    cannot make this pass."""
    script = (
        "import sys\n"
        "sys.path = [p for p in sys.path if p not in ('', '.')]\n"
        "import ctrlrun\n"
        "for name in ('research', 'framework_probe'):\n"
        "    try:\n"
        "        __import__(name)\n"
        "    except ImportError:\n"
        "        continue\n"
        "    raise SystemExit(f'{name} is importable')\n"
        "print('ok')\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, f"{finished.stdout}\n{finished.stderr}"
    assert finished.stdout.strip() == "ok"


def test_T124b_ctrlrun_names_no_framework_in_its_dependencies_or_extras():
    import tomllib

    document = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = document["project"]
    extras = [item for group in project["optional-dependencies"].values() for item in group]
    declared = " ".join([*project["dependencies"], *extras]).lower()

    for framework in ("langgraph", "langchain", "crewai", "openai-agents", "autogen"):
        assert framework not in declared, framework


def test_T124b_the_package_ships_nothing_from_research():
    import tomllib

    document = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert document["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "research" not in manifest or "prune research" in manifest


# --- the fairness rules are in the README, and they are normative ---------------------------


def test_the_readme_leads_with_behaviour_not_quality():
    """§7.3 rule 6 — in the **first** paragraph, because that is the sentence a reader who
    only skims the table will have seen."""
    readme = (PROBE_ROOT / "README.md").read_text(encoding="utf-8")
    first = readme.split("\n\n")[1]

    assert "behaviour, not quality" in first


def test_the_readme_cites_each_framework_with_a_link_and_the_date_read():
    readme = (PROBE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "2026-09-04" in readme
    for framework in ("langgraph", "crewai", "openai-agents", "autogen-agentchat"):
        assert framework in readme, framework
    for link in (
        "https://langchain-ai.github.io/langgraph/",
        "https://docs.crewai.com/",
        "https://openai.github.io/openai-agents-python/",
        "https://microsoft.github.io/autogen/stable/",
        "https://modelcontextprotocol.io/",
    ):
        assert link in readme, link
    # And what could not be established, said rather than implied.
    assert "could not be established from primary documentation" in readme


def test_the_scenario_text_is_shared_and_not_per_adapter():
    """§7.3 rule 2 — byte-identical across adapters, asserted by there being one copy of it.

    Three frameworks read a tool's description from its docstring. Each adapter therefore sets
    `__doc__` from the scenario rather than writing the sentence again: a second copy is a diff
    nobody recorded, and this test is what caught the first three.
    """
    import inspect

    for adapter in all_adapters():
        source = inspect.getsource(sys.modules[type(adapter).__module__])
        assert scenario_module.TOOL_DESCRIPTION not in source, adapter.name
        assert scenario_module.DOUBLE_REFUND_SCENARIO.prompt not in source, adapter.name


# --- nothing anywhere claims the harness has been run against a real framework ---------------

#: The four whose adapters have never been executed. The stubs and the MCP client have — they
#: run in this repository's CI, end to end over a loopback socket.
UNEXECUTED = ("langgraph", "crewai", "openai-agents", "autogen")


def test_the_readme_says_the_framework_adapters_have_never_been_executed():
    """The study means nothing until the adapters have run, so the sentence that says they have
    not is at the top of the harness README rather than in a per-adapter docstring nobody
    reads. `results/` is empty for the same reason."""
    readme = " ".join((PROBE_ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "The four framework adapters have never been executed" in readme
    assert "adapters written from documented APIs, unexecuted" in readme
    assert "no README, changelog entry or post may say otherwise" in readme


@pytest.mark.parametrize("framework", UNEXECUTED)
def test_every_unexecuted_adapter_says_so_in_its_own_docstring(framework):
    module = __import__(
        f"framework_probe.adapters.{framework.replace('-', '_')}", fromlist=["ADAPTER"]
    )

    docstring = " ".join((module.__doc__ or "").lower().split())

    assert "never executed" in docstring, framework
    assert "not run in this repository's ci" in docstring, framework


def test_no_top_level_document_claims_the_harness_was_run_against_a_framework():
    """A sentence naming one of these four outside the harness's own directory is the shape of
    an overclaim, so there are none — and this test is what keeps it that way when somebody
    writes the release post."""
    checked = ["README.md", "CHANGELOG.md", "docs/verify.md", "docs/OWASP-AGENTIC-TOP10.md"]

    offending = []
    for name in checked:
        path = REPO_ROOT / name
        if not path.exists():  # pragma: no cover - not a checkout
            continue
        text = path.read_text(encoding="utf-8").lower()
        offending += [(name, framework) for framework in UNEXECUTED if framework in text]

    assert not offending, offending


def test_the_results_directory_holds_nothing_but_its_placeholder():
    present = sorted(path.name for path in (PROBE_ROOT / "results").iterdir())

    assert present == [".gitkeep"], present

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

import inspect
import json
import re
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


def test_T124_the_only_results_checked_in_are_the_published_run():
    """§7.4 — `results/` was empty through v0.4 because nothing had been run. v0.5's item 1
    is the item that publishes, and it publishes one run: the dated pair the maintainer read.
    The rule that survives is that no *other* results file rides along, so a second run made
    by a session and never read cannot reach the repository under cover of the first.

    This reads the git index, where the companion on-disk check reads the directory. The two
    disagree exactly when `.gitignore` has swallowed a file, which is how v0.2 shipped four
    policy files that every green build had already accounted for."""
    checked_in = subprocess.run(
        ["git", "ls-files", "research/framework-probe/results"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if checked_in.returncode != 0:  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    tracked = sorted(
        name.rsplit("/", 1)[-1]
        for name in checked_in.stdout.split()
        if not name.endswith(".gitkeep")
    )

    assert tracked == ["2026-09-05.json", "2026-09-05.md"], tracked


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

#: The two whose adapters have still never been run against a model, and never executed at all.
#: The stubs and the MCP client have — they run in this repository's CI, end to end over a
#: loopback socket — and LangGraph and the Agents SDK were run against `gpt-4o-mini` on
#: 2026-09-05, which is what `results/` holds.
NEVER_EXECUTED = ("crewai", "autogen")

#: The two that were, with the version each was run against. Read at runtime everywhere else
#: (T123); written here because the *claim* is about a specific past run, and a claim about a
#: past run cannot be re-derived from what happens to be installed now.
MEASURED = {"langgraph": "1.2.11", "openai-agents": "0.22.0"}

#: The two that reached the model call, with the version each was executed against. Read at
#: runtime everywhere else (T123); written here because the *claim* in the README is about a
#: specific past run, and a claim about a past run cannot be re-derived from what happens to be
#: installed now.


def test_the_readme_says_which_two_were_run_and_which_two_were_not():
    """The claim that matters most is the negative one, and it is in the leading blockquote.

    Two of four were measured; two were not, and nothing in this file is a finding about those
    two. A sentence that let a reader carry "the frameworks were measured" away from a run that
    covered half of them is the shape of an overclaim, and this is what stops it."""
    block = leading_blockquote()

    assert "Two of the four framework adapters have now been run against a model" in block
    assert "Two have not" in block
    assert "were not installed and remain unexecuted in every sense" in block
    assert "Nothing in this file is a finding about either of them" in block


def leading_blockquote() -> str:
    """The README's opening blockquote, as one whitespace-collapsed string.

    Scoped to the block rather than to the file because "in the same paragraph" is the claim,
    and a substring search over the whole README passes with the bounding sentence moved to the
    bottom — which is the exact failure the assertion is meant to prevent.
    """
    lines = (PROBE_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    quoted = [line[1:] for line in lines if line.startswith(">")]
    assert quoted, "the README's leading blockquote is gone"
    return " ".join(" ".join(quoted).split())


def test_the_measured_run_states_its_date_its_versions_and_its_repetitions():
    """A published finding about somebody else's project carries what would let a reader
    reproduce it or date it: which versions, which model, how many times, and when."""
    block = leading_blockquote()

    assert "2026-09-05" in block
    assert "gpt-4o-mini" in block
    assert "five repetitions" in block
    for framework, version in MEASURED.items():
        assert version in block, framework


def test_the_readme_says_what_the_run_does_not_establish():
    """One model, one prompt, one remote. A table with other projects' names in it has to say
    what it did not vary, in the same document and not in a reply to a complaint."""
    readme = " ".join((PROBE_ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "What this run does not establish" in readme
    assert "One model, one prompt, one remote" in readme


def test_the_readme_explains_that_executed_once_hides_the_mutation():
    """`executed_once` in the approval-mutation column means the *mutated* action ran, which
    reads like the opposite of what it is. `outcome` is a closed set fixed by SPEC-v0.4 §7.4
    and has no value for "the mutation landed", so the README is where that is said."""
    readme = " ".join((PROBE_ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "does **not** mean the scenario was handled well" in readme
    assert "the human never approved" in readme


@pytest.mark.parametrize("framework", NEVER_EXECUTED)
def test_a_never_executed_adapter_makes_the_stronger_claim_in_its_docstring(framework):
    module = __import__(
        f"framework_probe.adapters.{framework.replace('-', '_')}", fromlist=["ADAPTER"]
    )

    docstring = " ".join((module.__doc__ or "").lower().split())

    assert "never executed at all" in docstring, framework


WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def test_the_readme_agrees_with_itself_about_how_many_defects_the_run_found():
    """Two sentences counted the findings and disagreed, in the one section whose whole purpose
    is precision about what the run established. A number written twice must be written twice
    the same, and it must be the number of rows in the table it introduces."""
    readme = (PROBE_ROOT / "README.md").read_text(encoding="utf-8")
    rows = [line for line in readme.splitlines() if re.match(r"^\| \d+ \|", line)]
    written = {
        WORDS[word.lower()]
        for word in re.findall(r"(\w+) defects", readme)
        if word.lower() in WORDS
    }

    assert rows, "the findings table is gone"
    assert written == {len(rows)}, (written, len(rows))


@pytest.mark.parametrize("framework", NEVER_EXECUTED)
def test_an_unexecuted_adapter_says_so_in_its_own_docstring(framework):
    module = __import__(
        f"framework_probe.adapters.{framework.replace('-', '_')}", fromlist=["ADAPTER"]
    )

    docstring = " ".join((module.__doc__ or "").lower().split())

    assert "never run against a model" in docstring, framework
    assert "never executed at all" in docstring, framework


@pytest.mark.parametrize("framework", sorted(MEASURED))
def test_a_measured_adapter_says_which_version_and_which_model_it_was_run_on(framework):
    """A measurement is a claim about a specific installation on a specific day, so the
    docstring names it. Without the version the sentence reads as a standing property of the
    adapter, and it is not: the next breaking release can invalidate it, and then the only
    thing that would say so is a version that is no longer the one installed."""
    module = __import__(
        f"framework_probe.adapters.{framework.replace('-', '_')}", fromlist=["ADAPTER"]
    )

    docstring = " ".join((module.__doc__ or "").split())

    assert "2026-09-05" in docstring, framework
    assert MEASURED[framework] in docstring, framework
    assert "gpt-4o-mini" in docstring, framework


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
        offending += [(name, framework) for framework in NEVER_EXECUTED if framework in text]

    assert not offending, offending


def test_the_results_directory_holds_a_run_and_the_table_rendered_from_it():
    """`results/` was empty on purpose until there was a run somebody had read. There is one
    now, and the Markdown beside it is rendered from the JSON and never written by hand -- so
    the assertion is that the two agree, not that a file exists."""
    import json

    from framework_probe.results import to_markdown

    present = sorted(path.name for path in (PROBE_ROOT / "results").iterdir())
    assert present == [".gitkeep", "2026-09-05.json", "2026-09-05.md"], present

    document = json.loads((PROBE_ROOT / "results" / "2026-09-05.json").read_text())
    rendered = (PROBE_ROOT / "results" / "2026-09-05.md").read_text()

    assert rendered == to_markdown(document) + "\n", (
        "the table on disk is not what the JSON renders to; it was edited by hand, or the "
        "renderer changed and the table was not regenerated"
    )


def test_the_published_run_is_what_the_readme_says_it_is():
    """The README's numbers and the results file must not drift apart. A README quoting a run
    that is no longer the one checked in is the shape of every stale finding."""
    import json

    document = json.loads((PROBE_ROOT / "results" / "2026-09-05.json").read_text())
    by_cell = {(row["framework"], row["scenario"]): row for row in document["results"]}

    assert by_cell[("langgraph", "double-refund")]["outcome"] == "executed_once"
    assert by_cell[("openai-agents", "double-refund")]["outcome"] == "executed_twice"
    # The pair that proves the harness can tell the two apart (SPEC-v0.4 §7, the stubs).
    assert by_cell[("stub-retrying", "double-refund")]["outcome"] == "executed_twice"
    assert by_cell[("stub-not-retrying", "double-refund")]["outcome"] == "executed_once"
    for framework in ("crewai", "autogen"):
        assert "not installed" in by_cell[(framework, "double-refund")]["notes"]
    for row in document["results"]:
        if row["framework"] in MEASURED:
            assert "5 runs" in row["notes"], row


# --- What running the harness against real installations found (item 1) --------------------


def test_one_model_string_is_shared_by_every_adapter():
    """§7.3 rule 2. `openai:gpt-4o-mini` here and `gpt-4o-mini` there meant one
    `CTRLRUN_PROBE_MODEL` could not satisfy both, and the values happening to coincide today is
    not the same as there being one of them."""
    from framework_probe.adapters._framework import PROBE_MODEL

    driven = ("langgraph", "openai_agents", "crewai", "autogen")
    for name in driven:
        module = __import__(f"framework_probe.adapters.{name}", fromlist=["MODEL"])
        assert module.MODEL is PROBE_MODEL, name
        assert "os.environ" not in inspect.getsource(module), (
            f"{name} reads CTRLRUN_PROBE_MODEL for itself; that is the drift the shared "
            "constant exists to prevent"
        )


def test_an_adapter_declares_every_distribution_its_run_imports():
    """§7.3 rule 5's "skipped **by name**" is only reachable if `available()` knows what `run()`
    needs. It did not: with `langchain` absent, the LangGraph adapter said it was available,
    raised `ImportError`, and the table carried a row named `langgraph`, with LangGraph's
    version, whose outcome was `error` — the harness's own environment reported as a framework
    that broke."""
    from importlib.metadata import packages_distributions

    from framework_probe.adapters import all_adapters

    #: Top-level module -> the distributions that provide it, read from the installed
    #: environment rather than guessed: `openai-agents` installs `agents`, and a test that
    #: assumed the two names matched would pass for the wrong reason on every adapter whose
    #: names happen to agree.
    provided_by = packages_distributions()

    for adapter in all_adapters():
        source = inspect.getsource(adapter.run)
        imported = set(re.findall(r"^\s+from (\w+)[. ]", source, re.M))
        imported |= set(re.findall(r"^\s+import (\w+)", source, re.M))
        declared = {adapter.distribution, *getattr(adapter, "requires", ())}
        named = set(getattr(adapter, "modules", ()))
        for module in imported:
            if module == "framework_probe":
                continue
            providers = set(provided_by.get(module, ()))
            if providers:
                # Installed here, so the real mapping is readable and is what gets asserted.
                # It also keeps `modules` honest: a declaration that contradicted the
                # environment would fail on a developer machine that has the framework.
                assert not (module in named and not providers & declared), (
                    f"{adapter.name} declares {module!r} in `modules`, but the installed "
                    f"environment says it comes from {sorted(providers)}, none of which is "
                    f"declared {sorted(declared)}"
                )
            elif module in named:
                # Not installed, so the mapping cannot be read and the adapter's own
                # declaration stands in. This is the CI path: none of these frameworks is a
                # dependency of this repository, so nothing here is installed.
                continue
            else:
                # Neither installed nor declared. Falling back to `module.replace("_", "-")`
                # here is what made this test pass locally and fail in CI: it assumes the
                # import name and the distribution name agree, which is the assumption this
                # test exists to refuse. `openai-agents` installs `agents` and they do not.
                providers = {module.replace("_", "-")}
            assert providers & declared, (
                f"{adapter.name}.run() imports {module!r} (from {sorted(providers)}), which is "
                f"in neither `distribution` nor `requires` {sorted(declared)} — a missing "
                f"dependency would be reported as a framework that broke"
            )


def test_is_installed_is_all_of_them_and_not_any():
    from framework_probe.adapters.base import is_installed

    assert is_installed("ctrlrun") is True
    assert is_installed("ctrlrun", "no-such-distribution-9e1f") is False
    assert is_installed("no-such-distribution-9e1f") is False


def test_an_error_row_carries_the_exception_and_a_successful_row_does_not():
    """Both halves. The second is the one with a failure mode: a stub whose response was
    deliberately lost has an exception too, and repeating it beside `executed_once` would make
    every successful row read like a broken one."""
    from framework_probe.adapters.base import Attempt
    from framework_probe.results import ERROR, EXECUTED_ONCE
    from framework_probe.runner import _notes

    assert _notes(Attempt(error="Boom: why"), ERROR) == "Boom: why"
    assert _notes(Attempt(error="Boom: why", notes="context"), ERROR) == "context; Boom: why"
    assert _notes(Attempt(error="Boom: why", notes="context"), EXECUTED_ONCE) == "context"
    assert _notes(Attempt(notes="context"), ERROR) == "context"
    assert _notes(Attempt(), ERROR) == ""


def test_a_frameworks_exception_text_cannot_restructure_the_table():
    """`notes` used to be written only by this harness's authors. It now carries a measured
    framework's exception string, and a `|` or a newline in one of those does not make an ugly
    cell — it makes a *different table*: the row gains a phantom column and then terminates."""
    from framework_probe.results import SCHEMA, to_markdown

    document = {
        "schema": SCHEMA,
        "run_at": "2026-09-04T12:00:00+00:00",
        "python": "3.12.3",
        "remote": "fake-mcp/1",
        "results": [
            {
                "framework": "nasty",
                "version": "1.0",
                "adapter": "research/framework-probe/framework_probe/adapters/nasty.py",
                "scenario": scenario,
                "outcome": "error",
                "effects_observed": 0,
                "requests_observed": 0,
                "config_deviation": None,
                "notes": "ValueError: boom | pipe and\nnewline here",
            }
            for scenario in ("double-refund", "approval-mutation")
        ],
    }

    table = to_markdown(document)
    body = [line for line in table.splitlines() if line.startswith("| nasty")]

    assert len(body) == 1, table
    assert len(re.findall(r"(?<!\\)\|", body[0])) == 7, body[0]
    assert "\n" not in body[0]


def test_a_very_long_note_does_not_run_away_with_the_row():
    from framework_probe.results import NOTE_LIMIT, cell

    rendered = cell("x" * (NOTE_LIMIT * 3))

    assert len(rendered) == NOTE_LIMIT
    assert rendered.endswith("…")

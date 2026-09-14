# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the HB1603 dive-profile recipes, chiefly their tier split.

The workflow's whole economy rests on one property: the survey tier must hash
identically in survey_preprocess.yaml and dive_profiles.yaml, so that building
the shared Sv store once makes every survey step a cache hit in the analysis
run. If that breaks, each analysis silently recomputes Sv for a 3322-file
cruise, and nothing about the output looks wrong.

It is easy to break by accident, because a step's hash folds in its parents'.
Reordering an include, adding a param to a shared sub-recipe, or letting
crop_max_range_m drift between the two files is enough. These tests fail loudly
when it happens.
"""

from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.api import _load_dag
from aa_recipe_manager.executor.checkpoint import compute_step_hashes

RECIPES = (
    Path(__file__).resolve().parent.parent / "examples" / "HB1603" / "UC1"
)
SURVEY_RECIPE = RECIPES / "survey_preprocess.yaml"
ANALYSIS_RECIPE = RECIPES / "dive_profiles.yaml"
FULL_RECIPE = RECIPES / "full_analysis.yaml"

#: The dive full_analysis.yaml leaves out, whose dive data is incorrect.
EXCLUDED_DIVE = "SWD_20160703-OE20"

#: Every step at or above the shared Sv checkpoint.
SURVEY_STEPS = (
    "query_ncei",
    "scan_raw_config",
    "record_raw_configs",
    "standardize_cal",
    "build_cal_mapping",
    "read_raw",
    "combine_raw",
    "extract_cal_params",
    "compute_sv",
    "compute_transducer_depth",
    "ep_add_depth",
    "crop_survey_range",
    "merge_survey_sv",
    "rechunk_survey_sv",
)

#: Steps that only exist below it.
ANALYSIS_ONLY_STEPS = (
    "plan_dives",
    "select_window",
    "compute_mvbs",
    "merge_dive_mvbs",
    "run_hdbscan",
    "label_all_points",
    "generate_sv_codes",
    "sv_code_depth_table",
)

# The dive configs and line files live outside this repo.
requires_data = pytest.mark.skipif(
    not (RECIPES / "sub_recipes" / "survey_sv.yaml").exists()
    or not (RECIPES.parents[3] / "NEFSC_UC1" / "full_data" / "Auxiliary").exists(),
    reason="HB1603 dive-profile inputs not available",
)


def _hashes(recipe, inputs=None, monkeypatch=None):
    """Step hashes for a recipe, resolved from its own directory.

    The recipes use paths relative to themselves, which is the convention the
    other example recipes follow.
    """
    inputs = inputs or {}
    dag = _load_dag(recipe, input_values=inputs, check_versions=False)
    return compute_step_hashes(dag, inputs)


@pytest.fixture
def in_recipe_dir(monkeypatch):
    monkeypatch.chdir(RECIPES)


# ---------------------------------------------------------------------------
# Both recipes are valid
# ---------------------------------------------------------------------------


@requires_data
def test_survey_recipe_validates(in_recipe_dir):
    report = api.dry_run(str(SURVEY_RECIPE))
    assert not report.errors


@requires_data
def test_analysis_recipe_validates(in_recipe_dir):
    report = api.dry_run(str(ANALYSIS_RECIPE))
    assert not report.errors


@requires_data
def test_full_analysis_recipe_validates(in_recipe_dir):
    report = api.dry_run(str(FULL_RECIPE))
    assert not report.errors


# ---------------------------------------------------------------------------
# The tier split
# ---------------------------------------------------------------------------


@requires_data
@pytest.mark.parametrize("analysis_recipe", [ANALYSIS_RECIPE, FULL_RECIPE])
def test_survey_tier_hashes_identically_in_both_recipes(in_recipe_dir, analysis_recipe):
    """The property the whole workflow's economy rests on."""
    survey = _hashes(str(SURVEY_RECIPE))
    analysis = _hashes(str(analysis_recipe))

    mismatched = {
        step: (survey.get(step), analysis.get(step))
        for step in SURVEY_STEPS
        if survey.get(step) is None or survey.get(step) != analysis.get(step)
    }
    assert not mismatched, (
        f"survey steps differ between survey_preprocess.yaml and "
        f"{analysis_recipe.name}, so the analysis run will recompute Sv for "
        f"the whole cruise: {sorted(mismatched)}"
    )


@requires_data
def test_the_analysis_recipe_actually_contains_its_own_tier(in_recipe_dir):
    """Guards the test above from passing because the steps are simply absent."""
    analysis = _hashes(str(ANALYSIS_RECIPE))
    for step in SURVEY_STEPS + ANALYSIS_ONLY_STEPS:
        assert step in analysis, f"{step} missing from the analysis recipe"


# ---------------------------------------------------------------------------
# full_analysis.yaml: the 13-dive production run
# ---------------------------------------------------------------------------


@requires_data
def test_full_analysis_names_every_dive_but_the_bad_one(in_recipe_dir):
    """The recipe states its study population, so a typo must not survive here.

    include_labels is only read by plan_dive_datasets, which runs below the
    survey tier. A misspelled label would therefore raise hours into a run,
    after the whole cruise had been through compute_sv. Checking the labels
    against the configs on disk turns that into a test failure.
    """
    dag = _load_dag(str(FULL_RECIPE), input_values={}, check_versions=False)
    labels = dag.nodes["plan_dives"].resolved_params["include_labels"]

    available = {p.stem for p in (RECIPES / "Auxiliary" / "JSON_Files").glob("SWD_*.json")}
    unknown = sorted(set(labels) - available)
    assert not unknown, f"include_labels names dives with no config: {unknown}"

    assert EXCLUDED_DIVE not in labels, (
        f"{EXCLUDED_DIVE} has incorrect dive data and must stay out of the "
        "pooled clustering, which has no way to quarantine one bad dive"
    )
    # Every single-dive config except the excluded one. The combined
    # SWD_20160707-OE20-OE29-OE32 view is not a dive and plan_dive_datasets
    # skips it, so it is absent from both sides.
    expected = available - {EXCLUDED_DIVE, "SWD_20160707-OE20-OE29-OE32"}
    assert set(labels) == expected, (
        "the analysis population has drifted from the configs on disk: "
        f"missing {sorted(expected - set(labels))}, extra {sorted(set(labels) - expected)}"
    )


@requires_data
def test_choosing_the_dives_does_not_disturb_the_survey_tier(in_recipe_dir):
    """Naming dives is an analysis input and must stay below the checkpoint."""
    base = _hashes(str(FULL_RECIPE))
    fewer = _hashes(str(FULL_RECIPE), {"dive_labels": ["SWD_20160719-OE07"]})

    disturbed = [s for s in SURVEY_STEPS if base.get(s) != fewer.get(s)]
    assert not disturbed, (
        f"changing dive_labels re-hashed survey steps {disturbed}; the dive "
        "selection has leaked above the shared checkpoint"
    )
    assert base["plan_dives"] != fewer["plan_dives"], (
        "dive_labels changed nothing at all, so it is not reaching the planner"
    )


@requires_data
def test_full_analysis_plots_on_a_bin_axis(in_recipe_dir):
    """A clock-time axis would squeeze all 13 dives into a hairline.

    The pooled fan-in concatenates on real ping_time, and the dives carry 214
    minutes spread across 38 days. Both "datetime" and "seconds" draw that span
    literally, so an echogram of the combined set is 0.4% data. "bins" plots
    against MVBS bin index, which the empty time between dives never entered.
    """
    dag = _load_dag(str(FULL_RECIPE), input_values={}, check_versions=False)
    plotting = {
        node_id: node
        for node_id, node in dag.nodes.items()
        if str(node.step.op).startswith("plot_")
    }
    assert plotting, "expected the pooled echograms to be wired up"

    wrong = {
        node_id: node.resolved_params.get("x_axis_units")
        for node_id, node in plotting.items()
        if node.resolved_params.get("x_axis_units") not in ("bins", "pings")
    }
    assert not wrong, (
        "these plots use a clock-time x axis over a 38-day span, so every dive "
        f"collapses to a hairline: {wrong}"
    )


@requires_data
def test_the_mvbs_plot_has_its_ping_reference(in_recipe_dir):
    """plot_sv_echogram raises on MVBS data when ds_Sv_source is absent.

    ping_min and ping_max index the original Sv ping axis, and ds_Sv_source is
    what converts them onto the MVBS grid, so the op refuses to guess. Leaving
    it unwired fails only once the run reaches the figure, hours in.
    """
    dag = _load_dag(str(FULL_RECIPE), input_values={}, check_versions=False)
    sources = {
        edge.source_step_id
        for edge in dag.edges
        if edge.target_step_id == "plot_window_mvbs"
        and edge.target_input == "ds_Sv_source"
    }
    assert sources == {"merge_window_sv"}, (
        "plot_window_mvbs plots MVBS and needs ds_Sv_source wired to the "
        f"pre-MVBS Sv fan-in; found {sources or 'nothing'}"
    )


@requires_data
def test_a_dive_input_does_not_disturb_the_survey_tier(in_recipe_dir):
    """A window is allowed to change the analysis and nothing above it."""
    base = _hashes(str(ANALYSIS_RECIPE))
    moved = _hashes(str(ANALYSIS_RECIPE), {"pad_minutes": 5.0})

    disturbed = [s for s in SURVEY_STEPS if base.get(s) != moved.get(s)]
    assert not disturbed, (
        f"changing pad_minutes re-hashed survey steps {disturbed}; a dive input "
        "has leaked above the shared checkpoint"
    )
    assert base["plan_dives"] != moved["plan_dives"], (
        "pad_minutes changed nothing at all, so it is not reaching the planner"
    )


@requires_data
def test_the_survey_ceiling_invalidates_the_crop_and_below_only(in_recipe_dir):
    """Raising the ceiling re-crops and re-merges; it does not recompute Sv."""
    base = _hashes(str(ANALYSIS_RECIPE))
    raised = _hashes(str(ANALYSIS_RECIPE), {"crop_max_range_m": 2000.0})

    changed = {s for s in SURVEY_STEPS if base.get(s) != raised.get(s)}
    assert changed == {"crop_survey_range", "merge_survey_sv", "rechunk_survey_sv"}
    # The expensive half is above the crop and must survive.
    assert base["compute_sv"] == raised["compute_sv"]
    assert base["read_raw"] == raised["read_raw"]


@requires_data
def test_the_chunk_size_only_touches_the_rechunk(in_recipe_dir):
    base = _hashes(str(ANALYSIS_RECIPE))
    rechunked = _hashes(str(ANALYSIS_RECIPE), {"survey_chunk_ping_time": 2000})

    changed = {s for s in SURVEY_STEPS if base.get(s) != rechunked.get(s)}
    assert changed == {"rechunk_survey_sv"}


# ---------------------------------------------------------------------------
# Chain contiguity
# ---------------------------------------------------------------------------


@requires_data
@pytest.mark.parametrize(
    "recipe", [SURVEY_RECIPE, ANALYSIS_RECIPE, FULL_RECIPE, RECIPES / "smoke_test.yaml"]
)
def test_the_per_file_chain_stays_one_chain(in_recipe_dir, recipe):
    """read_raw through crop_survey_range must run as a single mapped chain.

    The calibration steps are independent of the per-file chain, so a
    breadth-first topological order used to place them between read_raw and
    combine_raw. That split the chain, and combine_raw then received every
    file's store instead of its own, which surfaced downstream as
    extract_standardized_calibration_parameters indexing a list of EchoData
    with a string.
    """
    from aa_recipe_manager.parallel import group_mapped_chains

    dag = _load_dag(str(recipe), input_values={}, check_versions=False)
    chains = {c.member_ids[0]: c.member_ids for c in group_mapped_chains(dag)}
    assert chains["scan_raw_config"] == ["scan_raw_config"]
    assert chains["read_raw"] == [
        "read_raw",
        "combine_raw",
        "extract_cal_params",
        "compute_sv",
        "compute_transducer_depth",
        "ep_add_depth",
        "crop_survey_range",
    ]


@requires_data
@pytest.mark.parametrize(
    "recipe", [SURVEY_RECIPE, ANALYSIS_RECIPE, FULL_RECIPE, RECIPES / "smoke_test.yaml"]
)
def test_transmit_power_tolerance_is_wide_enough_for_leg_one(in_recipe_dir, recipe):
    """HB1603 ran 18/38 kHz at 2000 W on leg 1 and the only .cal is 1000 W.

    Without the widened tolerance those two channels match nothing and four of
    the fourteen dives lose the frequencies the SVCode analysis is built on.
    1000 W is the smallest tolerance that admits the pair, so a smaller one is
    the same as none at all.
    """
    dag = _load_dag(str(recipe), input_values={}, check_versions=False)
    tolerances = dag.nodes["build_cal_mapping"].resolved_params["tolerances"]
    assert tolerances["transmit_power"] >= 1000.0

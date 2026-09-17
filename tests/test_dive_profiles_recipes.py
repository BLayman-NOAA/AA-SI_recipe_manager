# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the HB1603 dive-profile recipes, chiefly their tier split.

The workflow's whole economy rests on one property: the survey tier must hash
identically in survey_preprocess.yaml and full_analysis.yaml, so that building
the shared store once makes every survey step a cache hit in the analysis run,
and the per-file half must hash identically in sv_window_example.yaml too, so
a subset analysis reuses the per-file Sv. If that breaks, an analysis silently
recomputes Sv for a 3322-file cruise, and nothing about the output looks wrong.

It is easy to break by accident, because a step's hash folds in its parents'.
Reordering an include, adding a param to a shared sub-recipe, or letting
crop_max_range_m drift between the files is enough. These tests fail loudly
when it happens.

The recipes live in the AA-SI_Full_Pipeline_Example repo, a sibling of this
one in the workspace; the tests skip when it is not checked out alongside.
"""

from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.api import _load_dag
from aa_recipe_manager.executor.checkpoint import compute_step_hashes
from aa_recipe_manager.parallel import group_mapped_chains

RECIPES = (
    Path(__file__).resolve().parents[2]
    / "AA-SI_Full_Pipeline_Example" / "example_recipes" / "HB1603" / "UC1"
)
SURVEY_RECIPE = RECIPES / "survey_preprocess.yaml"
FULL_RECIPE = RECIPES / "full_analysis.yaml"
WINDOW_RECIPE = RECIPES / "sv_window_example.yaml"

#: The dive full_analysis.yaml leaves out, whose dive data is incorrect.
EXCLUDED_DIVE = "SWD_20160703-OE20"

#: Every step of the per-file chain, through the checkpointed per-file Sv.
PER_FILE_STEPS = (
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
)

#: The per-file grid and the fan-ins that complete the shared store.
GRID_STEPS = (
    "survey_surface_mask",
    "survey_frequency_mask",
    "survey_combine_masks",
    "survey_apply_mask",
    "survey_remove_noise",
    "survey_mask_sparse",
    "survey_file_mvbs",
    "survey_cell_stats",
    "merge_survey_mvbs",
    "rechunk_survey_mvbs",
    "merge_survey_cell_stats",
)

SURVEY_STEPS = PER_FILE_STEPS + GRID_STEPS

#: Steps that only exist below the survey tier.
ANALYSIS_ONLY_STEPS = (
    "plan_dives",
    "select_window",
    "merge_dive_mvbs",
    "merge_dive_cell_stats",
    "run_hdbscan",
    "label_all_points",
    "generate_sv_codes",
    "sv_code_depth_table",
)

requires_data = pytest.mark.skipif(
    not FULL_RECIPE.exists()
    or not (RECIPES / "Auxiliary" / "JSON_Files").exists(),
    reason="HB1603 UC1 recipes not checked out alongside this repo",
)


def _hashes(recipe, inputs=None):
    """Step hashes for a recipe, resolved from its own directory."""
    inputs = inputs or {}
    dag = _load_dag(str(recipe), input_values=inputs, check_versions=False)
    return compute_step_hashes(dag, inputs)


def _dag(recipe, inputs=None):
    return _load_dag(str(recipe), input_values=inputs or {}, check_versions=False)


@pytest.fixture
def in_recipe_dir(monkeypatch):
    monkeypatch.chdir(RECIPES)


# ---------------------------------------------------------------------------
# All three recipes are valid
# ---------------------------------------------------------------------------


@requires_data
@pytest.mark.parametrize("recipe", [SURVEY_RECIPE, FULL_RECIPE, WINDOW_RECIPE])
def test_recipe_validates(in_recipe_dir, recipe):
    report = api.dry_run(str(recipe))
    assert not report.errors


# ---------------------------------------------------------------------------
# The tier split
# ---------------------------------------------------------------------------


@requires_data
def test_survey_tier_hashes_identically_in_preprocess_and_analysis(in_recipe_dir):
    """The property the whole workflow's economy rests on."""
    survey = _hashes(SURVEY_RECIPE)
    analysis = _hashes(FULL_RECIPE)

    mismatched = sorted(
        step for step in SURVEY_STEPS
        if survey.get(step) is None or survey.get(step) != analysis.get(step)
    )
    assert not mismatched, (
        "survey steps differ between survey_preprocess.yaml and "
        f"full_analysis.yaml, so the analysis run will recompute the survey: "
        f"{mismatched}"
    )


@requires_data
def test_per_file_chain_hashes_identically_in_the_window_example(in_recipe_dir):
    """A subset analysis must find the per-file Sv the survey run wrote."""
    analysis = _hashes(FULL_RECIPE)
    window = _hashes(WINDOW_RECIPE)

    mismatched = sorted(
        step for step in PER_FILE_STEPS
        if window.get(step) is None or window.get(step) != analysis.get(step)
    )
    assert not mismatched, (
        "per-file steps differ between full_analysis.yaml and "
        f"sv_window_example.yaml, so the window run recomputes Sv: {mismatched}"
    )


@requires_data
def test_the_analysis_recipe_actually_contains_its_own_tier(in_recipe_dir):
    """Guards the tests above from passing because the steps are simply absent."""
    analysis = _hashes(FULL_RECIPE)
    for step in SURVEY_STEPS + ANALYSIS_ONLY_STEPS:
        assert step in analysis, f"{step} missing from full_analysis.yaml"


# ---------------------------------------------------------------------------
# What each shared input invalidates
# ---------------------------------------------------------------------------


def _survey_steps_from(step):
    return set(SURVEY_STEPS[SURVEY_STEPS.index(step):])


@requires_data
def test_the_survey_ceiling_invalidates_the_crop_and_below_only(in_recipe_dir):
    """Raising the ceiling re-crops and re-grids; it does not recompute Sv."""
    base = _hashes(FULL_RECIPE)
    raised = _hashes(FULL_RECIPE, {"crop_max_range_m": 2000.0})

    changed = {s for s in SURVEY_STEPS if base.get(s) != raised.get(s)}
    assert changed == _survey_steps_from("crop_survey_range")
    assert base["compute_sv"] == raised["compute_sv"]
    assert base["read_raw"] == raised["read_raw"]


@requires_data
def test_the_grid_extent_invalidates_the_grid_and_below_only(in_recipe_dir):
    """A new depth extent re-bins every file; the per-file Sv survives."""
    base = _hashes(FULL_RECIPE)
    deeper = _hashes(FULL_RECIPE, {"mvbs_range_var_max": "2010m"})

    changed = {s for s in SURVEY_STEPS if base.get(s) != deeper.get(s)}
    assert changed == {
        "survey_file_mvbs", "survey_cell_stats",
        "merge_survey_mvbs", "rechunk_survey_mvbs", "merge_survey_cell_stats",
    }
    assert base["crop_survey_range"] == deeper["crop_survey_range"]


@requires_data
def test_the_chunk_size_only_touches_the_rechunk(in_recipe_dir):
    base = _hashes(FULL_RECIPE)
    rechunked = _hashes(FULL_RECIPE, {"survey_chunk_ping_time": 2000})

    changed = {s for s in SURVEY_STEPS if base.get(s) != rechunked.get(s)}
    assert changed == {"rechunk_survey_mvbs"}


@requires_data
def test_choosing_the_dives_does_not_disturb_the_survey_tier(in_recipe_dir):
    """Naming dives is an analysis input and must stay below the checkpoint."""
    base = _hashes(FULL_RECIPE)
    fewer = _hashes(FULL_RECIPE, {"dive_labels": ["SWD_20160719-OE07"]})

    disturbed = [s for s in SURVEY_STEPS if base.get(s) != fewer.get(s)]
    assert not disturbed, (
        f"changing dive_labels re-hashed survey steps {disturbed}; the dive "
        "selection has leaked above the shared checkpoint"
    )
    assert base["plan_dives"] != fewer["plan_dives"], (
        "dive_labels changed nothing at all, so it is not reaching the planner"
    )


@requires_data
def test_a_dive_input_does_not_disturb_the_survey_tier(in_recipe_dir):
    """A window is allowed to change the analysis and nothing above it."""
    base = _hashes(FULL_RECIPE)
    moved = _hashes(FULL_RECIPE, {"pad_minutes": 5.0})

    disturbed = [s for s in SURVEY_STEPS if base.get(s) != moved.get(s)]
    assert not disturbed, (
        f"changing pad_minutes re-hashed survey steps {disturbed}; a dive input "
        "has leaked above the shared checkpoint"
    )
    assert base["plan_dives"] != moved["plan_dives"]


@requires_data
def test_a_window_input_does_not_disturb_the_per_file_chain(in_recipe_dir):
    """The window example must vary its window without touching the Sv."""
    base = _hashes(WINDOW_RECIPE)
    moved = _hashes(WINDOW_RECIPE, {"window_start": "2016-07-25T00:00:00"})

    disturbed = [s for s in PER_FILE_STEPS if base.get(s) != moved.get(s)]
    assert not disturbed, f"the window leaked above crop_survey_range: {disturbed}"
    assert base["window_sv"] != moved["window_sv"]


# ---------------------------------------------------------------------------
# full_analysis.yaml: the 13-dive production run
# ---------------------------------------------------------------------------


@requires_data
def test_full_analysis_names_every_dive_but_the_bad_one(in_recipe_dir):
    """The recipe states its study population, so a typo must not survive here.

    include_labels is only read by plan_dive_datasets, which runs below the
    survey tier. A misspelled label would therefore raise after the survey
    tier had run. Checking the labels against the configs on disk turns that
    into a test failure.
    """
    labels = _dag(FULL_RECIPE).nodes["plan_dives"].resolved_params["include_labels"]

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
def test_full_analysis_plots_on_a_bin_axis(in_recipe_dir):
    """A clock-time axis would squeeze all 13 dives into a hairline.

    The pooled fan-in concatenates on real ping_time, and the dives carry 214
    minutes spread across 38 days. Both "datetime" and "seconds" draw that span
    literally, so an echogram of the combined set is 0.4% data. "bins" plots
    against MVBS bin index, which the empty time between dives never entered.
    """
    dag = _dag(FULL_RECIPE)
    plotting = {
        node_id: node for node_id, node in dag.nodes.items()
        if str(node.step.op).startswith("plot_")
    }
    assert plotting, "expected the pooled report to be wired up"

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
def test_overlapping_dives_are_deduplicated_and_carry_no_lines(in_recipe_dir):
    """Four of the 13 dives overlap another in time.

    The shared minutes are the same survey cells cut twice, so both dive
    fan-ins must dedup with "first" or the merged index is duplicated and out
    of order. And no dive line may ride on the merged axis: two dives at one
    ping have two depths, so generate_sv_codes attaches each dive's lines to
    its own slice instead.
    """
    dag = _dag(FULL_RECIPE)
    for step in ("merge_dive_mvbs", "merge_dive_cell_stats"):
        assert dag.nodes[step].resolved_params.get("on_duplicate") == "first", step

    overlays = [n for n, node in dag.nodes.items() if node.step.op == "add_line_overlay"]
    assert not overlays, f"dive lines attached before the fan-in: {overlays}"
    assert dag.nodes["generate_sv_codes"].implementation is not None


@requires_data
def test_survey_fan_ins_average_the_boundary_bins(in_recipe_dir):
    """Per-file binning emits a boundary bin from both sides; they are averaged."""
    dag = _dag(FULL_RECIPE)
    for step in ("merge_survey_mvbs", "merge_survey_cell_stats"):
        assert dag.nodes[step].resolved_params.get("on_duplicate") == "mean", step


# ---------------------------------------------------------------------------
# Chain contiguity
# ---------------------------------------------------------------------------


@requires_data
@pytest.mark.parametrize(
    "recipe, tail",
    [
        (SURVEY_RECIPE, GRID_STEPS[:8]),
        (FULL_RECIPE, GRID_STEPS[:8]),
        (WINDOW_RECIPE, ("window_sv", "window_mvbs")),
    ],
)
def test_the_per_file_chain_stays_one_chain(in_recipe_dir, recipe, tail):
    """read_raw through the last per-file step must run as a single mapped chain.

    The calibration steps are independent of the per-file chain, so a
    breadth-first topological order once placed them between read_raw and
    combine_raw. That split the chain, and combine_raw then received every
    file's store instead of its own. A split anywhere has the same effect on
    whatever member follows it.
    """
    dag = _dag(recipe)
    chains = {c.member_ids[0]: c.member_ids for c in group_mapped_chains(dag)}
    assert chains["scan_raw_config"] == ["scan_raw_config"]
    assert chains["read_raw"] == list(PER_FILE_STEPS[5:]) + list(tail)


@requires_data
@pytest.mark.parametrize("recipe", [SURVEY_RECIPE, FULL_RECIPE, WINDOW_RECIPE])
def test_transmit_power_tolerance_is_wide_enough_for_leg_one(in_recipe_dir, recipe):
    """HB1603 ran 18/38 kHz at 2000 W on leg 1 and the only .cal is 1000 W.

    Without the widened tolerance those two channels match nothing and four of
    the dives lose the frequencies the SVCode analysis is built on. 1000 W is
    the smallest tolerance that admits the pair.
    """
    tolerances = _dag(recipe).nodes["build_cal_mapping"].resolved_params["tolerances"]
    assert tolerances["transmit_power"] >= 1000.0

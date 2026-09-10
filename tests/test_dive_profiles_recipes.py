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


# ---------------------------------------------------------------------------
# The tier split
# ---------------------------------------------------------------------------


@requires_data
def test_survey_tier_hashes_identically_in_both_recipes(in_recipe_dir):
    """The property the whole workflow's economy rests on."""
    survey = _hashes(str(SURVEY_RECIPE))
    analysis = _hashes(str(ANALYSIS_RECIPE))

    mismatched = {
        step: (survey.get(step), analysis.get(step))
        for step in SURVEY_STEPS
        if survey.get(step) is None or survey.get(step) != analysis.get(step)
    }
    assert not mismatched, (
        "survey steps differ between the two recipes, so the analysis run will "
        f"recompute Sv for the whole cruise: {sorted(mismatched)}"
    )


@requires_data
def test_the_analysis_recipe_actually_contains_its_own_tier(in_recipe_dir):
    """Guards the test above from passing because the steps are simply absent."""
    analysis = _hashes(str(ANALYSIS_RECIPE))
    for step in SURVEY_STEPS + ANALYSIS_ONLY_STEPS:
        assert step in analysis, f"{step} missing from the analysis recipe"


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
    "recipe", [SURVEY_RECIPE, ANALYSIS_RECIPE, RECIPES / "smoke_test.yaml"]
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
    "recipe", [SURVEY_RECIPE, ANALYSIS_RECIPE, RECIPES / "smoke_test.yaml"]
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

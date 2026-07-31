# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for explain-cache: per-step hit/miss diagnosis with payload diffs."""

from __future__ import annotations

from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.explain import explain_cache
from aa_recipe_manager.model.types import ExecutionHints

from test_executor import (  # noqa: F401  (helper scaffolding)
    _linear_inc_dag,
    _linear_multiply_dag,
    _sink_after_chain_dag,
    helper_module,
)


def _warm(dag, tmp_path, **kwargs):
    return SequentialExecutor().execute(
        dag,
        inputs={"seed": 1},
        user_cache_dir=tmp_path / "user_cache",
        checkpoint_mode="eager",
        **kwargs,
    )


def _by_step(explanation):
    return {step.step_id: step for step in explanation.steps}


class TestExplainCache:
    def test_hits_report_their_tier(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        _warm(dag, tmp_path)
        report = explain_cache(
            dag, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        steps = _by_step(report)
        assert all(step.status == "hit" for step in steps.values())
        assert all(step.tier == "user" for step in steps.values())
        assert "HIT" in report.format_text()

    def test_survey_tier_hit_reported(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        # Warm the *survey* root via a curated run.
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            user_cache_dir=tmp_path / "curator_cache",
            survey_cache_dir=tmp_path / "survey_cache",
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        report = explain_cache(
            dag,
            inputs={"seed": 1},
            user_cache_dir=tmp_path / "empty_user_cache",
            survey_cache_dir=tmp_path / "survey_cache",
        )
        steps = _by_step(report)
        assert all(step.status == "hit" for step in steps.values())
        assert all(step.tier == "survey" for step in steps.values())
        assert set(report.tiers) == {"user", "survey"}

    def test_param_change_names_exact_field(self, helper_module, tmp_path):
        dag = _linear_multiply_dag()
        _warm(dag, tmp_path)

        forked = _linear_multiply_dag().model_copy(deep=True)
        forked.nodes["scale"].step.params["factor"] = 5
        forked.nodes["scale"].resolved_params["factor"] = 5

        report = explain_cache(
            forked, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        steps = _by_step(report)
        assert steps["start"].status == "hit"
        assert steps["first"].status == "hit"
        assert steps["scale"].status == "miss"

        diffs = {d.path: d for d in steps["scale"].candidate.differences}
        assert diffs["fingerprint.params.factor"].stored == 2
        assert diffs["fingerprint.params.factor"].current == 5
        assert diffs["fingerprint.resolved_params.factor"].stored == 2
        # Only the param fields diverge — nothing else in the fingerprint.
        assert set(diffs) == {
            "fingerprint.params.factor",
            "fingerprint.resolved_params.factor",
        }
        assert "factor: 2 -> 5" in report.format_text().replace("'", "")

    def test_epoch_change_names_epoch_field(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        _warm(dag, tmp_path)

        bumped = _linear_inc_dag()
        bumped.recipe.execution = ExecutionHints(cache_epoch="2026-07")
        report = explain_cache(
            bumped, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        steps = _by_step(report)
        assert steps["start"].status == "miss"
        diffs = {d.path: d for d in steps["start"].candidate.differences}
        assert diffs["epoch"].stored is None
        assert diffs["epoch"].current == "2026-07"

    def test_upstream_only_change_names_parent_step(self, helper_module, tmp_path):
        dag = _linear_multiply_dag()
        _warm(dag, tmp_path)

        # Change 'first' (mid-chain): 'scale' itself is untouched, so its miss
        # is purely an upstream (parents) divergence.
        forked = _linear_multiply_dag().model_copy(deep=True)
        forked.nodes["first"].step.params["factor"] = 7
        forked.nodes["first"].resolved_params["factor"] = 7

        report = explain_cache(
            forked, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        steps = _by_step(report)
        assert steps["scale"].status == "miss"
        parent_diffs = [
            d
            for d in steps["scale"].candidate.differences
            if d.path.startswith("parents[")
        ]
        assert parent_diffs, "expected a parents[...] divergence"
        assert "upstream change in step 'first'" in parent_diffs[0].path
        # No fingerprint-level differences for the untouched step itself.
        assert all(
            d.path.startswith("parents[")
            for d in steps["scale"].candidate.differences
        )

    def test_never_cached_step(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        report = explain_cache(
            dag, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        assert all(step.status == "never-cached" for step in report.steps)
        assert "NEVER CACHED" in report.format_text()

    def test_marker_hit_reported(self, helper_module, tmp_path):
        dag = _sink_after_chain_dag()
        _warm(dag, tmp_path)
        report = explain_cache(
            dag, inputs={"seed": 1}, user_cache_dir=tmp_path / "user_cache"
        )
        steps = _by_step(report)
        assert steps["report"].status == "marker-hit"
        assert "HIT (marker)" in report.format_text()

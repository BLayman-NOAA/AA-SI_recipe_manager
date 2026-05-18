# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the round-trip tracker."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.tracker.pipeline_tracker import PipelineTracker


MINIMAL_RECIPE = {
    "recipe": {
        "name": "test_pipeline",
        "version": "1.0",
        "schema_version": "1",
    },
    "inputs": {
        "raw_folder": {"type": "path", "default": "/tmp/raw"},
    },
    "steps": [
        {"id": "query_ncei", "op": "query_ncei_data", "params": {"file_time_start": "2016-01-01"}},
        {"id": "download_raw", "op": "download_ncei_data", "params": {"output_dir": "/tmp/raw"}},
        {"id": "open_raw", "op": "open_raw_files", "params": {"sonar_model": "EK60"}},
    ],
}


class TestPipelineTrackerRecording:
    def test_step_records_execution(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        assert len(tracker._executed) == 1
        assert tracker._executed[0][0] == "query_ncei"

    def test_step_records_op(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        assert tracker._executed[0][1] == "query_ncei_data"

    def test_step_records_params(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data", params={"file_time_start": "2020-01-01"}):
            pass
        assert tracker._executed[0][2]["file_time_start"] == "2020-01-01"

    def test_step_records_execution_order(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        with tracker.step("download_raw", op="download_ncei_data"):
            pass
        assert [e[0] for e in tracker._executed] == ["query_ncei", "download_raw"]

    def test_step_no_params_defaults_to_empty(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        assert tracker._executed[0][2] == {}

    def test_does_not_suppress_exceptions(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with pytest.raises(ValueError):
            with tracker.step("query_ncei", op="query_ncei_data"):
                raise ValueError("boom")


class TestSaveRecipe:
    def test_save_recipe_returns_string(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        result = tracker.save_recipe()
        assert isinstance(result, str)

    def test_save_recipe_is_valid_yaml(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        assert parsed is not None

    def test_save_recipe_executed_steps_only(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        steps = parsed["steps"]
        assert len(steps) == 1
        assert steps[0]["id"] == "query_ncei"

    def test_save_recipe_preserves_execution_order(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("download_raw", op="download_ncei_data"):
            pass
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        step_ids = [s["id"] for s in parsed["steps"]]
        assert step_ids == ["download_raw", "query_ncei"]

    def test_save_recipe_param_changes_reflected(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data", params={"file_time_start": "2022-06-01"}):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        params = parsed["steps"][0]["params"]
        assert params["file_time_start"] == "2022-06-01"

    def test_save_recipe_unexecuted_steps_absent(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        step_ids = {s["id"] for s in parsed["steps"]}
        assert "download_raw" not in step_ids
        assert "open_raw" not in step_ids

    def test_save_recipe_writes_to_file(self, tmp_path):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        out = tmp_path / "modified.yaml"
        tracker.save_recipe(out)
        assert out.exists()
        content = out.read_text()
        assert "query_ncei" in content

    def test_save_recipe_preserves_original_recipe_metadata(self):
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass
        serialized = tracker.save_recipe()
        yaml = YAML()
        parsed = yaml.load(serialized)
        assert parsed["recipe"]["name"] == "test_pipeline"

    def test_original_recipe_not_mutated(self):
        import copy

        original = copy.deepcopy(MINIMAL_RECIPE)
        tracker = PipelineTracker(MINIMAL_RECIPE)
        with tracker.step("query_ncei", op="query_ncei_data", params={"file_time_start": "9999-01-01"}):
            pass
        tracker.save_recipe()
        assert MINIMAL_RECIPE["steps"][0]["params"]["file_time_start"] == "2016-01-01"

    def test_save_recipe_round_trips_when_initialized_from_flat_recipe_dump(self, tmp_path):
        flat_recipe = {
            "name": "test_pipeline",
            "version": "1.0",
            "description": None,
            "author": None,
            "inputs": {"raw_folder": {"type": "path", "default": "/tmp/raw"}},
            "steps": [{"id": "query_ncei", "op": "query_ncei_data", "inputs": {}, "params": {}}],
            "outputs": None,
            "execution": None,
            "schema_version": "1",
        }

        tracker = PipelineTracker(flat_recipe)
        with tracker.step("query_ncei", op="query_ncei_data"):
            pass

        out = tmp_path / "roundtrip.yaml"
        tracker.save_recipe(out)
        recipe = load_recipe(out)
        assert recipe.name == "test_pipeline"
        assert recipe.schema_version == "1"

    def test_save_recipe_ignores_include_entries_when_indexing_original_steps(self):
        recipe = {
            "recipe": {
                "name": "modular_pipeline",
                "version": "1.0",
                "schema_version": "1",
            },
            "steps": [
                {"id": "query_ncei", "op": "query_ncei_data"},
                {"include": "processing_lvls_1_to_3.yaml"},
            ],
        }
        tracker = PipelineTracker(recipe)

        with tracker.step("query_ncei", op="query_ncei_data"):
            pass

        serialized = tracker.save_recipe()
        parsed = YAML().load(serialized)
        assert [step["id"] for step in parsed["steps"]] == ["query_ncei"]


class TestNonScalarParamWarning:
    def test_non_scalar_param_emits_warning(self):
        import warnings

        tracker = PipelineTracker(MINIMAL_RECIPE)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with tracker.step("query_ncei", op="query_ncei_data", params={"ds": object()}):
                pass
        assert any(issubclass(w.category, UserWarning) and "ds" in str(w.message) for w in caught)

    def test_scalar_param_no_warning(self):
        import warnings

        tracker = PipelineTracker(MINIMAL_RECIPE)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with tracker.step("query_ncei", op="query_ncei_data", params={"val": 42, "flag": True, "name": "x"}):
                pass
        assert not any(issubclass(w.category, UserWarning) for w in caught)

    def test_list_of_scalars_no_warning(self):
        import warnings

        tracker = PipelineTracker(MINIMAL_RECIPE)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with tracker.step("query_ncei", op="query_ncei_data", params={"freqs": [18, 38, 120]}):
                pass
        assert not any(issubclass(w.category, UserWarning) for w in caught)

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the YAML recipe parser and param resolver."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.model.types import Step
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.resolver.params import (
    extract_edge_refs,
    extract_input_refs,
    resolve_input_refs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_recipe(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


MINIMAL_RECIPE = """\
    recipe:
      name: minimal_pipeline
      version: "1.0"
      schema_version: "1"
    inputs: {}
    steps:
      - id: open_raw
        op: open_raw_files
        params:
          netcdf_output_folder: "./out"
          sonar_model: "EK60"
    outputs: {}
    """


# ---------------------------------------------------------------------------
# load_recipe
# ---------------------------------------------------------------------------

class TestLoadRecipe:
    def test_valid_file_returns_recipe(self, tmp_path):
        p = _write_recipe(tmp_path, MINIMAL_RECIPE)
        recipe = load_recipe(p)
        assert recipe.name == "minimal_pipeline"
        assert recipe.version == "1.0"
        assert recipe.schema_version == "1"
        assert len(recipe.steps) == 1

    def test_missing_file_raises_recipe_parse_error(self, tmp_path):
        with pytest.raises(RecipeParseError, match="not found"):
            load_recipe(tmp_path / "missing.yaml")

    def test_bad_yaml_raises_recipe_parse_error(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{ this: is: not: valid: yaml :::}")
        with pytest.raises(RecipeParseError):
            load_recipe(p)

    def test_bad_schema_version_raises_recipe_parse_error(self, tmp_path):
        content = MINIMAL_RECIPE.replace('schema_version: "1"', 'schema_version: "99"')
        p = _write_recipe(tmp_path, content)
        with pytest.raises(RecipeParseError):
            load_recipe(p)

    def test_steps_with_no_inputs_key_normalised(self, tmp_path):
        content = """\
            recipe:
              name: no_inputs
              version: "1.0"
              schema_version: "1"
            steps:
              - id: open_raw
                op: open_raw_files
                params:
                  netcdf_output_folder: ./out
                  sonar_model: EK60
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        step = recipe.steps[0]
        assert step.inputs == {}

    def test_steps_with_no_params_key_normalised(self, tmp_path):
        content = """\
            recipe:
              name: no_params
              version: "1.0"
              schema_version: "1"
            steps:
              - id: query
                op: query_ncei_data
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        assert recipe.steps[0].params == {}


# ---------------------------------------------------------------------------
# extract_edge_refs
# ---------------------------------------------------------------------------

class TestExtractEdgeRefs:
    def test_single_ref(self):
        step = Step(id="b", op="compute_sv", inputs={"echodata": "${open_raw.echodata}"})
        refs = extract_edge_refs(step)
        assert refs == [("open_raw", "echodata", "b", "echodata")]

    def test_list_refs_fan_in(self):
        step = Step(id="c", op="combine_masks", inputs={
            "masks": ["${a.mask}", "${b.mask}"]
        })
        refs = extract_edge_refs(step)
        assert ("a", "mask", "c", "masks") in refs
        assert ("b", "mask", "c", "masks") in refs
        assert len(refs) == 2

    def test_no_refs(self):
        step = Step(id="a", op="query_ncei_data")
        assert extract_edge_refs(step) == []

    def test_non_ref_inputs_ignored(self):
        step = Step(id="b", op="compute_sv", inputs={"echodata": "literal_value"})
        assert extract_edge_refs(step) == []


# ---------------------------------------------------------------------------
# extract_input_refs
# ---------------------------------------------------------------------------

class TestExtractInputRefs:
    def test_full_match(self):
        result = extract_input_refs({"folder": "${inputs.raw_folder}"})
        assert result == {"folder": "raw_folder"}

    def test_partial_match_excluded(self):
        result = extract_input_refs({"path": "${inputs.folder}/sub"})
        assert result == {}

    def test_empty(self):
        assert extract_input_refs({}) == {}


# ---------------------------------------------------------------------------
# resolve_input_refs
# ---------------------------------------------------------------------------

class TestResolveInputRefs:
    def test_full_substitution(self):
        result = resolve_input_refs(
            {"dir": "${inputs.output_folder}"},
            {"output_folder": "/data/out"},
        )
        assert result["dir"] == "/data/out"

    def test_partial_interpolation(self):
        result = resolve_input_refs(
            {"path": "${inputs.base}/sub"},
            {"base": "/data"},
        )
        assert result["path"] == "/data/sub"

    def test_unresolvable_left_as_is(self):
        params = {"path": "${inputs.missing}"}
        result = resolve_input_refs(params, {})
        assert result["path"] == "${inputs.missing}"

    def test_non_string_values_unchanged(self):
        result = resolve_input_refs({"count": 5}, {"count": 99})
        assert result["count"] == 5

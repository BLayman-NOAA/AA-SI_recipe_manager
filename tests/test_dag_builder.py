# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the DAG builder."""

from __future__ import annotations

import importlib.metadata
import textwrap
import time
from pathlib import Path

import pytest

from aa_recipe_manager.exceptions import RecipeValidationError
from aa_recipe_manager.model.types import (
    ParamDeclaration,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry
from aa_recipe_manager.registry.registry import Registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_recipe(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _make_registry(*specs: Spec) -> Registry:
    """Build a Registry containing only the given specs (no implementations)."""
    reg = Registry()
    for spec in specs:
        reg.register_spec(spec)
    return reg


FOUR_STEP_RECIPE = """\
    recipe:
      name: simple_ek60_pipeline
      version: "1.0"
      schema_version: "1"
    inputs:
      raw_input_folder:
        type: path
        default: "__RAW_INPUT_FOLDER__"
      netcdf_output_folder:
        type: path
        default: "__NETCDF_OUTPUT_FOLDER__"
    steps:
      - id: query_ncei
        op: query_ncei_data
        params:
          file_time_start: "2016-07-25T20:58"
          file_time_end: "2016-07-25T21:45"
      - id: download_raw
        op: download_ncei_data
        inputs:
          results: ${query_ncei.ncei_results}
        params:
          output_dir: ${inputs.raw_input_folder}
      - id: setup_files
        op: setup_raw_files
        depends_on: [download_raw]
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sv_output_folder: "./sv_files"
          output_logs_folder: "./logs"
      - id: open_raw
        op: open_raw_files
        inputs:
          raw_file_paths: ${setup_files.raw_file_paths}
        params:
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sonar_model: "EK60"
    """


def _write_four_step_recipe(tmp_path: Path) -> Path:
    raw_input_folder = tmp_path / "raw_files"
    raw_input_folder.mkdir()
    recipe_text = FOUR_STEP_RECIPE.replace(
        "__RAW_INPUT_FOLDER__", raw_input_folder.as_posix()
    ).replace(
        "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
    )
    return _write_recipe(tmp_path, recipe_text)


# ---------------------------------------------------------------------------
# 4-step pipeline
# ---------------------------------------------------------------------------

class TestFourStepPipeline:
    def test_dag_has_four_nodes(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        assert len(dag.nodes) == 4

    def test_dag_has_data_and_ordering_edges(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        # At minimum: query->download (data), setup_files->open_raw (data),
        # download->setup_files (ordering only from depends_on)
        assert len(dag.edges) >= 3

    def test_topological_order_has_all_steps(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        assert set(dag.topological_order) == {"query_ncei", "download_raw", "setup_files", "open_raw"}

    def test_query_ncei_first_in_order(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        assert dag.topological_order[0] == "query_ncei"

    def test_open_raw_last_in_order(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        assert dag.topological_order[-1] == "open_raw"

    def test_input_defaults_substituted_in_resolved_params(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        node = dag.nodes["download_raw"]
        assert Path(node.resolved_params.get("output_dir")) == tmp_path / "raw_files"

    def test_node_spec_set_correctly(self, tmp_path):
        p = _write_four_step_recipe(tmp_path)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        dag = build_dag(recipe, reg)
        assert dag.nodes["open_raw"].spec is not None


# ---------------------------------------------------------------------------
# Validation — unknown op
# ---------------------------------------------------------------------------

class TestUnknownOp:
    def test_unknown_op_raises_validation_error(self, tmp_path):
        content = """\
            recipe:
              name: bad_recipe
              version: "1.0"
              schema_version: "1"
            steps:
              - id: mystery
                op: nonexistent_op
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        with pytest.raises(RecipeValidationError, match="nonexistent_op"):
            build_dag(recipe, reg)


# ---------------------------------------------------------------------------
# Validation — dangling step reference
# ---------------------------------------------------------------------------

class TestDanglingRefs:
    def test_dangling_depends_on_raises(self, tmp_path):
        content = """\
            recipe:
              name: dangling
              version: "1.0"
              schema_version: "1"
            steps:
              - id: step_b
                op: query_ncei_data
                depends_on: [ghost_step]
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        with pytest.raises(RecipeValidationError, match="ghost_step"):
            build_dag(recipe, reg)

    def test_dangling_input_ref_raises(self, tmp_path):
        content = """\
            recipe:
              name: dangling_input
              version: "1.0"
              schema_version: "1"
            steps:
              - id: step_b
                op: download_ncei_data
                inputs:
                  results: ${ghost_step.ncei_results}
                params:
                  output_dir: "./out"
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        with pytest.raises(RecipeValidationError, match="ghost_step"):
            build_dag(recipe, reg)


# ---------------------------------------------------------------------------
# Validation — duplicate step IDs
# ---------------------------------------------------------------------------

class TestDuplicateStepIds:
    def test_duplicate_step_id_raises(self):
        recipe = Recipe(
            name="dupe_ids",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="step_a", op="query_ncei_data"),
                Step(id="step_a", op="compute_sv"),
            ],
        )
        reg = load_builtin_registry()
        with pytest.raises(RecipeValidationError, match="step_a"):
            build_dag(recipe, reg)


# ---------------------------------------------------------------------------
# Validation — cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    def test_cycle_raises_validation_error(self):
        """Build a 2-node cycle directly without YAML to keep test self-contained."""
        spec_a = Spec(
            op="op_a",
            description="A",
            inputs={"x": PortDeclaration(type="Dataset")},
            outputs={"y": PortDeclaration(type="Dataset")},
        )
        spec_b = Spec(
            op="op_b",
            description="B",
            inputs={"x": PortDeclaration(type="Dataset")},
            outputs={"y": PortDeclaration(type="Dataset")},
        )
        reg = _make_registry(spec_a, spec_b)

        recipe = Recipe(
            name="cycle",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="a", op="op_a", inputs={"x": "${b.y}"}),
                Step(id="b", op="op_b", inputs={"x": "${a.y}"}),
            ],
        )
        with pytest.raises(RecipeValidationError, match="[Cc]ycle"):
            build_dag(recipe, reg)


# ---------------------------------------------------------------------------
# Errors collected in bulk
# ---------------------------------------------------------------------------

class TestBulkErrors:
    def test_multiple_errors_raised_together(self, tmp_path):
        """Two unknown ops in the same recipe → single error with both names."""
        content = """\
            recipe:
              name: multi_bad
              version: "1.0"
              schema_version: "1"
            steps:
              - id: a
                op: fake_op_1
              - id: b
                op: fake_op_2
            """
        p = _write_recipe(tmp_path, content)
        recipe = load_recipe(p)
        reg = load_builtin_registry()
        with pytest.raises(RecipeValidationError) as exc_info:
            build_dag(recipe, reg)
        msg = str(exc_info.value)
        assert "fake_op_1" in msg
        assert "fake_op_2" in msg


# ---------------------------------------------------------------------------
# Validation - path params
# ---------------------------------------------------------------------------

class TestPathValidation:
    def test_missing_required_existing_path_raises(self, tmp_path):
        reg = Registry()
        reg.register_spec(
            Spec(
                op="needs_input_path",
                description="Requires an existing input path",
                params={
                    "input_path": ParamDeclaration(
                        type="path",
                        required=True,
                        constraints={"must_exist": True},
                    )
                },
            )
        )

        recipe = Recipe(
            name="missing_path",
            version="1.0",
            schema_version="1",
            steps=[
                Step(
                    id="reader",
                    op="needs_input_path",
                    params={"input_path": str(tmp_path / "missing.txt")},
                )
            ],
        )

        with pytest.raises(RecipeValidationError, match="does not exist"):
            build_dag(recipe, reg)

    def test_missing_output_path_allowed_when_spec_opts_out(self, tmp_path):
        reg = Registry()
        reg.register_spec(
            Spec(
                op="writes_output_path",
                description="Creates its output folder on demand",
                params={
                    "output_dir": ParamDeclaration(
                        type="path",
                        required=True,
                        constraints={"must_exist": False},
                    )
                },
            )
        )

        recipe = Recipe(
            name="output_path",
            version="1.0",
            schema_version="1",
            steps=[
                Step(
                    id="writer",
                    op="writes_output_path",
                    params={"output_dir": str(tmp_path / "not-created-yet")},
                )
            ],
        )

        dag = build_dag(recipe, reg)
        assert dag.nodes["writer"].resolved_params["output_dir"] == str(
            tmp_path / "not-created-yet"
        )


# ---------------------------------------------------------------------------
# Validation — missing required param
# ---------------------------------------------------------------------------

class TestMissingRequiredParam:
    def test_missing_required_param_raises(self):
        reg = Registry()
        reg.register_spec(
            Spec(
                op="needs_param",
                description="Requires a numeric param",
                params={
                    "window_size": ParamDeclaration(
                        type="int",
                        required=True,
                    )
                },
            )
        )
        recipe = Recipe(
            name="missing_param",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="needs_param")],
        )
        with pytest.raises(RecipeValidationError, match="window_size"):
            build_dag(recipe, reg)

    def test_param_with_default_not_required(self):
        reg = Registry()
        reg.register_spec(
            Spec(
                op="has_default",
                description="Param has a default",
                params={
                    "threshold": ParamDeclaration(
                        type="float",
                        required=True,
                        default=0.5,
                    )
                },
            )
        )
        recipe = Recipe(
            name="use_default",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="has_default")],
        )
        dag = build_dag(recipe, reg)
        assert dag.nodes["step1"] is not None


# ---------------------------------------------------------------------------
# Validation — param type mismatch warning
# ---------------------------------------------------------------------------

class TestParamTypeMismatchWarning:
    def test_type_mismatch_warns_but_builds_dag(self):
        reg = Registry()
        reg.register_spec(
            Spec(
                op="typed_param_op",
                description="Expects a float param",
                params={
                    "threshold": ParamDeclaration(type="float", required=True)
                },
            )
        )
        recipe = Recipe(
            name="mismatch",
            version="1.0",
            schema_version="1",
            steps=[
                Step(
                    id="step1",
                    op="typed_param_op",
                    params={"threshold": "not_a_float"},
                )
            ],
        )
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            dag = build_dag(recipe, reg)
        assert dag.nodes["step1"] is not None
        warning_msgs = [str(w.message) for w in caught]
        assert any("threshold" in m and "float" in m for m in warning_msgs)


# ---------------------------------------------------------------------------
# Integration - larger builtin recipe
# ---------------------------------------------------------------------------

HB1603_STYLE_RECIPE = """\
    recipe:
      name: hb1603_style_pipeline
      version: "1.0"
      schema_version: "1"
    inputs:
      raw_input_folder:
        type: path
        default: "{raw_input_folder}"
      cal_input_folder:
        type: path
        default: "{cal_input_folder}"
      line_file_path:
        type: path
        default: "{line_file_path}"
    steps:
      - id: query_ncei
        op: query_ncei_data
        params:
          file_time_start: "2016-07-25T20:58"
          file_time_end: "2016-07-25T21:45"
      - id: download_raw
        op: download_ncei_data
        inputs:
          results: ${{query_ncei.ncei_results}}
        params:
          output_dir: "{downloads_dir}"
      - id: setup_files
        op: setup_raw_files
        depends_on: [download_raw]
        params:
          raw_input_folder: ${{inputs.raw_input_folder}}
          netcdf_output_folder: "{netcdf_dir}"
          sv_output_folder: "{sv_dir}"
          output_logs_folder: "{logs_dir}"
      - id: cal_map
        op: generate_standardized_cal_mapping
        params:
          raw_input_folder: ${{inputs.raw_input_folder}}
          cal_input_folder: ${{inputs.cal_input_folder}}
          output_base: "{cal_output_dir}"
          cruise_id: "HB1603"
          record_author: "Tester"
      - id: open_raw
        op: open_raw_files
        inputs:
          raw_file_paths: ${{setup_files.raw_file_paths}}
        params:
          netcdf_output_folder: "{netcdf_dir}"
          sonar_model: "EK60"
      - id: compute_sv
        op: compute_sv
        inputs:
          echodata: ${{open_raw.echodata}}
      - id: detect_bottom
        op: detect_seafloor
        inputs:
          ds_Sv: ${{compute_sv.ds_Sv}}
          echodata: ${{open_raw.echodata}}
      - id: mask_bottom
        op: create_seafloor_mask
        inputs:
          ds_Sv: ${{compute_sv.ds_Sv}}
          seafloor_depth: ${{detect_bottom.seafloor_depth}}
      - id: mask_surface
        op: create_surface_mask
        inputs:
          ds_Sv: ${{compute_sv.ds_Sv}}
      - id: mask_freq
        op: create_frequency_mask
        inputs:
          ds_Sv: ${{compute_sv.ds_Sv}}
        params:
          frequencies_to_mask: [38000]
      - id: combine_masks
        op: combine_masks
        inputs:
          masks:
            - ${{mask_bottom.mask}}
            - ${{mask_surface.mask}}
            - ${{mask_freq.mask}}
      - id: apply_mask
        op: apply_sv_mask
        inputs:
          ds_Sv: ${{compute_sv.ds_Sv}}
          mask: ${{combine_masks.mask}}
      - id: compute_mvbs
        op: compute_mvbs
        inputs:
          ds_Sv: ${{apply_mask.ds_Sv}}
        params:
          range_bin: "2m"
          ping_time_bin: "10s"
      - id: add_overlay
        op: add_line_overlay
        inputs:
          ds: ${{compute_mvbs.ds_MVBS}}
        params:
          line_file_path: ${{inputs.line_file_path}}
          line_name: "dive_profile"
      - id: plot_mvbs
        op: plot_sv_echogram
        inputs:
          ds_Sv: ${{add_overlay.ds}}
        params:
          min_depth: 0
          max_depth: 400
          ping_max: 50
    """


ML_STYLE_RECIPE = """\
    recipe:
      name: ml_recipe_slice
      version: "1.0"
      schema_version: "1"
    inputs:
      ds_mvbs:
        type: Dataset
      echodata:
        type: EchoData
      ds_sv:
        type: Dataset
    steps:
      - id: reshape_ml
        op: reshape_for_ml
        inputs:
          ds_MVBS: ${{inputs.ds_mvbs}}
        params:
          data_var: "Sv"
          dataset_name: "ml_data_clean"
          feature_strategy: "baseline_plus_differences"
      - id: add_aux
        op: add_auxiliary_features
        inputs:
          ds_ml: ${{reshape_ml.ds_ml_ready}}
          echodata: ${{inputs.echodata}}
        params:
          dataset_name: "ml_data_clean"
          features: ["depth", "seafloor_depth"]
      - id: normalize
        op: normalize_ml_data
        inputs:
          ds_ml: ${{add_aux.ds_ml_ready}}
        params:
          method: "standard"
          dataset_name: "ml_data_clean"
          normalization_name: "normalized_data"
      - id: cluster
        op: run_hdbscan
        inputs:
          ds_normalized: ${{normalize.ds_normalized}}
        params:
          dataset_name: "ml_data_clean"
          normalization_name: "normalized_data"
          ml_result_name: "clusters"
          min_cluster_size: 10
      - id: embed
        op: embed_clustering_results
        inputs:
          ds_normalized: ${{normalize.ds_normalized}}
          clustering_results: ${{cluster.clustering_results}}
        params:
          dataset_name: "ml_data_clean"
          ml_result_name: "clusters"
      - id: plot_clusters
        op: plot_clustering_report
        inputs:
          ds_normalized: ${{embed.ds_normalized}}
          clustering_results: ${{cluster.clustering_results}}
          ds_Sv: ${{inputs.ds_sv}}
        params:
          dataset_name: "ml_data_clean"
          ml_result_name: "clusters"
    """


ML_PLOT_RECIPE = """\
    recipe:
      name: ml_plot_recipe_slice
      version: "1.0"
      schema_version: "1"
    inputs:
      ds_normalized:
        type: Dataset
      ds_sv:
        type: Dataset
    steps:
      - id: plot_ml
        op: plot_ml_echogram
        inputs:
          ds_normalized: ${{inputs.ds_normalized}}
          ds_Sv: ${{inputs.ds_sv}}
        params:
          dataset_name: "ml_data_clean"
          normalization_name: "normalized_data"
          max_depth: 400
          ping_max: 50
    """


class TestBuiltinIntegration:
  def test_hb1603_style_recipe_builds_with_modular_masks(self, tmp_path, monkeypatch):
    original_version = importlib.metadata.version

    def _version(name: str):
      if name == "aa-si-calibration":
        return "0.1.0"
      return original_version(name)

    monkeypatch.setattr(importlib.metadata, "version", _version)

    raw_input_folder = tmp_path / "raw"
    cal_input_folder = tmp_path / "cal"
    downloads_dir = tmp_path / "downloads"
    netcdf_dir = tmp_path / "netcdf"
    sv_dir = tmp_path / "sv"
    logs_dir = tmp_path / "logs"
    cal_output_dir = tmp_path / "cal-out"
    line_file_path = tmp_path / "dive_profile.csv"

    raw_input_folder.mkdir()
    cal_input_folder.mkdir()
    line_file_path.write_text("ping_time,depth\n0,10\n", encoding="utf-8")

    recipe_text = HB1603_STYLE_RECIPE.format(
      raw_input_folder=raw_input_folder.as_posix(),
      cal_input_folder=cal_input_folder.as_posix(),
      line_file_path=line_file_path.as_posix(),
      downloads_dir=downloads_dir.as_posix(),
      netcdf_dir=netcdf_dir.as_posix(),
      sv_dir=sv_dir.as_posix(),
      logs_dir=logs_dir.as_posix(),
      cal_output_dir=cal_output_dir.as_posix(),
    )
    recipe_path = _write_recipe(tmp_path, recipe_text)

    recipe = load_recipe(recipe_path)
    reg = load_builtin_registry()

    start = time.perf_counter()
    dag = build_dag(recipe, reg)
    elapsed = time.perf_counter() - start

    assert len(dag.nodes) == 15
    assert len(dag.edges) >= 16
    assert dag.topological_order.index("compute_sv") < dag.topological_order.index(
      "combine_masks"
    )
    assert dag.topological_order[-1] == "plot_mvbs"
    assert dag.nodes["compute_sv"].implementation.callable_path == "echopype.calibrate.compute_Sv"
    assert dag.nodes["compute_mvbs"].implementation.callable_path == (
      "echopype.commongrid.compute_MVBS"
    )
    assert dag.nodes["plot_mvbs"].implementation.callable_path == (
      "aa_si_visualization.echogram.plot_sv_echogram"
    )
    assert dag.nodes["plot_mvbs"].implementation.param_map == {
      "ds_Sv_source": "ds_Sv_original"
    }
    assert dag.nodes["plot_mvbs"].spec.sink is True
    assert elapsed < 1.0

  def test_ml_recipe_slice_builds_with_refactored_ops(self, tmp_path):
    recipe_path = _write_recipe(tmp_path, ML_STYLE_RECIPE)

    recipe = load_recipe(recipe_path)
    reg = load_builtin_registry()
    dag = build_dag(recipe, reg)

    assert dag.topological_order == [
      "reshape_ml",
      "add_aux",
      "normalize",
      "cluster",
      "embed",
      "plot_clusters",
    ]
    assert dag.nodes["cluster"].implementation.callable_path == "aa_si_ml.ml.run_hdbscan"
    assert dag.nodes["embed"].implementation.callable_path == (
      "aa_si_ml.ml.embed_clustering_results"
    )
    assert dag.nodes["plot_clusters"].implementation.callable_path == (
      "aa_si_ml.ml.plot_clustering_report"
    )
    assert dag.nodes["plot_clusters"].spec.sink is True

  def test_ml_plot_recipe_slice_builds_with_visualization_mapping(self, tmp_path):
    recipe_path = _write_recipe(tmp_path, ML_PLOT_RECIPE)

    recipe = load_recipe(recipe_path)
    reg = load_builtin_registry()
    dag = build_dag(recipe, reg)

    assert dag.topological_order == ["plot_ml"]
    assert dag.nodes["plot_ml"].implementation.callable_path == (
      "aa_si_visualization.echogram.plot_flattened_data_echogram"
    )
    assert dag.nodes["plot_ml"].implementation.param_map == {
      "ds_normalized": "ds_ml",
      "dataset_name": "ml_dataset_name",
      "normalization_name": "ml_specific_data_name",
      "ds_Sv": "ds_Sv_original",
    }
    assert dag.nodes["plot_ml"].spec.sink is True

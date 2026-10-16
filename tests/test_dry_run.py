# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the DryRunEngine and DryRunReport."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import aa_recipe_manager
from aa_recipe_manager.validation import DryRunEngine, DryRunReport, DryRunStepInfo
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry


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
        op: initial_setup
        depends_on: [download_raw]
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sv_output_folder: "./sv_files"
          output_logs_folder: "./logs"
      - id: open_raw
        op: read_raw_files
        inputs:
          raw_file_paths: ${setup_files.raw_file_paths}
        params:
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sonar_model: "EK60"
    """


def _write_recipe(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _build_four_step_dag(tmp_path: Path):
    raw_dir = tmp_path / "raw_files"
    raw_dir.mkdir()
    recipe_text = FOUR_STEP_RECIPE.replace(
        "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
    ).replace(
        "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
    )
    p = _write_recipe(tmp_path, recipe_text)
    recipe = load_recipe(p)
    reg = load_builtin_registry()
    return build_dag(recipe, reg)


class TestDryRunReport:
    def test_is_valid_true_for_valid_dag(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        assert report.is_valid is True

    def test_resolved_steps_count_matches_dag(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        assert len(report.resolved_steps) == len(dag.topological_order)

    def test_resolved_steps_have_expected_step_ids(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        step_ids = {s.step_id for s in report.resolved_steps}
        assert step_ids == set(dag.topological_order)

    def test_no_errors_for_valid_dag(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        assert report.errors == []

    def test_dag_diagram_is_none_without_visualize(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False, visualize=False)
        assert report.dag_diagram is None

    def test_dag_diagram_starts_with_graph_td_when_visualize(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False, visualize=True)
        assert report.dag_diagram is not None
        assert report.dag_diagram.startswith("graph TD")

    def test_dag_diagram_contains_all_step_ids(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False, visualize=True)
        diagram = report.dag_diagram
        for step_id in dag.topological_order:
            assert step_id in diagram

    def test_format_text_contains_recipe_name(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        text = report.format_text()
        assert "simple_ek60_pipeline" in text

    def test_format_text_contains_step_ids(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        text = report.format_text()
        assert "query_ncei" in text
        assert "open_raw" in text

    def test_format_text_valid_recipe_success_line(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        text = report.format_text()
        assert "No issues found" in text

    def test_format_text_invalid_report_failure_line(self):
        report = DryRunReport(
            is_valid=False,
            errors=["Step 'x': unknown op 'bad_op'."],
            recipe_label="Recipe: test_recipe",
        )
        text = report.format_text()
        assert "failed" in text.lower() or "error" in text.lower()

    def test_step_info_has_op(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        ops = {s.op for s in report.resolved_steps}
        assert "query_ncei_data" in ops
        assert "read_raw_files" in ops

    def test_step_info_version_status_no_impl_for_custom_steps(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        for step_info in report.resolved_steps:
            assert step_info.version_status in {"ok", "warning", "error", "no_impl"}


class TestDryRunEngineVersionChecks:
    def test_check_versions_false_skips_version_check(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        engine = DryRunEngine()
        report = engine.run(dag, check_versions=False)
        for step_info in report.resolved_steps:
            if step_info.version_status != "no_impl":
                assert step_info.installed_version is None

    def test_run_applies_input_overrides_to_reported_params(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        override_dir = tmp_path / "override_raw"
        override_dir.mkdir()

        report = DryRunEngine().run(
            dag,
            inputs={"raw_input_folder": override_dir.as_posix()},
            check_versions=False,
        )

        setup_step = next(
            step for step in report.resolved_steps if step.step_id == "setup_files"
        )
        assert setup_step.params["raw_input_folder"] == override_dir.as_posix()


class TestDryRunPublicAPI:
    def test_api_dry_run_returns_report_for_valid_recipe(self, tmp_path):
        raw_dir = tmp_path / "raw_files"
        raw_dir.mkdir()
        recipe_text = FOUR_STEP_RECIPE.replace(
            "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
        ).replace(
            "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
        )
        p = _write_recipe(tmp_path, recipe_text)
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert isinstance(report, DryRunReport)
        assert report.is_valid is True

    def test_api_dry_run_returns_invalid_report_for_missing_file(self):
        report = aa_recipe_manager.dry_run("/nonexistent/path/recipe.yaml", check_versions=False)
        assert isinstance(report, DryRunReport)
        assert report.is_valid is False
        assert len(report.errors) > 0

        def test_api_dry_run_reports_undeclared_pipeline_input(self, tmp_path):
                recipe = _write_recipe(
                        tmp_path,
                        """\
                        recipe:
                            name: missing_pipeline_input
                            version: "1.0"
                            schema_version: "1"
                        steps:
                            - id: query
                                op: query_ncei_data
                                params:
                                    file_time_start: "2016-07-25T20:58"
                                    file_time_end: "2016-07-25T21:45"
                            - id: setup
                                op: initial_setup
                                params:
                                    raw_input_folder: "./raw"
                                    netcdf_output_folder: ${inputs.netcdf_output_folder}
                                    sv_output_folder: "./sv"
                                    output_logs_folder: "./logs"
                                    raw_file_names: ${inputs.raw_file_names}
                            - id: stats
                                op: compute_per_cell_statistics
                                inputs:
                                    ds_Sv: ${setup.raw_file_paths}
                                params:
                                    range_bin: ${inputs.range_bin}
                                    ping_time_bin: "10s"
                                    statistics:
                                        - cv
                        """,
                )

                report = aa_recipe_manager.dry_run(recipe, check_versions=False)

                assert report.is_valid is False
                assert any("undeclared pipeline input 'range_bin'" in err for err in report.errors)

    def test_api_dry_run_never_raises(self):
        try:
            report = aa_recipe_manager.dry_run("/totally/fake/path.yaml", check_versions=False)
            assert not report.is_valid
        except Exception as exc:
            pytest.fail(f"dry_run raised unexpectedly: {exc}")

    def test_api_dry_run_with_visualize_includes_diagram(self, tmp_path):
        raw_dir = tmp_path / "raw_files"
        raw_dir.mkdir()
        recipe_text = FOUR_STEP_RECIPE.replace(
            "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
        ).replace(
            "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
        )
        p = _write_recipe(tmp_path, recipe_text)
        report = aa_recipe_manager.dry_run(p, check_versions=False, visualize=True)
        assert report.dag_diagram is not None
        assert "graph TD" in report.dag_diagram

    def test_api_dry_run_applies_input_overrides(self, tmp_path):
        raw_dir = tmp_path / "raw_files"
        raw_dir.mkdir()
        override_dir = tmp_path / "override_raw"
        override_dir.mkdir()
        recipe_text = FOUR_STEP_RECIPE.replace(
            "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
        ).replace(
            "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
        )
        p = _write_recipe(tmp_path, recipe_text)

        report = aa_recipe_manager.dry_run(
            p,
            inputs={"raw_input_folder": override_dir.as_posix()},
            check_versions=False,
        )

        setup_step = next(
            step for step in report.resolved_steps if step.step_id == "setup_files"
        )
        assert setup_step.params["raw_input_folder"] == override_dir.as_posix()

    def test_api_dry_run_accepts_custom_step_without_dependency(self, tmp_path):
        p = _write_recipe(
            tmp_path,
            """\
            recipe: {name: no_dep, version: "1.0", schema_version: "1"}
            steps:
              - id: plain
                op: custom
                custom_spec:
                  description: Return the constant 7.
                  callable_path: _stage9_helpers.const7
                  outputs:
                    v: {type: int}
                  output_map: {v: __return__}
            """,
        )
        report = aa_recipe_manager.dry_run(p, check_versions=True)
        assert report.is_valid, report.errors
        (step,) = report.resolved_steps
        assert step.package_name is None
        assert step.version_status == "ok"
        (line,) = [ln for ln in report.format_text().splitlines() if "plain" in ln]
        assert line.endswith("custom [OK]")


UNSET_INPUT_RECIPE = """\
    recipe: {name: unset_input, version: "1.0", schema_version: "1"}
    inputs:
      seconds: {type: float}
    steps:
      - id: start
        op: custom
        custom_spec:
          description: Return the constant 7.
          callable_path: _stage9_helpers.const7
          outputs:
            v: {type: int}
          output_map: {v: __return__}
      - id: wait
        op: custom
        inputs:
          x: ${start.v}
        params:
          seconds: ${inputs.seconds}
        custom_spec:
          description: Sleep, then pass the value on.
          callable_path: _stage9_helpers.sleep_for
          inputs:
            x: {type: int}
          params:
            seconds: {type: float, required: __REQUIRED__}
          outputs:
            out: {type: int}
          output_map: {out: __return__}
    """


class TestUnsetRecipeInputs:
    def test_required_param_fed_by_an_unset_input_is_an_error(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert not report.is_valid
        (error,) = report.errors
        assert "step 'wait' param 'seconds' needs recipe input 'seconds'" in error

    def test_a_value_for_the_input_clears_the_error(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        report = aa_recipe_manager.dry_run(p, inputs={"seconds": 0.1}, check_versions=False)
        assert report.is_valid, report.errors

    def test_optional_param_fed_by_an_unset_input_is_fine(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "false"))
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.is_valid, report.errors

    def test_cli_dry_run_reads_the_run_config_inputs(self, tmp_path):
        from click.testing import CliRunner

        from aa_recipe_manager.cli import main

        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        runner = CliRunner()
        failed = runner.invoke(main, ["dry-run", str(p)])
        assert failed.exit_code == 1
        assert "needs recipe input 'seconds'" in failed.output
        config = tmp_path / "recipe.config.yaml"
        config.write_text("inputs:\n  seconds: 0.1\n")
        passed = runner.invoke(main, ["dry-run", str(p)])
        assert passed.exit_code == 0, passed.output

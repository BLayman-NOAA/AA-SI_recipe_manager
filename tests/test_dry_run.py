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
        assert report.required_inputs == []

    def test_a_required_slot_makes_the_input_required(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        report = aa_recipe_manager.dry_run(
            p, inputs={"seconds": 0.1}, check_versions=False
        )
        assert report.required_inputs == ["seconds"]

    def test_input_port_fed_by_an_unset_input_is_an_error(self, tmp_path):
        p = _write_recipe(
            tmp_path,
            UNSET_INPUT_RECIPE.replace("x: ${start.v}", "x: ${inputs.seconds}").replace(
                "__REQUIRED__", "false"
            ),
        )
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        (error,) = report.errors
        assert "step 'wait' input 'x' needs recipe input 'seconds'" in error
        assert report.required_inputs == ["seconds"]

    def test_null_for_the_input_is_unset(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        report = aa_recipe_manager.dry_run(
            p, inputs={"seconds": None}, check_versions=False
        )
        (error,) = report.errors
        assert "step 'wait' param 'seconds' needs recipe input 'seconds'" in error

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


REQUIRED_INPUT_RECIPE = UNSET_INPUT_RECIPE.replace(
    "seconds: {type: float}", "seconds: {type: float, required: true}"
).replace("__REQUIRED__", "false")


class TestRequiredRecipeInputs:
    def test_no_default_alone_does_not_make_an_input_required(self, tmp_path):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "false"))
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.is_valid, report.errors

    def test_no_default_feeding_a_required_param_gets_the_unset_input_error(
        self, tmp_path
    ):
        p = _write_recipe(tmp_path, UNSET_INPUT_RECIPE.replace("__REQUIRED__", "true"))
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        (error,) = report.errors
        assert error.startswith("step 'wait' param 'seconds' needs recipe input")

    def test_a_required_input_with_no_value_is_an_error(self, tmp_path):
        p = _write_recipe(tmp_path, REQUIRED_INPUT_RECIPE)
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert not report.is_valid
        assert report.errors == [
            "Recipe input 'seconds' is required and has no value: set it in the "
            "run config's inputs, with --input, or give it a default."
        ]
        assert report.required_inputs == ["seconds"]

    def test_required_with_a_null_default_is_still_required(self, tmp_path):
        recipe = REQUIRED_INPUT_RECIPE.replace(
            "required: true}", "required: true, default: null}"
        )
        p = _write_recipe(tmp_path, recipe)
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        (error,) = report.errors
        assert "Recipe input 'seconds' is required and has no value" in error

    def test_a_recipe_that_fails_to_build_still_names_explicit_requirements(
        self, tmp_path
    ):
        recipe = REQUIRED_INPUT_RECIPE.replace("op: custom", "op: no_such_op", 1)
        p = _write_recipe(tmp_path, recipe)
        report = aa_recipe_manager.dry_run(load_recipe(p), check_versions=False)
        assert not report.is_valid
        assert report.required_inputs == ["seconds"]

    def test_a_required_input_given_null_is_an_error(self, tmp_path):
        p = _write_recipe(tmp_path, REQUIRED_INPUT_RECIPE)
        report = aa_recipe_manager.dry_run(
            p, inputs={"seconds": None}, check_versions=False
        )
        (error,) = report.errors
        assert "'seconds' is required, and the inputs given set it to null" in error

    def test_a_value_or_a_default_clears_the_error(self, tmp_path):
        p = _write_recipe(tmp_path, REQUIRED_INPUT_RECIPE)
        given = aa_recipe_manager.dry_run(
            p, inputs={"seconds": 1.0}, check_versions=False
        )
        assert given.is_valid, given.errors
        p = _write_recipe(
            tmp_path,
            REQUIRED_INPUT_RECIPE.replace(
                "seconds: {type: float, required: true}",
                "seconds: {type: float, default: 0.1}",
            ),
        )
        assert aa_recipe_manager.dry_run(p, check_versions=False).is_valid

    def test_cli_dry_run_and_edit_check_refuse_it(self, tmp_path):
        from click.testing import CliRunner

        from aa_recipe_manager.cli import main

        p = _write_recipe(tmp_path, REQUIRED_INPUT_RECIPE)
        runner = CliRunner()
        failed = runner.invoke(main, ["dry-run", str(p)])
        assert failed.exit_code == 1
        assert "Recipe input 'seconds' is required and has no value" in failed.output
        checked = runner.invoke(main, ["edit", str(p), "--check", "--dry-run"])
        assert checked.exit_code == 1
        assert "Recipe input 'seconds' is required and has no value" in checked.output
        passed = runner.invoke(main, ["dry-run", str(p), "--input", "seconds=0.1"])
        assert passed.exit_code == 0, passed.output


PLOT_WINDOW_RECIPE = """\
    recipe: {name: plot_window, version: "1.0", schema_version: "1"}
    inputs:
      max_depth:
        type: float
        description: No default plots the full range.
      time_min:
        type: str
        description: No defaults span the whole time range.
      time_max:
        type: str
    steps:
      - id: start
        op: custom
        custom_spec:
          description: Stand in for the Sv dataset.
          callable_path: _stage9_helpers.const7
          outputs:
            v: {type: int}
          output_map: {v: __return__}
      - id: plot
        op: custom
        inputs:
          ds: ${start.v}
        params:
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
        custom_spec:
          description: Plot an echogram, optionally windowed.
          callable_path: _stage9_helpers.inc
          inputs:
            ds: {type: int}
          params:
            max_depth: {type: float, required: false}
            time_min: {type: str, required: false}
            time_max: {type: str, required: false}
          outputs:
            out: {type: int}
          output_map: {out: __return__}
    """


class TestOptionalWindowInputs:
    """Inputs left without a default on purpose, as the HB2407 herring recipe does."""

    def test_unset_window_inputs_are_valid(self, tmp_path):
        p = _write_recipe(tmp_path, PLOT_WINDOW_RECIPE)
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.is_valid, report.errors
        assert report.required_inputs == []

    def test_a_window_given_is_valid(self, tmp_path):
        p = _write_recipe(tmp_path, PLOT_WINDOW_RECIPE)
        report = aa_recipe_manager.dry_run(
            p,
            inputs={"time_min": "2024-09-24T00:00:00", "time_max": None},
            check_versions=False,
        )
        assert report.is_valid, report.errors

    def test_cli_dry_run_and_edit_check_accept_it(self, tmp_path):
        from click.testing import CliRunner

        from aa_recipe_manager.cli import main

        p = _write_recipe(tmp_path, PLOT_WINDOW_RECIPE)
        runner = CliRunner()
        passed = runner.invoke(main, ["dry-run", str(p), "--no-check-versions"])
        assert passed.exit_code == 0, passed.output
        checked = runner.invoke(main, ["edit", str(p), "--check", "--dry-run"])
        assert checked.exit_code == 0, checked.output


HDBSCAN_RECIPE = """\
    recipe: {name: exclusive, version: "1.0", schema_version: "1"}
    inputs:
      fraction: {type: float, default: null}
    steps:
      - id: start
        op: custom
        custom_spec:
          description: Stand in for the normalized features.
          callable_path: _stage9_helpers.const7
          outputs:
            v: {type: Dataset}
          output_map: {v: __return__}
      - id: cluster
        op: run_hdbscan
        inputs:
          ds_normalized: ${start.v}
        params:
          dataset_name: ml
          normalization_name: norm
          ml_result_name: hdbscan
    __PARAMS__
    """

EXCLUSIVE_ERROR = (
    "step 'cluster' sets both 'min_cluster_size' and 'min_cluster_size_fraction', "
    "which op 'run_hdbscan' declares mutually exclusive: unset one of them"
)


def _hdbscan_recipe(tmp_path: Path, *lines: str) -> Path:
    params = "".join(f"          {line}\n" for line in lines)
    return _write_recipe(tmp_path, HDBSCAN_RECIPE.replace("    __PARAMS__\n", params))


class TestExclusiveParams:
    @pytest.mark.parametrize("size", ["10", "0"])
    def test_both_set_is_an_error(self, tmp_path, size):
        p = _hdbscan_recipe(
            tmp_path, f"min_cluster_size: {size}", "min_cluster_size_fraction: 0.01"
        )
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert not report.is_valid
        assert report.errors == [EXCLUSIVE_ERROR]

    def test_one_null_is_fine(self, tmp_path):
        p = _hdbscan_recipe(
            tmp_path, "min_cluster_size: 10", "min_cluster_size_fraction: null"
        )
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.is_valid, report.errors

    def test_neither_set_is_fine(self, tmp_path):
        p = _hdbscan_recipe(tmp_path)
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.is_valid, report.errors

    def test_an_input_bound_param_counts_once_it_resolves(self, tmp_path):
        p = _hdbscan_recipe(
            tmp_path,
            "min_cluster_size: 10",
            "min_cluster_size_fraction: ${inputs.fraction}",
        )
        unset = aa_recipe_manager.dry_run(p, check_versions=False)
        assert unset.is_valid, unset.errors
        given = aa_recipe_manager.dry_run(
            p, inputs={"fraction": 0.01}, check_versions=False
        )
        assert given.errors == [EXCLUSIVE_ERROR]

    def test_an_include_override_counts(self, tmp_path):
        child = _hdbscan_recipe(
            tmp_path,
            "min_cluster_size: 10",
            "min_cluster_size_fraction: ${inputs.fraction}",
        )
        child.rename(tmp_path / "child.yaml")
        parent = tmp_path / "parent.yaml"
        parent.write_text(
            textwrap.dedent(
                """\
                recipe: {name: parent, version: "1.0", schema_version: "1"}
                steps:
                  - include: child.yaml
                    input_overrides:
                      fraction: 0.01
                """
            )
        )
        report = aa_recipe_manager.dry_run(parent, check_versions=False)
        assert report.errors == [EXCLUSIVE_ERROR]

    def test_a_swept_param_counts(self, tmp_path):
        p = _hdbscan_recipe(tmp_path, "min_cluster_size_fraction: 0.01")
        p.write_text(
            p.read_text()
            + "    sweep:\n      min_cluster_size: [500, 1000]\n"
        )
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert report.errors == [EXCLUSIVE_ERROR]

    def test_cli_dry_run_and_edit_check_refuse_it(self, tmp_path):
        from click.testing import CliRunner

        from aa_recipe_manager.cli import main

        p = _hdbscan_recipe(
            tmp_path,
            "min_cluster_size: 10",
            "min_cluster_size_fraction: ${inputs.fraction}",
        )
        runner = CliRunner()
        passed = runner.invoke(main, ["dry-run", str(p)])
        assert passed.exit_code == 0, passed.output
        args = ["--input", "fraction=0.01"]
        failed = runner.invoke(main, ["dry-run", str(p), *args])
        assert failed.exit_code == 1
        assert "declares mutually exclusive: unset one of them" in failed.output
        checked = runner.invoke(main, ["edit", str(p), "--check", "--dry-run", *args])
        assert checked.exit_code == 1
        assert "declares mutually exclusive: unset one of them" in checked.output

    @pytest.mark.parametrize("extends", [None, "run_hdbscan"])
    def test_a_custom_spec_naming_a_missing_param_is_an_error(self, tmp_path, extends):
        p = _write_recipe(
            tmp_path,
            f"""\
            recipe: {{name: bad_exclusive, version: "1.0", schema_version: "1"}}
            steps:
              - id: start
                op: custom
                custom_spec:
                  description: Names a param it does not declare.
                  extends: {extends or "null"}
                  callable_path: _stage9_helpers.const7
                  params:
                    a: {{type: int, constraints: {{exclusive_with: [missing]}}}}
                  outputs:
                    v: {{type: int}}
                  output_map: {{v: __return__}}
            """,
        )
        report = aa_recipe_manager.dry_run(p, check_versions=False)
        assert not report.is_valid
        assert len(report.errors) == 1
        assert (
            "Step 'start': custom_spec is not valid: op "
            f"{extends or 'custom'!r} param 'a': constraints.exclusive_with names "
            "'missing', which is not another param of this op."
        ) in report.errors[0]

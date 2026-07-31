# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""CLI integration tests using click.testing.CliRunner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from aa_recipe_manager.cli import (
    _format_duration,
    _resolve_executor_options,
    main,
)
from aa_recipe_manager.config import RunConfig

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


def _write_recipe(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw_files"
    raw_dir.mkdir()
    content = FOUR_STEP_RECIPE.replace(
        "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
    ).replace(
        "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
    )
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


class TestGenerateCommand:
    def test_generate_exits_zero(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert result.exit_code == 0, result.output

    def test_generate_creates_output_file(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert out.exists()

    def test_generate_prints_output_path(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert "Generated" in result.output

    def test_generate_fails_without_overwrite_if_file_exists(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        out.write_text("{}")
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert result.exit_code != 0

    def test_generate_succeeds_with_overwrite_flag(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        out.write_text("{}")
        runner = CliRunner()
        result = runner.invoke(
            main, ["generate", str(recipe_path), "-o", str(out), "--overwrite"]
        )
        assert result.exit_code == 0, result.output

    def test_generate_script_format_creates_python_script(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.py"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--format", "script"],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "tracker = PipelineTracker" in out.read_text(encoding="utf-8")

    def test_generate_no_provenance_omits_provenance_recorder(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--no-provenance"],
        )
        assert result.exit_code == 0, result.output
        assert "ProvenanceRecorder" not in out.read_text(encoding="utf-8")

    def test_generate_no_tracker_omits_tracker_code(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--no-tracker"],
        )
        assert result.exit_code == 0, result.output
        notebook = json.loads(out.read_text(encoding="utf-8"))
        sources: list[str] = []
        for cell in notebook["cells"]:
            source = cell["source"]
            if isinstance(source, list):
                sources.append("".join(source))
            else:
                sources.append(source)
        combined = "\n".join(sources)
        assert "PipelineTracker" not in combined
        assert "tracker.step(" not in combined
        assert "save_recipe" not in combined
        assert "query_ncei_data(" in combined

    def test_generate_cache_aware_emits_cache_code(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--cache-aware"],
        )
        assert result.exit_code == 0, result.output
        notebook = json.loads(out.read_text(encoding="utf-8"))
        sources: list[str] = []
        for cell in notebook["cells"]:
            source = cell["source"]
            if isinstance(source, list):
                sources.append("".join(source))
            else:
                sources.append(source)
        combined = "\n".join(sources)
        assert "_recipe_manager_cache_dir = 'recipe_cache'" in combined

    def test_generate_default_output_path_next_to_recipe(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path)])
        assert result.exit_code == 0, result.output
        expected = tmp_path / "simple_ek60_pipeline.ipynb"
        assert expected.exists()

    def test_generate_nonexistent_recipe_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["generate", "/no/such/recipe.yaml"])
        assert result.exit_code != 0


class TestDryRunCommand:
    def test_dry_run_exits_zero_for_valid_recipe(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert result.exit_code == 0, result.output

    def test_dry_run_output_contains_step_ids(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert "query_ncei" in result.output
        assert "open_raw" in result.output

    def test_dry_run_output_contains_recipe_name(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert "simple_ek60_pipeline" in result.output

    def test_dry_run_visualize_includes_mermaid(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["dry-run", str(recipe_path), "--no-check-versions", "--visualize"]
        )
        assert "graph TD" in result.output

    def test_dry_run_nonexistent_recipe_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", "/no/such/recipe.yaml"])
        assert result.exit_code != 0

    def test_dry_run_bad_input_format_fails(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["dry-run", str(recipe_path), "--no-check-versions", "--input", "badformat"],
        )
        assert result.exit_code != 0

    def test_dry_run_input_option_accepted(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        override_dir = tmp_path / "override_raw"
        override_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "dry-run",
                str(recipe_path),
                "--no-check-versions",
                "--input",
                f"raw_input_folder={override_dir.as_posix()}",
            ],
        )
        assert result.exit_code == 0, result.output
        assert override_dir.as_posix() in result.output


class TestDepsCommand:
    def test_deps_text_exits_zero(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path)])
        assert result.exit_code == 0, result.output

    def test_deps_text_shows_dependency_info(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path)])
        assert "simple_ek60_pipeline" in result.output

    def test_deps_requirements_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "requirements"])
        assert result.exit_code == 0, result.output

    def test_deps_conda_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "conda"])
        assert result.exit_code == 0, result.output

    def test_deps_pyproject_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "pyproject"])
        assert result.exit_code == 0, result.output

    def test_deps_output_file(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "requirements.txt"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["deps", str(recipe_path), "--format", "requirements", "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()


class TestSchemaCommand:
    def test_schema_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        assert result.exit_code == 0, result.output

    def test_schema_outputs_valid_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_schema_contains_recipe_type(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        parsed = json.loads(result.output)
        assert "title" in parsed or "$defs" in parsed or "properties" in parsed

    def test_schema_output_to_file(self, tmp_path):
        out = tmp_path / "schema.json"
        runner = CliRunner()
        result = runner.invoke(main, ["schema", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        with open(out) as fh:
            parsed = json.load(fh)
        assert isinstance(parsed, dict)


class TestDurationFormatting:
    """The run summary's total time (seconds / m+s / h+m+s)."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0.0, "0.00s"),
            (12.345, "12.35s"),
            (59.994, "59.99s"),
            (60.0, "1m 00s (60.0s)"),
            (198.84, "3m 19s (198.8s)"),
            (3852.31, "1h 04m 12s (3852.3s)"),
        ],
    )
    def test_format_duration(self, seconds, expected):
        assert _format_duration(seconds) == expected

    def test_long_durations_keep_raw_seconds_for_comparison(self):
        # Run-to-run comparison needs the unrounded value, so it stays in parens.
        assert "(174.9s)" in _format_duration(174.89)


class TestHelpOutput:
    def test_main_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
        assert "dry-run" in result.output
        assert "deps" in result.output
        assert "schema" in result.output

    def test_run_help_lists_checkpoint_format(self):
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "checkpoint-format" in result.output

    def test_run_help_lists_regenerate_choices(self):
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--regenerate" in result.output
        for choice in ("auto", "off", "sinks", "all"):
            assert choice in result.output

    def test_run_rejects_invalid_regenerate_choice(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(recipe_path), "--regenerate", "bogus"]
        )
        assert result.exit_code != 0
        assert "bogus" in result.output


class TestExecutorOptionResolution:
    """Dask sizing from the run config is a standing default, not a stray flag.

    Rejecting it the way an explicitly typed --dask-scheduler is rejected made
    every non-Dask run fail for anyone whose config carried those keys.
    """

    def test_config_dask_keys_do_not_fail_a_sequential_run(self):
        cfg = RunConfig(dask_scheduler="processes", dask_workers=4)
        executor, options = _resolve_executor_options("sequential", None, None, cfg)
        assert executor == "sequential"
        assert options == {}

    def test_config_dask_keys_apply_when_the_config_also_selects_dask(self):
        cfg = RunConfig(executor="dask", dask_scheduler="processes", dask_workers=4)
        executor, options = _resolve_executor_options("sequential", None, None, cfg)
        assert executor == "dask"
        assert options == {"scheduler": "processes", "n_workers": 4}

    def test_explicit_flags_win_over_config(self):
        cfg = RunConfig(executor="dask", dask_scheduler="processes", dask_workers=4)
        executor, options = _resolve_executor_options("dask", "threads", 2, cfg)
        assert options == {"scheduler": "threads", "n_workers": 2}

    def test_explicit_flag_with_non_dask_executor_still_fails(self):
        with pytest.raises(SystemExit):
            _resolve_executor_options("sequential", "processes", None, RunConfig())

    def test_cli_run_does_not_abort_on_config_supplied_dask_keys(
        self, tmp_path, monkeypatch
    ):
        """End to end through run_cmd, with the pipeline itself stubbed out."""
        import aa_recipe_manager.cli as cli
        from aa_recipe_manager.executor.base import ExecutionResult

        recipe_path = _write_recipe(tmp_path)
        cfg = tmp_path / "run.config.yaml"
        cfg.write_text("dask_scheduler: processes\ndask_workers: 4\n")

        seen: dict[str, object] = {}

        def fake_execute(recipe, **kwargs):
            seen.update(kwargs)
            return ExecutionResult()

        monkeypatch.setattr(cli.api, "execute", fake_execute)
        result = CliRunner().invoke(
            main, ["run", str(recipe_path), "--config", str(cfg)]
        )
        assert result.exit_code == 0, result.output
        assert "require --executor dask" not in result.output
        # Sequential run: the config's Dask sizing is simply not applied.
        assert seen["executor"] == "sequential"
        assert seen["executor_options"] is None


class TestRunCommandValidation:
    def test_no_checkpoints_conflicts_with_checkpoint_mode(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "run",
                str(recipe_path),
                "--no-checkpoints",
                "--checkpoint-mode",
                "explicit",
            ],
        )
        assert result.exit_code != 0
        assert "cannot be combined" in result.output

    def test_no_checkpoints_conflicts_with_checkpoint_step(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "run",
                str(recipe_path),
                "--no-checkpoints",
                "--checkpoint",
                "query_ncei",
            ],
        )
        assert result.exit_code != 0
        assert "cannot be combined" in result.output

    def test_invalid_checkpoint_format_rejected(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "run",
                str(recipe_path),
                "--checkpoint-format",
                "hdf5",
            ],
        )
        assert result.exit_code != 0


class TestKeepTemp:
    """--keep-temp leaves the run scratch dir in place for post-run profiling."""

    def test_keep_temp_flag_forwarded_to_api(self, tmp_path, monkeypatch):
        import aa_recipe_manager.cli as cli
        from aa_recipe_manager.executor.base import ExecutionResult

        captured = {}

        def fake_execute(recipe, **kwargs):
            captured.update(kwargs)
            return ExecutionResult()

        monkeypatch.setattr(cli.api, "execute", fake_execute)
        recipe_path = _write_recipe(tmp_path)
        result = CliRunner().invoke(main, ["run", str(recipe_path), "--keep-temp"])
        assert result.exit_code == 0, result.output
        assert captured.get("keep_temp") is True

    def test_default_does_not_keep_temp(self, tmp_path, monkeypatch):
        import aa_recipe_manager.cli as cli
        from aa_recipe_manager.executor.base import ExecutionResult

        captured = {}
        monkeypatch.setattr(
            cli.api, "execute",
            lambda recipe, **kw: captured.update(kw) or ExecutionResult(),
        )
        recipe_path = _write_recipe(tmp_path)
        CliRunner().invoke(main, ["run", str(recipe_path)])
        assert captured.get("keep_temp") is False

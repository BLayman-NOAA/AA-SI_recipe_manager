# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for spec libraries: ops a recipe uses that are not built in."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.exceptions import RecipeValidationError
from aa_recipe_manager.registry.loader import (
    CONTRIB_SPECS_ROOT,
    load_builtin_registry,
    load_registry,
    resolve_spec_dir,
)

_ADD_SPEC = """\
op: tst_add
description: Add two numbers.
category: processing
params:
  a:
    type: float
    required: true
  b:
    type: float
    required: true
outputs:
  total:
    type: float
implementations:
  - key: default
    callable_path: _stage9_helpers.add
    default: true
    dependency:
      name: aa-recipe-manager
      version: ">=0.1"
      source: local
    output_map:
      total: __return__
"""

_RECIPE = """\
recipe:
  name: spec_dir_recipe
  version: "1.0"
  schema_version: "1"

steps:
  - id: add
    op: tst_add
    params:
      a: 2
      b: 3
"""


@pytest.fixture
def spec_dir(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "tst_add.yaml").write_text(_ADD_SPEC, encoding="utf-8")
    (folder / "README.md").write_text("not a spec", encoding="utf-8")
    return folder


@pytest.fixture
def recipe_path(tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(_RECIPE, encoding="utf-8")
    return path


def test_a_library_op_is_loaded_when_a_recipe_uses_it(spec_dir):
    registry = load_registry({"tst_add", "compute_sv"}, [spec_dir])

    assert registry.has_spec("tst_add")
    assert registry.spec_file("tst_add") == spec_dir / "tst_add.yaml"


def test_a_library_op_is_not_loaded_when_unused(spec_dir):
    assert not load_registry({"compute_sv"}, [spec_dir]).has_spec("tst_add")
    assert not load_builtin_registry().has_spec("tst_add")


def test_a_library_never_replaces_a_builtin_op(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "shadow.yaml").write_text(
        textwrap.dedent("""            op: compute_sv
            description: A replacement.
            category: processing
        """),
        encoding="utf-8",
    )

    registry = load_registry({"compute_sv"}, [folder])

    assert registry.spec_file("compute_sv") != folder / "shadow.yaml"


def test_two_libraries_defining_a_needed_op_are_rejected(spec_dir, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "tst_add.yaml").write_text(_ADD_SPEC, encoding="utf-8")

    with pytest.raises(ValueError, match="tst_add"):
        load_registry({"tst_add"}, [spec_dir, other])


def test_an_op_in_no_library_is_left_for_the_dag_builder(spec_dir):
    assert not load_registry({"no_such_op"}, [spec_dir]).has_spec("no_such_op")


def test_a_missing_folder_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="Spec folder not found"):
        resolve_spec_dir(tmp_path / "absent")


def test_contrib_names_resolve_to_the_shipped_folder():
    assert resolve_spec_dir("contrib:echodataflow") == (
        CONTRIB_SPECS_ROOT / "echodataflow" / "specs"
    )


def test_the_echodataflow_contrib_set_loads_and_resolves():
    import importlib

    registry = load_registry({"plan_time_windows"})
    impl = registry.get_implementation("plan_time_windows", check_versions=False)
    module, _, name = impl.callable_path.rpartition(".")

    assert callable(getattr(importlib.import_module(module), name))


def test_the_echodataflow_reference_recipe_is_valid(tmp_path):
    recipe = (
        Path(__file__).resolve().parents[2]
        / "AA-SI_Full_Pipeline_Example" / "example_recipes" / "echodataflow"
        / "edf_raw2Sv_MVBS_postprocessing.yaml"
    )
    if not recipe.exists():
        pytest.skip("AA-SI_Full_Pipeline_Example is not checked out beside this repo")

    report = api.dry_run(
        recipe,
        inputs={"path_raw": str(tmp_path)},
        check_versions=False,
    )

    assert report.is_valid, report.errors


def test_an_unknown_contrib_name_lists_the_available_sets():
    with pytest.raises(FileNotFoundError, match="echodataflow"):
        resolve_spec_dir("contrib:no_such_set")


def test_a_recipe_using_a_folder_op_runs_with_spec_dirs(spec_dir, recipe_path):
    result = api.execute(recipe_path, no_checkpoints=True, spec_dirs=[spec_dir])

    assert result.outputs["add"]["total"] == 5


def test_without_spec_dirs_the_op_is_unknown(recipe_path):
    with pytest.raises(RecipeValidationError, match="unknown op 'tst_add'"):
        api.execute(recipe_path, no_checkpoints=True)


def test_spec_dirs_leave_builtin_step_hashes_unchanged(spec_dir):
    examples = Path(__file__).parent.parent / "examples"
    recipe = examples / "HB2407" / "demo_pipeline_1.yaml"
    if not recipe.exists():
        pytest.skip("reference recipe not present")

    plain = api.step_hashes(recipe, base_dir=recipe.parent)
    extended = api.step_hashes(recipe, base_dir=recipe.parent, spec_dirs=[spec_dir])

    assert {k: v.step_hash for k, v in plain.items()} == {
        k: v.step_hash for k, v in extended.items()
    }


def test_dry_run_reports_a_bad_spec_dir_instead_of_raising(recipe_path, tmp_path):
    report = api.dry_run(recipe_path, spec_dirs=[tmp_path / "absent"])

    assert not report.is_valid
    assert "Spec folder not found" in report.errors[0]


def test_cli_dry_run_takes_spec_dir(cli_runner, spec_dir, recipe_path):
    without = cli_runner.invoke(main, ["dry-run", str(recipe_path)])
    with_dir = cli_runner.invoke(
        main, ["dry-run", str(recipe_path), "--spec-dir", str(spec_dir)]
    )

    assert without.exit_code == 1
    assert "tst_add" in without.output
    assert with_dir.exit_code == 0, with_dir.output


def test_cli_run_takes_spec_dir(cli_runner, spec_dir, recipe_path, tmp_path):
    result = cli_runner.invoke(
        main,
        [
            "run", str(recipe_path),
            "--no-checkpoints",
            "--outputs-dir", str(tmp_path / "outputs"),
            "--spec-dir", str(spec_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Executed 1 step(s)" in result.output


def test_the_run_config_names_spec_libraries(
    cli_runner, spec_dir, recipe_path, tmp_path
):
    """A relative folder in the config is found beside the config file."""
    config = tmp_path / "run.config.yaml"
    config.write_text(f"spec_dirs: [{spec_dir.name}]\n", encoding="utf-8")

    result = cli_runner.invoke(
        main,
        [
            "run", str(recipe_path), "--config", str(config),
            "--no-checkpoints", "--outputs-dir", str(tmp_path / "outputs"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Executed 1 step(s)" in result.output


def test_a_contrib_op_needs_no_spec_dir_anywhere(tmp_path):
    recipe = tmp_path / "windows.yaml"
    recipe.write_text(textwrap.dedent("""        recipe:
          name: windows_only
          version: "1.0"
          schema_version: "1"
        steps:
          - id: plan
            op: plan_time_windows
            inputs:
              raw_file_paths: ["D20240801-T120000.raw", "D20240801-T121000.raw"]
            params:
              window_mins: 10
    """), encoding="utf-8")

    hashes = api.step_hashes(recipe)
    result = api.execute(recipe, no_checkpoints=True)

    assert "plan" in hashes
    assert [w["label"] for w in result.outputs["plan"]["windows"]] == [
        "20240801T120000", "20240801T121000",
    ]

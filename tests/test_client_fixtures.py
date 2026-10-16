# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the small recipes the recipe manager client is tested against."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.parser.yaml_reader import load_recipe

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"
RECIPES = [
    "flat.yaml",
    "with_sub.yaml",
    "mapped.yaml",
    "custom_no_cache_key.yaml",
    "slow.yaml",
    "failing.yaml",
    "paths.yaml",
]


@pytest.mark.parametrize("name", RECIPES)
def test_every_fixture_loads_and_validates(name):
    report = api.dry_run(FIXTURES / name, check_versions=False, base_dir=FIXTURES)
    assert report.is_valid, report.errors


def test_fixture_hashes_are_stable():
    expected = json.loads(
        (FIXTURES / "expected_hashes.json").read_text(encoding="utf-8")
    )
    found = {
        name: {
            k: v.step_hash
            for k, v in api.step_hashes(FIXTURES / name, base_dir=FIXTURES).items()
        }
        for name in RECIPES
    }
    assert found == expected, (
        "Fixture step hashes changed. Regenerate expected_hashes.json only when "
        "changing a hash is the point of the change."
    )


def test_with_sub_include_tree():
    recipe = load_recipe(FIXTURES / "with_sub.yaml")
    leaf, chain = recipe.include_blocks
    assert (leaf.source, leaf.path) == ("leaf.yaml", "sub/leaf.yaml")
    assert (chain.source, chain.path) == ("chain.yaml", "sub/chain.yaml")
    assert leaf.overrides == {"value": "${chain_inc.out}", "k": "${inputs.k}"}
    assert chain.overrides == {"source": "${seed.items}", "k": 5}
    assert leaf.map_over == "${inputs.source}"
    assert chain.map_over is None
    assert chain.name == "client_chain"
    assert chain.step_ids == ["chain_inc", "leaf_add"]
    assert leaf.bindings == {
        "value": [{"step": "leaf_add", "slot": "inputs.x"}],
        "k": [{"step": "leaf_add", "slot": "params.k"}],
    }
    # Through the nested include to the step that reads it, and the map over
    # the chain gives its included leaf.
    assert chain.bindings == {
        "source": [
            {"step": "chain_inc", "slot": "map_over"},
            {"step": "leaf_add", "slot": "map_over"},
        ],
        "k": [{"step": "leaf_add", "slot": "params.k"}],
    }
    # Every sub-recipe input is overridden, so none reaches the root.
    assert list(chain.inputs) == ["source", "k"]
    assert list(leaf.inputs) == ["value", "k"]
    assert chain.promoted == leaf.promoted == []

    graph = api.recipe_graph(FIXTURES / "with_sub.yaml")
    includes = {include["id"]: include for include in graph["includes"]}
    assert list(includes) == ["inc:sub/leaf.yaml", "inc:sub/chain.yaml"]
    assert includes["inc:sub/leaf.yaml"]["parent"] == "inc:sub/chain.yaml"
    assert includes["inc:sub/chain.yaml"]["parent"] is None
    assert includes["inc:sub/chain.yaml"]["bindings"] == chain.bindings
    assert includes["inc:sub/chain.yaml"]["consumed"] == [
        {"step": "leaf_add", "output": "out", "by": ["total"]}
    ]
    steps = {n["id"]: n for n in graph["nodes"] if n["kind"] == "step"}
    assert steps["chain_inc"]["include"] == "inc:sub/chain.yaml"
    assert steps["chain_inc"]["document"] == "sub/chain.yaml"
    assert steps["leaf_add"]["include"] == "inc:sub/leaf.yaml"
    assert steps["seed"]["include"] is None
    assert steps["seed"]["document"] == "with_sub.yaml"
    inputs = {n["id"]: n for n in graph["nodes"] if n["kind"] == "input"}
    assert inputs["in:n"]["declared_in"] == "with_sub.yaml"



def test_include_bindings_name_the_slots_a_value_test_misses():
    """A number override feeds a param whose name does not end the input's."""
    herring = Path(__file__).parents[1] / "docs" / "recipes" / "HB2407"
    recipe = load_recipe(herring / "Herring_10-15_per_file.yaml")
    blocks = {block.source: block for block in recipe.include_blocks}
    block = blocks["processing_lvl_2_per_file.yaml"]
    assert block.bindings["surface_mask_depth"] == [
        {"step": "create_surface_mask", "slot": "params.surface_depth_m"}
    ]


def test_input_nodes_name_the_sub_recipe_that_declares_them():
    """Promoted inputs trace to their file; the root keeps its own."""
    hb1603 = Path(__file__).parents[1] / "docs" / "recipes" / "HB1603"
    graph = api.recipe_graph(hb1603 / "survey_echogram_pyramid.yaml")
    inputs = {n["id"]: n for n in graph["nodes"] if n["kind"] == "input"}
    assert inputs["in:cruise_name"]["declared_in"] == (
        "sub_recipes/processing_lvl_0_user.yaml"
    )
    # Also declared in the viewer seabed level, which the root overrides.
    assert inputs["in:crop_max_range_m"]["declared_in"] == (
        "survey_echogram_pyramid.yaml"
    )
    # An explicit default: null leaves the input optional.
    assert inputs["in:noise_background_max"]["required"] is False
    includes = {include["source"]: include for include in graph["includes"]}
    lvl_0 = includes["sub_recipes/processing_lvl_0_user.yaml"]
    assert [entry["name"] for entry in lvl_0["inputs"]] == [
        "cruise_name",
        "file_time_start",
        "file_time_end",
        "cal_input_folder",
        "calibration_output_dir",
        "cruise_id",
        "record_author",
        "max_scan_bytes",
        "match_tolerances",
    ]


def test_recipe_graph_attaches_given_hashes():
    path = FIXTURES / "flat.yaml"
    hashes = {k: v.step_hash for k, v in api.step_hashes(path).items()}
    graph = api.recipe_graph(path, hashes=hashes)
    assert graph["schema"] == 2
    assert graph["title"] == "client_flat"
    for node in graph["nodes"]:
        if node["kind"] == "step":
            assert node["hash"] == hashes[node["id"]]


def test_dry_run_checks_relative_paths_under_base_dir(tmp_path, monkeypatch):
    recipe = tmp_path / "recipes" / "rel.yaml"
    (recipe.parent / "Auxiliary").mkdir(parents=True)
    recipe.write_text(
        "recipe: {name: rel, version: '1.0', schema_version: '1'}\n"
        "steps:\n"
        "  - id: read\n"
        "    op: custom\n"
        "    params: {folder: ./Auxiliary}\n"
        "    custom_spec:\n"
        "      description: Read a folder.\n"
        "      callable_path: _stage9_helpers.const7\n"
        "      params: {folder: {type: path}}\n"
        "      dependency: {name: pytest, version: '>=7.0', source: pypi}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert not api.dry_run(recipe, check_versions=False).is_valid
    report = api.dry_run(recipe, check_versions=False, base_dir=recipe.parent)
    assert report.is_valid, report.errors


def test_run_config_finds_the_file_beside_the_recipe(tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    recipe = tmp_path / "flat.yaml"
    recipe.write_text((FIXTURES / "flat.yaml").read_text(encoding="utf-8"))
    (tmp_path / "flat.config.yaml").write_text(
        "inputs: {factor: 4}\nexecutor: dask\n", encoding="utf-8"
    )
    found = api.run_config(recipe)
    assert found.inputs == {"factor": 4}
    assert found.executor == "dask"


def test_run_config_searches_the_given_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "recipes"
    folder.mkdir()
    (folder / "aa-recipe.config.yaml").write_text("executor: dask\n")
    assert api.run_config(folder / "flat.yaml").executor != "dask"
    found = api.run_config(folder / "flat.yaml", cwd=folder)
    assert found.executor == "dask"


def test_op_catalog_lists_categories():
    catalog = api.op_catalog()
    assert catalog["counts"]["ops"] == len(catalog["ops"]) > 0
    assert "calibration" in catalog["categories"]


def test_cli_graph_prints_steps_and_json(cli_runner):
    path = str(FIXTURES / "with_sub.yaml")
    result = cli_runner.invoke(main, ["graph", path])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["seed", "custom", "-"]
    assert lines[1].split() == ["chain_inc", "custom", "inc:sub/chain.yaml"]

    result = cli_runner.invoke(main, ["graph", path, "--json"])
    assert result.exit_code == 0, result.output
    graph = json.loads(result.output)
    assert graph["schema"] == 2
    assert [i["id"] for i in graph["includes"]] == [
        "inc:sub/leaf.yaml",
        "inc:sub/chain.yaml",
    ]


def test_cli_ops_filters_by_category(cli_runner):
    result = cli_runner.invoke(main, ["ops", "--category", "calibration", "--json"])
    assert result.exit_code == 0, result.output
    catalog = json.loads(result.output)
    assert catalog["ops"]
    assert {op["category"] for op in catalog["ops"]} == {"calibration"}
    assert catalog["categories"] == ["calibration"]
    assert catalog["counts"]["ops"] == len(catalog["ops"])
    assert catalog["counts"]["implementations"] == sum(
        len(op["implementations"]) for op in catalog["ops"]
    )

    result = cli_runner.invoke(main, ["ops"])
    assert result.exit_code == 0
    assert any(line.startswith("compute_sv ") for line in result.output.splitlines())

    result = cli_runner.invoke(main, ["ops", "--category", "nope"])
    assert result.exit_code == 1
    assert "unknown category" in result.output


def test_include_path_falls_back_to_absolute_across_drives(monkeypatch):
    def no_relative_path(path, start=None):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr("os.path.relpath", no_relative_path)
    recipe = load_recipe(FIXTURES / "with_sub.yaml")
    leaf, chain = recipe.include_blocks
    assert chain.path == (FIXTURES / "sub" / "chain.yaml").resolve().as_posix()
    assert leaf.path == (FIXTURES / "sub" / "leaf.yaml").resolve().as_posix()

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The ``aa-recipe artifacts`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"
FLAT = str(FIXTURES / "flat.yaml")


def _saved_bump(tmp_path) -> Path:
    cache = tmp_path / "cache"
    api.execute(
        FLAT, user_cache_dir=cache, outputs_dir=tmp_path / "outputs",
        targets=["bump"],
    )
    return cache


def test_text_lists_the_artifact(cli_runner, tmp_path):
    cache = _saved_bump(tmp_path)
    result = cli_runner.invoke(
        main, ["artifacts", FLAT, "bump", "--user-cache-dir", str(cache)]
    )
    assert result.exit_code == 0, result.output
    found = api.step_artifacts(FLAT, "bump", user_cache_dir=cache)
    head, row = result.output.strip().splitlines()
    assert head.startswith(f"bump  {found.step_hash[:12]}  held by: user")
    assert row.split() == ["out", "json", "user", found.artifacts[0].url]


def test_json_is_the_api_answer(cli_runner, tmp_path):
    cache = _saved_bump(tmp_path)
    result = cli_runner.invoke(
        main, ["artifacts", FLAT, "bump", "--user-cache-dir", str(cache), "--json"]
    )
    assert result.exit_code == 0, result.output
    found = api.step_artifacts(FLAT, "bump", user_cache_dir=cache)
    assert json.loads(result.output) == found.to_dict()


def test_nothing_found_is_not_an_error(cli_runner, tmp_path):
    result = cli_runner.invoke(
        main, ["artifacts", FLAT, "bump", "--user-cache-dir", str(tmp_path / "c")]
    )
    assert result.exit_code == 0, result.output
    assert "held by: none" in result.output


def test_unknown_step_exits_1(cli_runner, tmp_path):
    result = cli_runner.invoke(
        main, ["artifacts", FLAT, "bmp", "--user-cache-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "unknown step id" in result.output
    assert "'bump'" in result.output


# CLI inputs arrive as strings, as for 'run' and 'preview'.
@pytest.mark.filterwarnings("ignore:Step 'scaled'")
def test_input_changes_the_hash(cli_runner, tmp_path):
    result = cli_runner.invoke(
        main,
        ["artifacts", FLAT, "scaled", "--user-cache-dir", str(tmp_path),
         "--input", "factor=4", "--json"],
    )
    assert result.exit_code == 0, result.output
    four = api.step_hashes(FLAT, inputs={"factor": "4"})["scaled"].step_hash
    assert json.loads(result.output)["step_hash"] == four
    assert four != api.step_hashes(FLAT)["scaled"].step_hash


def test_base_dir(cli_runner, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    recipe = FIXTURES / "paths.yaml"
    monkeypatch.chdir(FIXTURES)
    api.execute(
        recipe, user_cache_dir=cache, outputs_dir=tmp_path / "outputs",
        targets=["bump"],
    )
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(
        main,
        ["artifacts", str(recipe), "bump", "--user-cache-dir", str(cache),
         "--base-dir", str(FIXTURES), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["held_by"] == ["user"]


def test_mapped_rows_name_instance_and_item(cli_runner, tmp_path):
    cache = tmp_path / "cache"
    recipe = str(FIXTURES / "mapped.yaml")
    api.execute(
        recipe, user_cache_dir=cache, outputs_dir=tmp_path / "outputs",
        targets=["proc2"], checkpoint_steps=["seg"],
    )
    result = cli_runner.invoke(
        main, ["artifacts", recipe, "proc2", "--user-cache-dir", str(cache)]
    )
    assert result.exit_code == 0, result.output
    head, *rows = result.output.strip().splitlines()
    assert "mapped  held by: user  3 of 3 instances found" in head
    assert [row.split()[3:5] for row in rows] == [
        ["#0", "10"], ["#1", "20"], ["#2", "30"],
    ]

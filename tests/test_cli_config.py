# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""``aa-recipe config init`` and ``config set``: a run config edited in place."""

from __future__ import annotations

from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.editing import run_config
from editing.test_run_config import RECIPE


@pytest.fixture
def recipe(tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "r.yaml"
    path.write_text(RECIPE, encoding="utf-8")
    return path


def test_init_writes_the_template_and_refuses_an_existing_file(cli_runner, recipe):
    target = recipe.with_name("r.config.yaml")

    result = cli_runner.invoke(main, ["config", "init", str(recipe)])

    assert result.exit_code == 0, result.output
    assert f"wrote {target}" in result.output
    assert target.read_text(encoding="utf-8") == run_config.template(
        "r.yaml", [("raw", "Raw files.")]
    )
    target.write_text("executor: dask\n", encoding="utf-8")
    result = cli_runner.invoke(main, ["config", "init", str(recipe)])
    assert result.exit_code == 1
    assert "r.config.yaml exists already. Pass --overwrite" in result.output
    assert target.read_text(encoding="utf-8") == "executor: dask\n"
    result = cli_runner.invoke(main, ["config", "init", str(recipe), "--overwrite"])
    assert result.exit_code == 0, result.output
    assert "raw: null" in target.read_text(encoding="utf-8")


def test_set_edits_the_config_in_use_keeping_its_comments(cli_runner, recipe):
    assert cli_runner.invoke(main, ["config", "init", str(recipe)]).exit_code == 0
    target = recipe.with_name("r.config.yaml")
    before = target.read_text(encoding="utf-8")

    result = cli_runner.invoke(
        main,
        [
            "config",
            "set",
            str(recipe),
            "executor=dask",
            "dask_workers=4",
            "--input",
            "raw=data/x.raw",
            "--input",
            "level=3.0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"wrote {target}" in result.output
    text = target.read_text(encoding="utf-8")
    expected = (
        before.replace("executor: null", "executor: dask")
        .replace("dask_workers: null", "dask_workers: 4")
        .replace("  raw: null\n", "  raw: data/x.raw\n  level: 3.0\n")
    )
    assert text == expected
    loaded = api.run_config(recipe)
    assert loaded.executor == "dask" and loaded.dask_workers == 4
    assert loaded.inputs == {"raw": "data/x.raw", "level": 3.0}

    result = cli_runner.invoke(
        main, ["config", "set", str(recipe), "executor=dask", "--unset-input", "level"]
    )
    assert result.exit_code == 0, result.output
    assert api.run_config(recipe).inputs == {"raw": "data/x.raw"}

    result = cli_runner.invoke(main, ["config", "set", str(recipe), "executor=dask"])
    assert result.exit_code == 0, result.output
    assert "No changes" in result.output


def test_set_takes_a_named_config_and_refuses_bad_settings(cli_runner, recipe):
    other = recipe.with_name("shared.yaml")
    other.write_text("# Shared.\nexecutor: sequential\n", encoding="utf-8")
    args = ["config", "set", str(recipe)]

    result = cli_runner.invoke(main, [*args, "executor=dask"])
    assert result.exit_code == 1
    assert "uses no run config file" in result.output

    result = cli_runner.invoke(main, [*args, "executor=dask", "--config", str(other)])
    assert result.exit_code == 0, result.output
    assert other.read_text(encoding="utf-8") == "# Shared.\nexecutor: dask\n"

    result = cli_runner.invoke(main, [*args, "colour=red", "--config", str(other)])
    assert result.exit_code == 1
    assert "Not a run setting: colour" in result.output
    result = cli_runner.invoke(main, [*args, "--config", str(other)])
    assert result.exit_code == 1
    assert "Nothing to set" in result.output


def test_set_writes_a_whole_number_for_a_float_input_as_a_float(cli_runner, recipe):
    assert cli_runner.invoke(main, ["config", "init", str(recipe)]).exit_code == 0
    target = recipe.with_name("r.config.yaml")

    result = cli_runner.invoke(
        main, ["config", "set", str(recipe), "--input", "level=1800"]
    )

    assert result.exit_code == 0, result.output
    assert "  level: 1800.0\n" in target.read_text(encoding="utf-8")
    assert api.run_config(recipe).inputs["level"] == 1800.0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["executor=bogus"], "executor must be sequential, dask or prefect"),
        (["dask_workers=two"], "dask_workers must be an integer"),
        (["temp_dir=3"], "temp_dir must be a string"),
        (["--input", "level=.nan"], "NaN and infinity cannot be set"),
        (["--input", "level=.inf"], "NaN and infinity cannot be set"),
    ],
)
def test_set_refuses_bad_values(cli_runner, recipe, args, message):
    assert cli_runner.invoke(main, ["config", "init", str(recipe)]).exit_code == 0
    target = recipe.with_name("r.config.yaml")
    before = target.read_bytes()

    result = cli_runner.invoke(main, ["config", "set", str(recipe), *args])

    assert result.exit_code == 1
    assert message in result.output
    assert target.read_bytes() == before


def test_set_warns_of_an_input_the_recipe_does_not_declare(cli_runner, recipe):
    assert cli_runner.invoke(main, ["config", "init", str(recipe)]).exit_code == 0

    result = cli_runner.invoke(
        main, ["config", "set", str(recipe), "--input", "nope=1"]
    )

    assert result.exit_code == 0, result.output
    assert "r.yaml declares no input 'nope'" in result.output
    assert api.run_config(recipe).inputs["nope"] == 1


def test_set_only_this_recipe_writes_its_own_copy(cli_runner, recipe):
    text = "# Shared.\nexecutor: sequential\n# End.\n"
    shared = recipe.with_name("aa-recipe.config.yaml")
    shared.write_text(text, encoding="utf-8")
    own = recipe.with_name("r.config.yaml")
    args = ["config", "set", str(recipe), "executor=dask", "--only-this-recipe"]

    result = cli_runner.invoke(main, args)

    assert result.exit_code == 0, result.output
    assert f"wrote {own}" in result.output
    assert own.read_text(encoding="utf-8") == text.replace("sequential", "dask")
    assert shared.read_text(encoding="utf-8") == text
    assert api.run_config(recipe).source == own

    result = cli_runner.invoke(main, args)
    assert result.exit_code == 1
    assert "r.config.yaml exists already. Edit it without" in result.output

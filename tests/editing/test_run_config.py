# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Edits to a run config file's text keep its comments where they belong."""

from __future__ import annotations

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.editing import RecipeDocument, TargetExists, run_config
from aa_recipe_manager.parser.yaml_reader import load_recipe

RECIPE = """\
recipe: {name: r, version: "1.0", schema_version: "1"}
inputs:
  raw:
    type: str
    description: |
      Raw
      files.
  window:
    type: str
    default: null
  level:
    type: float
    default: 2.0
steps:
  - id: start
    op: custom
    params:
      raw: ${inputs.raw}
    custom_spec:
      description: Seven.
      callable_path: _stage9_helpers.const7
      outputs:
        v: {type: int}
      output_map: {v: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""


def edited(text, inputs=None, **settings):
    document = RecipeDocument.from_text(text, "x.config.yaml")
    run_config.apply(document, inputs, settings)
    return document.text


@pytest.mark.parametrize(
    "text, inputs, settings, expected",
    [
        ("", {"a": 1}, {"executor": "dask"}, "executor: dask\ninputs:\n  a: 1\n"),
        ("", None, {"executor": None}, ""),
        ("# note\n", {"a": 1}, {}, "# note\ninputs:\n  a: 1\n"),
        (
            "executor: sequential\n\n# Inputs.\ninputs:\n  a: 1\n",
            None,
            {"dask_workers": 2},
            "executor: sequential\ndask_workers: 2\n\n# Inputs.\ninputs:\n  a: 1\n",
        ),
        (
            "inputs:\n  a: 1  # one\n  b: 2  # two\n\n# Settings.\nexecutor: dask\n",
            {"a": 1},
            {},
            "inputs:\n  a: 1  # one\n\n# Settings.\nexecutor: dask\n",
        ),
        (
            "inputs:\n  a: 1\n\n# Settings.\nexecutor: dask\n",
            {"a": 1, "b": 2},
            {},
            "inputs:\n  a: 1\n  b: 2\n\n# Settings.\nexecutor: dask\n",
        ),
        (
            "storage_options: null\n\n# Inputs.\ninputs:\n",
            None,
            {"storage_options": {"token": "t"}},
            "storage_options: {token: t}\n\n# Inputs.\ninputs:\n",
        ),
        (
            "executor: dask\n\n# Inputs.\ninputs:\n",
            None,
            {"storage_options": {"token": "t"}},
            "executor: dask\nstorage_options: {token: t}\n\n# Inputs.\ninputs:\n",
        ),
        (
            "storage_options:\n  # inner\n  token: a\n\n# Next.\nexecutor: dask\n",
            None,
            {"storage_options": {"token": "b"}},
            "storage_options:\n  # inner\n  token: b\n\n# Next.\nexecutor: dask\n",
        ),
        ("inputs:\n  s: 1\n", {"s": 1.0}, {}, "inputs:\n  s: 1.0\n"),
        ('inputs:\n  s: "a"\n', {"s": "b"}, {}, 'inputs:\n  s: "b"\n'),
    ],
)
def test_apply(text, inputs, settings, expected):
    assert edited(text, inputs, **settings) == expected


def test_unchanged_values_leave_the_text_alone():
    text = "user_cache_dir: ./c  # mine\ninputs:\n  a: [1, 2]\n  n: .nan\n"
    assert edited(text, {"a": [1, 2], "n": float("nan")}, user_cache_dir="./c") == text


def test_template_loads_as_an_empty_config(tmp_path):
    text = run_config.template("r.yaml", [("raw", "Raw\nfiles.")])
    assert "  # Required. Raw files.\n  raw: null\n" in text
    path = tmp_path / "r.config.yaml"
    path.write_text(text, encoding="utf-8")
    loaded = api.run_config(tmp_path / "r.yaml")
    assert loaded.source == path and loaded.inputs == {"raw": None}
    assert loaded.user_cache_dir is None and loaded.dask_workers is None


def test_create_lists_the_required_inputs_and_refuses_an_existing_file(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(RECIPE, encoding="utf-8")
    recipe = load_recipe(path)
    assert run_config.required_inputs(recipe) == [("raw", "Raw\nfiles.\n")]

    target = run_config.create(recipe, path)
    assert target == run_config.config_candidate(path) == tmp_path / "r.config.yaml"
    assert target.read_text(encoding="utf-8") == run_config.template(
        "r.yaml", [("raw", "Raw files.")]
    )
    target.write_text("executor: dask\n", encoding="utf-8")
    with pytest.raises(TargetExists, match="exists already") as raised:
        run_config.create(recipe, path)
    assert raised.value.paths == [target]
    assert target.read_text(encoding="utf-8") == "executor: dask\n"
    run_config.create(recipe, path, overwrite=True)
    assert "raw: null" in target.read_text(encoding="utf-8")


def test_required_inputs_are_those_dry_run_reports(tmp_path):
    text = RECIPE.replace(
        "  level:\n",
        "  forced:\n    type: str\n    required: true\n"
        "  unread:\n    type: str\n  level:\n",
    )
    path = tmp_path / "r.yaml"
    path.write_text(text, encoding="utf-8")
    recipe = load_recipe(path)
    names = [name for name, _ in run_config.required_inputs(recipe)]
    assert names == ["raw", "forced"]
    report = api.dry_run(path, check_versions=False, base_dir=tmp_path)
    assert report.required_inputs == names


def test_a_file_that_is_not_a_mapping_is_refused():
    with pytest.raises(ValueError, match="mapping"):
        edited("- a\n- b\n", {"a": 1})


def test_an_unknown_setting_is_refused():
    with pytest.raises(ValueError, match="Not a run setting: colour"):
        edited("executor: dask\n", None, colour="red")


def test_copy_to_keeps_the_text_and_refuses_an_existing_file(tmp_path):
    shared = tmp_path / "aa-recipe.config.yaml"
    text = b"# Shared.\r\nexecutor: sequential\r\n# After.\r\n"
    shared.write_bytes(text)
    target = tmp_path / "r.config.yaml"

    copy = run_config.copy_to(RecipeDocument.load(shared), target)
    assert not target.exists()
    run_config.apply(copy, None, {"executor": "dask"})
    copy.write()

    assert target.read_bytes() == text.replace(b"sequential", b"dask")
    assert shared.read_bytes() == text
    with pytest.raises(TargetExists, match="exists already") as raised:
        run_config.copy_to(RecipeDocument.load(shared), target)
    assert raised.value.paths == [target]

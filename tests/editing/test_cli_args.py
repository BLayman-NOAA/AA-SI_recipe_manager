# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for reading ``aa-recipe edit`` option values into operations."""

from __future__ import annotations

import json

import pytest

from aa_recipe_manager.editing import (
    RecipeWorkspace,
    ReplaceText,
    SetCheckpoint,
    SetIncludeOverride,
    SetParam,
    SetRecipeInput,
    UnsetIncludeOverride,
    UnsetParam,
)
from aa_recipe_manager.editing.cli_args import (
    edit_operations,
    include_key,
    input_document,
    operations_from_json,
    param_key,
    split_assignment,
    yaml_value,
)

from .conftest import copy_fixture


def test_split_assignment():
    assert split_assignment(" a.b = 5 ", "--set", "K=V") == ("a.b", "5")
    assert split_assignment("a=b=c", "--set", "K=V") == ("a", "b=c")
    assert split_assignment("a=", "--set", "K=V") == ("a", "")
    for bad in ("a", "=5", " =5"):
        with pytest.raises(ValueError, match="--set must be in K=V format"):
            split_assignment(bad, "--set", "K=V")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("5", 5),
        ("5.0", 5.0),
        ("true", True),
        ("[1, 2]", [1, 2]),
        ("{a: 1}", {"a": 1}),
        ("word", "word"),
        ("", None),
        ('""', ""),
        ("${inputs.x}", "${inputs.x}"),
        ("2016-08-14T14:50:00", "2016-08-14T14:50:00"),
        ("2016-08-14 14:50:00", "2016-08-14 14:50:00"),
        ("2016-08-14", "2016-08-14"),
    ],
)
def test_yaml_value(text, value):
    read = yaml_value(text, "--set")
    assert read == value
    assert type(read) is type(value)


def test_yaml_value_rejects_invalid_yaml():
    with pytest.raises(ValueError, match="--set: VALUE '\\[1' is not valid YAML"):
        yaml_value("[1", "--set")


def test_param_key():
    assert param_key("s.p", "--set") == ("s", "p", ())
    assert param_key("s.filters.CRUISE_NAME", "--set") == (
        "s",
        "filters",
        ("CRUISE_NAME",),
    )
    assert param_key("s.ext.0", "--set") == ("s", "ext", (0,))
    for bad in ("s", "s.", ".p", "s..p"):
        with pytest.raises(ValueError, match="--unset needs STEP.PARAM"):
            param_key(bad, "--unset")


def test_include_key():
    assert include_key("sub/x.yaml:k", "--override") == ("sub/x.yaml", "k")
    assert include_key("C:/a/x.yaml:k", "--override") == ("C:/a/x.yaml", "k")
    for bad in ("k", ":k", "sub/x.yaml:"):
        with pytest.raises(ValueError, match="--override needs INCLUDE:NAME"):
            include_key(bad, "--override")


def test_input_document(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))

    assert input_document(ws, "", "n") == "with_sub.yaml"
    assert input_document(ws, "sub/leaf.yaml", "k") == "sub/leaf.yaml"
    with pytest.raises(ValueError, match="no file of the recipe declares input"):
        input_document(ws, "", "nope")
    with pytest.raises(ValueError, match="sub/leaf.yaml, sub/chain.yaml"):
        input_document(ws, "", "k")


def test_operations_from_json():
    ops = [SetParam("a", "b", 5), SetCheckpoint("a", None)]
    text = json.dumps([op.to_dict() for op in ops])

    assert operations_from_json(text) == ops
    with pytest.raises(ValueError, match="--ops: not valid JSON"):
        operations_from_json("[")
    with pytest.raises(ValueError, match="must hold a JSON list"):
        operations_from_json("{}")
    with pytest.raises(ValueError, match="--ops entry 1: Unknown operation kind"):
        operations_from_json(
            '[{"kind": "replace_text", "document": "a", "text": ""}, {"kind": "nope"}]'
        )


def test_edit_operations_in_order(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))

    ops = edit_operations(
        ws,
        sets=["seed.n=4", "seed.cfg.x=[1]"],
        unsets=["seed.n"],
        checkpoints=["seed=always", "total=none"],
        overrides=["sub/chain.yaml:k=7"],
        unset_overrides=["sub/leaf.yaml:k"],
        input_defaults=["n=5", "sub/leaf.yaml:k="],
        replace_texts=[("with_sub.yaml", "a: 1\n")],
    )

    assert ops == [
        SetParam("seed", "n", 4),
        SetParam("seed", "cfg", [1], ("x",)),
        UnsetParam("seed", "n"),
        SetCheckpoint("seed", "always"),
        SetCheckpoint("total", None),
        SetIncludeOverride("sub/chain.yaml", "k", 7),
        UnsetIncludeOverride("sub/leaf.yaml", "k"),
        SetRecipeInput("with_sub.yaml", "n", "default", 5),
        SetRecipeInput("sub/leaf.yaml", "k", "default", None),
        ReplaceText("with_sub.yaml", "a: 1\n"),
    ]


def test_edit_operations_rejects_a_checkpoint_value(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))

    with pytest.raises(ValueError, match="always, never, save or none"):
        edit_operations(ws, checkpoints=["seed=sometimes"])

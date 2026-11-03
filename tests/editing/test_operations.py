# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for edit operations: minimal diffs, exact inverses and rejection."""

from __future__ import annotations

import difflib
import json
import shutil

import pytest

from aa_recipe_manager.editing import (
    KINDS,
    EditRejected,
    HistoryEntry,
    RecipeWorkspace,
    ReplaceText,
    SetCheckpoint,
    SetIncludeOverride,
    SetParam,
    SetRecipeInput,
    UnknownTarget,
    UnsetIncludeOverride,
    UnsetParam,
    UnsetRecipeInput,
    operation_from_dict,
)
from aa_recipe_manager.parser.yaml_reader import load_recipe
from test_disposal_fan_in import _recipe, helpers  # noqa: F401

from .conftest import (
    DOCS_ROOTS,
    FIXTURES,
    HAVE_EXAMPLES,
    UC1,
    UC1_ROOTS,
    copy_fixture,
)

#: A step with nested and list params, for operations with a ``path``.
NESTED = """\
recipe:
  name: nested
  version: "1.0"
  schema_version: "1"

steps:
  - id: pick
    op: custom
    params:
      filters:
        CRUISE_NAME: HB1603
        LEG: 1
      extensions:
        - .raw
        - .idx
    custom_spec:
      description: Pick files.
      callable_path: _stage9_helpers.const7
      params:
        filters: {type: dict}
        extensions: {type: list}
      outputs:
        v: {type: int}
      output_map: {v: __return__}
"""

#: Steps whose params end the step, followed by a blank line and a comment.
TRAILING = """\
recipe:
  name: trailing
  version: "1.0"
  schema_version: "1"

inputs:
  n:
    type: int
    default: 3

steps:
  - id: first
    op: custom
    custom_spec:
      description: First.
      callable_path: _stage9_helpers.const7
      outputs:
        v: {type: int}
      output_map: {v: __return__}
    params:
      a: 1  # why

  # The second step.
  - id: second
    op: custom
    custom_spec:
      description: Second.
      callable_path: _stage9_helpers.const7
      outputs:
        v: {type: int}
      output_map: {v: __return__}
    params:
      b: 2

  - id: third
    op: noop
    map_over: ${first.v}

  - id: fourth
    op: noop
"""


def _open(tmp_path, name: str) -> RecipeWorkspace:
    """A workspace on a copy of a fixture, or of NESTED or TRAILING."""
    inline = {"nested.yaml": NESTED, "trailing.yaml": TRAILING}
    if name in inline:
        path = tmp_path / name
        path.write_text(inline[name], encoding="utf-8")
    else:
        path = copy_fixture(tmp_path, name)
    return RecipeWorkspace.open(path)


def _bytes(ws: RecipeWorkspace) -> dict[str, bytes]:
    return {label: ws.document(label).to_bytes() for label in ws.files()}


def _changed_lines(old: str, new: str) -> list[str]:
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), n=0)
    return [
        line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


WITH_SUB_TEXT = (FIXTURES / "with_sub.yaml").read_text(encoding="utf-8")

CASES = [
    ("with_sub.yaml", SetParam("seed", "n", 4)),
    ("with_sub.yaml", SetParam("leaf_add", "k", 9)),
    ("with_sub.yaml", SetParam("seed", "extra", [1, 2])),
    ("with_sub.yaml", SetParam("chain_inc", "p", {"a": 1})),
    ("nested.yaml", SetParam("pick", "filters", "HB1507", ("CRUISE_NAME",))),
    ("nested.yaml", SetParam("pick", "filters", 2, ("NEW",))),
    ("nested.yaml", SetParam("pick", "extensions", ".bot", (1,))),
    ("nested.yaml", UnsetParam("pick", "filters", ("LEG",))),
    ("with_sub.yaml", UnsetParam("seed", "n")),
    ("trailing.yaml", UnsetParam("second", "b")),
    ("sv.yaml", SetCheckpoint("sv", None)),
    ("with_sub.yaml", SetCheckpoint("chain_inc", "always")),
    ("with_sub.yaml", SetIncludeOverride("sub/chain.yaml", "k", 7)),
    ("with_sub.yaml", SetIncludeOverride("sub/leaf.yaml", "k", "${inputs.source}")),
    ("with_sub.yaml", SetIncludeOverride("sub/leaf.yaml", "extra", 1)),
    ("with_sub.yaml", UnsetIncludeOverride("sub/chain.yaml", "k")),
    ("with_sub.yaml", UnsetIncludeOverride("sub/leaf.yaml", "k")),
    ("with_sub.yaml", SetRecipeInput("with_sub.yaml", "n", "default", 5)),
    ("with_sub.yaml", SetRecipeInput("sub/leaf.yaml", "k", "default", 2)),
    ("with_sub.yaml", UnsetRecipeInput("with_sub.yaml", "n", "description")),
    ("trailing.yaml", UnsetRecipeInput("trailing.yaml", "n", "default")),
    (
        "with_sub.yaml",
        ReplaceText("with_sub.yaml", WITH_SUB_TEXT.replace("default: 3", "default: 4")),
    ),
]


@pytest.mark.parametrize(
    ("name", "op"), CASES, ids=[f"{n}-{op.kind}-{i}" for i, (n, op) in enumerate(CASES)]
)
def test_each_operation_and_its_inverse_restore_the_bytes(tmp_path, name, op):
    ws = _open(tmp_path, name)
    original = _bytes(ws)

    entry = ws.apply(op)
    assert ws.dirty() == entry.documents
    edited = ws.document(entry.documents[0]).text
    assert ws.undo() is entry
    assert _bytes(ws) == original
    assert ws.dirty() == []

    assert ws.redo() is entry
    assert ws.dirty() == entry.documents
    assert ws.document(entry.documents[0]).text == edited


def test_an_edit_lands_in_the_file_that_declares_the_step(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")
    original = _bytes(ws)

    entry = ws.apply(SetParam("leaf_add", "k", 9))

    assert entry.documents == ["sub/leaf.yaml"]
    assert _bytes(ws)["with_sub.yaml"] == original["with_sub.yaml"]
    assert ws.to_recipe().steps[2].params["k"] == 9


def test_an_override_is_edited_where_the_include_entry_is(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")

    entry = ws.apply(SetIncludeOverride("sub/leaf.yaml", "k", 8))

    assert entry.documents == ["sub/chain.yaml"]
    assert "      k: 8\n" in ws.document("sub/chain.yaml").text


def test_each_edit_changes_one_line(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")
    doc = ws.document("with_sub.yaml")

    ws.apply(SetParam("seed", "n", 4))

    assert _changed_lines(doc.saved_text, doc.text) == [
        "-      n: ${inputs.n}\n",
        "+      n: 4\n",
    ]


@pytest.mark.skipif(not HAVE_EXAMPLES, reason="AA-SI_Full_Pipeline_Example is absent")
def test_edits_are_minimal_and_keep_comments(tmp_path):
    source = UC1 / "sub_recipes" / "processing_lvl_2_user.yaml"
    path = tmp_path / source.name
    shutil.copy2(source, path)
    ws = RecipeWorkspace.open(path)
    doc = ws.root

    ws.apply(SetParam("dive_remove_noise", "SNR_threshold", 5))

    assert _changed_lines(doc.saved_text, doc.text) == [
        "-      SNR_threshold: ${inputs.noise_snr_threshold}\n",
        "+      SNR_threshold: 5\n",
    ]
    assert "  # Now on seabed-masked Sv: the point of this recipe.\n" in doc.text
    assert doc.to_bytes().count(b"\r\n") == source.read_bytes().count(b"\r\n")


TO_RECIPE_ROOTS = [
    *sorted(FIXTURES.glob("*.yaml")),
    *DOCS_ROOTS,
    *(UC1_ROOTS if HAVE_EXAMPLES else []),
]


@pytest.mark.parametrize("path", TO_RECIPE_ROOTS, ids=lambda p: p.name)
def test_to_recipe_equals_load_recipe(path):
    ws = RecipeWorkspace.open(path)

    assert ws.to_recipe().model_dump() == load_recipe(path).model_dump()


def test_saved_edit_loads_like_the_cli(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.apply(SetIncludeOverride("sub/chain.yaml", "k", 7))

    ws.save()

    assert load_recipe(ws.root.origin).model_dump() == ws.to_recipe().model_dump()


@pytest.mark.parametrize(
    "op",
    [
        SetParam("nope", "n", 1),
        UnsetParam("seed", "nope"),
        UnsetParam("total", "n"),
        SetParam("seed", "n", 1, ("deeper", "still")),
        SetCheckpoint("nope", "always"),
        SetIncludeOverride("sub/none.yaml", "k", 1),
        UnsetIncludeOverride("sub/chain.yaml", "nope"),
        SetRecipeInput("none.yaml", "n", "default", 1),
        SetRecipeInput("with_sub.yaml", "nope", "default", 1),
        SetRecipeInput("with_sub.yaml", "n", "bogus", 1),
        UnsetRecipeInput("with_sub.yaml", "n", "required"),
        ReplaceText("sub/none.yaml", "a: 1\n"),
    ],
    ids=lambda op: op.kind,
)
def test_unknown_step_param_include_document_raise_unknown_target(tmp_path, op):
    ws = _open(tmp_path, "with_sub.yaml")
    original = _bytes(ws)

    with pytest.raises(UnknownTarget):
        ws.apply(op)

    assert _bytes(ws) == original
    assert not ws.history.can_undo


def test_removing_a_list_entry_is_refused(tmp_path):
    ws = _open(tmp_path, "nested.yaml")

    with pytest.raises(ValueError, match="set the whole list instead"):
        ws.apply(UnsetParam("pick", "extensions", (0,)))
    assert ws.dirty() == []


@pytest.mark.parametrize(
    ("op", "message"),
    [
        (SetParam("seed", "cfg", 1, ("x",)), "No 'cfg' in step 'seed'"),
        (SetParam("seed", "n", 1, ("x",)), "'n' in step 'seed' is not a container"),
        (SetParam("seed", "n", 1, ("x", "y")), "No 'n.x' in step 'seed'"),
        (SetParam("pick", "filters", 1, ("NOPE", "x")), "No 'filters.NOPE'"),
        (SetParam("pick", "extensions", 1, (5, "x")), "No 'extensions.5'"),
        (UnsetParam("pick", "filters", ("NOPE", "x")), "No 'filters.NOPE'"),
    ],
    ids=["missing", "scalar", "past_a_scalar", "missing_key", "past_the_end", "unset"],
)
def test_a_nested_path_that_does_not_exist_is_an_unknown_target(tmp_path, op, message):
    ws = _open(tmp_path, "nested.yaml" if op.step == "pick" else "with_sub.yaml")
    original = _bytes(ws)

    with pytest.raises(UnknownTarget, match=message):
        ws.apply(op)
    assert _bytes(ws) == original


def test_an_operation_keeps_its_own_copy_of_a_value(tmp_path):
    value = {"a": [1]}
    op = SetParam("seed", "extra", value)
    value["a"].append(2)
    ws = _open(tmp_path, "with_sub.yaml")

    ws.apply(op)
    ws.undo()
    value["a"].append(3)
    ws.redo()

    assert op.value == {"a": [1]}
    assert ws.recipe.steps[0].params["extra"] == {"a": [1]}


def test_an_edit_that_changes_nothing_is_not_recorded(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")

    assert ws.apply(SetParam("seed", "n", "${inputs.n}")) is None
    assert ws.apply(ReplaceText("with_sub.yaml", WITH_SUB_TEXT)) is None
    assert not ws.history.can_undo
    assert ws.dirty() == []


@pytest.mark.parametrize(
    "op",
    [
        ReplaceText("with_sub.yaml", WITH_SUB_TEXT.replace("id: total", "id: seed")),
        ReplaceText("with_sub.yaml", "steps: [unclosed\n"),
        ReplaceText("sub/leaf.yaml", "- a list\n"),
        SetRecipeInput("with_sub.yaml", "n", "required", True),
    ],
    ids=["duplicate_id", "bad_yaml", "not_a_mapping", "required_with_default"],
)
def test_parse_failure_is_rolled_back(tmp_path, op):
    ws = _open(tmp_path, "with_sub.yaml")
    original = _bytes(ws)
    data = ws.document(op.document).dump()

    with pytest.raises(EditRejected) as info:
        ws.apply(op)

    assert info.value.problems
    assert _bytes(ws) == original
    assert ws.document(op.document).dump() == data
    assert ws.dirty() == []
    assert not ws.history.can_undo
    ws.apply(SetParam("seed", "n", 4))
    assert ws.undo() is not None
    assert _bytes(ws) == original


@pytest.mark.usefixtures("helpers")
def test_checkpoint_removal_rejected_by_the_dag_builder(tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(_recipe(convert_checkpoint="always"), encoding="utf-8")
    ws = RecipeWorkspace.open(path)
    original = path.read_bytes()

    with pytest.raises(EditRejected) as info:
        ws.apply(SetCheckpoint("convert", None))

    assert any(
        "set 'checkpoint: always' on 'convert'" in p for p in info.value.problems
    )
    assert ws.root.to_bytes() == original
    assert ws.dirty() == []
    assert not ws.history.can_undo


@pytest.mark.usefixtures("helpers")
def test_checkpoint_that_fixes_the_fan_in_is_accepted_and_undone(tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(_recipe(), encoding="utf-8")
    ws = RecipeWorkspace.open(path)
    assert ws.validate(check_versions=False).errors

    ws.apply(SetCheckpoint("convert", "always"))
    assert not ws.validate().errors

    ws.undo()
    assert ws.root.to_bytes() == path.read_bytes()


@pytest.mark.usefixtures("helpers")
@pytest.mark.parametrize(
    ("op", "error"),
    [
        (UnsetParam("fetch", "nope"), UnknownTarget),
        (UnsetParam("fetch", "tags", (0,)), ValueError),
        (SetCheckpoint("convert", None), EditRejected),
        (ReplaceText("recipe.yaml", "steps: [oops\n"), EditRejected),
    ],
    ids=["unknown", "list_entry", "dag", "bad_yaml"],
)
def test_a_refused_edit_leaves_the_next_edit_minimal(tmp_path, op, error):
    text = (
        _recipe(convert_checkpoint="always")
        .replace("steps:\n", "steps:\n\n  # The segments.\n")
        .replace("      item: ${_item}\n", "      item: ${_item}\n      tags: [1, 2]\n")
    )
    path = tmp_path / "recipe.yaml"
    path.write_text(text, encoding="utf-8")
    ws = RecipeWorkspace.open(path)

    with pytest.raises(error):
        ws.apply(op)

    assert ws.apply(SetCheckpoint("convert", "always")) is None
    assert ws.dirty() == []
    ws.apply(SetParam("fetch", "tags", [3, 4]))
    assert _changed_lines(text, ws.root.text) == [
        "-      tags: [1, 2]\n",
        "+      tags: [3, 4]\n",
    ]


def test_validation_errors_from_a_param_edit_are_kept(tmp_path):
    ws = _open(tmp_path, "flat.yaml")

    entry = ws.apply(SetParam("scaled", "factor", "${inputs.missing}"))

    assert ws.dirty() == ["flat.yaml"]
    assert "${inputs.missing}" in ws.root.text
    errors = " ".join(ws.validate().errors)
    assert "undeclared pipeline input 'missing'" in errors
    assert ws.history.undo_stack == [entry]


SAMPLE_OPS = [
    SetParam("s", "p", {"a": [1, 2.5, None, True]}, ("x", 0), 2),
    UnsetParam("s", "p", ("x",)),
    SetCheckpoint("s", "always"),
    SetCheckpoint("s", None, 3),
    SetIncludeOverride("sub/x.yaml", "n", "${inputs.n}"),
    UnsetIncludeOverride("sub/x.yaml", "n"),
    SetRecipeInput("root.yaml", "n", "default", 5),
    UnsetRecipeInput("root.yaml", "n", "default"),
    ReplaceText("root.yaml", "a: 1\n"),
]


def test_history_entries_serialize(tmp_path):
    assert {op.kind for op in SAMPLE_OPS} == set(KINDS)
    for op in SAMPLE_OPS:
        data = json.loads(json.dumps(op.to_dict()))
        assert data["kind"] == op.kind
        assert operation_from_dict(data) == op

    ws = _open(tmp_path, "with_sub.yaml")
    entry = ws.apply(SetParam("seed", "n", 4))
    data = json.loads(json.dumps(entry.to_dict()))
    assert set(data) == {"seq", "time", "op", "inverse", "documents"}
    assert data["op"] == {
        "kind": "set_param",
        "step": "seed",
        "name": "n",
        "value": 4,
        "path": [],
        "position": None,
    }
    assert HistoryEntry.from_dict(data) == entry
    assert json.loads(json.dumps(ws.history.to_dict()))["undo"] == [data]


def test_the_wire_shape_takes_optional_fields_as_missing():
    op = operation_from_dict(
        {"kind": "set_param", "step": "a", "name": "b", "value": 5}
    )
    assert op == SetParam("a", "b", 5)


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "rename_step", "step": "a"},
        {"step": "a"},
        {"kind": "set_param", "step": "a", "name": "b"},
        {"kind": "set_param", "step": "a", "name": "b", "value": 1, "extra": 2},
        {"kind": "set_checkpoint", "step": "a", "value": "sometimes"},
        {"kind": "set_param", "step": 3, "name": "b", "value": 1},
        {"kind": "set_param", "step": "a", "name": "b", "value": 1, "path": "x"},
        {"kind": "set_param", "step": "a", "name": "b", "value": 1, "position": -1},
        {"kind": "set_param", "step": "a", "name": "b", "value": 1, "path": [-1]},
        ["set_param"],
    ],
)
def test_malformed_operations_raise_value_error(data):
    with pytest.raises(ValueError):
        operation_from_dict(data)


@pytest.mark.parametrize(
    ("op", "inserted_after"),
    [
        (SetParam("first", "c", 3), "      a: 1  # why\n"),
        (SetParam("second", "c", 3), "      b: 2\n"),
        (SetCheckpoint("third", "never"), "    map_over: ${first.v}\n"),
    ],
    ids=["after_comment", "plain", "end_of_step"],
)
def test_new_key_lands_before_the_trailing_blank_line(tmp_path, op, inserted_after):
    ws = _open(tmp_path, "trailing.yaml")
    original = ws.root.text

    ws.apply(op)

    new_line = "      c: 3\n" if op.kind == "set_param" else "    checkpoint: never\n"
    assert _changed_lines(original, ws.root.text) == ["+" + new_line]
    assert inserted_after + new_line in ws.root.text
    ws.undo()
    assert ws.root.text == original


def test_new_params_block_lands_before_the_trailing_blank_line(tmp_path):
    ws = _open(tmp_path, "trailing.yaml")
    original = ws.root.text

    ws.apply(SetParam("third", "c", 3))

    assert _changed_lines(original, ws.root.text) == ["+    params:\n", "+      c: 3\n"]
    assert "    map_over: ${first.v}\n    params:\n      c: 3\n\n  - id: fourth" in (
        ws.root.text
    )
    ws.undo()
    assert ws.root.text == original


def test_removed_last_key_hands_on_the_blank_line(tmp_path):
    ws = _open(tmp_path, "trailing.yaml")
    original = ws.root.text

    ws.apply(UnsetParam("second", "b"))

    assert _changed_lines(original, ws.root.text) == ["-    params:\n", "-      b: 2\n"]
    assert "output_map: {v: __return__}\n\n  - id: third" in ws.root.text


def test_checkpoint_is_inserted_before_inputs(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")
    chain = ws.document("sub/chain.yaml")
    original = chain.text

    ws.apply(SetCheckpoint("chain_inc", "always"))

    assert _changed_lines(original, chain.text) == ["+    checkpoint: always\n"]
    assert (
        "    map_over: ${inputs.source}\n    checkpoint: always\n    inputs:\n"
        in chain.text
    )


def test_a_quoted_value_keeps_its_quotes(tmp_path):
    ws = _open(tmp_path, "nested.yaml")
    ws.apply(ReplaceText("nested.yaml", NESTED.replace("LEG: 1", 'LEG: "1"')))

    ws.apply(SetParam("pick", "filters", "2", ("LEG",)))

    assert '        LEG: "2"\n' in ws.root.text


def test_undo_and_redo_with_an_empty_history(tmp_path):
    ws = _open(tmp_path, "flat.yaml")

    assert ws.undo() is None
    assert ws.redo() is None


def test_a_new_edit_clears_the_redo_stack(tmp_path):
    ws = _open(tmp_path, "with_sub.yaml")
    ws.apply(SetParam("seed", "n", 4))
    ws.undo()

    second = ws.apply(SetParam("seed", "n", 5))

    assert not ws.history.can_redo
    assert [e.seq for e in ws.history.undo_stack] == [second.seq] == [2]


def test_a_new_params_block_follows_inputs_in_a_step_out_of_house_order(tmp_path):
    path = tmp_path / "order.yaml"
    path.write_text(
        "recipe: {name: o, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: a\n"
        "    op: noop\n"
        "    execution:\n"
        "      executor: sequential\n"
        "    inputs:\n"
        "      x: ${inputs.x}\n",
        encoding="utf-8",
    )
    ws = RecipeWorkspace.open(path)

    ws.apply(SetParam("a", "k", 1))

    assert ws.root.text.endswith("      x: ${inputs.x}\n    params:\n      k: 1\n")


@pytest.mark.parametrize(
    "build",
    [
        lambda v: SetParam("pick", "tick", v),
        lambda v: SetParam("pick", "filters", {"LEG": [1.0, v]}),
        lambda v: SetParam("pick", "filters", v, path=("LEG",)),
        lambda v: SetIncludeOverride("sub/leaf.yaml", "k", v),
        lambda v: SetRecipeInput("with_sub.yaml", "n", "default", (v,)),
    ],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_are_refused(build, bad):
    with pytest.raises(ValueError, match="NaN and infinity cannot be set"):
        build(bad)


def test_nan_and_infinity_are_refused_on_the_wire():
    data = {"kind": "set_param", "step": "s", "name": "k", "value": float("nan")}
    with pytest.raises(ValueError, match="edit the YAML text"):
        operation_from_dict(json.loads(json.dumps(data)))


def test_an_inverse_may_put_back_a_nan_the_file_held(tmp_path):
    path = tmp_path / "nested.yaml"
    path.write_text(NESTED.replace("LEG: 1", "LEG: .nan"), encoding="utf-8")
    ws = RecipeWorkspace.open(path)
    original = ws.root.text

    entry = ws.apply(SetParam("pick", "filters", 2, path=("LEG",)))

    assert entry.inverse.value != entry.inverse.value
    restored = HistoryEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
    assert restored.inverse.value != restored.inverse.value
    ws.undo()
    assert ws.root.text == original

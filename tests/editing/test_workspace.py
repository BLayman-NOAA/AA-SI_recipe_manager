# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for RecipeWorkspace: working copies, hashes, save, save as, revert."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.config import load_run_config
from aa_recipe_manager.docs.recipe_code import _placeholder_paths
from aa_recipe_manager.editing import (
    ChangedOnDisk,
    EditRejected,
    RecipeWorkspace,
    ReplaceText,
    SetCheckpoint,
    SetIncludeOverride,
    SetParam,
    SetRecipeInput,
    TargetExists,
    UnknownTarget,
    UnsetIncludeOverride,
    UnsetParam,
    WorkspaceError,
    digest,
)
from aa_recipe_manager.editing.workspace import _undeclared_names
from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry

from .conftest import (
    DOCS_ROOTS,
    FIXTURES,
    HAVE_EXAMPLES,
    UC1,
    UC1_ROOTS,
    copy_fixture,
)
from .test_save_as import _tree

LAYOUT = ["with_sub.yaml", "sub/chain.yaml", "sub/leaf.yaml"]


def _hashes(recipe, **kwargs) -> dict[str, str]:
    return {k: v.step_hash for k, v in api.step_hashes(recipe, **kwargs).items()}


def _bytes(ws: RecipeWorkspace) -> dict[str, bytes]:
    return {label: ws.document(label).to_bytes() for label in ws.files()}


def test_working_copy_mirrors_the_layout_and_hashes_identically(tmp_path):
    original = {name: (FIXTURES / name).read_bytes() for name in LAYOUT}

    ws = RecipeWorkspace.open(FIXTURES / "with_sub.yaml", copy_to=tmp_path)

    for name in LAYOUT:
        assert (tmp_path / name).read_bytes() == original[name]
        assert (FIXTURES / name).read_bytes() == original[name]
    assert ws.root.path == tmp_path / "with_sub.yaml"
    assert ws.root.origin == FIXTURES / "with_sub.yaml"
    assert ws.base_dir == FIXTURES
    expected = _hashes(FIXTURES / "with_sub.yaml", base_dir=FIXTURES)
    assert _hashes(ws.to_recipe(), base_dir=FIXTURES) == expected
    assert _hashes(tmp_path / "with_sub.yaml", base_dir=FIXTURES) == expected


def test_a_relative_path_hashes_through_base_dir_from_another_cwd(
    tmp_path, monkeypatch
):
    ws = RecipeWorkspace.open(FIXTURES / "paths.yaml", copy_to=tmp_path / "copy")
    monkeypatch.chdir(tmp_path)

    expected = _hashes(FIXTURES / "paths.yaml", base_dir=FIXTURES)

    assert _hashes(ws.to_recipe(), base_dir=ws.base_dir) == expected
    assert _hashes(tmp_path / "copy" / "paths.yaml", base_dir=FIXTURES) == expected
    assert ws.validate().errors == []


def _reference_inputs(path, recipe, registry):
    """The recipe and inputs a reference root hashes with."""
    if path.is_relative_to(UC1):
        return recipe, load_run_config(recipe_path=path).inputs
    placeholder, inputs, _ = _placeholder_paths(recipe, registry)
    return placeholder, inputs


def _uses_gcs(recipe, inputs) -> bool:
    """Whether a path input still points at gs:// after the given inputs."""
    inputs = inputs or {}
    values = [
        inputs.get(name, declaration.default)
        for name, declaration in recipe.inputs.items()
        if declaration.type == "path"
    ]
    return any(isinstance(v, str) and v.startswith("gs://") for v in values)


@pytest.mark.parametrize(
    "path",
    [*(UC1_ROOTS if HAVE_EXAMPLES else []), *DOCS_ROOTS],
    ids=lambda p: p.relative_to(p.parents[1]).as_posix(),
)
def test_reference_recipes_hash_identically_from_a_working_copy(tmp_path, path):
    registry = load_builtin_registry()
    base_dir = path.parent
    copy = tmp_path / "copy"
    ws = RecipeWorkspace.open(path, copy_to=copy)

    results = []
    for recipe in (load_recipe(path), ws.to_recipe(), load_recipe(copy / path.name)):
        recipe, inputs = _reference_inputs(path, recipe, registry)
        if _uses_gcs(recipe, inputs):
            pytest.skip("reads gs:// inputs")
        results.append(_hashes(recipe, inputs=inputs, base_dir=base_dir))

    assert results[0]
    assert results[1] == results[0]
    assert results[2] == results[0]


@pytest.mark.parametrize(
    "path",
    [
        FIXTURES / "with_sub.yaml",
        pytest.param(
            UC1 / "dive_analysis.yaml",
            marks=pytest.mark.skipif(not HAVE_EXAMPLES, reason="no example recipes"),
        ),
    ],
    ids=lambda p: p.name,
)
def test_files_labels_match_the_graph(path):
    ws = RecipeWorkspace.open(path)
    graph = api.recipe_graph(path)

    assert ws.files() == [path.name, *[inc["source"] for inc in graph["includes"]]]
    for label in ws.files():
        assert ws.document(label).origin == (path.parent / label).resolve()


def test_graph_from_the_workspace_matches_the_file(tmp_path):
    path = FIXTURES / "with_sub.yaml"
    ws = RecipeWorkspace.open(path, copy_to=tmp_path)

    assert api.recipe_graph(ws.to_recipe(), source=ws.root.origin.name) == (
        api.recipe_graph(path)
    )


def test_step_and_include_locations(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))

    assert ws.step_location("seed") == (ws.root, 0)
    assert ws.step_location("total") == (ws.root, 2)
    assert ws.step_location("leaf_add") == (ws.document("sub/leaf.yaml"), 0)
    assert ws.include_location("sub/chain.yaml") == (ws.root, 1)
    assert ws.include_location("sub/leaf.yaml") == (ws.document("sub/chain.yaml"), 1)
    with pytest.raises(UnknownTarget):
        ws.step_location("nope")
    with pytest.raises(UnknownTarget):
        ws.document("sub/nope.yaml")


def _age(paths) -> None:
    """Set modification times well in the past, so a rewrite is visible."""
    for path in paths:
        os.utime(path, (1_000_000_000, 1_000_000_000))


def test_save_writes_only_changed_files(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    paths = [tmp_path / name for name in LAYOUT]
    _age(paths)
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("leaf_add", "k", 9))
    assert ws.dirty() == ["sub/leaf.yaml"]

    written = ws.save()

    assert written == [tmp_path / "sub" / "leaf.yaml"]
    assert [p.stat().st_mtime > 1_000_000_000 for p in paths] == [False, False, True]
    assert ws.dirty() == []
    assert load_recipe(root).model_dump() == ws.to_recipe().model_dump()
    assert ws.save() == []


def test_undo_to_the_original_makes_a_file_clean(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.apply(SetParam("leaf_add", "k", "${inputs.k}"))

    assert ws.dirty() == []
    ws.undo()
    assert ws.dirty() == ["sub/leaf.yaml"]


def test_flush_writes_the_copy_not_the_origin(tmp_path):
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    root = copy_fixture(origin_dir, "with_sub.yaml")
    leaf = (origin_dir / "sub" / "leaf.yaml").read_bytes()
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work)
    assert ws.flush() == []

    ws.apply(SetParam("leaf_add", "k", 9))

    assert ws.flush() == [work / "sub" / "leaf.yaml"]
    assert b"k: 9" in (work / "sub" / "leaf.yaml").read_bytes()
    assert (origin_dir / "sub" / "leaf.yaml").read_bytes() == leaf
    assert ws.flush() == []
    assert ws.dirty() == ["sub/leaf.yaml"]

    assert ws.save() == [origin_dir / "sub" / "leaf.yaml"]
    assert (origin_dir / "sub" / "leaf.yaml").read_bytes() == (
        work / "sub" / "leaf.yaml"
    ).read_bytes()
    assert ws.dirty() == []
    assert ws.flush() == []


def test_in_memory_workspace_flushes_nothing(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    ws.apply(SetParam("seed", "n", 4))

    assert ws.flush() == []
    assert ws.dirty() == ["with_sub.yaml"]


def test_save_as_beside_the_root(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    original = root.read_bytes()
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("seed", "n", 4))
    ws.apply(SetParam("leaf_add", "k", 9))
    target = tmp_path / "with_sub_v2.yaml"

    result = ws.save_as(target)

    assert result.written == [tmp_path / "sub" / "leaf.yaml", target]
    assert result.root == target
    assert (result.rewrites, result.config, result.rehashed) == ({}, None, [])
    assert root.read_bytes() == original
    assert ws.root.origin == ws.root.path == target
    assert ws.files()[0] == "with_sub_v2.yaml"
    assert ws.dirty() == []
    assert ws.history.can_undo
    assert load_recipe(target).model_dump() == ws.to_recipe().model_dump()

    ws.apply(SetParam("seed", "n", 5))
    assert ws.save() == [target]
    assert root.read_bytes() == original


def test_save_as_beside_the_root_of_a_working_copy(tmp_path):
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    root = copy_fixture(origin_dir, "with_sub.yaml")
    ws = RecipeWorkspace.open(root, copy_to=tmp_path / "work")
    ws.apply(SetParam("seed", "n", 4))

    assert ws.save_as(origin_dir / "v2.yaml").written == [origin_dir / "v2.yaml"]

    assert ws.root.path == tmp_path / "work" / "with_sub.yaml"
    assert ws.files()[0] == "v2.yaml"
    assert ws.document("v2.yaml") is ws.root
    assert b"n: 4" in (origin_dir / "v2.yaml").read_bytes()
    assert b"n: 4" not in root.read_bytes()


def test_save_as_onto_a_sub_recipe_is_refused(tmp_path):
    (tmp_path / "root.yaml").write_text(
        "recipe: {name: r, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - include: part.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "part.yaml").write_text(
        "recipe: {name: p, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: a\n"
        "    op: noop\n",
        encoding="utf-8",
    )
    ws = RecipeWorkspace.open(tmp_path / "root.yaml")

    with pytest.raises(WorkspaceError, match="sub-recipe"):
        ws.save_as(tmp_path / "part.yaml")


def test_revert_restores_origins_and_clears_history(tmp_path):
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    root = copy_fixture(origin_dir, "with_sub.yaml")
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.apply(SetIncludeOverride("sub/chain.yaml", "k", 7))
    ws.flush()

    ws.revert()

    assert ws.dirty() == []
    assert not ws.history.can_undo and not ws.history.can_redo
    for name in LAYOUT:
        assert (work / name).read_bytes() == (origin_dir / name).read_bytes()
    assert _bytes(ws) == {name: (origin_dir / name).read_bytes() for name in LAYOUT}


def _outside_tree(tmp_path):
    """A root in ``r/`` that includes ``../shared/y.yaml``."""
    (tmp_path / "r").mkdir()
    (tmp_path / "shared").mkdir()
    (tmp_path / "r" / "root.yaml").write_text(
        "recipe: {name: r, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: a\n"
        "    op: noop\n"
        "  - include: ../shared/y.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "shared" / "y.yaml").write_text(
        "recipe: {name: y, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: b\n"
        "    op: noop\n",
        encoding="utf-8",
    )
    return tmp_path / "r" / "root.yaml"


@pytest.mark.parametrize("copying", [False, True], ids=["in_memory", "copying"])
def test_include_outside_the_root_folder_is_refused(tmp_path, copying):
    root = _outside_tree(tmp_path)
    copy_to = tmp_path / "work" if copying else None

    with pytest.raises(WorkspaceError, match=r"Include '\.\./shared/y\.yaml'"):
        RecipeWorkspace.open(root, copy_to=copy_to)
    assert not (tmp_path / "work" / "shared").exists()


@pytest.mark.parametrize("copying", [False, True], ids=["in_memory", "copying"])
def test_replace_text_that_adds_an_include_loads_the_new_file(tmp_path, copying):
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    root = copy_fixture(origin_dir, "flat.yaml")
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work if copying else None)
    assert ws.files() == ["flat.yaml"]
    text = ws.root.text.replace(
        "\noutputs:",
        "\n  - include: sub/leaf.yaml\n"
        "    input_overrides:\n"
        "      value: ${bump.out}\n"
        "      k: 2\n"
        "\noutputs:",
    )

    ws.apply(ReplaceText("flat.yaml", text))

    assert ws.files() == ["flat.yaml", "sub/leaf.yaml"]
    assert ws.step_location("leaf_add")[0] is ws.document("sub/leaf.yaml")
    assert (work / "sub" / "leaf.yaml").is_file() == copying
    assert [s.id for s in ws.to_recipe().steps] == [
        "start",
        "bump",
        "scaled",
        "leaf_add",
    ]
    ws.apply(SetParam("leaf_add", "k", 9))
    assert ws.dirty() == ["flat.yaml", "sub/leaf.yaml"]

    ws.undo()
    ws.undo()
    assert ws.files() == ["flat.yaml"]
    assert ws.dirty() == []
    with pytest.raises(UnknownTarget):
        ws.step_location("leaf_add")


def test_replace_text_that_includes_a_file_outside_the_folder_is_rejected(tmp_path):
    root = _outside_tree(tmp_path)
    text = root.read_text(encoding="utf-8")
    root.write_text(
        text.replace("  - include: ../shared/y.yaml\n", ""), encoding="utf-8"
    )
    ws = RecipeWorkspace.open(root)

    with pytest.raises(EditRejected, match=r"Include '\.\./shared/y\.yaml'"):
        ws.apply(ReplaceText("root.yaml", text))
    assert ws.dirty() == []
    assert ws.files() == ["root.yaml"]


def test_open_reports_a_missing_include_as_load_recipe_does(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    shutil.rmtree(tmp_path / "sub")

    with pytest.raises(RecipeParseError, match="Included recipe file not found"):
        RecipeWorkspace.open(root)
    with pytest.raises(RecipeParseError, match="Included recipe file not found"):
        load_recipe(root)


def test_strict_checks_use_the_given_inputs(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))

    entry = ws.apply(SetCheckpoint("seed", "always"), inputs={"n": 2})

    assert entry.documents == ["with_sub.yaml"]
    assert ws.validate({"n": 2}).errors == []


def test_a_dropped_include_is_neither_dirty_nor_saved_until_undone(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    leaf = (tmp_path / "sub" / "leaf.yaml").read_bytes()
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("leaf_add", "k", 9))
    block = ws.root.text[ws.root.text.index("  - include: sub/chain.yaml") :]
    dropped = ws.root.text.replace(block[: block.index("  - id: total")], "")

    ws.apply(ReplaceText("with_sub.yaml", dropped))

    assert ws.files() == ["with_sub.yaml"]
    assert ws.dirty() == ["with_sub.yaml"]
    assert ws.save() == [root]
    assert (tmp_path / "sub" / "leaf.yaml").read_bytes() == leaf

    ws.undo()
    assert ws.files() == ["with_sub.yaml", "sub/leaf.yaml", "sub/chain.yaml"]
    assert ws.dirty() == ["with_sub.yaml", "sub/leaf.yaml"]
    assert "      k: 9\n" in ws.document("sub/leaf.yaml").text


def test_a_working_copy_in_the_recipe_folder_is_refused(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")

    with pytest.raises(WorkspaceError, match="another folder than the recipe's"):
        RecipeWorkspace.open(root, copy_to=tmp_path)


STYLED = (
    "recipe:\n"
    '  name: "styled"\n'
    '  version: "1"\n'
    '  schema_version: "1"\n'
    "\n"
    "inputs:\n"
    "  window:\n"
    "    type: str\n"
    '    default: "2016-08-14T14:40:00"\n'
    '    description: "When the window starts."\n'
    "\n"
    "steps:\n"
    "  - id: cluster\n"
    "    op: noop\n"
    "    params:\n"
    '      method: "leaf"\n'
    "      plot_window: [0, 2400]\n"
    '      style: {color: "#D08BFF", linewidth: 1.8, linestyle: ":"}\n'
    '      units: "bins"\n'
    "      # How far apart the ticks are.\n"
    "      tick: 0.5  # seconds\n"
    "\n"
    "  # The part below is per file.\n"
    "  - include: part.yaml\n"
    "    input_overrides:\n"
    '      snr: "6.0dB"\n'
    "      pings: [1, 2]\n"
)

PART = (
    "recipe: {name: part, version: '1', schema_version: '1'}\n"
    "inputs:\n"
    "  snr: {type: str}\n"
    "  pings: {type: list}\n"
    "steps:\n"
    "  - id: denoise\n"
    "    op: noop\n"
    "    params:\n"
    "      snr: ${inputs.snr}\n"
    "      pings: ${inputs.pings}\n"
)


def _styled(folder):
    (folder / "styled.yaml").write_bytes(STYLED.replace("\n", "\r\n").encode())
    (folder / "part.yaml").write_text(PART, encoding="utf-8")
    return folder / "styled.yaml"


#: Edits of every kind to the styled recipe, all in its root.
STYLED_OPS = [
    UnsetParam("cluster", "method"),
    SetParam("cluster", "plot_window", 5),
    UnsetParam("cluster", "style", path=("linestyle",)),
    UnsetParam("cluster", "units"),
    SetParam("cluster", "tick", "fast"),
    UnsetIncludeOverride("part.yaml", "snr"),
    UnsetIncludeOverride("part.yaml", "pings"),
    SetRecipeInput("styled.yaml", "window", "description", None),
    SetRecipeInput("styled.yaml", "window", "default", 3),
]


def test_undo_and_redo_give_back_the_exact_bytes(tmp_path):
    root = _styled(tmp_path)
    ws = RecipeWorkspace.open(root)
    original = _bytes(ws)
    for op in STYLED_OPS:
        assert ws.apply(op) is not None
    edited = _bytes(ws)

    while ws.undo() is not None:
        pass
    assert _bytes(ws) == original
    assert ws.dirty() == []
    assert ws.save() == []
    assert ws.recipe.model_dump() == load_recipe(root).model_dump()

    while ws.redo() is not None:
        pass
    assert _bytes(ws) == edited
    assert ws.dirty() == ["styled.yaml"]


def test_a_new_edit_after_undo_clears_redo_and_a_no_op_records_nothing(tmp_path):
    ws = RecipeWorkspace.open(_styled(tmp_path))
    ws.apply(SetParam("cluster", "tick", 2))
    ws.undo()

    assert ws.apply(SetParam("cluster", "method", "leaf")) is None
    assert ws.history.can_redo
    ws.apply(SetParam("cluster", "method", "eom"))
    assert not ws.history.can_redo
    assert len(ws.history.undo_stack) == 1
    assert '      method: "eom"\n' in ws.root.text


def test_undo_past_a_save_is_dirty_and_revert_reloads(tmp_path):
    root = _styled(tmp_path)
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("cluster", "tick", 2))
    ws.save()

    ws.undo()
    assert ws.dirty() == ["styled.yaml"]
    assert "tick: 0.5  # seconds" in ws.root.text

    ws.revert()
    assert ws.dirty() == []
    assert "tick: 2    # seconds" in ws.root.text
    assert not ws.history.can_undo


def test_a_new_list_of_scalars_is_flow_style(tmp_path):
    ws = RecipeWorkspace.open(_styled(tmp_path))

    ws.apply(SetParam("cluster", "freqs", [38, 70]))
    ws.apply(SetParam("cluster", "method", ["a", "b"]))

    assert "      freqs: [38, 70]\n" in ws.root.text
    assert "      method: [a, b]\n" in ws.root.text


def test_a_second_copy_into_an_edited_copy_is_refused(tmp_path):
    (tmp_path / "src").mkdir()
    root = copy_fixture(tmp_path / "src", "with_sub.yaml")
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()
    copied = (work / "sub" / "leaf.yaml").read_bytes()

    with pytest.raises(WorkspaceError, match="edit the copy directly") as raised:
        RecipeWorkspace.open(root, copy_to=work)
    assert str(work / "sub" / "leaf.yaml") in str(raised.value)
    assert (work / "sub" / "leaf.yaml").read_bytes() == copied

    shutil.rmtree(work)
    RecipeWorkspace.open(root, copy_to=work)
    reopened = RecipeWorkspace.open(root, copy_to=work)
    assert reopened.root.path == work / "with_sub.yaml"


def test_save_refuses_files_another_workspace_changed(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    first = RecipeWorkspace.open(root)
    second = RecipeWorkspace.open(root)
    first.apply(SetParam("leaf_add", "k", 9))
    first.apply(SetParam("seed", "n", 4))
    first.save()
    leaf = (tmp_path / "sub" / "leaf.yaml").read_bytes()
    second.apply(SetParam("leaf_add", "k", 2))
    second.apply(SetParam("seed", "n", 5))

    with pytest.raises(ChangedOnDisk) as raised:
        second.save()
    assert raised.value.paths == [tmp_path / "sub" / "leaf.yaml", root]
    assert (tmp_path / "sub" / "leaf.yaml").read_bytes() == leaf
    with pytest.raises(ChangedOnDisk):
        second.save_as(tmp_path / "v2.yaml")
    assert not (tmp_path / "v2.yaml").exists()

    second.revert()
    second.apply(SetParam("leaf_add", "k", 2))
    assert second.save() == [tmp_path / "sub" / "leaf.yaml"]


def test_validate_reports_warnings_instead_of_raising_them(tmp_path, recwarn):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    ws.apply(SetParam("seed", "n", "abc"))
    ws.apply(SetIncludeOverride("sub/chain.yaml", "kk", 1))

    report = ws.validate()

    assert report.is_valid
    assert report.warnings == [
        "Step 'seed': param 'n' has type 'int' but received str.",
        "Include 'sub/chain.yaml': override 'kk' is not an input of the sub-recipe.",
    ]
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


def test_validate_warns_of_a_param_the_op_does_not_declare(tmp_path):
    (tmp_path / "r.yaml").write_text(
        "recipe: {name: r, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: both\n"
        "    op: combine_masks\n"
        "    inputs: {masks: x}\n"
        "    params: {mode: and, mood: or}\n"
        "  - id: apply\n"
        "    op: apply_sv_mask\n"
        "    params: {fill_value: 0}\n",
        encoding="utf-8",
    )

    warnings = RecipeWorkspace.open(tmp_path / "r.yaml").validate().warnings

    assert "Step 'both': op 'combine_masks' has no param 'mood'." in warnings
    assert not [w for w in warnings if "'mode'" in w or "apply_sv_mask" in w]


@pytest.mark.parametrize(
    "path",
    [*(UC1_ROOTS if HAVE_EXAMPLES else []), *DOCS_ROOTS],
    ids=lambda p: p.name,
)
def test_reference_recipes_name_only_declared_params_and_inputs(path):
    assert _undeclared_names(load_recipe(path)) == []


def _journaled(tmp_path):
    """The two level include fixture in ``src/``, copied to ``work/`` with a journal."""
    (tmp_path / "src").mkdir()
    root = copy_fixture(tmp_path / "src", "with_sub.yaml")
    ws = RecipeWorkspace.open(
        root, copy_to=tmp_path / "work", journal=tmp_path / "journal"
    )
    return ws, root


def _reopen(tmp_path, root):
    return RecipeWorkspace.reopen(
        tmp_path / "work" / root.name, root, journal=tmp_path / "journal"
    )


def _journal_lines(tmp_path) -> list[dict]:
    text = (tmp_path / "journal" / "history.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_reopen_restores_undo_and_redo_byte_exact(tmp_path):
    (tmp_path / "src").mkdir()
    root = _styled(tmp_path / "src")
    work, journal = tmp_path / "work", tmp_path / "journal"
    ws = RecipeWorkspace.open(root, copy_to=work, journal=journal)
    original = _bytes(ws)
    for op in STYLED_OPS:
        assert ws.apply(op) is not None
    edited = _bytes(ws)
    ws.undo()
    ws.undo()
    ws.flush()
    now, dirty = _bytes(ws), ws.dirty()

    reopened = RecipeWorkspace.reopen(work / "styled.yaml", root, journal=journal)

    assert reopened.warnings == []
    assert _bytes(reopened) == now
    assert reopened.dirty() == dirty
    assert len(reopened.history.undo_stack) == 7
    assert len(reopened.history.redo_stack) == 2
    while reopened.undo() is not None:
        pass
    reopened.flush()
    assert _bytes(reopened) == original
    assert (work / "styled.yaml").read_bytes() == original["styled.yaml"]
    assert reopened.dirty() == []
    while reopened.redo() is not None:
        pass
    reopened.flush()
    assert _bytes(reopened) == edited

    again = RecipeWorkspace.reopen(work / "styled.yaml", root, journal=journal)
    assert _bytes(again) == edited
    assert len(again.history.undo_stack) == 9
    assert not again.history.can_redo
    assert again.apply(SetParam("cluster", "tick", 2)).seq == 10


def test_reopen_rewrites_a_copy_the_journal_is_ahead_of(tmp_path):
    ws, root = _journaled(tmp_path)
    original = ws.root.text
    ws.apply(SetParam("seed", "n", 4))
    ws.flush()
    copied = tmp_path / "work" / "with_sub.yaml"
    expected = copied.read_bytes()
    assert b"n: 4" in expected
    copied.write_bytes(expected.replace(b"n: 4", b"n: 7"))

    reopened = _reopen(tmp_path, root)

    assert reopened.warnings == [
        "with_sub.yaml was behind its history and was restored from it."
    ]
    assert copied.read_bytes() == expected
    assert reopened.undo().seq == 1
    assert reopened.root.text == original


def test_reopen_restores_missing_copy_files_from_the_journal(tmp_path):
    ws, root = _journaled(tmp_path)
    ws.apply(SetParam("seed", "n", 4))
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()
    work = tmp_path / "work"
    names = ("with_sub.yaml", "sub/leaf.yaml")
    copied = {name: (work / name).read_bytes() for name in names}
    for name in copied:
        (work / name).unlink()

    reopened = _reopen(tmp_path, root)

    assert reopened.warnings == [
        f"{name} was missing from the working copy and was restored from its history."
        for name in ("with_sub.yaml", "sub/leaf.yaml")
    ]
    assert {name: (work / name).read_bytes() for name in copied} == copied
    assert [entry.seq for entry in reopened.history.undo_stack] == [1, 2]
    assert reopened.dirty() == ["with_sub.yaml", "sub/leaf.yaml"]
    assert [r["record"] for r in _journal_lines(tmp_path)] == [
        "entry",
        "entry",
        "saved",
    ]
    reopened.undo()
    reopened.undo()
    assert reopened.dirty() == []


def test_reopen_takes_a_missing_saved_text_from_a_matching_origin(tmp_path):
    ws, root = _journaled(tmp_path)
    ws.apply(SetParam("seed", "n", 4))
    ws.flush()
    saved = ws.journal._last_saved()["saved"]
    for label in ("with_sub.yaml", "sub/leaf.yaml"):
        (tmp_path / "journal" / "texts" / saved[label]).unlink()

    reopened = _reopen(tmp_path, root)

    assert reopened.warnings == [
        "1 older edit could not be restored.",
        "with_sub.yaml: its saved text is missing from the history; the file "
        "counts as unsaved.",
    ]
    assert reopened.dirty() == ["with_sub.yaml"]
    assert reopened.root.saved_text is None
    assert reopened.document("sub/leaf.yaml").saved_text == (
        (root.parent / "sub" / "leaf.yaml").read_text(encoding="utf-8")
    )
    assert reopened.check_disk() == ["with_sub.yaml"]


def test_a_journal_that_cannot_record_rolls_the_edit_back(tmp_path, monkeypatch):
    ws, root = _journaled(tmp_path)
    before = ws.root.text

    def fail(entry):
        raise OSError("disk full")

    monkeypatch.setattr(ws.journal, "record", fail)
    with pytest.raises(OSError, match="disk full"):
        ws.apply(SetParam("seed", "n", 4))

    assert ws.root.text == before
    assert not ws.history.can_undo
    assert ws.recipe.steps[0].params["n"] == "${inputs.n}"
    monkeypatch.undo()
    assert ws.apply(SetParam("seed", "n", 4)).seq == 1


def test_reopen_restores_saved_text_when_the_origin_changed(tmp_path):
    ws, root = _journaled(tmp_path)
    leaf = root.parent / "sub" / "leaf.yaml"
    saved = ws.document("sub/leaf.yaml").saved_text
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()
    leaf.write_bytes(leaf.read_bytes() + b"# changed by hand\n")

    reopened = _reopen(tmp_path, root)

    document = reopened.document("sub/leaf.yaml")
    assert document.saved_text == saved
    assert reopened.dirty() == ["sub/leaf.yaml"]
    assert reopened.check_disk() == ["sub/leaf.yaml"]
    with pytest.raises(ChangedOnDisk):
        reopened.save()
    assert b"# changed by hand" in leaf.read_bytes()
    assert reopened.save(overwrite=True) == [leaf]
    assert leaf.read_bytes() == document.to_bytes()
    assert reopened.dirty() == []
    assert reopened.check_disk() == []
    assert _reopen(tmp_path, root).dirty() == []


def test_sync_disk_reloads_clean_files_and_reports_conflicts(tmp_path):
    ws, root = _journaled(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()
    chain, leaf = root.parent / "sub" / "chain.yaml", root.parent / "sub" / "leaf.yaml"
    old_chain = ws.document("sub/chain.yaml").text
    chain.write_bytes(chain.read_bytes() + b"# changed by hand\n")
    leaf.write_bytes(leaf.read_bytes() + b"# changed by hand\n")
    root.write_text("steps: [\n", encoding="utf-8")
    root_text, leaf_text = ws.root.text, ws.document("sub/leaf.yaml").text

    result = ws.sync_disk()

    [entry] = result.reloaded
    assert entry.documents == ["sub/chain.yaml"]
    assert entry.inverse == ReplaceText("sub/chain.yaml", old_chain)
    conflicts = {conflict.label: conflict for conflict in result.conflicts}
    assert set(conflicts) == {"with_sub.yaml", "sub/leaf.yaml"}
    assert conflicts["sub/leaf.yaml"].error is None
    disk = leaf.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert conflicts["sub/leaf.yaml"].disk_digest == digest(disk)
    assert conflicts["with_sub.yaml"].error.startswith("line 2: ")
    assert "unicode string" not in conflicts["with_sub.yaml"].error
    assert conflicts["with_sub.yaml"].disk_digest == digest("steps: [\n")
    reloaded = ws.document("sub/chain.yaml")
    assert reloaded.text == reloaded.saved_text
    assert reloaded.text.endswith("# changed by hand\n")
    assert ws.root.text == root_text
    assert ws.document("sub/leaf.yaml").text == leaf_text
    assert sorted(ws.check_disk()) == ["sub/leaf.yaml", "with_sub.yaml"]
    assert [line["record"] for line in _journal_lines(tmp_path)][-2:] == [
        "entry",
        "saved",
    ]

    ws.undo()
    assert reloaded.text == old_chain
    assert ws.dirty() == ["sub/leaf.yaml", "sub/chain.yaml"]


def test_reload_refuses_a_broken_origin(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    chain, leaf = tmp_path / "sub" / "chain.yaml", tmp_path / "sub" / "leaf.yaml"
    chain.write_bytes(chain.read_bytes() + b"# changed by hand\n")
    leaf.write_text("- not a recipe\n", encoding="utf-8")
    leaf_text = ws.document("sub/leaf.yaml").text

    with pytest.raises(EditRejected) as raised:
        ws.reload(["sub/chain.yaml", "sub/leaf.yaml"])

    assert raised.value.problems[0].startswith("sub/leaf.yaml: ")
    assert ws.document("sub/chain.yaml").text.endswith("# changed by hand\n")
    assert ws.document("sub/leaf.yaml").text == leaf_text
    assert [e.documents for e in ws.history.undo_stack] == [["sub/chain.yaml"]]
    assert ws.check_disk() == ["sub/leaf.yaml"]


def test_reload_writes_one_saved_record(tmp_path):
    ws, root = _journaled(tmp_path)
    for name in ("sub/chain.yaml", "sub/leaf.yaml"):
        path = root.parent / name
        path.write_bytes(path.read_bytes() + b"# changed by hand\n")

    assert len(ws.reload()) == 2

    records = [line["record"] for line in _journal_lines(tmp_path)]
    assert records[-3:] == ["entry", "entry", "saved"]
    assert records.count("saved") == 2


def test_reopen_with_another_origin_is_refused(tmp_path):
    _, root = _journaled(tmp_path)
    (tmp_path / "other").mkdir()
    other = copy_fixture(tmp_path / "other", "with_sub.yaml")

    with pytest.raises(WorkspaceError, match="belongs to"):
        RecipeWorkspace.reopen(
            tmp_path / "work" / "with_sub.yaml", other, journal=tmp_path / "journal"
        )
    with pytest.raises(WorkspaceError, match="not a working copy"):
        RecipeWorkspace.reopen(root, root, journal=tmp_path / "journal")


def test_reopen_without_a_journal_starts_empty(tmp_path):
    (tmp_path / "src").mkdir()
    root = copy_fixture(tmp_path / "src", "with_sub.yaml")
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()

    reopened = RecipeWorkspace.reopen(work / "with_sub.yaml", root)

    assert reopened.journal is None
    assert reopened.warnings == []
    assert not reopened.history.can_undo
    leaf = reopened.document("sub/leaf.yaml")
    assert leaf.path == work / "sub" / "leaf.yaml"
    assert "      k: 9\n" in leaf.text
    assert reopened.dirty() == ["sub/leaf.yaml"]
    assert reopened.save() == [root.parent / "sub" / "leaf.yaml"]


def test_a_journal_needs_a_working_copy(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")

    with pytest.raises(WorkspaceError, match="needs a working copy"):
        RecipeWorkspace.open(root, journal=tmp_path / "journal")


def _files(folder) -> dict:
    return {p: p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_save_as_dry_run_writes_nothing_and_reports_the_same(tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    root = _tree(tmp_path / "src")
    (root.parent / "root.config.yaml").write_text(
        "user_cache_dir: ./cache\n", encoding="utf-8"
    )
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("write_out", "folder", "./edited"))
    before = _files(tmp_path)
    target = tmp_path / "elsewhere" / "root.yaml"

    dry = ws.save_as(target, dry_run=True)

    assert _files(tmp_path) == before
    assert dry.dry_run
    assert dry.existing == []
    assert dry.rewrites
    assert ws.history.can_undo
    assert ws.root.origin == root
    assert ws.base_dir == root.parent
    real = ws.save_as(target)
    assert not real.dry_run
    assert {**dry.to_dict(), "dry_run": False} == real.to_dict()

    beside = root.parent / "v2.yaml"
    beside.write_text("x: 1\n", encoding="utf-8")
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("write_out", "folder", "./beside"))
    before = _files(tmp_path)
    dry = ws.save_as(beside, dry_run=True)
    assert _files(tmp_path) == before
    assert dry.existing == [beside]
    assert dry.written == [
        root.parent / "sub" / "x.yaml",
        root.parent / "v2.config.yaml",
        beside,
    ]
    with pytest.raises(TargetExists):
        ws.save_as(beside)
    real = ws.save_as(beside, overwrite=True)
    assert {**dry.to_dict(), "dry_run": False} == real.to_dict()


def test_save_as_elsewhere_clears_the_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    root = _tree(tmp_path / "src")
    work, journal = tmp_path / "work", tmp_path / "journal"
    ws = RecipeWorkspace.open(root, copy_to=work, journal=journal)
    ws.apply(SetParam("write_out", "folder", "./edited"))
    target = tmp_path / "elsewhere" / "root.yaml"

    ws.save_as(target)

    [record] = _journal_lines(tmp_path)
    assert record["record"] == "saved"
    assert record["origin"] == str(target)
    reopened = RecipeWorkspace.reopen(work / "root.yaml", target, journal=journal)
    assert not reopened.history.can_undo
    assert reopened.dirty() == []
    assert reopened.warnings == []


def test_reopen_after_save_as_beside_keeps_the_history(tmp_path):
    ws, root = _journaled(tmp_path)
    ws.apply(SetParam("seed", "n", 4))
    ws.save_as(root.parent / "v2.yaml")
    ws.apply(SetParam("seed", "n", 5))
    ws.flush()

    reopened = RecipeWorkspace.reopen(
        tmp_path / "work" / "with_sub.yaml",
        root.parent / "v2.yaml",
        journal=tmp_path / "journal",
    )

    assert reopened.warnings == []
    assert reopened.label(reopened.root) == "v2.yaml"
    assert reopened.root.text == ws.root.text
    assert reopened.dirty() == ["v2.yaml"]
    reopened.undo()
    assert reopened.dirty() == []
    reopened.undo()
    assert reopened.root.text == root.read_bytes().decode().replace("\r\n", "\n")


def test_revert_clears_the_journal(tmp_path):
    ws, root = _journaled(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()

    ws.revert()

    [record] = _journal_lines(tmp_path)
    assert record["record"] == "saved"
    reopened = _reopen(tmp_path, root)
    assert not reopened.history.can_undo
    assert reopened.dirty() == []


def test_snapshot_writes_the_layout_without_repointing(tmp_path):
    ws, _ = _journaled(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    snap = tmp_path / "snap"

    assert ws.snapshot(snap) == (snap / "with_sub.yaml").resolve()

    for label in LAYOUT:
        assert (snap / label).read_bytes() == ws.document(label).to_bytes()
    assert ws.root.path == (tmp_path / "work" / "with_sub.yaml").resolve()
    assert ws.dirty() == ["sub/leaf.yaml"]
    ws.apply(SetParam("leaf_add", "k", 2))
    ws.flush()
    assert b"      k: 9" in (snap / "sub" / "leaf.yaml").read_bytes()

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for Save As into another folder: copies, path rewrites, hashes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from aa_recipe_manager import api
from aa_recipe_manager.editing import (
    RecipeWorkspace,
    SetParam,
    TargetExists,
    WorkspaceError,
    path_sites,
)
from aa_recipe_manager.editing.rebase import rebase
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry

from .conftest import HAVE_EXAMPLES, UC1


def _spec(mode: str = "off", must_exist: bool = True, kind: str = "path") -> str:
    """A custom spec block reading one ``folder`` param."""
    extra = f", fingerprint_mode: {mode}" if mode != "off" else ""
    if not must_exist:
        extra += ", constraints: {must_exist: false}"
    return (
        "    custom_spec:\n"
        "      description: Count the entries in a folder.\n"
        "      callable_path: _stage9_helpers.count_entries\n"
        "      params:\n"
        f"        folder: {{type: {kind}{extra}}}\n"
        "      outputs:\n"
        "        n: {type: int}\n"
        "      output_map: {n: __return__}\n"
        "      dependency: {name: pytest, version: '>=7.0', source: pypi}\n"
    )


def _tree(folder: Path) -> Path:
    """A recipe with one of each kind of path, and its data folders.

    Returns:
        The root recipe file.
    """
    for name in ("data_a", "data_b", "data_c", "data_d", "data_e"):
        (folder / name).mkdir(parents=True)
        (folder / name / "f.txt").write_text(name, encoding="utf-8")
    absolute = (folder / "data_a").as_posix()
    (folder / "sub").mkdir()
    (folder / "root.yaml").write_text(
        "# Root recipe for the Save As tests.\n"
        "recipe:\n"
        "  name: save_as_tree\n"
        '  version: "1"\n'
        '  schema_version: "1"\n'
        "\n"
        "inputs:\n"
        "  in_dir:\n"
        "    type: path\n"
        '    default: "./data_c"\n'
        "  fp_in:\n"
        "    type: path\n"
        "    default: ./data_e\n"
        "  label:\n"
        "    type: str\n"
        '    default: "./not/a/path"\n'
        "\n"
        "steps:\n"
        "  - id: off_read\n"
        "    op: custom\n"
        "    params:\n"
        '      folder: "./data_a"  # read as written\n' + _spec() + "\n"
        "  - id: fp_read\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ./data_b/\n" + _spec("size") + "\n"
        "  - id: ref_read\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ${inputs.in_dir}\n" + _spec() + "\n"
        "  - id: fp_ref\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ${inputs.fp_in}\n" + _spec("size") + "\n"
        "  - id: remote_read\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: gs://bucket/data\n" + _spec() + "\n"
        "  - id: abs_read\n"
        "    op: custom\n"
        "    params:\n"
        f"      folder: {absolute}\n" + _spec() + "\n"
        "  - id: untyped\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ./looks/like/a/path\n" + _spec(kind="str") + "\n"
        "  - include: sub/x.yaml\n"
        "    input_overrides:\n"
        '      out_dir: "./outputs"  # where results go\n'
        "      fp_dir: ./data_d\n",
        encoding="utf-8",
    )
    (folder / "sub" / "x.yaml").write_text(
        "recipe:\n"
        "  name: save_as_sub\n"
        '  version: "1"\n'
        '  schema_version: "1"\n'
        "\n"
        "inputs:\n"
        "  out_dir:\n"
        "    type: path\n"
        "    default: ./sub_out\n"
        "  fp_dir:\n"
        "    type: path\n"
        "    default: ./sub_fp\n"
        "\n"
        "steps:\n"
        "  - id: write_out\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ${inputs.out_dir}\n" + _spec(must_exist=False) + "\n"
        "  - id: fp_sub\n"
        "    op: custom\n"
        "    params:\n"
        "      folder: ${inputs.fp_dir}\n" + _spec("size"),
        encoding="utf-8",
    )
    return folder / "root.yaml"


def _hashes(recipe, **kwargs) -> dict[str, str]:
    return {k: v.step_hash for k, v in api.step_hashes(recipe, **kwargs).items()}


def _tree_bytes(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*.yaml"))
    }


@pytest.fixture(autouse=True)
def _no_env_config(monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)


def test_rebase_strings(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"

    assert rebase("./x", a, b) == ("../a/x", False)
    assert rebase("../y", a, b / "c") == ("../../y", False)
    assert rebase("x/y/", a, b) == ("../a/x/y/", False)
    assert rebase("./x", a / "b", a) == ("./b/x", False)
    assert rebase("x", a / "b", a) == ("b/x", False)
    assert rebase("x\\y", a, b) == ("../a/x/y", False)
    for value in (
        "gs://bucket/x",
        "memory://x",
        (tmp_path / "abs").as_posix(),
        "/abs/x",
        "~/x",
        "${inputs.x}",
        "./a/${_item}",
        "",
    ):
        assert rebase(value, a, b) == (value, False)

    def other_drive(path, start=None):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", other_drive)
    new, absolute = rebase("./x", a, b)
    assert absolute
    assert new == (a / "x").as_posix()
    assert "\\" not in new


def test_path_sites_finds_every_kind(tmp_path):
    root = _tree(tmp_path / "src")
    ws = RecipeWorkspace.open(root)

    sites = path_sites(ws, load_builtin_registry())

    found = {(s.document, s.kind, s.where, s.value, s.fingerprinted) for s in sites}
    assert found == {
        ("root.yaml", "input_default", "inputs.in_dir.default", "./data_c", False),
        ("root.yaml", "input_default", "inputs.fp_in.default", "./data_e", True),
        ("root.yaml", "param", "off_read.folder", "./data_a", False),
        ("root.yaml", "param", "fp_read.folder", "./data_b/", True),
        ("root.yaml", "override", "sub/x.yaml:out_dir", "./outputs", False),
        ("root.yaml", "override", "sub/x.yaml:fp_dir", "./data_d", True),
        ("sub/x.yaml", "input_default", "inputs.out_dir.default", "./sub_out", False),
        ("sub/x.yaml", "input_default", "inputs.fp_dir.default", "./sub_fp", False),
    }


def test_a_literal_path_port_is_a_site(tmp_path):
    (tmp_path / "root.yaml").write_text(
        "recipe: {name: r, version: '1', schema_version: '1'}\n"
        "steps:\n"
        "  - id: reader\n"
        "    op: custom\n"
        "    inputs:\n"
        "      src: ./data\n"
        "    custom_spec:\n"
        "      description: Read a folder.\n"
        "      callable_path: _stage9_helpers.count_entries\n"
        "      inputs:\n"
        "        src: {type: path}\n"
        "      outputs:\n"
        "        n: {type: int}\n"
        "      output_map: {n: __return__}\n",
        encoding="utf-8",
    )
    ws = RecipeWorkspace.open(tmp_path / "root.yaml")

    [site] = path_sites(ws, load_builtin_registry())

    assert (site.kind, site.where, site.value) == (
        "port",
        "reader.inputs.src",
        "./data",
    )
    assert site.key == ("steps", 0, "inputs", "src")


def test_save_as_rejects_an_unknown_rebase_mode(tmp_path):
    ws = RecipeWorkspace.open(_tree(tmp_path / "src"))

    with pytest.raises(ValueError, match="paths"):
        ws.save_as(tmp_path / "other" / "root.yaml", paths="none")
    assert not (tmp_path / "other").exists()


def test_save_as_elsewhere_mirrors_and_rewrites(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    originals = _tree_bytes(src)
    original = load_recipe(root).model_dump()
    ws = RecipeWorkspace.open(root)
    target = tmp_path / "elsewhere" / "new.yaml"

    result = ws.save_as(target)

    new_dir = target.parent
    assert result.root == target
    assert result.written == [new_dir / "sub" / "x.yaml", target]
    assert result.config is None
    assert result.absolute == []
    assert _tree_bytes(src) == originals
    assert set(result.rewrites) == {"new.yaml", "sub/x.yaml"}
    for rewrites in result.rewrites.values():
        for rewrite in rewrites:
            assert "\\" not in rewrite.new
            assert (new_dir / rewrite.new).resolve() == (src / rewrite.old).resolve()
    assert [r.where for r in result.rewrites["new.yaml"]] == [
        "inputs.in_dir.default",
        "inputs.fp_in.default",
        "off_read.folder",
        "fp_read.folder",
        "sub/x.yaml:out_dir",
        "sub/x.yaml:fp_dir",
    ]
    text = target.read_text(encoding="utf-8")
    assert '      folder: "../src/data_a" # read as written\n' in text
    assert '      out_dir: "../src/outputs" # where results go\n' in text
    assert "      folder: ../src/data_b/\n" in text
    assert "      folder: gs://bucket/data\n" in text
    assert "      folder: ./looks/like/a/path\n" in text

    expected = json.dumps(original)
    for rewrites in result.rewrites.values():
        for rewrite in rewrites:
            expected = expected.replace(f'"{rewrite.old}"', f'"{rewrite.new}"')
    assert json.dumps(load_recipe(target).model_dump()) == expected
    assert api.dry_run(target, base_dir=new_dir).is_valid

    assert ws.root.origin == ws.root.path == target
    assert ws.base_dir == new_dir
    assert ws.files() == ["new.yaml", "sub/x.yaml"]
    assert ws.document("sub/x.yaml").origin == new_dir / "sub" / "x.yaml"
    assert ws.dirty() == []
    assert ws.to_recipe().model_dump() == load_recipe(target).model_dump()


def test_save_as_elsewhere_carries_edits_and_clears_history(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    originals = _tree_bytes(src)
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("write_out", "folder", "./edited"))
    target = tmp_path / "elsewhere" / "root.yaml"

    ws.save_as(target)

    assert _tree_bytes(src) == originals
    assert not ws.history.can_undo
    sub = (target.parent / "sub" / "x.yaml").read_text(encoding="utf-8")
    assert "folder: ../src/edited\n" in sub
    ws.apply(SetParam("off_read", "folder", "./data_a"))
    assert ws.save() == [target]
    assert _tree_bytes(src) == originals


def test_save_as_hash_report_and_keep_paths(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    original = _hashes(root, base_dir=src)

    moved = tmp_path / "all" / "root.yaml"
    result = RecipeWorkspace.open(root).save_as(moved)
    copied = _hashes(moved, base_dir=moved.parent)
    changed = [step for step, h in copied.items() if original[step] != h]
    assert changed == ["off_read", "ref_read", "write_out"]
    assert result.rehashed == changed

    kept = tmp_path / "kept" / "root.yaml"
    for name in ("data_a", "data_c"):
        shutil.copytree(src / name, kept.parent / name)
    result = RecipeWorkspace.open(root).save_as(kept, paths="fingerprinted")
    assert result.rehashed == []
    assert _hashes(kept, base_dir=kept.parent) == original
    assert {r.where for r in result.rewrites["root.yaml"]} == {
        "inputs.fp_in.default",
        "fp_read.folder",
        "sub/x.yaml:fp_dir",
    }
    assert "sub/x.yaml" not in result.rewrites


def test_rehashed_is_none_when_the_copy_cannot_be_hashed(tmp_path):
    root = _tree(tmp_path / "src")

    result = RecipeWorkspace.open(root).save_as(
        tmp_path / "kept" / "root.yaml", paths="fingerprinted"
    )

    assert result.rehashed is None


@pytest.mark.parametrize("name", ["root.config.yaml", "aa-recipe.config.yaml"])
def test_save_as_copies_and_rebases_the_run_config(tmp_path, name):
    src = tmp_path / "src"
    root = _tree(src)
    (src / name).write_text(
        "# My settings.\n"
        "user_cache_dir: ./cache  # keep\n"
        "temp_dir: gs://bucket/tmp\n"
        "outputs_dir: out/\n"
        "storage_options:\n"
        "  token: ./key.json\n"
        "inputs:\n"
        "  in_dir: ./data_c\n"
        "  label: ./not/a/path\n",
        encoding="utf-8",
    )
    original = (src / name).read_bytes()
    target = tmp_path / "other" / "copy.yaml"

    result = RecipeWorkspace.open(root).save_as(target)

    config = target.parent / "copy.config.yaml"
    assert result.config == config
    assert result.written[-2:] == [config, target]
    assert (src / name).read_bytes() == original
    assert config.read_text(encoding="utf-8") == (
        "# My settings.\n"
        "user_cache_dir: ../src/cache # keep\n"
        "temp_dir: gs://bucket/tmp\n"
        "outputs_dir: ../src/out/\n"
        "storage_options:\n"
        "  token: ./key.json\n"
        "inputs:\n"
        "  in_dir: ../src/data_c\n"
        "  label: ./not/a/path\n"
    )
    assert [r.where for r in result.rewrites["copy.config.yaml"]] == [
        "user_cache_dir",
        "outputs_dir",
        "inputs.in_dir",
    ]
    assert result.rehashed == ["off_read", "ref_read", "write_out"]


def test_keep_paths_leaves_hashed_config_inputs(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text(
        "user_cache_dir: ./cache\ninputs:\n  in_dir: ./data_c\n  fp_in: ./data_e\n",
        encoding="utf-8",
    )
    target = tmp_path / "other" / "root.yaml"

    result = RecipeWorkspace.open(root).save_as(target, paths="fingerprinted")

    assert [r.where for r in result.rewrites["root.config.yaml"]] == [
        "user_cache_dir",
        "inputs.fp_in",
    ]


def test_save_as_without_the_config(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("user_cache_dir: ./cache\n", encoding="utf-8")

    result = RecipeWorkspace.open(root).save_as(
        tmp_path / "other" / "root.yaml", copy_config=False
    )

    assert result.config is None
    assert not (tmp_path / "other" / "root.config.yaml").exists()


def test_a_config_named_by_the_environment_is_not_copied(tmp_path, monkeypatch):
    src = tmp_path / "src"
    root = _tree(src)
    shared = tmp_path / "shared.config.yaml"
    shared.write_text("user_cache_dir: ./cache\n", encoding="utf-8")
    monkeypatch.setenv("AA_RECIPE_CONFIG", str(shared))

    result = RecipeWorkspace.open(root).save_as(tmp_path / "other" / "root.yaml")

    assert result.config is None
    assert "root.config.yaml" not in result.rewrites


def test_save_as_inside_the_source_tree(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    target = src / "variants" / "new.yaml"

    result = RecipeWorkspace.open(root).save_as(target)

    assert (src / "variants" / "sub" / "x.yaml").is_file()
    assert result.rewrites["new.yaml"][2].new == "../data_a"
    assert result.rewrites["new.yaml"][3].new == "../data_b/"
    assert api.dry_run(target, base_dir=target.parent).is_valid


def test_save_as_refuses_to_overwrite_unless_asked(tmp_path):
    root = _tree(tmp_path / "src")
    dest = tmp_path / "dest"
    (dest / "sub").mkdir(parents=True)
    (dest / "sub" / "x.yaml").write_text("old\n", encoding="utf-8")
    (dest / "new.yaml").write_text("old root\n", encoding="utf-8")
    ws = RecipeWorkspace.open(root)

    with pytest.raises(WorkspaceError, match="existing files"):
        ws.save_as(dest / "new.yaml")
    assert (dest / "new.yaml").read_text(encoding="utf-8") == "old root\n"
    assert ws.root.origin == root

    ws.save_as(dest / "new.yaml", overwrite=True)
    assert (dest / "sub" / "x.yaml").read_text(encoding="utf-8") != "old\n"


@pytest.mark.parametrize("beside", [False, True])
def test_an_existing_root_path_needs_overwrite(tmp_path, beside):
    root = _tree(tmp_path / "src")
    target = (root.parent if beside else tmp_path / "dest") / "new.yaml"
    target.parent.mkdir(exist_ok=True)
    target.write_text("old root\n", encoding="utf-8")
    ws = RecipeWorkspace.open(root)

    with pytest.raises(TargetExists, match="existing files") as raised:
        ws.save_as(target)
    assert raised.value.paths == [target]
    assert target.read_text(encoding="utf-8") == "old root\n"
    assert ws.root.origin == root

    ws.save_as(target, overwrite=True)
    assert load_recipe(target).name == "save_as_tree"


@pytest.mark.parametrize("beside", [False, True])
def test_save_as_onto_a_run_config_is_refused_even_with_overwrite(tmp_path, beside):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("temp_dir: ./t\n", encoding="utf-8")
    shared = tmp_path / "shared.yaml"
    shared.write_text("temp_dir: ./s\n", encoding="utf-8")
    folder = src if beside else tmp_path / "dest"
    folder.mkdir(exist_ok=True)
    (folder / "aa-recipe.config.yaml").write_text("temp_dir: ./a\n", encoding="utf-8")
    originals = _tree_bytes(tmp_path)
    ws = RecipeWorkspace.open(root)

    for target, config in (
        (src / "root.config.yaml", None),
        (folder / "aa-recipe.config.yaml", None),
        (shared, shared),
    ):
        with pytest.raises(WorkspaceError, match="is a run config"):
            ws.save_as(target, config=config, overwrite=True)
    assert _tree_bytes(tmp_path) == originals


def test_save_as_onto_the_discovered_run_config_is_refused(tmp_path, monkeypatch):
    root = _tree(tmp_path / "src")
    shared = tmp_path / "elsewhere" / "settings.yaml"
    shared.parent.mkdir()
    shared.write_text("temp_dir: ./s\n", encoding="utf-8")
    monkeypatch.setenv("AA_RECIPE_CONFIG", str(shared))

    with pytest.raises(WorkspaceError, match="is a run config"):
        RecipeWorkspace.open(root).save_as(shared, overwrite=True)
    assert shared.read_text(encoding="utf-8") == "temp_dir: ./s\n"


def test_save_as_onto_a_file_of_the_recipe_is_refused(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    originals = _tree_bytes(src)

    with pytest.raises(WorkspaceError, match="a file of this recipe"):
        RecipeWorkspace.open(root).save_as(src / "sub" / "x.yaml")
    assert _tree_bytes(src) == originals


def test_save_as_onto_the_recipes_own_file_is_refused(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    originals = _tree_bytes(src)
    ws = RecipeWorkspace.open(root)

    for overwrite in (False, True):
        with pytest.raises(WorkspaceError, match="recipe's own file; use Save"):
            ws.save_as(root, overwrite=overwrite)
    assert _tree_bytes(src) == originals


def test_save_as_same_folder_rewrites_nothing(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    config = src / "root.config.yaml"
    config.write_text("user_cache_dir: ./cache  # kept\n", encoding="utf-8")
    ws = RecipeWorkspace.open(root)
    ws.apply(SetParam("off_read", "folder", "./data_b"))

    result = ws.save_as(src / "v2.yaml")

    assert result.written == [src / "v2.config.yaml", src / "v2.yaml"]
    assert (src / "v2.config.yaml").read_bytes() == config.read_bytes()
    assert (result.rewrites, result.absolute, result.config, result.rehashed) == (
        {},
        [],
        src / "v2.config.yaml",
        [],
    )
    assert ws.history.can_undo
    data = json.loads(json.dumps(result.to_dict()))
    assert data["root"] == (src / "v2.yaml").as_posix()


def test_save_as_from_a_working_copy(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    work = tmp_path / "work"
    ws = RecipeWorkspace.open(root, copy_to=work)
    target = tmp_path / "elsewhere" / "root.yaml"

    result = ws.save_as(target)

    assert ws.root.path == work / "root.yaml"
    assert ws.root.origin == target
    assert ws.document("sub/x.yaml").path == work / "sub" / "x.yaml"
    assert ws.document("sub/x.yaml").origin == target.parent / "sub" / "x.yaml"
    assert (work / "root.yaml").read_bytes() == target.read_bytes()
    assert (work / "sub" / "x.yaml").read_bytes() == (
        target.parent / "sub" / "x.yaml"
    ).read_bytes()
    assert ws.dirty() == []
    assert ws.flush() == []
    assert result.rehashed == ["off_read", "ref_read", "write_out"]


def test_save_as_result_serializes(tmp_path):
    root = _tree(tmp_path / "src")

    result = RecipeWorkspace.open(root).save_as(tmp_path / "other" / "root.yaml")

    data = json.loads(json.dumps(result.to_dict()))
    assert set(data) == {
        "root",
        "written",
        "rewrites",
        "absolute",
        "config",
        "rehashed",
        "existing",
        "dry_run",
    }
    assert data["existing"] == []
    assert data["dry_run"] is False
    assert data["rewrites"]["root.yaml"][0] == {
        "where": "inputs.in_dir.default",
        "old": "./data_c",
        "new": "../src/data_c",
        "absolute": False,
    }


DEMO = UC1 / "demo_dive_analysis.yaml"

#: The demo's steps with a path hashed as written (``off``), whose own
#: fingerprint changes when the paths are rewritten.
DEMO_OFF_STEPS = [
    "plan_dives",
    "record_raw_configs",
    "standardize_cal",
    "build_cal_mapping",
    "generate_sv_codes",
    "sv_code_depth_table",
]

#: The longest file name the demo reads through a rewritten path.
DEMO_LONGEST_READ = "Auxiliary/seabed_lines/d20160719_t000758-t115251_seabed.evl"


def _short_folder(tmp_path_factory) -> Path:
    """A fresh folder close to the top of pytest's temporary tree.

    A rewritten path leads up from the copy and down to UC1, and Windows
    without long path support stops reading at 260 characters, so the copy
    goes as high as pytest allows.
    """
    return Path(tempfile.mkdtemp(prefix="m4", dir=tmp_path_factory.getbasetemp()))


def _fits(folder: Path) -> bool:
    """Whether the demo's rewritten reads from ``folder`` fit in 260 characters."""
    if os.name != "nt":
        return True
    reach = os.path.relpath(UC1 / DEMO_LONGEST_READ, folder)
    return len(str(folder)) + 1 + len(reach) < 260


def _demo_hashes(path: Path) -> dict[str, api.StepHash]:
    from aa_recipe_manager.config import load_run_config

    inputs = load_run_config(recipe_path=path).inputs
    return api.step_hashes(path, inputs=inputs, base_dir=path.parent)


@pytest.mark.skipif(not HAVE_EXAMPLES, reason="example recipes not checked out")
def test_demo_recipe_saved_elsewhere(tmp_path_factory):
    folder = _short_folder(tmp_path_factory)
    if not _fits(folder):
        pytest.skip(f"paths from {folder} to UC1 exceed the Windows path limit")
    sub_recipes = _tree_bytes(UC1 / "sub_recipes")
    original = _demo_hashes(DEMO)
    target = folder / "copy" / "demo_copy.yaml"

    result = RecipeWorkspace.open(DEMO).save_as(target)

    new_dir = target.parent
    config = new_dir / "demo_copy.config.yaml"
    assert len(result.written) == 10
    assert result.written[-2:] == [config, target]
    assert result.config == config
    assert len(list((new_dir / "sub_recipes").glob("*.yaml"))) == 8
    assert _tree_bytes(UC1 / "sub_recipes") == sub_recipes
    assert api.dry_run(target, base_dir=new_dir).is_valid
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert (new_dir / settings["user_cache_dir"]).resolve() == (
        UC1 / "demo" / "user"
    ).resolve()

    copied = _demo_hashes(target)
    changed = [s for s in copied if copied[s].step_hash != original[s].step_hash]
    assert result.rehashed == changed
    own = [
        step
        for step in copied
        if copied[step].payload["fingerprint"] != original[step].payload["fingerprint"]
    ]
    assert own == DEMO_OFF_STEPS
    assert {"dive_seabed_line", "run_hdbscan"} <= set(changed)

    kept = folder / "kept" / "demo_copy.yaml"
    shutil.copytree(UC1 / "Auxiliary", kept.parent / "Auxiliary")
    result = RecipeWorkspace.open(DEMO).save_as(kept, paths="fingerprinted")
    assert result.rehashed == []
    assert [r.where for r in result.rewrites["demo_copy.yaml"]] == [
        "inputs.cal_input_folder.default",
        "sub_recipes/processing_lvl_2_user.yaml:seabed_lines_dir",
    ]
    shutil.rmtree(folder, ignore_errors=True)


def test_save_as_same_folder_config_options(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("temp_dir: ./t\n", encoding="utf-8")
    (src / "v2.config.yaml").write_text("temp_dir: ./mine\n", encoding="utf-8")
    ws = RecipeWorkspace.open(root)

    with pytest.raises(TargetExists, match="existing files") as raised:
        ws.save_as(src / "v2.yaml")
    assert raised.value.paths == [src / "v2.config.yaml"]
    assert not (src / "v2.yaml").exists()

    result = ws.save_as(src / "v2.yaml", copy_config=False)
    assert result.config is None
    assert (src / "v2.config.yaml").read_text(encoding="utf-8") == "temp_dir: ./mine\n"

    result = ws.save_as(src / "v3.yaml")
    assert result.config == src / "v3.config.yaml"
    assert (src / "v3.config.yaml").read_bytes() == (
        src / "v2.config.yaml"
    ).read_bytes()


def test_save_as_same_folder_overwrites_the_config_when_asked(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("temp_dir: ./t\n", encoding="utf-8")
    (src / "v2.config.yaml").write_text("temp_dir: ./mine\n", encoding="utf-8")

    result = RecipeWorkspace.open(root).save_as(src / "v2.yaml", overwrite=True)

    assert result.config == src / "v2.config.yaml"
    assert (src / "v2.config.yaml").read_text(encoding="utf-8") == "temp_dir: ./t\n"


def test_save_as_elsewhere_uses_the_given_run_config(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("temp_dir: ./found\n", encoding="utf-8")
    chosen = tmp_path / "chosen.config.yaml"
    chosen.write_text("temp_dir: ./chosen  # the one in use\n", encoding="utf-8")
    target = tmp_path / "other" / "new.yaml"

    result = RecipeWorkspace.open(root).save_as(target, config=chosen)

    assert result.config == target.parent / "new.config.yaml"
    text = result.config.read_text(encoding="utf-8")
    assert text == "temp_dir: ../src/chosen # the one in use\n"
    assert [r.where for r in result.rewrites["new.config.yaml"]] == ["temp_dir"]


def test_save_as_same_folder_leaves_another_run_config_alone(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    (src / "root.config.yaml").write_text("temp_dir: ./t\n", encoding="utf-8")
    chosen = tmp_path / "chosen.config.yaml"
    chosen.write_text("temp_dir: ./chosen\n", encoding="utf-8")

    result = RecipeWorkspace.open(root).save_as(src / "v2.yaml", config=chosen)

    assert result.config is None
    assert not (src / "v2.config.yaml").exists()
    assert result.written == [src / "v2.yaml"]


def test_save_as_with_a_missing_run_config_is_refused(tmp_path):
    src = tmp_path / "src"
    root = _tree(src)
    target = tmp_path / "other" / "new.yaml"

    with pytest.raises(WorkspaceError, match="Run config not found"):
        RecipeWorkspace.open(root).save_as(target, config=tmp_path / "none.yaml")
    assert not target.parent.exists()

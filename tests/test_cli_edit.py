# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for ``aa-recipe edit``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.editing import RecipeWorkspace
from editing.conftest import FIXTURES, HAVE_EXAMPLES, UC1, copy_fixture
from editing.test_operations import NESTED
from editing.test_save_as import _tree
from test_disposal_fan_in import _recipe, helpers  # noqa: F401


def _bytes(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*.yaml"))
    }


def _hashes(recipe: Path, **kwargs) -> dict[str, str]:
    return {k: v.step_hash for k, v in api.step_hashes(recipe, **kwargs).items()}


def _changed(before: dict[str, bytes], folder: Path) -> list[str]:
    after = _bytes(folder)
    return sorted(name for name in after if after[name] != before.get(name))


@pytest.fixture(autouse=True)
def _no_env_config(monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)


@pytest.fixture
def root(tmp_path) -> Path:
    """A copy of the two level include fixture, alone in its folder."""
    (tmp_path / "src").mkdir()
    return copy_fixture(tmp_path / "src", "with_sub.yaml")


def test_dry_run_prints_a_diff_and_writes_nothing(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(
        main, ["edit", str(root), "--set", "leaf_add.k=9", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "--- a/sub/leaf.yaml" in result.output
    assert "-      k: ${inputs.k}\n+      k: 9\n" in result.output
    assert "a/with_sub.yaml" not in result.output
    assert "a/sub/chain.yaml" not in result.output
    assert _bytes(root.parent) == before


def test_dry_run_without_edits_says_so(cli_runner, root):
    result = cli_runner.invoke(main, ["edit", str(root), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "No changes." in result.output
    assert "---" not in result.output


def test_set_writes_only_the_declaring_file(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(main, ["edit", str(root), "--set", "leaf_add.k=9"])

    assert result.exit_code == 0, result.output
    assert "edited sub/leaf.yaml" in result.output
    assert "Valid." in result.output
    assert f"wrote {root.parent / 'sub' / 'leaf.yaml'}" in result.output
    assert _changed(before, root.parent) == ["sub/leaf.yaml"]
    leaf = yaml.safe_load((root.parent / "sub" / "leaf.yaml").read_text())
    assert leaf["steps"][0]["params"]["k"] == 9


def test_values_are_yaml_and_dotted_names_reach_nested_entries(cli_runner, tmp_path):
    path = tmp_path / "nested.yaml"
    path.write_text(NESTED, encoding="utf-8")

    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(path),
            "--set",
            "pick.filters.CRUISE_NAME=HB1507",
            "--set",
            "pick.extensions.1=.bot",
            "--set",
            "pick.filters.LEG=[1, 2]",
            "--unset",
            "pick.filters.NONE_SUCH",
        ],
    )
    assert result.exit_code == 1
    assert "NONE_SUCH" in result.output
    assert path.read_text(encoding="utf-8") == NESTED

    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(path),
            "--set",
            "pick.filters.CRUISE_NAME=HB1507",
            "--set",
            "pick.extensions.1=.bot",
            "--set",
            "pick.filters.LEG=[1, 2]",
        ],
    )
    assert result.exit_code == 0, result.output
    params = yaml.safe_load(path.read_text())["steps"][0]["params"]
    assert params["filters"] == {"CRUISE_NAME": "HB1507", "LEG": [1, 2]}
    assert params["extensions"] == [".raw", ".bot"]


def test_checkpoint_set_and_removed(cli_runner, root):
    chain = root.parent / "sub" / "chain.yaml"
    original = chain.read_bytes()

    result = cli_runner.invoke(
        main, ["edit", str(root), "--checkpoint", "chain_inc=always"]
    )
    assert result.exit_code == 0, result.output
    step = yaml.safe_load(chain.read_text())["steps"][0]
    assert step["checkpoint"] == "always"

    result = cli_runner.invoke(
        main, ["edit", str(root), "--checkpoint", "chain_inc=none"]
    )
    assert result.exit_code == 0, result.output
    assert chain.read_bytes() == original

    result = cli_runner.invoke(
        main, ["edit", str(root), "--checkpoint", "chain_inc=maybe"]
    )
    assert result.exit_code == 1
    assert "always, never, save or none" in result.output


def test_override_and_unset_override(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(
        main, ["edit", str(root), "--override", "sub/chain.yaml:k=7"]
    )
    assert result.exit_code == 0, result.output
    assert _changed(before, root.parent) == ["with_sub.yaml"]
    include = yaml.safe_load(root.read_text())["steps"][1]
    assert include["input_overrides"]["k"] == 7

    result = cli_runner.invoke(
        main, ["edit", str(root), "--unset-override", "sub/leaf.yaml:k"]
    )
    assert result.exit_code == 0, result.output
    assert _changed(before, root.parent) == ["sub/chain.yaml", "with_sub.yaml"]


def test_input_default_finds_the_declaring_file(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(main, ["edit", str(root), "--input-default", "n=5"])
    assert result.exit_code == 0, result.output
    assert _changed(before, root.parent) == ["with_sub.yaml"]
    assert yaml.safe_load(root.read_text())["inputs"]["n"]["default"] == 5

    result = cli_runner.invoke(main, ["edit", str(root), "--input-default", "k=2"])
    assert result.exit_code == 1
    assert "sub/leaf.yaml, sub/chain.yaml" in result.output

    result = cli_runner.invoke(
        main, ["edit", str(root), "--input-default", "sub/leaf.yaml:k=2"]
    )
    assert result.exit_code == 0, result.output
    leaf = yaml.safe_load((root.parent / "sub" / "leaf.yaml").read_text())
    assert leaf["inputs"]["k"]["default"] == 2


def test_replace_text_from_a_file_and_from_stdin(cli_runner, root, tmp_path):
    text = root.read_text(encoding="utf-8").replace("default: 3", "default: 4")
    new = tmp_path / "new.yaml"
    new.write_text(text, encoding="utf-8")

    result = cli_runner.invoke(
        main, ["edit", str(root), "--replace-text", f"with_sub.yaml={new}"]
    )
    assert result.exit_code == 0, result.output
    assert root.read_text(encoding="utf-8") == text

    result = cli_runner.invoke(
        main,
        ["edit", str(root), "--replace-text", "with_sub.yaml=-"],
        input=text.replace("default: 4", "default: 6"),
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(root.read_text())["inputs"]["n"]["default"] == 6


def test_replace_text_that_does_not_parse_is_refused(cli_runner, root, tmp_path):
    before = _bytes(root.parent)
    bad = tmp_path / "bad.yaml"
    bad.write_text("steps: [\n", encoding="utf-8")

    result = cli_runner.invoke(
        main, ["edit", str(root), "--replace-text", f"with_sub.yaml={bad}"]
    )

    assert result.exit_code == 1
    assert "replace_text with_sub.yaml refused" in result.output
    assert _bytes(root.parent) == before


def test_ops_file_in_the_wire_format(cli_runner, root, tmp_path):
    ops = [
        {"kind": "set_param", "step": "leaf_add", "name": "k", "value": 9, "path": []},
        {"kind": "set_checkpoint", "step": "chain_inc", "value": "always"},
        {
            "kind": "set_include_override",
            "include": "sub/chain.yaml",
            "name": "k",
            "value": 7,
        },
        {
            "kind": "set_recipe_input",
            "document": "with_sub.yaml",
            "name": "n",
            "field": "default",
            "value": 5,
        },
    ]
    ops_file = tmp_path / "ops.json"
    ops_file.write_text(json.dumps(ops), encoding="utf-8")
    before = _bytes(root.parent)

    result = cli_runner.invoke(main, ["edit", str(root), "--ops", str(ops_file)])

    assert result.exit_code == 0, result.output
    assert _changed(before, root.parent) == [
        "sub/chain.yaml",
        "sub/leaf.yaml",
        "with_sub.yaml",
    ]
    assert yaml.safe_load(root.read_text())["inputs"]["n"]["default"] == 5


def test_ops_from_stdin_and_bad_ops(cli_runner, root):
    op = {"kind": "set_param", "step": "seed", "name": "n", "value": 4}

    result = cli_runner.invoke(
        main, ["edit", str(root), "--ops", "-", "--dry-run"], input=json.dumps([op])
    )
    assert result.exit_code == 0, result.output
    assert "+      n: 4" in result.output

    result = cli_runner.invoke(
        main, ["edit", str(root), "--ops", "-"], input=json.dumps([{"kind": "nope"}])
    )
    assert result.exit_code == 1
    assert "--ops entry 0: Unknown operation kind 'nope'" in result.output

    result = cli_runner.invoke(main, ["edit", str(root), "--ops", "-"], input="{}")
    assert result.exit_code == 1
    assert "JSON list" in result.output


def test_save_as_beside_the_root(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(
        main,
        ["edit", str(root), "--set", "seed.n=4", "-o", str(root.parent / "copy2.yaml")],
    )

    assert result.exit_code == 0, result.output
    assert _changed(before, root.parent) == ["copy2.yaml"]
    copy = yaml.safe_load((root.parent / "copy2.yaml").read_text())
    assert copy["steps"][0]["params"]["n"] == 4
    assert "rebased" not in result.output


def test_copy_to_leaves_the_originals_and_hashes_identically(
    cli_runner, root, tmp_path
):
    before = _bytes(root.parent)
    work = tmp_path / "work"

    result = cli_runner.invoke(main, ["edit", str(root), "--copy-to", str(work)])

    assert result.exit_code == 0, result.output
    assert _bytes(root.parent) == before
    assert _bytes(work) == before
    assert _hashes(work / "with_sub.yaml", base_dir=root.parent) == _hashes(
        root, base_dir=root.parent
    )

    result = cli_runner.invoke(
        main, ["edit", str(root), "--set", "leaf_add.k=9", "--copy-to", str(work)]
    )
    assert result.exit_code == 0, result.output
    assert _bytes(root.parent) == before
    assert _changed(before, work) == ["sub/leaf.yaml"]


def test_unknown_step_exits_with_its_name(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(main, ["edit", str(root), "--set", "no_such_step.k=1"])

    assert result.exit_code == 1
    assert "no_such_step" in result.output
    assert _bytes(root.parent) == before


@pytest.mark.usefixtures("helpers")
def test_checkpoint_removal_a_fan_in_needs_is_refused(cli_runner, tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(_recipe(convert_checkpoint="always"), encoding="utf-8")
    original = path.read_bytes()

    result = cli_runner.invoke(
        main, ["edit", str(path), "--checkpoint", "convert=none"]
    )

    assert result.exit_code == 1
    assert "set 'checkpoint: always' on 'convert'" in result.output
    assert path.read_bytes() == original


def test_json_output(cli_runner, root):
    result = cli_runner.invoke(
        main, ["edit", str(root), "--set", "leaf_add.k=9", "--dry-run", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload) == {"files", "history", "validation", "written", "diffs"}
    assert [f["label"] for f in payload["files"]] == [
        "with_sub.yaml",
        "sub/leaf.yaml",
        "sub/chain.yaml",
    ]
    assert [f["dirty"] for f in payload["files"]] == [False, True, False]
    assert set(payload["files"][0]) == {"label", "path", "origin", "dirty"}
    assert payload["history"][0]["op"] == {
        "kind": "set_param",
        "step": "leaf_add",
        "name": "k",
        "value": 9,
        "path": [],
        "position": None,
    }
    assert payload["validation"] == {"valid": True, "errors": [], "warnings": []}
    assert payload["written"] == []
    assert list(payload["diffs"]) == ["sub/leaf.yaml"]


def test_check_refuses_to_write_an_invalid_result(cli_runner, root):
    before = _bytes(root.parent)

    result = cli_runner.invoke(
        main, ["edit", str(root), "--set", "leaf_add.k=${inputs.missing}", "--check"]
    )
    assert result.exit_code == 1
    assert "Validation errors:" in result.output
    assert _bytes(root.parent) == before

    result = cli_runner.invoke(
        main, ["edit", str(root), "--set", "leaf_add.k=${inputs.missing}"]
    )
    assert result.exit_code == 0, result.output
    assert "Validation errors:" in result.output
    assert _changed(before, root.parent) == ["sub/leaf.yaml"]


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("seed.cfg.x", "No 'cfg' in step 'seed'"),
        ("seed.n.x", "'n' in step 'seed' is not a container"),
    ],
)
def test_a_nested_set_through_nothing_exits_with_the_place(
    cli_runner, root, key, message
):
    before = _bytes(root.parent)

    result = cli_runner.invoke(main, ["edit", str(root), "--set", f"{key}=1"])

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output
    assert _bytes(root.parent) == before


def test_option_combinations_are_checked(cli_runner, root, tmp_path):
    cases = [
        (["--dry-run", "--copy-to", str(tmp_path / "w")], "--dry-run writes nothing"),
        (["--keep-paths"], "--keep-paths needs --output"),
        (["--overwrite", "--copy-to", str(tmp_path / "w")], "does nothing with"),
        (["--undo"], "--undo needs a working copy with a history"),
        (["--redo", "2"], "--redo needs a working copy with a history"),
        (["--reload"], "--reload needs a working copy with a history"),
        (["--copy-to", str(root.parent)], "another folder than the recipe's"),
        (
            ["-o", str(tmp_path / "x.yaml"), "--copy-to", str(tmp_path / "w")],
            "--output and --copy-to cannot be combined",
        ),
        (["--set", "seed=4"], "needs STEP.PARAM"),
        (["--set", "seed.n"], "STEP.PARAM=VALUE format"),
        (["--override", "k=7"], "needs INCLUDE:NAME"),
    ]
    for args, message in cases:
        result = cli_runner.invoke(main, ["edit", str(root), *args])
        assert result.exit_code == 1, args
        assert message in result.output, (args, result.output)


def _source_tree(tmp_path: Path) -> Path:
    """The Save As test tree with a run config beside it."""
    root = _tree(tmp_path / "src")
    (root.parent / "root.config.yaml").write_text(
        "# Settings for the root recipe.\nuser_cache_dir: ./cache\n", encoding="utf-8"
    )
    return root


def _preview_hashes(cli_runner, recipe: Path, config: Path) -> dict[str, str]:
    result = cli_runner.invoke(
        main, ["preview", recipe.name, "--config", str(config), "--json"]
    )
    assert result.exit_code == 0, result.output
    return {
        row["step_id"]: row["step_hash"] for row in json.loads(result.output)["steps"]
    }


def test_save_as_elsewhere_rewrites_and_reports(cli_runner, tmp_path, monkeypatch):
    root = _source_tree(tmp_path)
    before = _bytes(root.parent)
    target = tmp_path / "other" / "new.yaml"

    result = cli_runner.invoke(main, ["edit", str(root), "-o", str(target)])

    assert result.exit_code == 0, result.output
    assert _bytes(root.parent) == before
    assert (
        "rebased new.yaml off_read.folder: ./data_a -> ../src/data_a" in result.output
    )
    assert "rebased new.config.yaml user_cache_dir: ./cache -> ../src/cache" in (
        result.output
    )
    assert f"run config copied to {target.parent / 'new.config.yaml'}" in result.output
    assert (
        "3 of 9 steps hash differently from the original; the copy starts a new "
        "cache for them: off_read, ref_read, write_out"
    ) in result.output
    assert f"wrote {target}" in result.output

    monkeypatch.chdir(root.parent)
    original = _preview_hashes(cli_runner, root, root.parent / "root.config.yaml")
    monkeypatch.chdir(target.parent)
    copied = _preview_hashes(cli_runner, target, target.parent / "new.config.yaml")
    assert {s for s in copied if copied[s] != original[s]} == {
        "off_read",
        "ref_read",
        "write_out",
    }


def test_save_as_elsewhere_keep_paths(cli_runner, tmp_path, monkeypatch):
    root = _source_tree(tmp_path)
    target = tmp_path / "kept" / "new.yaml"

    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--keep-paths"]
    )
    assert result.exit_code == 0, result.output
    assert "folders the recipe reads by plain path are missing" in result.output

    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--keep-paths"]
    )
    assert result.exit_code == 1
    assert "Save As would replace existing files" in result.output

    for name in ("data_a", "data_c"):
        (target.parent / name).mkdir()
        (target.parent / name / "f.txt").write_text(name, encoding="utf-8")
    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--keep-paths", "--overwrite"]
    )
    assert result.exit_code == 0, result.output
    assert "All 9 steps hash as in the original; the copy shares its caches." in (
        result.output
    )
    assert "off_read.folder" not in result.output

    monkeypatch.chdir(root.parent)
    original = _preview_hashes(cli_runner, root, root.parent / "root.config.yaml")
    monkeypatch.chdir(target.parent)
    assert _preview_hashes(cli_runner, target, target.parent / "new.config.yaml") == (
        original
    )


def test_save_as_elsewhere_json_and_no_config(cli_runner, tmp_path):
    root = _source_tree(tmp_path)
    target = tmp_path / "other" / "new.yaml"

    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--no-config", "--json"]
    )

    assert result.exit_code == 0, result.output
    save_as = json.loads(result.output)["save_as"]
    assert set(save_as) == {
        "root",
        "written",
        "rewrites",
        "absolute",
        "config",
        "rehashed",
        "existing",
        "dry_run",
    }
    assert save_as["config"] is None
    assert save_as["rehashed"] == ["off_read", "ref_read", "write_out"]
    assert {
        "where": "off_read.folder",
        "old": "./data_a",
        "new": "../src/data_a",
        "absolute": False,
    } in save_as["rewrites"]["new.yaml"]
    assert not (target.parent / "new.config.yaml").exists()


def test_save_as_elsewhere_copies_the_config_given(cli_runner, tmp_path):
    root = _source_tree(tmp_path)
    chosen = tmp_path / "chosen.config.yaml"
    chosen.write_text("temp_dir: ./chosen\n", encoding="utf-8")
    target = tmp_path / "other" / "new.yaml"

    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--config", str(chosen)]
    )

    assert result.exit_code == 0, result.output
    copied = target.parent / "new.config.yaml"
    assert copied.read_text(encoding="utf-8") == "temp_dir: ../src/chosen\n"


def test_save_as_beside_copies_the_recipe_config(cli_runner, tmp_path):
    root = _source_tree(tmp_path)
    target = root.parent / "v2.yaml"

    result = cli_runner.invoke(main, ["edit", str(root), "-o", str(target)])

    assert result.exit_code == 0, result.output
    assert (root.parent / "v2.config.yaml").read_bytes() == (
        root.parent / "root.config.yaml"
    ).read_bytes()
    assert f"wrote {root.parent / 'v2.config.yaml'}" in result.output


def test_a_typed_time_stays_a_quoted_string(cli_runner, root):
    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(root),
            "--override",
            "sub/chain.yaml:k=2016-08-14T14:50:00",
            "--input-default",
            "n=2016-08-14",
        ],
    )

    assert result.exit_code == 0, result.output
    text = root.read_text(encoding="utf-8")
    assert '      k: "2016-08-14T14:50:00"\n' in text
    assert '    default: "2016-08-14"\n' in text


def test_validation_warnings_are_listed_not_raised(cli_runner, root):
    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(root),
            "--set",
            "seed.n=abc",
            "--override",
            "sub/chain.yaml:kk=1",
            "--check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Valid, with 2 warning(s).\nWarnings:\n" in result.output
    assert "  - Step 'seed': param 'n' has type 'int' but received str.\n" in (
        result.output
    )
    assert "override 'kk' is not an input of the sub-recipe" in result.output
    assert "UserWarning" not in result.output


def test_an_existing_output_needs_overwrite_and_a_run_config_is_refused(
    cli_runner, tmp_path
):
    root = _source_tree(tmp_path)
    sibling = root.parent / "sibling.yaml"
    sibling.write_text("# another recipe\n", encoding="utf-8")
    before = _bytes(root.parent)

    for target in (sibling, tmp_path / "dest" / "sibling.yaml"):
        target.parent.mkdir(exist_ok=True)
        target.write_text("# another recipe\n", encoding="utf-8")
        result = cli_runner.invoke(main, ["edit", str(root), "-o", str(target)])
        assert result.exit_code == 1
        assert "would replace existing files" in result.output
        assert "or pass --overwrite to replace them." in result.output
        assert "allow overwriting" not in result.output
        assert target.read_text(encoding="utf-8") == "# another recipe\n"
    assert _bytes(root.parent) == before

    config = root.parent / "root.config.yaml"
    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(config), "--overwrite"]
    )
    assert result.exit_code == 1
    assert "'root.config.yaml' is a run config" in result.output
    assert _bytes(root.parent) == before

    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(sibling), "--overwrite", "--no-config"]
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(sibling.read_text())["recipe"]["name"] == "save_as_tree"


def test_copy_to_continues_an_edited_copy(cli_runner, root, tmp_path):
    before = _bytes(root.parent)
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]
    assert cli_runner.invoke(main, [*args, "--set", "leaf_add.k=9"]).exit_code == 0
    assert (work / ".aa-recipe" / "history.jsonl").is_file()

    result = cli_runner.invoke(main, [*args, "--set", "leaf_add.k=2", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [entry["op"]["value"] for entry in payload["history"]] == [9, 2]
    assert payload["disk"] == {"reloaded": [], "conflicts": []}
    assert "      k: 2\n" in (work / "sub" / "leaf.yaml").read_text()
    assert _bytes(root.parent) == before

    result = cli_runner.invoke(
        main, [*args, "--set", "leaf_add.k=${inputs.missing}", "--check"]
    )
    assert result.exit_code == 1
    assert "Edits taken back in the copy" in result.output
    assert "      k: 2\n" in (work / "sub" / "leaf.yaml").read_text()

    shutil.rmtree(work / ".aa-recipe")
    copied = _bytes(work)
    result = cli_runner.invoke(main, [*args, "--set", "leaf_add.k=3"])
    assert result.exit_code == 1
    assert "differ from the originals" in result.output
    assert _bytes(work) == copied


def test_undo_and_redo_on_a_copy(cli_runner, root, tmp_path):
    original = _bytes(root.parent)
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]
    result = cli_runner.invoke(
        main, [*args, "--set", "leaf_add.k=9", "--set", "seed.n=4"]
    )
    assert result.exit_code == 0, result.output
    edited = _bytes(work)

    result = cli_runner.invoke(main, [*args, "--undo", "2"])

    assert result.exit_code == 0, result.output
    assert "undid set_param seed.n\nundid set_param leaf_add.k\n" in result.output
    assert _bytes(work) == original

    result = cli_runner.invoke(main, [*args, "--redo"])
    assert result.exit_code == 0, result.output
    assert "redid set_param leaf_add.k\n" in result.output
    assert "redid set_param seed.n" not in result.output

    result = cli_runner.invoke(main, [*args, "--redo", "3"])
    assert result.exit_code == 0, result.output
    assert "redid set_param seed.n\nNothing more to redo.\n" in result.output
    assert _bytes(work) == edited
    assert _bytes(root.parent) == original


def test_an_undo_is_flushed_when_a_later_option_fails(cli_runner, root, tmp_path):
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]
    assert cli_runner.invoke(main, [*args, "--set", "leaf_add.k=9"]).exit_code == 0

    result = cli_runner.invoke(main, [*args, "--undo", "1", "--set", "bad"])

    assert result.exit_code != 0
    assert "undid set_param leaf_add.k" in result.output
    assert "      k: ${inputs.k}\n" in (work / "sub" / "leaf.yaml").read_text()
    result = cli_runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "behind its history" not in result.output


def test_copy_to_restores_a_missing_root_copy(cli_runner, root, tmp_path):
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]
    assert cli_runner.invoke(main, [*args, "--set", "seed.n=4"]).exit_code == 0
    edited = (work / root.name).read_bytes()
    (work / root.name).unlink()

    result = cli_runner.invoke(main, [*args, "--undo"])

    assert result.exit_code == 0, result.output
    assert (
        f"{root.name} was missing from the working copy and was restored from its "
        "history." in result.output
    )
    assert "undid set_param seed.n" in result.output
    result = cli_runner.invoke(main, [*args, "--redo"])
    assert result.exit_code == 0, result.output
    assert (work / root.name).read_bytes() == edited


def test_copy_to_syncs_and_reload_takes_conflicts(cli_runner, root, tmp_path):
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]
    assert cli_runner.invoke(main, [*args, "--set", "leaf_add.k=9"]).exit_code == 0
    chain, leaf = root.parent / "sub" / "chain.yaml", root.parent / "sub" / "leaf.yaml"
    chain.write_bytes(chain.read_bytes() + b"# changed by hand\n")
    leaf.write_bytes(leaf.read_bytes() + b"# changed by hand\n")

    result = cli_runner.invoke(main, args)

    assert result.exit_code == 0, result.output
    assert "reloaded sub/chain.yaml from disk\n" in result.output
    assert "changed on disk, kept: sub/leaf.yaml\n" in result.output
    assert (work / "sub" / "chain.yaml").read_bytes() == chain.read_bytes()
    assert b"# changed by hand" not in (work / "sub" / "leaf.yaml").read_bytes()

    result = cli_runner.invoke(main, [*args, "--reload"])
    assert result.exit_code == 0, result.output
    assert "reloaded sub/leaf.yaml from disk\n" in result.output
    assert (work / "sub" / "leaf.yaml").read_bytes() == leaf.read_bytes()

    result = cli_runner.invoke(main, [*args, "--undo"])
    assert result.exit_code == 0, result.output
    assert "undid replace_text sub/leaf.yaml\n" in result.output
    assert "      k: 9\n" in (work / "sub" / "leaf.yaml").read_text()

    copied = (work / "sub" / "chain.yaml").read_bytes()
    chain.write_text("steps: [\n", encoding="utf-8")
    result = cli_runner.invoke(main, [*args, "--json"])
    assert result.exit_code == 0, result.output
    (conflict,) = json.loads(result.output)["disk"]["conflicts"]
    assert conflict["label"] == "sub/chain.yaml"
    assert conflict["error"].startswith("line 2: ")
    assert (work / "sub" / "chain.yaml").read_bytes() == copied

    result = cli_runner.invoke(main, [*args, "--reload"])
    assert result.exit_code == 1
    assert "Reload refused: sub/chain.yaml, line 2: " in result.output


def test_save_as_dry_run_prints_the_report(cli_runner, tmp_path):
    root = _source_tree(tmp_path)
    target = tmp_path / "other" / "new.yaml"
    args = ["edit", str(root), "-o", str(target)]

    result = cli_runner.invoke(main, [*args, "--dry-run"])

    assert result.exit_code == 0, result.output
    rebased = "rebased new.yaml off_read.folder: ./data_a -> ../src/data_a"
    assert rebased in result.output
    assert f"would write {target.parent / 'sub' / 'x.yaml'}\n" in result.output
    assert f"would write {target}\n" in result.output
    assert "steps hash differently from the original" in result.output
    copied = target.parent / "new.config.yaml"
    assert f"would copy run config to {copied}\n" in result.output
    assert "run config copied" not in result.output
    assert not target.parent.exists()

    dry = json.loads(cli_runner.invoke(main, [*args, "--dry-run", "--json"]).output)
    real = json.loads(cli_runner.invoke(main, [*args, "--json"]).output)
    assert dry["written"] == []
    assert dry["save_as"]["dry_run"] is True
    assert {**dry["save_as"], "dry_run": False} == real["save_as"]
    assert [Path(p) for p in real["written"]] == [
        Path(p) for p in real["save_as"]["written"]
    ]

    text = target.read_bytes()
    result = cli_runner.invoke(main, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert f"Warning: {target} exists; it needs --overwrite." in result.output
    assert target.read_bytes() == text


def test_overwrite_saves_a_file_changed_on_disk(cli_runner, root, monkeypatch):
    leaf = root.parent / "sub" / "leaf.yaml"
    validate = RecipeWorkspace.validate

    def edit_by_hand_then_validate(self, *args, **kwargs):
        leaf.write_bytes(leaf.read_bytes() + b"# changed by hand\n")
        return validate(self, *args, **kwargs)

    monkeypatch.setattr(RecipeWorkspace, "validate", edit_by_hand_then_validate)
    args = ["edit", str(root), "--set", "leaf_add.k=9"]

    result = cli_runner.invoke(main, args)

    assert result.exit_code == 1
    assert "Changed on disk since they were read" in result.output
    assert "pass --overwrite to replace them" in result.output
    assert b"k: 9" not in leaf.read_bytes()

    result = cli_runner.invoke(main, [*args, "--overwrite"])
    assert result.exit_code == 0, result.output
    assert b"      k: 9" in leaf.read_bytes()
    assert leaf.read_bytes().count(b"# changed by hand") == 1


@pytest.mark.skipif(not HAVE_EXAMPLES, reason="example recipes not checked out")
def test_demo_copy_undo_and_redo(cli_runner, tmp_path):
    src = tmp_path / "uc1"
    src.mkdir()
    shutil.copy2(UC1 / "demo_dive_analysis.yaml", src)
    shutil.copytree(UC1 / "sub_recipes", src / "sub_recipes")
    root = src / "demo_dive_analysis.yaml"
    lvl2 = "sub_recipes/processing_lvl_2_user.yaml"
    work = tmp_path / "work"
    args = ["edit", str(root), "--copy-to", str(work)]

    result = cli_runner.invoke(
        main, [*args, "--set", "dive_remove_noise.SNR_threshold=5"]
    )
    assert result.exit_code == 0, result.output
    edited = (work / lvl2).read_bytes()
    assert edited != (UC1 / lvl2).read_bytes()

    result = cli_runner.invoke(main, [*args, "--undo"])
    assert result.exit_code == 0, result.output
    assert "undid set_param dive_remove_noise.SNR_threshold\n" in result.output
    assert (work / lvl2).read_bytes() == (UC1 / lvl2).read_bytes()

    result = cli_runner.invoke(main, [*args, "--redo"])
    assert result.exit_code == 0, result.output
    assert (work / lvl2).read_bytes() == edited

    target = tmp_path / "v2" / "v2.yaml"
    result = cli_runner.invoke(
        main, ["edit", str(root), "-o", str(target), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert f"would write {target}\n" in result.output
    assert not target.parent.exists()


def test_check_with_errors_prints_no_edits_and_makes_no_copy(
    cli_runner, root, tmp_path
):
    work = tmp_path / "work"

    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(root),
            "--set",
            "leaf_add.k=${inputs.missing}",
            "--check",
            "--copy-to",
            str(work),
        ],
    )

    assert result.exit_code == 1
    assert "edited" not in result.output
    assert "Nothing written" in result.output
    assert not work.exists()


def test_save_as_beside_warns_of_sub_recipes_saved_in_place(cli_runner, root):
    result = cli_runner.invoke(
        main,
        [
            "edit",
            str(root),
            "--set",
            "leaf_add.k=9",
            "-o",
            str(root.parent / "v2.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "saves the edited sub-recipes in place (sub/leaf.yaml)" in result.output
    assert "use --override on the include" in result.output


FLOATS = """\
recipe: {name: floats, version: "1.0", schema_version: "1"}
inputs:
  level:
    type: float
    default: 2.0
steps:
  - include: slow.yaml
    input_overrides:
      seconds: 0.5
"""


def floats_tree(folder: Path) -> Path:
    """A root with a float input, including slow.yaml with a float override."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURES / "slow.yaml", folder / "slow.yaml")
    root = folder / "floats.yaml"
    root.write_text(FLOATS, encoding="utf-8")
    return root


def test_a_whole_number_for_a_float_is_written_as_a_float(cli_runner, tmp_path):
    root = floats_tree(tmp_path)
    args = [
        "edit",
        str(root),
        "--set",
        "slow.seconds=1",
        "--override",
        "slow.yaml:seconds=3",
        "--input-default",
        "level=4",
        "--input-default",
        "slow.yaml:seconds=5",
    ]

    result = cli_runner.invoke(main, args)

    assert result.exit_code == 0, result.output
    text = root.read_text(encoding="utf-8")
    assert "      seconds: 3.0\n" in text
    assert "    default: 4.0\n" in text
    slow = (tmp_path / "slow.yaml").read_text(encoding="utf-8")
    assert "      seconds: 1.0\n" in slow
    assert "    default: 5.0\n" in slow

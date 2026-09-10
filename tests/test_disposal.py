# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for disposable outputs: deleting a step's scratch once it is read."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.exceptions import RecipeValidationError
from aa_recipe_manager.executor.disposal import (
    disposable_ports,
    dispose_step_outputs,
    dispose_value,
)

MOD = "ar_disposal_test_helpers"


@pytest.fixture
def helpers(tmp_path: Path) -> types.ModuleType:
    """Install throwaway callables that write and read real scratch files."""
    module = types.ModuleType(MOD)
    module.scratch_root = tmp_path / "scratch"  # type: ignore[attr-defined]
    module.scratch_root.mkdir(exist_ok=True)  # type: ignore[attr-defined]

    def make_items(n: int = 3) -> list[int]:
        return list(range(n))

    def fetch(item: int) -> dict[str, Any]:
        """Stand in for a download: write a file and a companion directory."""
        root = module.scratch_root / f"item{item}"  # type: ignore[attr-defined]
        root.mkdir(parents=True, exist_ok=True)
        payload = root / "data.raw"
        payload.write_text(f"payload {item}", encoding="utf-8")
        companion = root / "companions"
        companion.mkdir(exist_ok=True)
        (companion / "extra.bot").write_text("bot", encoding="utf-8")
        return {
            "path": payload.as_posix(),
            "written": [payload.as_posix(), companion.as_posix()],
        }

    def consume(path: str) -> int:
        """Stand in for reading the raw file: must run before disposal."""
        return len(Path(path).read_text(encoding="utf-8"))

    def total(values: list[int]) -> int:
        return sum(values)

    module.make_items = make_items  # type: ignore[attr-defined]
    module.fetch = fetch  # type: ignore[attr-defined]
    module.consume = consume  # type: ignore[attr-defined]
    module.total = total  # type: ignore[attr-defined]
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


DEP = '{name: pytest, version: ">=7.0", source: pypi}'


def _recipe(
    checkpoint: str = "never",
    disposable: bool = True,
    dispose_outputs: str = "",
) -> str:
    """Recipe text. ``dispose_outputs`` is the recipe-side opt-in line, if any."""
    opt_in = f"    dispose_outputs: [{dispose_outputs}]\n" if dispose_outputs else ""
    return f"""
recipe:
  name: streaming
  version: "1.0"
  description: fetch, read, dispose
  author: t
  schema_version: "1"
steps:
  - id: seg
    op: custom
    custom_spec:
      description: produce items
      callable_path: {MOD}.make_items
      outputs:
        items: {{type: list}}
      output_map: {{items: __return__}}
      dependency: {DEP}
  - id: fetch
    op: custom
    map_over: ${{seg.items}}
    checkpoint: {checkpoint}
{opt_in}    params:
      item: ${{_item}}
    custom_spec:
      description: download one item
      callable_path: {MOD}.fetch
      params:
        item: {{type: int}}
      outputs:
        path: {{type: path}}
        written: {{type: list, disposable: {str(disposable).lower()}}}
      output_map:
        path: "['path']"
        written: "['written']"
      dependency: {DEP}
  - id: consume
    op: custom
    map_over: ${{seg.items}}
    inputs:
      path: ${{fetch.path}}
    custom_spec:
      description: read the fetched file
      callable_path: {MOD}.consume
      inputs:
        path: {{type: path}}
      outputs:
        size: {{type: int}}
      output_map: {{size: __return__}}
      dependency: {DEP}
  - id: total
    op: custom
    collect: ${{consume.size}}
    inputs:
      values: ${{consume.size}}
    custom_spec:
      description: sum sizes
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "recipe.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# dispose_value
# ---------------------------------------------------------------------------


def test_dispose_value_removes_a_file(tmp_path):
    target = tmp_path / "f.raw"
    target.write_text("x", encoding="utf-8")

    assert dispose_value(target.as_posix()) == 1
    assert not target.exists()


def test_dispose_value_removes_a_directory_tree(tmp_path):
    root = tmp_path / "tree"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "f.txt").write_text("x", encoding="utf-8")

    assert dispose_value(root.as_posix()) == 1
    assert not root.exists()


def test_dispose_value_handles_a_list(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")

    assert dispose_value([a.as_posix(), b.as_posix()]) == 2
    assert not a.exists() and not b.exists()


def test_dispose_value_tolerates_missing_paths(tmp_path):
    """Disposal can run twice; the second pass must be a quiet no-op."""
    target = tmp_path / "gone.raw"
    target.write_text("x", encoding="utf-8")

    assert dispose_value(target.as_posix()) == 1
    assert dispose_value(target.as_posix()) == 0


def test_dispose_value_never_touches_remote_urls():
    """This deletes the run's own scratch, never a bucket object."""
    assert dispose_value("gs://bucket/survey/file.raw") == 0
    assert dispose_value(["s3://b/k", "https://example.com/f.raw"]) == 0


def test_dispose_value_ignores_non_path_values():
    assert dispose_value(42) == 0
    assert dispose_value({"a": 1}) == 0
    assert dispose_value(None) == 0


def test_disposable_ports_reads_the_spec():
    class _Port:
        def __init__(self, disposable):
            self.disposable = disposable

    class _Spec:
        outputs = {"keep": _Port(False), "scratch": _Port(True)}

    assert disposable_ports(_Spec()) == ["scratch"]
    assert dispose_step_outputs(_Spec(), None) == 0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_disposable_output_requires_checkpoint_never(tmp_path, helpers):
    recipe = _write(tmp_path, _recipe(checkpoint="always"))

    # dry_run collects validation errors into its report rather than raising.
    report = api.dry_run(recipe)

    message = " ".join(report.errors)
    assert "disposable output(s) written" in message
    assert "checkpoint: never" in message


def test_disposable_output_with_a_checkpoint_raises_from_build(tmp_path, helpers):
    with pytest.raises(RecipeValidationError, match="disposable output"):
        api.execute(
            _write(tmp_path, _recipe(checkpoint="always")),
            user_cache_dir=str(tmp_path / "cache"),
            outputs_dir=str(tmp_path / "out"),
        )


def test_checkpoint_never_with_a_disposable_output_validates(tmp_path, helpers):
    assert not api.dry_run(_write(tmp_path, _recipe(checkpoint="never"))).errors


# ---------------------------------------------------------------------------
# End to end: a mapped chain cleans up after each instance
# ---------------------------------------------------------------------------


def test_each_instance_disposes_its_own_scratch(tmp_path, helpers):
    recipe = _write(tmp_path, _recipe())

    result = api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )

    # The consumers ran before disposal, so the run still produced its answer.
    assert result.outputs["total"]["n"] == sum(
        len(f"payload {i}") for i in range(3)
    )
    # Every instance's scratch is gone.
    for i in range(3):
        assert not (helpers.scratch_root / f"item{i}" / "data.raw").exists()
        assert not (helpers.scratch_root / f"item{i}" / "companions").exists()


def test_without_the_flag_the_scratch_survives(tmp_path, helpers):
    """The control: disposal happens because the port is marked, not by accident."""
    recipe = _write(tmp_path, _recipe(disposable=False))

    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )

    for i in range(3):
        assert (helpers.scratch_root / f"item{i}" / "data.raw").exists()


def test_disposal_does_not_delete_the_non_disposable_sibling_port(tmp_path, helpers):
    """`path` names a file inside `written`, so it goes; `path` itself is not acted on."""
    recipe = _write(tmp_path, _recipe())

    result = api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )

    # The port value is still reported, it is only the bytes that are gone.
    assert result.outputs["fetch"]["path"]


# ---------------------------------------------------------------------------
# The recipe-side opt-in: dispose_outputs
# ---------------------------------------------------------------------------


def test_dispose_outputs_disposes_a_port_the_spec_did_not_mark(tmp_path, helpers):
    """An op cannot know a recipe's disk budget, so the recipe opts in.

    Marking the port on the spec instead would force checkpoint: never on every
    recipe that uses the op, which is not the op author's call to make.
    """
    recipe = _write(tmp_path, _recipe(disposable=False, dispose_outputs="written"))

    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )

    for i in range(3):
        assert not (helpers.scratch_root / f"item{i}" / "data.raw").exists()


def test_dispose_outputs_leaves_the_sibling_port_alone(tmp_path, helpers):
    """Opting one port in must not widen to the whole step."""
    recipe = _write(tmp_path, _recipe(disposable=False, dispose_outputs="path"))

    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )

    # 'path' named the file, so it goes; 'written' was never requested, and the
    # companions directory it names survives.
    for i in range(3):
        assert (helpers.scratch_root / f"item{i}" / "companions").exists()


def test_dispose_outputs_still_requires_checkpoint_never(tmp_path, helpers):
    """The opt-in does not get to bypass the rule the spec flag obeys."""
    recipe = _write(
        tmp_path, _recipe(checkpoint="always", disposable=False, dispose_outputs="written")
    )

    # dry_run collects validation errors into its report rather than raising.
    message = " ".join(api.dry_run(recipe).errors)
    assert "disposable output(s) written" in message
    assert "checkpoint: never" in message


def test_dispose_outputs_naming_an_unknown_port_is_an_error(tmp_path, helpers):
    """A typo must fail the build, not silently leave the scratch behind."""
    recipe = _write(tmp_path, _recipe(disposable=False, dispose_outputs="wrtten"))

    message = " ".join(api.dry_run(recipe).errors)
    assert "does not produce" in message
    assert "wrtten" in message

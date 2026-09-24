# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Validation: a disposed chain's datasets must be checkpointed before a fan-in."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.exceptions import RecipeValidationError

MOD = "ar_disposal_fan_in_helpers"
DEP = '{name: pytest, version: ">=7.0", source: pypi}'


@pytest.fixture
def helpers() -> types.ModuleType:
    """Callables the specs point at; validation never calls them."""
    module = types.ModuleType(MOD)
    module.make_items = lambda n=3: list(range(n))  # type: ignore[attr-defined]
    module.fetch = lambda item: {"path": f"item{item}.raw"}  # type: ignore[attr-defined]
    module.convert = lambda path: None  # type: ignore[attr-defined]
    module.merge = lambda datasets: None  # type: ignore[attr-defined]
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


def _recipe(
    convert_checkpoint: str | None = None,
    convert_type: str = "Dataset",
    dispose: bool = True,
    collect_from: str = "convert.ds",
    recipe_outputs: str = "",
) -> str:
    """seg -> fetch (disposes) -> convert -> merge (collects), like the raw-cal chain."""
    checkpoint = f"    checkpoint: {convert_checkpoint}\n" if convert_checkpoint else ""
    dispose_line = "    dispose_outputs: [path]\n" if dispose else ""
    return f"""
recipe:
  name: fan_in
  version: "1.0"
  description: dispose then collect
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
    checkpoint: never
{dispose_line}    params:
      item: ${{_item}}
    custom_spec:
      description: download one item
      callable_path: {MOD}.fetch
      params:
        item: {{type: int}}
      outputs:
        path: {{type: path}}
        raw: {{type: EchoData}}
      output_map:
        path: "['path']"
        raw: "['path']"
      dependency: {DEP}
  - id: convert
    op: custom
    map_over: ${{seg.items}}
{checkpoint}    inputs:
      path: ${{fetch.path}}
    custom_spec:
      description: open the fetched file lazily
      callable_path: {MOD}.convert
      inputs:
        path: {{type: path}}
      outputs:
        ds: {{type: {convert_type}}}
      output_map: {{ds: __return__}}
      dependency: {DEP}
  - id: merge
    op: custom
    collect: ${{{collect_from}}}
    inputs:
      datasets: ${{{collect_from}}}
    custom_spec:
      description: fan in
      callable_path: {MOD}.merge
      inputs:
        datasets: {{type: list, many: true}}
      outputs:
        merged: {{type: object}}
      output_map: {{merged: __return__}}
      dependency: {DEP}
{recipe_outputs}"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "recipe.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_uncheckpointed_dataset_collected_after_disposal_is_rejected(tmp_path, helpers):
    with pytest.raises(RecipeValidationError) as info:
        api.execute(
            _write(tmp_path, _recipe()),
            user_cache_dir=str(tmp_path / "cache"),
            outputs_dir=str(tmp_path / "out"),
        )
    message = str(info.value)
    assert "Step 'convert'" in message
    assert "by merge" in message
    assert "'fetch' earlier in the chain disposes" in message
    assert "set 'checkpoint: always' on 'convert'" in message


def test_dry_run_reports_the_rejection(tmp_path, helpers):
    report = api.dry_run(_write(tmp_path, _recipe()))

    assert any("disposes its outputs" in error for error in report.errors)


def test_checkpoint_always_on_the_collected_member_validates(tmp_path, helpers):
    assert not api.dry_run(_write(tmp_path, _recipe(convert_checkpoint="always"))).errors


def test_checkpoint_save_is_not_enough(tmp_path, helpers):
    report = api.dry_run(_write(tmp_path, _recipe(convert_checkpoint="save")))

    assert any("Step 'convert'" in error for error in report.errors)


def test_no_disposal_means_no_rule(tmp_path, helpers):
    assert not api.dry_run(_write(tmp_path, _recipe(dispose=False))).errors


@pytest.mark.parametrize("light", ["int", "str", "path", "list", "dict", "float", "bool"])
def test_small_outputs_are_exempt(tmp_path, helpers, light):
    assert not api.dry_run(_write(tmp_path, _recipe(convert_type=light))).errors


def test_reading_the_disposing_step_itself_is_rejected(tmp_path, helpers):
    report = api.dry_run(_write(tmp_path, _recipe(collect_from="fetch.raw")))

    message = " ".join(report.errors)
    assert "Step 'fetch' (output(s) raw)" in message
    assert "drop the disposal on 'fetch'" in message


def test_recipe_outputs_block_counts_as_a_reader(tmp_path, helpers):
    outputs = (
        "outputs:\n"
        "  converted:\n"
        "    step_id: convert\n"
        "    output_name: ds\n"
    )
    report = api.dry_run(
        _write(tmp_path, _recipe(collect_from="fetch.path", recipe_outputs=outputs))
    )

    message = " ".join(report.errors)
    assert "Step 'convert'" in message
    assert "the recipe outputs block" in message

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Resuming a mapped chain from the last member that has an instance checkpoint.

A chain instance runs its members in order and consults the cache one member at
a time, so a chain whose only checkpointed member is its last one used to re-run
every earlier member on every run even when that last member was a hit for every
instance. On a survey-scale fan-out that is most of the cost of the run. These
tests pin the skip, and pin the guard that gives it up when a skipped member's
output is still read by something.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from aa_recipe_manager import api

MOD = "ar_chain_resume_helpers"
DEP = '{name: pytest, version: ">=7.0", source: pypi}'


@pytest.fixture
def helpers() -> types.ModuleType:
    """Throwaway callables that record which members actually executed."""
    module = types.ModuleType(MOD)
    module.calls = []  # type: ignore[attr-defined]

    def make_items(n: int = 3) -> list[int]:
        return list(range(n))

    def fetch(item: int) -> int:
        module.calls.append(("fetch", item))  # type: ignore[attr-defined]
        return item * 10

    def consume(value: int) -> int:
        module.calls.append(("consume", value))  # type: ignore[attr-defined]
        return value + 1

    def total(values: list[int]) -> int:
        module.calls.append(("total", tuple(values)))  # type: ignore[attr-defined]
        return sum(v for v in values if v is not None)

    module.make_items = make_items  # type: ignore[attr-defined]
    module.fetch = fetch  # type: ignore[attr-defined]
    module.consume = consume  # type: ignore[attr-defined]
    module.total = total  # type: ignore[attr-defined]
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


def _recipe(*, extra_consumer: bool = False) -> str:
    """fetch -> consume (checkpointed) -> total.

    ``extra_consumer`` adds a second fan-in reading ``fetch`` directly, which
    must stop the skip: its input has to be computed.
    """
    tail = ""
    if extra_consumer:
        tail = f"""
  - id: audit
    op: custom
    collect: ${{fetch.value}}
    inputs:
      values: ${{fetch.value}}
    custom_spec:
      description: read the skippable member from outside the chain
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""
    return f"""
recipe:
  name: chain_resume
  version: "1.0"
  description: resume a mapped chain from its checkpointed tail
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
    params:
      item: ${{_item}}
    custom_spec:
      description: the expensive upstream member
      callable_path: {MOD}.fetch
      params:
        item: {{type: int}}
      outputs:
        value: {{type: int}}
      output_map: {{value: __return__}}
      dependency: {DEP}
  - id: consume
    op: custom
    map_over: ${{seg.items}}
    checkpoint: always
    inputs:
      value: ${{fetch.value}}
    custom_spec:
      description: the chain's surviving output
      callable_path: {MOD}.consume
      inputs:
        value: {{type: int}}
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
{tail}"""


def _run(tmp_path: Path, text: str):
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(text, encoding="utf-8")
    return api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )


def _ran(helpers, step: str) -> int:
    return sum(1 for name, _ in helpers.calls if name == step)


def test_first_run_executes_every_member(tmp_path, helpers):
    result = _run(tmp_path, _recipe())

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 3
    assert result.outputs["total"]["n"] == 33


def test_second_run_skips_the_members_below_the_checkpoint(tmp_path, helpers):
    _run(tmp_path, _recipe())
    helpers.calls.clear()

    result = _run(tmp_path, _recipe())

    # consume is a per-instance cache hit, so nothing above it needs to run.
    assert _ran(helpers, "fetch") == 0
    assert _ran(helpers, "consume") == 0
    # The chain really was scheduled: its collector still ran, on cached values.
    assert _ran(helpers, "total") == 1
    assert result.outputs["total"]["n"] == 33


def test_an_outside_consumer_of_a_skippable_member_blocks_the_skip(tmp_path, helpers):
    """``audit`` reads ``fetch`` directly, so ``fetch`` still has to be computed."""
    _run(tmp_path, _recipe(extra_consumer=True))
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(extra_consumer=True))

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 0, "the checkpointed member still loads"
    assert result.outputs["audit"]["n"] == 30


def test_force_recomputes_the_whole_chain(tmp_path, helpers):
    _run(tmp_path, _recipe())
    helpers.calls.clear()

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(_recipe(), encoding="utf-8")
    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
        force=True,
    )

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 3

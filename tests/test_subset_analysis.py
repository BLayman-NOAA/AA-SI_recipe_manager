# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Branching off a cached per-file chain to analyse a subset.

The shape sv_window_example.yaml relies on:

    seg -> fetch (mapped, checkpointed; the per-file Sv)
        -> window (mapped; None for the files a window does not touch)
        -> derive (mapped, checkpointed; per-file work on what survived)
        -> total  (collect derive.value; the fan-in)

Three things have to hold for it to be cheap and correct. An instance that
``window`` declared empty must pass through ``derive`` without invoking it,
and be dropped at the fan-in. ``fetch`` must never re-execute for a new window.
And the client must not reload every ``fetch`` checkpoint to fold a chain that
nothing outside it reads, which on a 3322-file survey is half an hour of bucket
reads per window for a value nobody looks at.
"""

from __future__ import annotations

import collections
import sys
import types
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.executor.tiered import TieredCheckpointStore

MOD = "ar_subset_helpers"
DEP = '{name: pytest, version: ">=7.0", source: pypi}'


@pytest.fixture
def helpers() -> types.ModuleType:
    module = types.ModuleType(MOD)
    module.calls = collections.Counter()  # type: ignore[attr-defined]

    def make_items(n: int = 10) -> list[int]:
        return list(range(n))

    def fetch(item: int) -> int:
        module.calls["fetch"] += 1  # type: ignore[attr-defined]
        return item * 10

    def window(value: int, keep: list[int]):
        module.calls["window"] += 1  # type: ignore[attr-defined]
        return value if value in keep else None

    def derive(value: int) -> int:
        module.calls["derive"] += 1  # type: ignore[attr-defined]
        assert value is not None, "derive must never see an empty instance"
        return value * 2

    def total(values: list) -> int:
        module.calls["total"] += 1  # type: ignore[attr-defined]
        return sum(v for v in values if v is not None)

    for name, fn in (("make_items", make_items), ("fetch", fetch),
                     ("window", window), ("derive", derive), ("total", total)):
        setattr(module, name, fn)
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


def _recipe(keep: str = "[30, 40]") -> str:
    return f"""
recipe:
  name: subset_analysis
  version: "1.0"
  description: branch off a cached per-file chain
  author: t
  schema_version: "1"
steps:
  - id: seg
    op: custom
    custom_spec:
      description: items
      callable_path: {MOD}.make_items
      outputs:
        items: {{type: list}}
      output_map: {{items: __return__}}
      dependency: {DEP}
  - id: fetch
    op: custom
    map_over: ${{seg.items}}
    checkpoint: always
    params:
      item: ${{_item}}
    custom_spec:
      description: the expensive per-file value
      callable_path: {MOD}.fetch
      params:
        item: {{type: int}}
      outputs:
        value: {{type: int}}
      output_map: {{value: __return__}}
      dependency: {DEP}
  - id: window
    op: custom
    map_over: ${{seg.items}}
    inputs:
      value: ${{fetch.value}}
    params:
      keep: {keep}
    custom_spec:
      description: empty for the files the window does not touch
      callable_path: {MOD}.window
      inputs:
        value: {{type: int}}
      params:
        keep: {{type: list}}
      outputs:
        value: {{type: int}}
      output_map: {{value: __return__}}
      dependency: {DEP}
  - id: derive
    op: custom
    map_over: ${{seg.items}}
    checkpoint: always
    inputs:
      value: ${{window.value}}
    custom_spec:
      description: per-file work on what survived the window
      callable_path: {MOD}.derive
      inputs:
        value: {{type: int}}
      outputs:
        value: {{type: int}}
      output_map: {{value: __return__}}
      dependency: {DEP}
  - id: total
    op: custom
    collect: ${{derive.value}}
    checkpoint: always
    inputs:
      values: ${{derive.value}}
    custom_spec:
      description: fan in
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""


def _run(tmp_path: Path, text: str):
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(text, encoding="utf-8")
    return api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )


@pytest.fixture
def load_counts():
    counts: collections.Counter = collections.Counter()
    original = TieredCheckpointStore.load

    def counting(self, step_id, **kwargs):
        counts[step_id] += 1
        return original(self, step_id, **kwargs)

    TieredCheckpointStore.load = counting
    try:
        yield counts
    finally:
        TieredCheckpointStore.load = original


def test_an_empty_instance_passes_through_without_invoking_the_op(tmp_path, helpers):
    result = _run(tmp_path, _recipe())

    assert helpers.calls["window"] == 10
    assert helpers.calls["derive"] == 2, "only the two files the window touched"
    assert result.outputs["derive"]["value"].count(None) == 8
    assert result.outputs["total"]["n"] == 140


def test_an_empty_instance_is_not_checkpointed(tmp_path, helpers):
    _run(tmp_path, _recipe())

    entries = list((tmp_path / "cache").rglob("derive/*/meta.json"))
    assert len(entries) == 2, "two real instances, no pickled Nones"


def test_a_new_window_reuses_the_expensive_member(tmp_path, helpers):
    _run(tmp_path, _recipe())
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(keep="[50]"))

    assert helpers.calls["fetch"] == 0
    assert helpers.calls["window"] == 10
    assert helpers.calls["derive"] == 1
    assert result.outputs["total"]["n"] == 100


def test_the_client_does_not_reload_a_member_nothing_outside_reads(
    tmp_path, helpers, load_counts
):
    """fetch is read only inside the chain, by window.

    The worker loads it once per instance to run window. The client used to
    load it all over again to fold the chain, then evict the result unread.
    """
    _run(tmp_path, _recipe())
    load_counts.clear()

    result = _run(tmp_path, _recipe(keep="[50]"))

    assert load_counts["fetch"] == 10, "worker-side loads only"
    # The value is still reachable, lazily, for anyone who does look.
    assert list(result.outputs["fetch"]["value"]) == [i * 10 for i in range(10)]
    assert load_counts["fetch"] == 20, "and reading it is what loads it"


def test_a_member_something_outside_reads_is_still_loaded(tmp_path, helpers, load_counts):
    """derive feeds the collector, so the client must materialise it.

    One derive instance is checkpointed and nine are empty, so the member is
    not all-checkpointed and is not deferred: the client reloads the one real
    instance to fold the fan-in list, exactly as it always did, and the empties
    ride along inline.
    """
    _run(tmp_path, _recipe())
    load_counts.clear()

    result = _run(tmp_path, _recipe(keep="[50]"))

    assert load_counts["derive"] == 1
    assert result.outputs["total"]["n"] == 100

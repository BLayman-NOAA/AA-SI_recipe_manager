# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The dry-run diagram shows data flow, with fan-out drawn once per chain."""

from __future__ import annotations

import sys
import types

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.validation import DryRunEngine

MOD = "ar_mermaid_helpers"
DEP = '{name: pytest, version: ">=7.0", source: pypi}'

RECIPE = f"""
recipe:
  name: mermaid
  version: "1.0"
  description: a mapped chain, a collector and a param reference
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
  - id: offset
    op: custom
    custom_spec:
      description: a scalar a later step takes as a param
      callable_path: {MOD}.offset
      outputs:
        amount: {{type: int}}
      output_map: {{amount: __return__}}
      dependency: {DEP}
  - id: fetch
    op: custom
    map_over: ${{seg.items}}
    params:
      item: ${{_item}}
    custom_spec:
      description: reads the item
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
    inputs:
      value: ${{fetch.value}}
    params:
      amount: ${{offset.amount}}
    custom_spec:
      description: reads its neighbour, not the item
      callable_path: {MOD}.consume
      inputs:
        value: {{type: int}}
      params:
        amount: {{type: int}}
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
      description: fan in
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""


@pytest.fixture
def helpers():
    module = types.ModuleType(MOD)
    module.make_items = lambda: [0, 1, 2]  # type: ignore[attr-defined]
    module.offset = lambda: 1  # type: ignore[attr-defined]
    module.fetch = lambda item: item * 10  # type: ignore[attr-defined]
    module.consume = lambda value, amount: value + amount  # type: ignore[attr-defined]
    module.total = lambda values: sum(values)  # type: ignore[attr-defined]
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


@pytest.fixture
def diagram(tmp_path, helpers) -> str:
    path = tmp_path / "recipe.yaml"
    path.write_text(RECIPE, encoding="utf-8")
    dag = api._load_dag(path, check_versions=False)
    return DryRunEngine()._build_mermaid(dag)


def _lines(diagram: str) -> list[str]:
    return [line.strip() for line in diagram.splitlines()]


def test_chain_is_one_box_entered_by_one_map_over_arrow(diagram):
    lines = _lines(diagram)
    assert lines[0] == "graph TD"
    start = lines.index('subgraph chain_0 ["map_over: seg.items, one instance per item"]')
    end = lines.index("end", start)
    inside = lines[start + 1:end]
    assert [line.split("[")[0] for line in inside] == ["fetch", "consume"]
    assert diagram.count("map_over: items") == 1
    assert 'seg -. "map_over: items" .-> chain_0' in lines


def test_only_the_member_that_reads_the_item_gets_an_item_arrow(diagram):
    lines = _lines(diagram)
    assert 'seg -->|"items (item)"| fetch' in lines
    assert not any(line.endswith("| consume") and "(item)" in line for line in lines)


def test_data_and_param_references_are_solid_arrows(diagram):
    lines = _lines(diagram)
    assert 'fetch -->|"value"| consume' in lines
    assert 'offset -->|"amount"| consume' in lines


def test_collector_is_entered_by_one_collect_arrow(diagram):
    lines = _lines(diagram)
    into_total = [line for line in lines if line.endswith("total") and "-" in line]
    assert into_total == ['consume -. "collect: size" .-> total']


def test_steps_outside_a_chain_are_declared_at_top_level(diagram):
    lines = _lines(diagram)
    assert any(line.startswith("seg[") for line in lines)
    assert any(line.startswith("total[") for line in lines)
    assert "[collect]" in next(line for line in lines if line.startswith("total["))

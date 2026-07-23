# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for Stage 8 fan-out / fan-in: map_over, collect, and sweep."""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import nbformat

from aa_recipe_manager.exceptions import RecipeValidationError
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.generator.backends.notebook import NotebookBackend
from aa_recipe_manager.resolver.dependencies import resolve_dependencies
from aa_recipe_manager.model.types import (
    CustomSpec,
    Dependency,
    PortDeclaration,
    ParamDeclaration,
    Recipe,
    Step,
    SweepDeclaration,
)
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.parallel import (
    derive_instance_hash,
    expand_sweep,
    group_mapped_chains,
    instance_discriminator,
)
from aa_recipe_manager.registry.registry import Registry

MOD = "ar_stage8_test_helpers"


@pytest.fixture
def helpers() -> types.ModuleType:
    """Install a throwaway module of deterministic callables and reset its log."""
    module = sys.modules.get(MOD)
    if module is None:
        module = types.ModuleType(MOD)
        module.call_log = []  # type: ignore[attr-defined]

        def _rec(name: str, **kw: Any) -> None:
            module.call_log.append((name, kw))  # type: ignore[attr-defined]

        def make_list(n: int = 3) -> list[int]:
            _rec("make_list", n=n)
            return [(i + 1) * 10 for i in range(n)]

        def make_scalar() -> int:
            _rec("make_scalar")
            return 7

        def inc(x: int) -> int:
            _rec("inc", x=x)
            return x + 1

        def scale(x: int, factor: int = 2) -> int:
            _rec("scale", x=x, factor=factor)
            return x * factor

        def offset(x: int, factor: int = 2, base: int = 0) -> int:
            _rec("offset", x=x, factor=factor, base=base)
            return x * factor + base

        def collect_sum(values: list[int]) -> int:
            _rec("collect_sum", values=list(values))
            return sum(values)

        def collect_named(out: list[int]) -> int:
            _rec("collect_named", out=list(out))
            return sum(out)

        for fn in (
            make_list, make_scalar, inc, scale, offset, collect_sum, collect_named
        ):
            setattr(module, fn.__name__, fn)
        sys.modules[MOD] = module
    module.call_log.clear()  # type: ignore[attr-defined]
    return module


def _dep() -> Dependency:
    return Dependency(name="pytest", version=">=7.0", source="pypi")


def _step(
    step_id: str,
    callable_name: str,
    *,
    inputs: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    in_ports: dict[str, PortDeclaration] | None = None,
    out_ports: dict[str, PortDeclaration] | None = None,
    param_decls: dict[str, ParamDeclaration] | None = None,
    output_map: dict[str, str] | None = None,
    map_over: str | None = None,
    collect: str | None = None,
    sweep: SweepDeclaration | None = None,
) -> Step:
    return Step(
        id=step_id,
        op="custom",
        inputs=inputs or {},
        params=params or {},
        map_over=map_over,
        collect=collect,
        sweep=sweep,
        custom_spec=CustomSpec(
            description=f"custom {callable_name}",
            callable_path=f"{MOD}.{callable_name}",
            inputs=in_ports or {},
            outputs=out_ports or {},
            params=param_decls or {},
            output_map=output_map or {},
            dependency=_dep(),
        ),
    )


def _recipe(steps: list[Step], inputs: dict[str, Any] | None = None) -> Recipe:
    return Recipe(
        name="stage8_pipeline",
        version="1.0.0",
        schema_version="1",
        inputs=inputs or {},
        steps=steps,
    )


def _run(steps: list[Step], **kw: Any):
    dag = build_dag(_recipe(steps), Registry(), check_versions=False)
    return SequentialExecutor().execute(dag, **kw)


_INT = PortDeclaration(type="int")
_LIST = PortDeclaration(type="list")
_MANY = PortDeclaration(type="int", many=True)


# ---------------------------------------------------------------------------
# map_over / collect
# ---------------------------------------------------------------------------


def _map_pipeline(collector_step: Step) -> list[Step]:
    producer = _step(
        "seg", "make_list",
        out_ports={"items": _LIST}, output_map={"items": "__return__"},
    )
    mapped = _step(
        "proc", "inc",
        inputs={"x": "${_item}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        output_map={"out": "__return__"},
        map_over="${seg.items}",
    )
    return [producer, mapped, collector_step]


def test_map_over_fans_out_and_collects(helpers):
    collector = _step(
        "merge", "collect_sum",
        inputs={"values": "${proc.out}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"},
        collect="${proc.out}",
    )
    result = _run(_map_pipeline(collector))
    # make_list -> [10, 20, 30]; inc each -> [11, 21, 31]; sum -> 63
    assert result.outputs["merge"]["total"] == 63
    assert result.outputs["proc"]["out"] == [11, 21, 31]
    inc_calls = [c for c in helpers.call_log if c[0] == "inc"]
    assert len(inc_calls) == 3


def test_collect_auto_binds_to_matching_port(helpers):
    # Collector wires nothing explicitly; the collected output name `out`
    # auto-binds to the collector's `out` input port.
    collector = _step(
        "merge", "collect_named",
        in_ports={"out": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"},
        collect="${proc.out}",
    )
    result = _run(_map_pipeline(collector))
    assert result.outputs["merge"]["total"] == 63


def test_single_item_transparency(helpers):
    # A non-list source runs the mapped step exactly once.
    producer = _step(
        "seg", "make_scalar",
        out_ports={"item": _INT}, output_map={"item": "__return__"},
    )
    mapped = _step(
        "proc", "inc",
        inputs={"x": "${_item}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        output_map={"out": "__return__"},
        map_over="${seg.item}",
    )
    result = _run([producer, mapped])
    assert result.outputs["proc"]["out"] == [8]  # inc(7), folded as a 1-list
    assert len([c for c in helpers.call_log if c[0] == "inc"]) == 1


def test_mapped_chain_shares_element_context(helpers):
    producer = _step(
        "seg", "make_list",
        out_ports={"items": _LIST}, output_map={"items": "__return__"},
    )
    a = _step(
        "a", "inc", inputs={"x": "${_item}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        output_map={"out": "__return__"}, map_over="${seg.items}",
    )
    b = _step(
        "b", "inc", inputs={"x": "${a.out}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        output_map={"out": "__return__"}, map_over="${seg.items}",
    )
    collector = _step(
        "merge", "collect_sum", inputs={"values": "${b.out}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"}, collect="${b.out}",
    )
    result = _run([producer, a, b, collector])
    # [10,20,30] -> a: [11,21,31] -> b: [12,22,32] -> sum 66
    assert result.outputs["b"]["out"] == [12, 22, 32]
    assert result.outputs["merge"]["total"] == 66


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def _const_step() -> Step:
    return _step(
        "const", "make_scalar",
        out_ports={"val": _INT}, output_map={"val": "__return__"},
    )


def test_sweep_zip(helpers):
    swept = _step(
        "sweep_scale", "scale",
        inputs={"x": "${const.val}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        param_decls={"factor": ParamDeclaration(type="int")},
        output_map={"out": "__return__"},
        sweep=SweepDeclaration(param_lists={"factor": [2, 3, 4]}, mode="zip"),
    )
    collector = _step(
        "merge", "collect_sum", inputs={"values": "${sweep_scale.out}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"}, collect="${sweep_scale.out}",
    )
    result = _run([_const_step(), swept, collector])
    # make_scalar -> 7; 7*2, 7*3, 7*4 -> [14, 21, 28] -> 63
    assert result.outputs["sweep_scale"]["out"] == [14, 21, 28]
    assert result.outputs["merge"]["total"] == 63


def test_sweep_grid_outer_product(helpers):
    swept = _step(
        "sweep_off", "offset",
        inputs={"x": "${const.val}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        param_decls={
            "factor": ParamDeclaration(type="int"),
            "base": ParamDeclaration(type="int"),
        },
        output_map={"out": "__return__"},
        sweep=SweepDeclaration(
            param_lists={"factor": [1, 2], "base": [0, 100]}, mode="grid"
        ),
    )
    result = _run([_const_step(), swept])
    # make_scalar -> 7; grid: (1,0)->7, (1,100)->107, (2,0)->14, (2,100)->114
    assert result.outputs["sweep_off"]["out"] == [7, 107, 14, 114]


def test_expand_sweep_helpers():
    zip_combos = expand_sweep(
        SweepDeclaration(param_lists={"a": [1, 2], "b": [3, 4]}, mode="zip")
    )
    assert zip_combos == [{"a": 1, "b": 3}, {"a": 2, "b": 4}]
    grid_combos = expand_sweep(
        SweepDeclaration(param_lists={"a": [1, 2], "b": [3, 4]}, mode="grid")
    )
    assert grid_combos == [
        {"a": 1, "b": 3}, {"a": 1, "b": 4}, {"a": 2, "b": 3}, {"a": 2, "b": 4}
    ]


def test_instance_hash_content_addressing():
    base = "abc123"
    # Serializable items dedupe by value; unserializable fall back to index.
    d_a = instance_discriminator(index=0, item="file_a.raw")
    d_a2 = instance_discriminator(index=5, item="file_a.raw")
    assert derive_instance_hash(base, d_a) == derive_instance_hash(base, d_a2)
    d_b = instance_discriminator(index=1, item="file_b.raw")
    assert derive_instance_hash(base, d_a) != derive_instance_hash(base, d_b)
    obj = object()
    d_obj = instance_discriminator(index=3, item=obj)
    assert d_obj == {"item_index": 3}


# ---------------------------------------------------------------------------
# per-instance checkpoint resume
# ---------------------------------------------------------------------------


def test_per_instance_checkpoint_resume(helpers, tmp_path):
    steps = _map_pipeline(
        _step(
            "merge", "collect_sum", inputs={"values": "${proc.out}"},
            in_ports={"values": _MANY}, out_ports={"total": _INT},
            output_map={"total": "__return__"}, collect="${proc.out}",
        )
    )
    ckpt = tmp_path / "cache"
    r1 = _run(steps, output_dir=ckpt, checkpoint_mode="eager")
    assert r1.outputs["merge"]["total"] == 63
    first_inc = [c for c in helpers.call_log if c[0] == "inc"]
    assert len(first_inc) == 3

    # Second run: every mapped instance is a cache hit; inc is never called.
    helpers.call_log.clear()
    r2 = _run(steps, output_dir=ckpt, checkpoint_mode="eager")
    assert r2.outputs["merge"]["total"] == 63
    assert [c for c in helpers.call_log if c[0] == "inc"] == []


def test_group_mapped_chains_partitions_by_source(helpers):
    steps = _map_pipeline(
        _step(
            "merge", "collect_sum", inputs={"values": "${proc.out}"},
            in_ports={"values": _MANY}, out_ports={"total": _INT},
            output_map={"total": "__return__"}, collect="${proc.out}",
        )
    )
    dag = build_dag(_recipe(steps), Registry(), check_versions=False)
    chains = group_mapped_chains(dag)
    assert len(chains) == 1
    assert chains[0].member_ids == ["proc"]
    assert chains[0].source_ref == "${seg.items}"


# ---------------------------------------------------------------------------
# validation (FR-14.6 / FR-18.3)
# ---------------------------------------------------------------------------


def _expect_error(steps: list[Step], needle: str) -> None:
    with pytest.raises(RecipeValidationError) as exc:
        build_dag(_recipe(steps), Registry(), check_versions=False)
    assert any(needle in e for e in exc.value.errors), exc.value.errors


def test_collect_on_non_mapped_step_errors(helpers):
    producer = _step(
        "seg", "make_list",
        out_ports={"items": _LIST}, output_map={"items": "__return__"},
    )
    collector = _step(
        "merge", "collect_sum", inputs={"values": "${seg.items}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"}, collect="${seg.items}",
    )
    _expect_error([producer, collector], "neither mapped")


def test_sweep_param_in_params_errors(helpers):
    swept = _step(
        "s", "scale", inputs={"x": "${inputs.x0}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        params={"factor": 9},
        param_decls={"factor": ParamDeclaration(type="int")},
        output_map={"out": "__return__"},
        sweep=SweepDeclaration(param_lists={"factor": [2, 3]}),
    )
    _expect_error([swept], "must not also appear")


def test_sweep_zip_unequal_lengths_errors(helpers):
    swept = _step(
        "s", "offset", inputs={"x": "${inputs.x0}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        param_decls={
            "factor": ParamDeclaration(type="int"),
            "base": ParamDeclaration(type="int"),
        },
        output_map={"out": "__return__"},
        sweep=SweepDeclaration(param_lists={"factor": [2, 3], "base": [0]}),
    )
    _expect_error([swept], "same length")


def test_item_ref_without_map_over_errors(helpers):
    lonely = _step(
        "p", "inc", inputs={"x": "${_item}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        output_map={"out": "__return__"},
    )
    _expect_error([lonely], "no map_over")


# ---------------------------------------------------------------------------
# code generation (8f)
# ---------------------------------------------------------------------------


def _generate_code_cells(steps: list[Step], tmp_path) -> list[str]:
    dag = build_dag(_recipe(steps), Registry(), check_versions=False)
    out = tmp_path / "nb.ipynb"
    NotebookBackend().generate(dag, resolve_dependencies(dag), out)
    nb = nbformat.reads(out.read_text(encoding="utf-8"), as_version=4)
    code = [c.source for c in nb.cells if c.cell_type == "code"]
    for src in code:
        ast.parse(src)  # every code cell must be valid Python
    return code


def test_map_over_notebook_emits_loop(helpers, tmp_path):
    collector = _step(
        "merge", "collect_sum", inputs={"values": "${proc.out}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"}, collect="${proc.out}",
    )
    code = "\n\n".join(_generate_code_cells(_map_pipeline(collector), tmp_path))
    assert "for _item in items:" in code
    assert "out = []" in code  # accumulator init
    assert "out.append(_iter_out)" in code
    # collector reads the accumulated list
    assert "collect_sum(values=out)" in code


def test_sweep_notebook_emits_loop(helpers, tmp_path):
    swept = _step(
        "sweep_scale", "scale",
        inputs={"x": "${const.val}"},
        in_ports={"x": _INT}, out_ports={"out": _INT},
        param_decls={"factor": ParamDeclaration(type="int")},
        output_map={"out": "__return__"},
        sweep=SweepDeclaration(param_lists={"factor": [2, 3, 4]}, mode="zip"),
    )
    code = "\n\n".join(_generate_code_cells([_const_step(), swept], tmp_path))
    assert "for _sweep in [{'factor': 2}, {'factor': 3}, {'factor': 4}]:" in code
    assert "_sweep['factor']" in code


# ---------------------------------------------------------------------------
# map_over + include composition (8g, FR-14.4)
# ---------------------------------------------------------------------------


_CHILD_YAML = """
recipe:
  name: per_item
  version: "1.0"
  schema_version: "1"
inputs:
  item_in:
    type: int
steps:
  - id: proc
    op: custom
    inputs:
      x: ${inputs.item_in}
    custom_spec:
      description: inc one item
      callable_path: ar_stage8_test_helpers.inc
      inputs:
        x: {type: int}
      outputs:
        out: {type: int}
      output_map:
        out: __return__
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""

_PARENT_YAML = """
recipe:
  name: mapped_include
  version: "1.0"
  schema_version: "1"
steps:
  - id: seg
    op: custom
    custom_spec:
      description: produce a list
      callable_path: ar_stage8_test_helpers.make_list
      outputs:
        items: {type: list}
      output_map:
        items: __return__
      dependency: {name: pytest, version: ">=7.0", source: pypi}
  - include: child.yaml
    map_over: ${seg.items}
    input_overrides:
      item_in: ${_item}
  - id: merge
    op: custom
    inputs:
      values: ${proc.out}
    collect: ${proc.out}
    custom_spec:
      description: sum
      callable_path: ar_stage8_test_helpers.collect_sum
      inputs:
        values: {type: int, many: true}
      outputs:
        total: {type: int}
      output_map:
        total: __return__
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""


def test_map_over_plus_include_fans_out_subworkflow(helpers, tmp_path):
    (tmp_path / "child.yaml").write_text(_CHILD_YAML, encoding="utf-8")
    parent = tmp_path / "parent.yaml"
    parent.write_text(_PARENT_YAML, encoding="utf-8")

    recipe = load_recipe(parent)
    # The included step inherits the include entry's map_over source and binds
    # its entry input to ${_item}.
    proc = next(s for s in recipe.steps if s.id == "proc")
    assert proc.map_over == "${seg.items}"
    assert proc.inputs["x"] == "${_item}"

    dag = build_dag(recipe, Registry(), check_versions=False)
    result = SequentialExecutor().execute(dag)
    # make_list -> [10,20,30]; inc each -> [11,21,31]; sum 63
    assert result.outputs["merge"]["total"] == 63
    assert len([c for c in helpers.call_log if c[0] == "inc"]) == 3


# ---------------------------------------------------------------------------
# example recipes (8h) — the sweep-ensemble and per-file map/collect demos
# validate structurally against the real builtin registry
# ---------------------------------------------------------------------------


_EXAMPLES = Path(__file__).resolve().parent.parent / "example_recipes"


def _dry_run_example(name: str):
    from aa_recipe_manager.registry.loader import load_builtin_registry
    from aa_recipe_manager.validation import DryRunEngine

    registry = load_builtin_registry()
    recipe = load_recipe(_EXAMPLES / name)
    dag = build_dag(recipe, registry, check_versions=False)
    report = DryRunEngine().run(dag, visualize=True, check_versions=False)
    return dag, report


def test_sweep_ensemble_example_valid():
    dag, report = _dry_run_example("machine_learning_sweep.yaml")
    assert report.is_valid, report.errors
    assert dag.nodes["hdbscan_sweep"].is_swept
    assert dag.nodes["embed_ensemble"].is_collector
    assert "[sweep]" in (report.dag_diagram or "")


def test_parallel_per_file_example_valid():
    dag, report = _dry_run_example("parallel_per_file_mvbs.yaml")
    assert report.is_valid, report.errors
    chains = group_mapped_chains(dag)
    assert len(chains) == 1
    assert chains[0].member_ids == [
        "read_raw", "combine_raw", "compute_sv", "compute_mvbs"
    ]
    assert dag.nodes["merge_mvbs"].is_collector


def test_merge_datasets_spec_registered():
    from aa_recipe_manager.registry.loader import load_builtin_registry

    assert "merge_datasets" in load_builtin_registry().list_ops()

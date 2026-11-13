# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the echodataflow flow generator.

The generated module is executed against a stub ``prefect`` and a stub
``echodataflow.deployment.task_runners``, in the way echodataflow's own tests
stub them, so the flow runs here without either package. The stub task runs
``submit`` at once and returns a finished future.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

import _stage9_helpers as helpers
from _stage9_helpers import INT, LIST, MANY, build, step
from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.generator.backends.echodataflow import EchodataflowBackend
from aa_recipe_manager.generator.echodataflow.plan import (
    UnsupportedRecipeError,
    plan_flow,
)
from aa_recipe_manager.model.types import (
    InputDeclaration,
    ParamDeclaration,
    PortDeclaration,
    SweepDeclaration,
)
from aa_recipe_manager.resolver.dependencies import resolve_dependencies

_DATASET = PortDeclaration(type="Dataset")
_MANY_DATASETS = PortDeclaration(type="Dataset", many=True)


class _Future:
    def __init__(self, value: Any) -> None:
        self._value = value

    def result(self) -> Any:
        return self._value


class _Task:
    """Stands in for a Prefect task: call it, or submit it for a finished future."""

    def __init__(self, fn, name: str | None = None) -> None:
        self.fn = fn
        self.name = name
        self.run_names: list[str] = []

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)

    def with_options(self, task_run_name: str | None = None, **_kwargs) -> _Task:
        self.run_names.append(task_run_name)
        return self

    def submit(self, *args, **kwargs) -> _Future:
        return _Future(self.fn(*args, **kwargs))


def _decorator(make):
    def decorate(fn=None, **kwargs):
        if fn is None:
            return lambda inner: make(inner, kwargs)
        return make(fn, kwargs)

    return decorate


@pytest.fixture
def stub_prefect(monkeypatch):
    prefect = types.ModuleType("prefect")
    prefect.task = _decorator(lambda fn, kw: _Task(fn, kw.get("name")))
    prefect.flow = _decorator(lambda fn, kw: fn)
    prefect.get_run_logger = lambda: logging.getLogger("generated_flow")
    runners = types.ModuleType("echodataflow.deployment.task_runners")
    runners.task_runner_from_environment = lambda: None
    monkeypatch.setitem(sys.modules, "prefect", prefect)
    monkeypatch.setitem(sys.modules, "echodataflow", types.ModuleType("echodataflow"))
    deployment = types.ModuleType("echodataflow.deployment")
    monkeypatch.setitem(sys.modules, "echodataflow.deployment", deployment)
    monkeypatch.setitem(sys.modules, "echodataflow.deployment.task_runners", runners)
    helpers.RECORDED.clear()
    return prefect


def _generate(dag, out_dir: Path) -> Path:
    return EchodataflowBackend().generate(dag, resolve_dependencies(dag), out_dir)


def _load(module_path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flow(module: types.ModuleType):
    names = [name for name in dir(module) if name.startswith("flow_")]
    assert len(names) == 1
    return getattr(module, names[0])


def _int_input(default: Any = None, required: bool | None = None) -> InputDeclaration:
    fields: dict[str, Any] = {"type": "int", "default": default}
    if required is not None:
        fields["required"] = required
    return InputDeclaration(**fields)


def _record(sid: str, ref: str, port: PortDeclaration = INT) -> Any:
    return step(sid, "record", inputs={"value": ref},
                in_ports={"value": port}, out_ports={"value": port},
                output_map={"value": "__return__"})


def _chain_dag(**input_decls: InputDeclaration):
    """seg -> chain (inc -> scale) over seg.items -> collect_sum -> record."""
    steps = [
        step("seg", "make_list", params={"n": "${inputs.n}"},
             param_decls={"n": ParamDeclaration(type="int")},
             out_ports={"items": LIST}, output_map={"items": "__return__"}),
        step("inc_it", "inc", inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"out": INT},
             output_map={"out": "__return__"}, map_over="${seg.items}"),
        step("scale_it", "scale", inputs={"x": "${inc_it.out}"},
             params={"factor": "${inputs.factor}"},
             in_ports={"x": INT},
             param_decls={"factor": ParamDeclaration(type="int", required=False)},
             out_ports={"scaled": INT}, output_map={"scaled": "__return__"},
             map_over="${seg.items}"),
        step("merge", "collect_sum", inputs={"values": "${scale_it.scaled}"},
             in_ports={"values": MANY}, out_ports={"total": INT},
             output_map={"total": "__return__"}, collect="${scale_it.scaled}"),
        _record("keep", "${merge.total}"),
    ]
    decls = {"n": _int_input(3), "factor": _int_input(4)}
    decls.update(input_decls)
    return build(steps, inputs_decl=decls)


def test_the_module_never_imports_the_recipe_manager(tmp_path):
    module_path = _generate(_chain_dag(), tmp_path)

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported
    assert not [name for name in imported if name.startswith("aa_recipe_manager")]
    top_level = {
        (n.module if isinstance(n, ast.ImportFrom) else n.names[0].name)
        for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert top_level == {
        "__future__", "datetime", "os", "pathlib", "prefect",
        "echodataflow.deployment.task_runners",
    }


def test_the_flow_runs_the_chain_and_the_fan_in(stub_prefect, tmp_path):
    module = _load(_generate(_chain_dag(), tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    # make_list(3) -> [10, 20, 30]; inc -> [11, 21, 31]; * 4 -> sum 252
    assert helpers.RECORDED == [252]
    assert module.task_inc_it.run_names == [
        "inc_it[0000]", "inc_it[0001]", "inc_it[0002]",
    ]


def test_flow_parameters_are_tunable(stub_prefect, tmp_path):
    module = _load(_generate(_chain_dag(), tmp_path / "gen"))

    _flow(module)(n=2, factor=1, output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [11 + 21]


def test_an_input_default_appears_once_as_the_flow_default(tmp_path):
    source = _generate(_chain_dag(factor=_int_input(987654)), tmp_path).read_text()

    assert source.count("987654") == 1
    assert "factor: int = 987654," in source
    assert "factor=factor" in source


def test_an_unset_optional_input_is_not_passed(stub_prefect, tmp_path):
    dag = _chain_dag(factor=InputDeclaration(type="int", required=False))
    module_path = _generate(dag, tmp_path / "gen")

    assert "**_given(factor=factor)" in module_path.read_text()
    _flow(_load(module_path))(output_dir=str(tmp_path / "out"))
    # scale's own default factor of 2 applies: (11 + 21 + 31) * 2
    assert helpers.RECORDED == [126]


def test_a_required_input_has_no_default(tmp_path):
    source = _generate(_chain_dag(n=_int_input(required=True)), tmp_path).read_text()

    assert "    n: int,\n" in source


def test_a_none_output_skips_only_its_own_instance(stub_prefect, tmp_path):
    steps = [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("pick", "keep_above", inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"kept": INT},
             output_map={"kept": "__return__"}, map_over="${seg.items}"),
        step("bump", "inc", inputs={"x": "${pick.kept}"},
             in_ports={"x": INT}, out_ports={"bumped": INT},
             output_map={"bumped": "__return__"}, map_over="${seg.items}"),
        step("gather", "concat", inputs={"values": "${bump.bumped}"},
             in_ports={"values": MANY}, out_ports={"all": LIST},
             output_map={"all": "__return__"}, collect="${bump.bumped}"),
        _record("keep", "${gather.all}", LIST),
    ]
    module = _load(_generate(build(steps), tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [[None, 21, 31]]


def test_a_chain_can_read_another_chains_whole_list(stub_prefect, tmp_path):
    steps = [
        step("files", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("per_file", "inc", inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"out": INT},
             output_map={"out": "__return__"}, map_over="${files.items}"),
        step("windows", "make_list", params={"n": 2},
             param_decls={"n": ParamDeclaration(type="int")},
             out_ports={"wins": LIST}, output_map={"wins": "__return__"}),
        step("per_window", "add_total",
             inputs={"x": "${_item}", "values": "${per_file.out}"},
             in_ports={"x": INT, "values": MANY}, out_ports={"sums": INT},
             output_map={"sums": "__return__"}, map_over="${windows.wins}"),
        step("gather", "concat", inputs={"values": "${per_window.sums}"},
             in_ports={"values": MANY}, out_ports={"all": LIST},
             output_map={"all": "__return__"}, collect="${per_window.sums}"),
        _record("keep", "${gather.all}", LIST),
    ]
    module = _load(_generate(build(steps), tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [[73, 83]]


def _dataset_dag():
    """A chain making datasets, checkpointed and handed off to a merge."""
    steps = [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("make", "small_dataset", inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"ds": _DATASET},
             output_map={"ds": "__return__"}, map_over="${seg.items}"),
        step("merge", "concat_small", inputs={"datasets": "${make.ds}"},
             in_ports={"datasets": _MANY_DATASETS},
             out_ports={"merged": _DATASET},
             output_map={"merged": "__return__"}, collect="${make.ds}"),
        step("total", "total_of", inputs={"ds": "${merge.merged}"},
             in_ports={"ds": _DATASET}, out_ports={"total": INT},
             output_map={"total": "__return__"}),
        _record("keep", "${total.total}"),
    ]
    steps[2] = steps[2].model_copy(update={"checkpoint": "always"})
    return build(steps)


def test_datasets_leaving_a_chain_are_written_and_reopened(stub_prefect, tmp_path):
    out_dir = tmp_path / "out"
    module = _load(_generate(_dataset_dag(), tmp_path / "gen"))

    _flow(module)(output_dir=str(out_dir))

    # make_list -> [10, 20, 30]; each dataset holds x and x + 1
    assert helpers.RECORDED == [10 + 11 + 20 + 21 + 30 + 31]
    stores = sorted(p.name for p in (out_dir / "make").iterdir())
    assert stores == ["0000.zarr", "0001.zarr", "0002.zarr"]
    assert (out_dir / "merge.zarr").is_dir()


def test_the_plan_hands_off_only_datasets_that_leave_or_are_checkpointed():
    plan = plan_flow(_dataset_dag())
    by_name = {unit.name: unit for unit in plan.units}

    assert by_name["make"].handoffs == {"ds": ("make", "ds")}
    assert by_name["merge"].handoffs == {"merged": ("merge", "merged")}
    assert by_name["total"].handoffs == {}
    assert plan.handoff_vars == {"ds", "merged"}


def test_quotes_in_the_description_still_compile(tmp_path):
    dag = _chain_dag()
    dag.recipe.description = 'Computes "Sv" from C:\\data and """more"""'

    source = _generate(dag, tmp_path).read_text(encoding="utf-8")

    assert 'Computes \\"Sv\\" from C:\\\\data' in source


def test_text_around_references_follows_the_executor(stub_prefect, tmp_path):
    """Only ${inputs.x} is filled in inside longer text; the rest stays literal."""
    steps = [
        step("note", "label",
             inputs={"text": 'run "${inputs.tag}" {x} ${_item}'},
             in_ports={"text": PortDeclaration(type="str")},
             out_ports={"text": PortDeclaration(type="str")},
             output_map={"text": "__return__"}, map_over="${seg.items}"),
        step("gather", "concat", inputs={"values": "${note.text}"},
             in_ports={"values": MANY}, out_ports={"all": LIST},
             output_map={"all": "__return__"}, collect="${note.text}"),
        _record("keep", "${gather.all}", LIST),
    ]
    seg = step("seg", "make_list", out_ports={"items": LIST},
               output_map={"items": "__return__"})
    dag = build([seg, *steps], inputs_decl={
        "tag": InputDeclaration(type="str", default="a1"),
    })
    module = _load(_generate(dag, tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [['run "a1" {x} ${_item}'] * 3]


@pytest.mark.parametrize("name", ["map_item", "output_dir", "logger", "_handoff"])
def test_names_the_flow_uses_itself_are_refused(name, tmp_path):
    steps = [
        step("seg", "make_list", out_ports={name: LIST},
             output_map={name: "__return__"}),
    ]
    with pytest.raises(UnsupportedRecipeError, match="used by the generated flow"):
        _generate(build(steps), tmp_path)


def test_an_output_named_like_an_input_is_qualified(stub_prefect, tmp_path):
    dag = _chain_dag(total=_int_input(5))

    module_path = _generate(dag, tmp_path / "gen")
    _flow(_load(module_path))(output_dir=str(tmp_path / "out"))

    assert 'merge_total = _out["merge_total"]' in module_path.read_text()
    assert helpers.RECORDED == [252]


def test_a_reserved_input_name_is_refused(tmp_path):
    dag = _chain_dag(map_item=_int_input(1))

    with pytest.raises(UnsupportedRecipeError, match="map_item"):
        _generate(dag, tmp_path)


def test_a_step_reference_inside_a_dict_param_is_refused(tmp_path):
    steps = [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("use", "count_items", params={"values": {"src": "${seg.items}"}},
             param_decls={"values": ParamDeclaration(type="dict")},
             out_ports={"n": INT}, output_map={"n": "__return__"}),
    ]
    with pytest.raises(UnsupportedRecipeError, match="inside a dict param"):
        _generate(build(steps), tmp_path)


def test_figure_params_save_per_step_and_never_show(tmp_path):
    figure = {
        name: ParamDeclaration(type="str", required=False)
        for name in ("save_image", "show", "save_dir")
    }
    steps = [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("plot_a", "count_items", inputs={"values": "${seg.items}"},
             in_ports={"values": LIST}, param_decls=figure, sink=True),
        step("plot_b", "count_items", inputs={"values": "${seg.items}"},
             in_ports={"values": LIST}, param_decls=figure,
             params={"show": True}, sink=True),
    ]
    source = _generate(build(steps), tmp_path).read_text(encoding="utf-8")

    assert 'save_dir=str(Path(output_dir) / "images" / "plot_a")' in source
    assert 'save_dir=str(Path(output_dir) / "images" / "plot_b")' in source
    assert "show=False" in source and "show=True" in source
    assert source.count("save_image=True") == 2


def test_a_chain_mapped_over_handed_off_datasets_gets_datasets(stub_prefect, tmp_path):
    """Items that are zarr paths are reopened before the chain uses them."""
    steps = [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("make", "small_dataset", inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"ds": _DATASET},
             output_map={"ds": "__return__"}, map_over="${seg.items}"),
        step("sum_each", "total_of", inputs={"ds": "${_item}"},
             in_ports={"ds": _DATASET}, out_ports={"total": INT},
             output_map={"total": "__return__"}, map_over="${make.ds}"),
        step("gather", "concat", inputs={"values": "${sum_each.total}"},
             in_ports={"values": MANY}, out_ports={"all": LIST},
             output_map={"all": "__return__"}, collect="${sum_each.total}"),
        _record("keep", "${gather.all}", LIST),
    ]
    module = _load(_generate(build(steps), tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [[21.0, 41.0, 61.0]]


def test_items_with_one_stem_get_distinct_stores(stub_prefect, tmp_path):
    from aa_recipe_manager.generator.echodataflow import flow_runtime as rt

    stems = rt._item_stems(["gs://b/leg1/f.raw", "gs://b/leg2/f.raw", "g.raw"])

    assert stems == ["f_0000", "f_0001", "g"]


def test_a_missing_optional_output_takes_its_default(stub_prefect, tmp_path):
    steps = [
        step("pair", "only_first", params={"x": 4},
             param_decls={"x": ParamDeclaration(type="int")},
             out_ports={
                 "first": INT,
                 "second": PortDeclaration(type="int", required=False, default=-1),
             },
             output_map={"first": "first", "second": "second"}),
        _record("keep", "${pair.second}"),
    ]
    module = _load(_generate(build(steps), tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [-1]


def test_a_date_value_is_passed_as_a_date(stub_prefect, tmp_path):
    import datetime

    steps = [
        step("day", "label", params={"text": datetime.date(2024, 11, 6)},
             param_decls={"text": ParamDeclaration(type="str")},
             out_ports={"text": PortDeclaration(type="str")},
             output_map={"text": "__return__"}),
        _record("keep", "${day.text}", PortDeclaration(type="str")),
    ]
    dag = build(steps, inputs_decl={
        "start": InputDeclaration(type="str", default=datetime.date(2024, 11, 6)),
    })
    module = _load(_generate(dag, tmp_path / "gen"))

    _flow(module)(output_dir=str(tmp_path / "out"))

    assert helpers.RECORDED == [datetime.date(2024, 11, 6)]
    assert "start: '2024-11-06'" in (tmp_path / "gen").joinpath(
        next(p.name for p in (tmp_path / "gen").glob("params.*.yaml"))
    ).read_text().replace('"', "'")


def test_a_remote_output_dir_is_refused(stub_prefect, tmp_path):
    module = _load(_generate(_chain_dag(), tmp_path / "gen"))

    with pytest.raises(ValueError, match="local folder"):
        _flow(module)(output_dir="gs://bucket/run1")


def test_a_sweep_is_rejected():
    steps = [
        step("sw", "scale", params={"x": 1},
             param_decls={"x": ParamDeclaration(type="int"),
                          "factor": ParamDeclaration(type="int")},
             out_ports={"out": INT}, output_map={"out": "__return__"},
             sweep=SweepDeclaration(param_lists={"factor": [1, 2]})),
    ]
    with pytest.raises(UnsupportedRecipeError, match="sweep"):
        plan_flow(build(steps))


def test_sidecars_match_the_flow_signature(tmp_path):
    module_path = _generate(_chain_dag(), tmp_path)
    slug = module_path.stem.removeprefix("flows_")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    flow_def = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == f"flow_{slug}"
    )
    signature = {arg.arg for arg in flow_def.args.args}

    params = yaml.safe_load((tmp_path / f"params.{slug}.yaml").read_text())
    deploy = yaml.safe_load((tmp_path / f"deploy.{slug}.yaml").read_text())
    registry = (tmp_path / "flow_registry_entry.txt").read_text()

    assert set(params["flows"]) == set(deploy["flows"]) == {slug}
    assert set(params["flows"][slug]) <= signature
    assert f'entrypoint="echodataflow/flows/flows_{slug}.py:flow_{slug}"' in registry
    assert not slug.startswith("_")


def _reference_recipe() -> Path:
    path = (
        Path(__file__).resolve().parents[2] / "AA-SI_Full_Pipeline_Example"
        / "example_recipes" / "echodataflow" / "edf_raw2Sv_MVBS_postprocessing.yaml"
    )
    if not path.exists():
        pytest.skip("AA-SI_Full_Pipeline_Example is not checked out beside this repo")
    return path


def test_the_reference_recipe_compiles(tmp_path):
    module_path = api.generate(
        _reference_recipe(),
        output=tmp_path,
        output_format="echodataflow",
        spec_dirs=["contrib:echodataflow"],
    )
    source = module_path.read_text(encoding="utf-8")

    assert module_path.name == "flows_edf_raw2sv_mvbs_postprocessing.py"
    assert "def flow_edf_raw2sv_mvbs_postprocessing(" in source
    assert "windows = _out[\"windows\"]" in source
    assert "_as_items(windows)" in source


def test_cli_generates_a_flow_folder(cli_runner, tmp_path):
    result = cli_runner.invoke(
        main,
        [
            "generate", str(_reference_recipe()), "--format", "echodataflow",
            "-o", str(tmp_path), "--spec-dir", "contrib:echodataflow",
        ],
    )

    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([
        "README.md", "deploy.edf_raw2sv_mvbs_postprocessing.yaml",
        "flow_registry_entry.txt", "flows_edf_raw2sv_mvbs_postprocessing.py",
        "params.edf_raw2sv_mvbs_postprocessing.yaml", "requirements.txt",
    ])
    again = cli_runner.invoke(
        main,
        [
            "generate", str(_reference_recipe()), "--format", "echodataflow",
            "-o", str(tmp_path), "--spec-dir", "contrib:echodataflow",
        ],
    )
    assert again.exit_code != 0
    assert "already exists" in again.output

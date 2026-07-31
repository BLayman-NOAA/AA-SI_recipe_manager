# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Deterministic callables and DAG builders shared by the Stage 9 tests.

This is a *real* importable module (not a ``sys.modules`` throwaway) so a Dask
worker *process* can import the callables by dotted path, exactly as the
executor does for a real op. The DAG builders keep the executor-backend tests
small and uniform.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from aa_recipe_manager.model.types import (
    CustomSpec,
    Dependency,
    InputDeclaration,
    ParamDeclaration,
    PortDeclaration,
    Recipe,
    Step,
    StepExecutionHints,
)
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.registry.registry import Registry

# ---------------------------------------------------------------------------
# Callables (pure, deterministic; safe to run in threads or processes)
# ---------------------------------------------------------------------------


def const7() -> int:
    return 7


def make_list(n: int = 3) -> list[int]:
    return [(i + 1) * 10 for i in range(n)]


def inc(x: int) -> int:
    return x + 1


def scale(x: int, factor: int = 2) -> int:
    return x * factor


def add(a: int, b: int) -> int:
    return a + b


def addk(x: int, k: int = 0) -> int:
    return x + k


def collect_sum(values: list[int]) -> int:
    return sum(values)


def concat(values: list[int]) -> list[int]:
    """A non-arithmetic fan-in: prove ``collect`` carries no merge assumption."""
    out: list[int] = []
    for v in values:
        out.extend(v if isinstance(v, list) else [v])
    return out


def pid_of(x: int) -> dict[str, int]:
    """Return this task's OS pid (to prove process isolation) plus the input."""
    return {"pid": os.getpid(), "x": x}


def boom(x: int) -> int:
    raise RuntimeError("boom")


# A module-global counter + gate used to prove two branch steps overlap in time.
_ENTER_LOCK = threading.Lock()
_CONCURRENT: dict[str, int] = {"current": 0, "max": 0}


def reset_overlap() -> None:
    _CONCURRENT["current"] = 0
    _CONCURRENT["max"] = 0


def overlap_probe(x: int, hold: float = 0.15) -> int:
    """Record peak concurrency: increment on entry, hold, decrement on exit."""
    with _ENTER_LOCK:
        _CONCURRENT["current"] += 1
        _CONCURRENT["max"] = max(_CONCURRENT["max"], _CONCURRENT["current"])
    time.sleep(hold)
    with _ENTER_LOCK:
        _CONCURRENT["current"] -= 1
    return x


def max_overlap() -> int:
    return _CONCURRENT["max"]


# A flaky callable that fails its first ``fail_until`` attempts, then succeeds —
# used to prove a backend's retry policy re-runs the step.
_FLAKY: dict[str, int] = {"attempts": 0, "fail_until": 0}


def reset_flaky(fail_until: int) -> None:
    _FLAKY["attempts"] = 0
    _FLAKY["fail_until"] = fail_until


def flaky(x: int) -> int:
    _FLAKY["attempts"] += 1
    if _FLAKY["attempts"] <= _FLAKY["fail_until"]:
        raise RuntimeError(f"flaky failure #{_FLAKY['attempts']}")
    return x + 100


def flaky_attempts() -> int:
    return _FLAKY["attempts"]


def noisy(x: int, hold: float = 0.05) -> int:
    """Print an identifiable line while overlapping with sibling instances.

    Used to prove each concurrent instance's stdout reaches the run log intact
    (a global ``redirect_stdout`` lost all but one instance's output).
    """
    print(f"noisy-start x={x}")
    time.sleep(hold)
    print(f"noisy-end x={x}")
    return x


# ---------------------------------------------------------------------------
# DAG builders
# ---------------------------------------------------------------------------

_MOD = __name__  # this module's importable name
_DEP = Dependency(name="pytest", version=">=7.0", source="pypi")
INT = PortDeclaration(type="int")
LIST = PortDeclaration(type="list")
MANY = PortDeclaration(type="int", many=True)


def step(
    sid: str,
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
    sweep: Any = None,
    sink: bool = False,
    execution: StepExecutionHints | None = None,
) -> Step:
    return Step(
        id=sid,
        op="custom",
        inputs=inputs or {},
        params=params or {},
        map_over=map_over,
        collect=collect,
        sweep=sweep,
        execution=execution,
        custom_spec=CustomSpec(
            description=f"custom {callable_name}",
            callable_path=f"{_MOD}.{callable_name}",
            inputs=in_ports or {},
            outputs=out_ports or {},
            params=param_decls or {},
            output_map=output_map or {},
            sink=sink,
            dependency=_DEP,
        ),
    )


def recipe(
    steps: list[Step],
    *,
    inputs: dict[str, InputDeclaration] | None = None,
    execution: Any = None,
) -> Recipe:
    return Recipe(
        name="stage9_pipeline",
        version="1.0.0",
        schema_version="1",
        inputs=inputs or {},
        steps=steps,
        execution=execution,
    )


def build(steps: list[Step], *, inputs_decl=None, input_values=None, execution=None):
    return build_dag(
        recipe(steps, inputs=inputs_decl, execution=execution),
        Registry(),
        input_values=input_values,
        check_versions=False,
    )


def diamond_steps(probe: bool = False) -> list[Step]:
    """start -> {branchA, branchB} -> combine (two independent branches)."""
    branch_fn = "overlap_probe" if probe else "inc"
    return [
        step("start", "const7", out_ports={"v": INT}, output_map={"v": "__return__"}),
        step(
            "branchA", branch_fn, inputs={"x": "${start.v}"},
            in_ports={"x": INT}, out_ports={"out": INT},
            output_map={"out": "__return__"},
        ),
        step(
            "branchB", "scale", inputs={"x": "${start.v}"},
            in_ports={"x": INT}, out_ports={"out": INT},
            output_map={"out": "__return__"},
        ),
        step(
            "combine", "add", inputs={"a": "${branchA.out}", "b": "${branchB.out}"},
            in_ports={"a": INT, "b": INT}, out_ports={"out": INT},
            output_map={"out": "__return__"},
        ),
    ]


def map_collect_steps(
    collector_callable: str = "collect_sum", mapped_callable: str = "inc"
) -> list[Step]:
    """seg -> (proc mapped over seg) -> merge (collect)."""
    return [
        step("seg", "make_list", out_ports={"items": LIST},
             output_map={"items": "__return__"}),
        step("proc", mapped_callable, inputs={"x": "${_item}"},
             in_ports={"x": INT}, out_ports={"out": INT},
             output_map={"out": "__return__"}, map_over="${seg.items}"),
        step("merge", collector_callable, inputs={"values": "${proc.out}"},
             in_ports={"values": MANY}, out_ports={"total": INT},
             output_map={"total": "__return__"}, collect="${proc.out}"),
    ]

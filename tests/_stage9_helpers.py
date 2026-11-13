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


def sleep_for(x: int, seconds: float = 0.2) -> int:
    """Sleep, then return ``x``: a step slow enough to cancel or kill."""
    time.sleep(seconds)
    return x


def count_entries(folder: str) -> int:
    """Return the number of entries in ``folder``."""
    return len(os.listdir(folder))


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


def partial_lines(x: int) -> int:
    """Print a split line, a whole line, and an unterminated tail."""
    print("first ", end="")
    print("line")
    print("second line")
    print("tail", end="")
    return x


def write_scratch(x: int) -> str:
    """Write ``x`` to a scratch file and return its path."""
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(handle, "w") as stream:
        stream.write(str(x))
    return path


def read_scratch(path: str) -> int:
    """Read the number a scratch file holds."""
    with open(path) as stream:
        return int(stream.read())


def sv_dataset(x: int = 0, pings: int = 12) -> Any:
    """A small gridded Sv dataset whose pings start ``x`` minutes in.

    Built with EchogramGL's synthetic fixture, imported here so that loading
    and validating a recipe never needs EchogramGL installed.
    """
    import numpy as np
    from aa_si_echogram_gl import fixtures

    ds = fixtures.synthetic(
        n_pings=pings, n_samples=20, gridded=True, gps=False, nodata=False
    )
    return ds.assign_coords(
        ping_time=ds["ping_time"] + np.timedelta64(int(x), "m")
    )


def sv_with_lines(ds: Any) -> Any:
    """The Sv dataset with two depth lines along ping_time.

    Written as add_line_overlay writes them: ``line_a`` is referenced to the
    surface and has one gap; ``line_b`` to the transducer. The dataset is also
    marked as echopype marks MVBS, so a header for it says its cells are
    labelled by their start.
    """
    import numpy as np

    pings = ds.sizes["ping_time"]
    line_a = 10 + 0.5 * np.arange(pings, dtype="float64")
    line_a[3] = np.nan
    line_b = 5 + 0.25 * np.arange(pings, dtype="float64")
    lined = ds.assign(
        line_a=(
            "ping_time",
            line_a,
            {"long_name": "line a", "units": "m", "vertical_reference": "surface"},
        ),
        line_b=(
            "ping_time",
            line_b,
            {"long_name": "line b", "units": "m", "vertical_reference": "transducer"},
        ),
    )
    lined.attrs = {**ds.attrs, "processing_function": "commongrid.compute_MVBS"}
    return lined


def count_items(values: list) -> int:
    """Count the collected values."""
    return len(values)


def keep_above(x: int, floor: int = 15) -> int | None:
    """Return ``x``, or None when it is not above ``floor``: an empty instance."""
    return x if x > floor else None


def add_total(x: int, values: list[int]) -> int:
    """Add the sum of a whole folded list to ``x``."""
    return x + sum(values)


def small_dataset(x: int) -> Any:
    """A two-sample dataset along ``t`` holding ``x`` and ``x + 1``."""
    import numpy as np
    import xarray as xr

    return xr.Dataset(
        {"v": ("t", np.array([x, x + 1], dtype=float), {"units": "m"})},
        coords={"t": np.array([2 * x, 2 * x + 1])},
    )


def concat_small(datasets: list, dim: str = "t") -> Any:
    """Concatenate datasets along ``dim``."""
    import xarray as xr

    return xr.concat(list(datasets), dim=dim)


def only_first(x: int) -> dict[str, int]:
    """A result dict holding ``first`` but not ``second``."""
    return {"first": x}


def label(text: str) -> str:
    """Return ``text``: a step whose output is a string param."""
    return text


def total_of(ds: Any) -> float:
    """Sum of a dataset's ``v``."""
    return float(ds["v"].sum())


#: Values passed to ``record``, for checking what a generated flow computed.
RECORDED: list[Any] = []


def record(value: Any) -> Any:
    """Keep ``value`` in RECORDED and return it."""
    RECORDED.append(value)
    return value


def console_note(x: int) -> int:
    """Print a line straight to the terminal, past the run log."""
    from aa_recipe_manager.executor.console import console_print

    console_print(f"console-note x={x}")
    return x


def stats_table() -> dict[str, Any]:
    """A small cluster statistics table in the ``aa-table/1`` shape.

    Laid out as ``aa_si_ml.ml.cluster_statistics_table`` writes it: two Sv
    features and one difference, two clusters and noise, and one null
    statistic (noise's difference std).
    """
    features = [
        {"name": "18kHz", "label": "18 kHz", "kind": "sv"},
        {"name": "38kHz", "label": "38 kHz", "kind": "sv"},
        {"name": "38kHz-18kHz_diff", "label": "38 kHz - 18 kHz", "kind": "difference"},
    ]
    stats = ("mean", "std", "min", "max")
    columns = [
        {"name": "group", "type": "str"},
        {"name": "group_id", "type": "int"},
        {"name": "n_samples", "type": "int"},
        {"name": "percentage", "type": "float", "unit": "%"},
    ]
    for feature in features:
        for stat in stats:
            columns.append({
                "name": f"{feature['name']}_{stat}", "type": "float", "unit": "dB",
                "feature": feature["name"], "stat": stat,
            })
    values = {
        "Cluster 0": (0, 1234, 41.2, [
            (-71.3, 4.1, -82.0, -60.5), (-68.9, 3.6, -79.4, -58.2),
            (2.4, 1.8, -3.1, 7.9),
        ]),
        "Cluster 1": (1, 1461, 48.8, [
            (-80.2, 5.3, -95.1, -66.7), (-77.5, 4.8, -90.3, -64.0),
            (2.7, 2.2, -4.0, 9.6),
        ]),
        "Noise": (-1, 300, 10.0, [
            (-88.4, 6.9, -110.2, -61.3), (-86.1, 7.4, -108.8, -59.9),
            (2.3, None, -6.5, 11.2),
        ]),
    }
    rows = []
    for group, (group_id, n, percentage, cells) in values.items():
        row = {
            "group": group, "group_id": group_id, "n_samples": n,
            "percentage": percentage,
        }
        for feature, cell in zip(features, cells):
            for stat, value in zip(stats, cell):
                row[f"{feature['name']}_{stat}"] = value
        rows.append(row)
    return {
        "schema": "aa-table/1",
        "title": "Cluster statistics",
        "description": "Sv statistics per cluster",
        "group_column": "group",
        "columns": columns,
        "features": features,
        "rows": rows,
        "source": {
            "ml_result_name": "hdbscan",
            "dataset_name": "fixture",
            "sv_data_var": "Sv",
            "n_total_samples": 2995,
            "n_clusters": 2,
            "n_noise": 300,
        },
    }


# A cancel token a step can set mid run, and a call counter, for the cancel
# tests. Thread backends share this module's globals with the test.
CANCEL_HOOK: dict[str, Any] = {}
_COUNTS: dict[str, int] = {"calls": 0}


def reset_counts() -> None:
    _COUNTS["calls"] = 0


def call_count() -> int:
    return _COUNTS["calls"]


def trip_cancel(x: int) -> int:
    """Count the call and cancel the run's token."""
    _COUNTS["calls"] += 1
    CANCEL_HOOK["token"].cancel("tripped")
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

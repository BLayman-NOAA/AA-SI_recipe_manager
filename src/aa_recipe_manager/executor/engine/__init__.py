# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Backend-agnostic execution engine.

The engine owns everything that is true of a run regardless of *where* its
steps execute: resolving the run context, planning which steps run, invoking a
step, and folding results back. A :class:`~aa_recipe_manager.executor.engine.
backends.base.SchedulerBackend` decides only *when and where* a unit of work
runs, so the sequential, Dask, and Prefect executors share one implementation
of the run loop rather than forking it three ways.
"""

from aa_recipe_manager.executor.engine.context import (
    RunContext,
    WorkerContext,
    build_run_context,
    resolve_outputs_dir,
    resolve_temp_dir,
)
from aa_recipe_manager.executor.engine.step import execute_step

__all__ = [
    "RunContext",
    "WorkerContext",
    "build_run_context",
    "execute_step",
    "resolve_outputs_dir",
    "resolve_temp_dir",
]

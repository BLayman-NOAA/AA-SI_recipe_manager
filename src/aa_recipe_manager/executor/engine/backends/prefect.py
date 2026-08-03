# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PrefectBackend: schedule tasks as Prefect task runs.

Each schedulable unit becomes a Prefect task submitted to the flow's task
runner, so Prefect provides retries, timeouts, tags, and dashboard visibility
while the shared :class:`~aa_recipe_manager.executor.engine.runner.
PipelineRunner` still owns planning, checkpointing, and the run manifest. Per
``software_architecture.md`` §5.3.2, pipeline- and step-level ``prefect_config``
(retries / timeout / tags) are merged by the runner and applied per task.

Prefect is an optional dependency (``pip install aa-recipe-manager[prefect]``);
it is imported inside methods only so the core package never needs it (NFR-4).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.engine.tasks import (
    ChainInstanceTask,
    StepTask,
    TaskResult,
    run_chain_instance,
    run_step_task,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aa_recipe_manager.executor.engine.context import WorkerContext

# prefect_config keys forwarded to Task.with_options(); anything else is ignored
# with no error so a recipe can carry backend-specific hints harmlessly.
_TASK_OPTION_KEYS = (
    "retries",
    "retry_delay_seconds",
    "timeout_seconds",
    "tags",
    "persist_result",
    "cache_policy",
)


def _dispatch(task: StepTask | ChainInstanceTask, wctx: WorkerContext) -> TaskResult:
    """Prefect task body: run the unit in whatever worker Prefect placed it."""
    # Only in a spawned worker process; see the matching note in backends/dask.py.
    if not wctx.in_client_process():
        os.environ.setdefault("MPLBACKEND", "Agg")
    if isinstance(task, ChainInstanceTask):
        return run_chain_instance(task, wctx)
    return run_step_task(task, wctx)


class PrefectBackend:
    """Scheduler backend backed by Prefect task submission."""

    in_process = False

    def __init__(self, *, max_concurrency: int | None = None) -> None:
        self._wctx: WorkerContext | None = None
        self._task = None
        # Prefect's default ThreadPoolTaskRunner overlaps task runs; the runner
        # only uses this to bound its in-flight frontier.
        self.max_concurrency = max_concurrency or (os.cpu_count() or 4)

    def start(self, wctx: WorkerContext) -> None:
        from prefect import task

        self._wctx = wctx
        # A single Prefect task wrapping the dispatcher; per-unit prefect_config
        # is layered on with ``.with_options`` at submit time.
        self._task = task(_dispatch, name="aa-recipe-step")

    def submit(
        self,
        task: StepTask | ChainInstanceTask,
        *,
        config: dict[str, Any] | None = None,
    ) -> Any:
        prefect_config = (config or {}).get("prefect_config") or {}
        options = {k: v for k, v in prefect_config.items() if k in _TASK_OPTION_KEYS}
        prefect_task = self._task.with_options(**options) if options else self._task
        return prefect_task.submit(task, self._wctx)

    def as_completed(self, handles: list[Any]) -> Iterator[Any]:
        from prefect.futures import as_completed

        yield from as_completed(handles)

    def result(self, handle: Any) -> TaskResult:
        return handle.result()

    def close(self) -> None:
        self._task = None
        self._wctx = None

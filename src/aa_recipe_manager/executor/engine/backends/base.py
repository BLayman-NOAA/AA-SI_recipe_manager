# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The scheduling-backend seam.

A :class:`SchedulerBackend` decides only *when and where* a task runs — the
run loop, checkpointing, planning, and bookkeeping live in
:class:`~aa_recipe_manager.executor.engine.runner.PipelineRunner`. The backend
receives already-built :class:`~aa_recipe_manager.executor.engine.tasks.StepTask`
/ :class:`~aa_recipe_manager.executor.engine.tasks.ChainInstanceTask` objects
and returns their :class:`~aa_recipe_manager.executor.engine.tasks.TaskResult`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aa_recipe_manager.executor.engine.context import WorkerContext
    from aa_recipe_manager.executor.engine.tasks import (
        ChainInstanceTask,
        StepTask,
        TaskResult,
    )


@runtime_checkable
class SchedulerBackend(Protocol):
    """Interface every executor backend implements.

    ``max_concurrency`` caps how many tasks the runner keeps in flight (1 for
    the inline backend, the worker/thread count for Dask). ``in_process`` is
    ``True`` when tasks share the client's heap, so a :class:`ValueRef` closure
    is free and never has to fall back to the checkpoint store.
    """

    max_concurrency: int
    in_process: bool

    def start(self, wctx: WorkerContext) -> None:
        """Prepare the backend for a run (spin up a client/pool if needed)."""
        ...

    def submit(
        self,
        task: StepTask | ChainInstanceTask,
        *,
        config: dict[str, Any] | None = None,
    ) -> Any:
        """Schedule ``task`` and return an opaque handle."""
        ...

    def as_completed(self, handles: list[Any]) -> Iterator[Any]:
        """Yield the given handles in completion order."""
        ...

    def result(self, handle: Any) -> TaskResult:
        """Return a completed handle's result, re-raising its task exception."""
        ...

    def close(self) -> None:
        """Release any resources the backend holds."""
        ...

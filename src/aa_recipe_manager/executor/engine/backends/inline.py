# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""InlineBackend: run each task synchronously in the client process.

This is the default backend and the one the :class:`SequentialExecutor` uses.
It runs each task to completion the moment it is submitted, so the wavefront
degenerates to topological order and the run is byte-identical to the old
single-threaded loop. It is also the reference every distributed backend is
checked against.
"""

from __future__ import annotations

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


class _Completed:
    """A resolved handle: either a result or the exception the task raised."""

    __slots__ = ("result", "error")

    def __init__(self, result: TaskResult | None, error: BaseException | None):
        self.result = result
        self.error = error


class InlineBackend:
    """Synchronous, single-slot backend."""

    max_concurrency = 1
    in_process = True

    def __init__(self) -> None:
        self._wctx: WorkerContext | None = None

    def start(self, wctx: WorkerContext) -> None:
        self._wctx = wctx

    def submit(
        self,
        task: StepTask | ChainInstanceTask,
        *,
        config: dict[str, Any] | None = None,
    ) -> _Completed:
        assert self._wctx is not None, "InlineBackend.start() was not called"
        try:
            if isinstance(task, ChainInstanceTask):
                result = run_chain_instance(task, self._wctx)
            else:
                result = run_step_task(task, self._wctx)
        except BaseException as exc:  # surfaced by result(); never swallowed
            return _Completed(None, exc)
        return _Completed(result, None)

    def as_completed(self, handles: list[_Completed]) -> Iterator[_Completed]:
        # Every handle is already resolved (submit ran the task); preserve the
        # submission order the runner passed in.
        yield from handles

    def result(self, handle: _Completed) -> TaskResult:
        if handle.error is not None:
            raise handle.error
        assert handle.result is not None
        return handle.result

    def close(self) -> None:
        self._wctx = None

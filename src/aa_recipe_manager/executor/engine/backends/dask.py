# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""DaskBackend: distribute tasks across a Dask cluster.

The backend defaults to a thread-based local cluster (no data pickling of the
kind that would defeat the checkpoint-reference data plane, and no worker
processes to spawn on Windows). A step — or the whole pipeline — can escalate
to worker *processes* via ``execution.dask_config.scheduler: processes`` for
GIL-bound work; ``scheduler`` may also be a scheduler address to run against an
external cluster.

Dask (``dask[distributed]``) is an optional dependency: everything is imported
inside methods so the core package never needs it (NFR-4).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import PipelineExecutionError
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

_THREADS = ("threads", "synchronous", "single-threaded", None)
_PROCESSES = "processes"


def _dispatch(task: StepTask | ChainInstanceTask, wctx: WorkerContext) -> TaskResult:
    """Worker entry point: run the task in whatever process Dask placed it."""
    # A process worker never initializes Tk (mapped sinks render headless).
    os.environ.setdefault("MPLBACKEND", "Agg")
    if isinstance(task, ChainInstanceTask):
        return run_chain_instance(task, wctx)
    return run_step_task(task, wctx)


class DaskBackend:
    """Scheduler backend backed by ``dask.distributed``."""

    in_process = False

    def __init__(
        self,
        scheduler: str | None = None,
        *,
        n_workers: int | None = None,
        threads_per_worker: int | None = None,
    ) -> None:
        # ``scheduler``: None/"threads" (default local threads), "processes"
        # (local worker processes), or a scheduler address ("tcp://…").
        self._scheduler = scheduler
        self._n_workers = n_workers
        self._threads_per_worker = threads_per_worker
        self._wctx: WorkerContext | None = None
        self._default_kind = self._classify(scheduler)
        # Lazily created clients/clusters, keyed by kind ("threads"/"processes").
        self._clients: dict[str, Any] = {}
        self._clusters: dict[str, Any] = {}
        self._external: Any = None
        self.max_concurrency = 1

    @staticmethod
    def _classify(scheduler: str | None) -> str:
        if scheduler in _THREADS:
            return "threads"
        if scheduler == _PROCESSES:
            return "processes"
        return "address"

    # -- lifecycle -----------------------------------------------------------

    def start(self, wctx: WorkerContext) -> None:
        self._wctx = wctx
        # Prime the default client so max_concurrency reflects real slots and a
        # bad scheduler address fails fast rather than mid-run.
        client = self._client_for(self._default_kind)
        self.max_concurrency = max(1, self._slot_count(client))

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:  # never mask the run's own outcome
                pass
        for cluster in self._clusters.values():
            try:
                cluster.close()
            except Exception:
                pass
        self._clients.clear()
        self._clusters.clear()
        self._external = None

    # -- client management ---------------------------------------------------

    def _client_for(self, kind: str):
        from dask.distributed import Client, LocalCluster

        # ``set_as_default=False`` keeps this client from hijacking the *global*
        # dask scheduler: otherwise, after the run closes its client, a lazy
        # dask array computed elsewhere (e.g. a later Zarr reload) would try the
        # dead distributed scheduler and fail. The executor always submits
        # against the explicit client object, so it needs no global default.
        if kind == "address":
            if self._external is None:
                self._external = Client(self._scheduler, set_as_default=False)
            return self._external
        if kind in self._clients:
            return self._clients[kind]
        cpu = os.cpu_count() or 2
        if kind == _PROCESSES:
            # One task per worker process: a task is a whole chain instance, and
            # extra threads inside a process would fight over the GIL — which is
            # the only reason to have escalated to processes in the first place.
            n_workers = self._n_workers or cpu
            threads = self._threads_per_worker or 1
        else:
            # Threads: the requested worker count is the *concurrency*, not a
            # multiplier. Dask's slot count is n_workers x threads_per_worker, so
            # asking for 4 workers on a 14-core box used to open 56 slots — and a
            # slot holds a whole chain instance (an entire raw file's EchoData)
            # in memory. One worker holding N threads gives exactly N slots and
            # keeps them in a shared heap, which is the point of the thread
            # backend (no pickling between tasks).
            n_workers = 1
            threads = self._threads_per_worker or self._n_workers or cpu
        cluster = LocalCluster(
            processes=(kind == _PROCESSES),
            n_workers=n_workers,
            threads_per_worker=threads,
            # ``None`` does *not* disable the dashboard — it falls back to the
            # default :8787 and warns when that is taken (a second run, or a
            # leftover cluster, collides). ":0" takes a free port; the URL is
            # reported via ``dashboard_link`` so a stuck run can be inspected.
            dashboard_address=":0",
            silence_logs=logging.WARNING,
        )
        client = Client(cluster, set_as_default=False)
        self._clusters[kind] = cluster
        self._clients[kind] = client
        return client

    def dashboard_link(self) -> str | None:
        """URL of the live Dask dashboard, if a local cluster is running."""
        for client in self._clients.values():
            link = getattr(client, "dashboard_link", None)
            if link:
                return link
        return None

    @staticmethod
    def _slot_count(client) -> int:
        info = client.scheduler_info()
        workers = info.get("workers", {})
        total = sum(w.get("nthreads", 1) for w in workers.values())
        return total or 1

    # -- submission ----------------------------------------------------------

    def submit(
        self,
        task: StepTask | ChainInstanceTask,
        *,
        config: dict[str, Any] | None = None,
    ) -> Any:
        dask_config = (config or {}).get("dask_config") or {}
        kind = self._target_kind(dask_config)
        if kind == "processes":
            heavy = task.closure.heavy_value_ref_steps()
            if heavy:
                raise PipelineExecutionError(
                    _task_step(task),
                    "cannot run this step in a Dask worker *process*: its "
                    f"upstream output(s) {heavy!r} would have to be pickled "
                    "across the process boundary. Mark those steps "
                    "'checkpoint: always' so the worker loads them from the "
                    "cache, or run this step with the default thread scheduler.",
                )
        client = self._client_for(kind)
        submit_kwargs: dict[str, Any] = {"pure": False}
        if "retries" in dask_config:
            submit_kwargs["retries"] = dask_config["retries"]
        if "priority" in dask_config:
            submit_kwargs["priority"] = dask_config["priority"]
        if dask_config.get("resources"):
            submit_kwargs["resources"] = dask_config["resources"]
        return client.submit(_dispatch, task, self._wctx, **submit_kwargs)

    def _target_kind(self, dask_config: dict[str, Any]) -> str:
        step_scheduler = dask_config.get("scheduler")
        if step_scheduler is None:
            return self._default_kind  # no per-step override: inherit the default
        if step_scheduler == _PROCESSES:
            return "processes"
        if step_scheduler in _THREADS:
            # An explicit threads/synchronous request wins over the default. Under
            # an external-cluster run there is no local thread pool to fall back
            # to, so honor that cluster; otherwise use a local thread cluster.
            return "address" if self._default_kind == "address" else "threads"
        return "address"  # an explicit scheduler address

    # -- completion ----------------------------------------------------------

    def as_completed(self, handles: list[Any]) -> Iterator[Any]:
        # Poll future status rather than distributed.wait / as_completed: those
        # need a *default* client, which we deliberately don't register (see
        # _client_for). Polling ``.status`` also works across futures from
        # different clients (threads + processes) in one call. The client's
        # background event loop updates the status as tasks finish.
        import time

        while True:
            done = [h for h in handles if h.status in ("finished", "error")]
            if done:
                yield from done
                return
            time.sleep(0.01)

    def result(self, handle: Any) -> TaskResult:
        return handle.result()


def _task_step(task: StepTask | ChainInstanceTask) -> str:
    return task.member_ids[0] if isinstance(task, ChainInstanceTask) else task.step_id

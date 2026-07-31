# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""DaskExecutor: the distributed backend selected by ``--executor dask``.

Thin ``PipelineExecutor`` that drives the shared engine
(:class:`~aa_recipe_manager.executor.engine.runner.PipelineRunner`) with a
:class:`~aa_recipe_manager.executor.engine.backends.dask.DaskBackend`. It
inherits the whole run scaffolding — argument validation, logging, the run
manifest, checkpoint integration, curated-environment checks, scratch cleanup —
from :class:`SequentialExecutor`; only the scheduling backend differs.

Dask is an optional dependency: it is imported lazily by the backend, so
importing this module never requires ``dask`` to be installed.
"""

from __future__ import annotations

from aa_recipe_manager.executor.sequential import SequentialExecutor


class DaskExecutor(SequentialExecutor):
    """Distribute a pipeline across a Dask cluster.

    Defaults to a thread-based local cluster; ``scheduler="processes"`` (or a
    per-step ``execution.dask_config.scheduler: processes``) escalates GIL-bound
    steps to worker processes, and ``scheduler="tcp://host:port"`` runs against
    an external cluster. ``map_over`` / ``sweep`` instances and independent DAG
    branches are submitted concurrently; results equal the sequential run.
    """

    def __init__(
        self,
        scheduler: str | None = None,
        *,
        n_workers: int | None = None,
        threads_per_worker: int | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._n_workers = n_workers
        self._threads_per_worker = threads_per_worker

    def _make_backend(self):
        from aa_recipe_manager.executor.engine.backends.dask import DaskBackend

        return DaskBackend(
            self._scheduler,
            n_workers=self._n_workers,
            threads_per_worker=self._threads_per_worker,
        )

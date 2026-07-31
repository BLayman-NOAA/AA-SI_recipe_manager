# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PrefectExecutor: the backend selected by ``--executor prefect``.

Runs the shared engine inside a Prefect ``@flow`` named after the recipe, with
each step submitted as a Prefect task (see
:class:`~aa_recipe_manager.executor.engine.backends.prefect.PrefectBackend`).
All the run scaffolding — logging, manifest, checkpoint integration,
curated-environment checks — is inherited unchanged from
:class:`SequentialExecutor`.

Prefect is an optional dependency, imported lazily; importing this module never
requires ``prefect`` to be installed.
"""

from __future__ import annotations

from typing import Any

from aa_recipe_manager.executor.sequential import SequentialExecutor


class PrefectExecutor(SequentialExecutor):
    """Orchestrate a pipeline with Prefect (retries, timeouts, dashboard).

    Prefect adds orchestration around the same DAG the sequential and Dask
    executors run; segment/branch concurrency comes from the flow's task
    runner. Results equal the sequential run.
    """

    def _make_backend(self):
        from aa_recipe_manager.executor.engine.backends.prefect import PrefectBackend

        return PrefectBackend()

    def execute(self, dag: Any, *args: Any, **kwargs: Any) -> Any:
        """Wrap the shared run loop in a Prefect flow named after the recipe.

        Every task the runner submits then belongs to this flow run, so the
        Prefect dashboard shows the pipeline as one flow with a task per step.
        """
        from prefect import flow

        recipe_name = getattr(getattr(dag, "recipe", None), "name", "aa-recipe")

        @flow(name=recipe_name)
        def _pipeline_flow() -> Any:
            return SequentialExecutor.execute(self, dag, *args, **kwargs)

        return _pipeline_flow()

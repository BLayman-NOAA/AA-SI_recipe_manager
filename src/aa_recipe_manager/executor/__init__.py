# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Direct pipeline execution: protocols, runtime context, executors."""

from aa_recipe_manager.executor.base import (
    ExecutionResult,
    NullProgressCallback,
    PipelineExecutor,
    ProgressCallback,
    StepRecord,
)
from aa_recipe_manager.executor.batch import (
    BatchExecutor,
    BatchResult,
    InputSet,
    input_sets_from_csv,
    input_sets_from_folder,
    input_sets_from_lists,
)
from aa_recipe_manager.executor.checkpoint import (
    CheckpointManager,
    ExecutionPlan,
    StepFingerprints,
    classify_steps,
    compute_step_fingerprints,
    compute_step_hashes,
    entry_dir_parts,
    explicit_checkpoint_steps,
    generate_run_id,
    plan_execution,
    resolve_checkpoint_policy,
)
from aa_recipe_manager.executor.invocation import (
    RuntimeContext,
    build_kwargs,
    extract_outputs,
    import_callable,
)
from aa_recipe_manager.executor.runtime_context import (
    ExecutionContext,
    execution_context,
    get_execution_context,
)
from aa_recipe_manager.executor.sequential import SequentialExecutor
from aa_recipe_manager.executor.tiered import (
    CheckpointStore,
    TieredCheckpointStore,
)

#: Recognized ``--executor`` / ``executor=`` names.
EXECUTOR_NAMES = ("sequential", "dask", "prefect")


def resolve_executor(name: str = "sequential", **options: object):
    """Construct a :class:`PipelineExecutor` for a backend name (FR-15.3).

    ``sequential`` (default) needs no extra dependency; ``dask`` and
    ``prefect`` require the matching optional extra and raise a friendly
    :class:`ImportError` naming the install command when it is missing.
    ``options`` are forwarded to the backend's constructor (e.g. ``scheduler``,
    ``n_workers`` for Dask).
    """
    if name == "sequential":
        return SequentialExecutor()
    if name == "dask":
        _require_module("dask", "dask", "aa-recipe-manager[dask]")
        from aa_recipe_manager.executor.dask_executor import DaskExecutor

        return DaskExecutor(**options)
    if name == "prefect":
        _require_module("prefect", "prefect", "aa-recipe-manager[prefect]")
        from aa_recipe_manager.executor.prefect_executor import PrefectExecutor

        return PrefectExecutor(**options)
    raise ValueError(
        f"unknown executor backend {name!r}; expected one of {EXECUTOR_NAMES}"
    )


def _require_module(module: str, extra_pkg: str, install: str) -> None:
    """Raise a friendly ImportError when an optional backend dep is absent.

    ``module`` must be a top-level name: ``find_spec`` imports the parent of a
    dotted name, so probing ``dask.distributed`` raises ModuleNotFoundError
    instead of returning None when dask is not installed.
    """
    import importlib.util

    if importlib.util.find_spec(module) is None:
        raise ImportError(
            f"the {extra_pkg!r} executor backend requires {extra_pkg}; "
            f"install it with: pip install {install}"
        )


__all__ = [
    "ExecutionResult",
    "NullProgressCallback",
    "PipelineExecutor",
    "ProgressCallback",
    "StepRecord",
    "CheckpointManager",
    "CheckpointStore",
    "TieredCheckpointStore",
    "ExecutionPlan",
    "StepFingerprints",
    "classify_steps",
    "compute_step_fingerprints",
    "compute_step_hashes",
    "entry_dir_parts",
    "explicit_checkpoint_steps",
    "generate_run_id",
    "plan_execution",
    "resolve_checkpoint_policy",
    "RuntimeContext",
    "ExecutionContext",
    "execution_context",
    "get_execution_context",
    "build_kwargs",
    "extract_outputs",
    "import_callable",
    "SequentialExecutor",
    "resolve_executor",
    "EXECUTOR_NAMES",
    "BatchExecutor",
    "BatchResult",
    "InputSet",
    "input_sets_from_folder",
    "input_sets_from_csv",
    "input_sets_from_lists",
]

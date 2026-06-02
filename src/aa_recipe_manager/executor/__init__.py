# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Direct pipeline execution: protocols, runtime context, executors."""

from aa_recipe_manager.executor.base import (
    ExecutionResult,
    NullProgressCallback,
    PipelineExecutor,
    ProgressCallback,
)
from aa_recipe_manager.executor.checkpoint import (
    CheckpointManager,
    ExecutionPlan,
    classify_steps,
    compute_step_hashes,
    explicit_checkpoint_steps,
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

__all__ = [
    "ExecutionResult",
    "NullProgressCallback",
    "PipelineExecutor",
    "ProgressCallback",
    "CheckpointManager",
    "ExecutionPlan",
    "classify_steps",
    "compute_step_hashes",
    "explicit_checkpoint_steps",
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
]

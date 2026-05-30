# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""SequentialExecutor: default in-process executor with checkpointing."""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor.base import (
    ExecutionResult,
    NullProgressCallback,
    ProgressCallback,
)
from aa_recipe_manager.executor.checkpoint import (
    CheckpointManager,
    compute_recipe_hash,
    resolve_checkpoint_policy,
)
from aa_recipe_manager.executor.invocation import (
    RuntimeContext,
    build_kwargs,
    extract_outputs,
    import_callable,
)
from aa_recipe_manager.executor.runtime_context import execution_context

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import CheckpointFormat, CheckpointMode, DAGNode, PipelineDAG


class SequentialExecutor:
    """Run a PipelineDAG's steps one at a time in topological order."""

    def execute(
        self,
        dag: PipelineDAG,
        inputs: dict[str, Any] | None = None,
        output_dir: str | Path | None = None,
        *,
        force: bool = False,
        no_checkpoints: bool = False,
        skip_sinks: bool = False,
        checkpoint_mode: CheckpointMode | str | None = None,
        checkpoint_steps: Iterable[str] | None = None,
        checkpoint_format: CheckpointFormat | str | None = None,
        progress: ProgressCallback | None = None,
    ) -> ExecutionResult:
        from aa_recipe_manager.provenance.recorder import ProvenanceRecorder

        pipeline_inputs = dict(inputs or {})
        runtime = RuntimeContext()
        progress = progress or NullProgressCallback()

        checkpoints: CheckpointManager | None = None
        resolved_output_dir: Path | None = None
        if output_dir is not None and not no_checkpoints:
            resolved_output_dir = Path(output_dir)
            # Resolve effective format: call-site arg > recipe hint > default "zarr"
            hints = dag.recipe.execution
            effective_format: str = (
                checkpoint_format
                or (hints.checkpoint_format if hints is not None else None)
                or "zarr"
            )
            checkpoints = CheckpointManager(
                resolved_output_dir,
                compute_recipe_hash(dag),
                preferred_format=effective_format,
            )
            resolved_output_dir.mkdir(parents=True, exist_ok=True)

        policy: set[str] = set()
        if checkpoints is not None:
            policy = resolve_checkpoint_policy(
                dag,
                mode=checkpoint_mode,
                extra_step_ids=set(checkpoint_steps or ()),
            )

        result = ExecutionResult(output_dir=resolved_output_dir)
        step_ids = list(dag.topological_order)
        total = len(step_ids)

        for index, step_id in enumerate(step_ids, start=1):
            node = dag.nodes[step_id]
            if skip_sinks and node.spec.sink:
                result.logs.append(f"skip sink: {step_id}")
                runtime.record(step_id, {})
                continue

            progress.on_step_start(step_id, index, total)
            start = time.perf_counter()

            try:
                # Cache reads are intentionally independent of the checkpoint
                # policy: any present, hash-matching checkpoint is loaded so
                # switching modes between runs (e.g. eager -> none) never
                # wastes work cached by a prior run.
                if (
                    checkpoints is not None
                    and not force
                    and not node.spec.sink
                    and node.spec.outputs
                    and checkpoints.has_checkpoint(step_id)
                ):
                    outputs = checkpoints.load(step_id)
                    runtime.record(step_id, outputs)
                    result.outputs[step_id] = outputs
                    result.skipped_steps.append(step_id)
                    elapsed = time.perf_counter() - start
                    result.logs.append(
                        f"cache hit: {step_id} ({elapsed:.3f}s)"
                    )
                    progress.on_step_end(
                        step_id, index, total, skipped=True, elapsed=elapsed
                    )
                    continue

                with execution_context(
                    mode="direct",
                    output_dir=resolved_output_dir,
                    step_id=step_id,
                ):
                    outputs = self._execute_step(node, runtime, pipeline_inputs)
            except PipelineExecutionError as exc:
                elapsed = time.perf_counter() - start
                progress.on_step_end(
                    step_id, index, total, elapsed=elapsed, error=exc
                )
                raise
            except Exception as exc:
                elapsed = time.perf_counter() - start
                wrapped = PipelineExecutionError(
                    step_id,
                    f"step {step_id!r} failed during execution: {exc}",
                    original=exc,
                )
                progress.on_step_end(
                    step_id, index, total, elapsed=elapsed, error=wrapped
                )
                raise wrapped from exc

            runtime.record(step_id, outputs)
            result.outputs[step_id] = outputs
            result.executed_steps.append(step_id)
            if checkpoints is not None and outputs and step_id in policy:
                checkpoints.save(step_id, outputs)
            elapsed = time.perf_counter() - start
            result.logs.append(f"ran: {step_id} ({elapsed:.3f}s)")
            progress.on_step_end(step_id, index, total, elapsed=elapsed)

        result.provenance = ProvenanceRecorder.capture(dag)
        return result

    def _execute_step(
        self,
        node: DAGNode,
        runtime: RuntimeContext,
        pipeline_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        step_id = node.step.id
        if node.implementation is None:
            raise PipelineExecutionError(
                step_id,
                f"step {step_id!r} has no resolved implementation",
            )

        impl = node.implementation
        try:
            callable_obj = import_callable(impl.callable_path)
        except (ImportError, AttributeError, TypeError) as exc:
            raise PipelineExecutionError(
                step_id,
                f"failed to import callable {impl.callable_path!r} for step "
                f"{step_id!r}: {exc}",
                callable_path=impl.callable_path,
                original=exc,
            ) from exc

        if impl.setup:
            try:
                setup_fn = import_callable(impl.setup)
            except (ImportError, AttributeError, TypeError) as exc:
                raise PipelineExecutionError(
                    step_id,
                    f"failed to import setup callable {impl.setup!r} for "
                    f"step {step_id!r}: {exc}",
                    callable_path=impl.setup,
                    original=exc,
                ) from exc
            setup_fn()

        try:
            kwargs = build_kwargs(node, runtime, pipeline_inputs)
        except (KeyError, ValueError) as exc:
            raise PipelineExecutionError(
                step_id,
                f"failed to build kwargs for step {step_id!r}: {exc}",
                callable_path=impl.callable_path,
                original=exc,
            ) from exc

        try:
            return_value = callable_obj(**kwargs)
        except Exception as exc:
            raise PipelineExecutionError(
                step_id,
                (
                    f"callable {impl.callable_path!r} raised {type(exc).__name__} "
                    f"for step {step_id!r}: {exc}"
                ),
                callable_path=impl.callable_path,
                original=exc,
            ) from exc

        try:
            outputs = extract_outputs(node, return_value)
        except Exception as exc:
            raise PipelineExecutionError(
                step_id,
                (
                    f"output_map extraction failed for step {step_id!r}: {exc}"
                ),
                callable_path=impl.callable_path,
                original=exc,
            ) from exc

        if impl.teardown:
            try:
                teardown_fn = import_callable(impl.teardown)
            except (ImportError, AttributeError, TypeError) as exc:
                raise PipelineExecutionError(
                    step_id,
                    f"failed to import teardown callable {impl.teardown!r} for "
                    f"step {step_id!r}: {exc}",
                    callable_path=impl.teardown,
                    original=exc,
                ) from exc
            teardown_fn()

        return outputs

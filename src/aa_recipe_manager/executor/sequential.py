# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""SequentialExecutor: default in-process executor with checkpointing."""

from __future__ import annotations

import gc
import io
import os
import shutil
import stat
import time
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
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
    compute_step_hashes,
    plan_execution,
    resolve_checkpoint_policy,
)
from aa_recipe_manager.executor.invocation import (
    RuntimeContext,
    build_kwargs,
    extract_outputs,
    import_callable,
)
from aa_recipe_manager.executor.runtime_context import execution_context
from aa_recipe_manager.storage import StorageLocation

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import CheckpointFormat, CheckpointMode, DAGNode, PipelineDAG


LOGS_DIR = "logs"
STANDARD_OUT_FILENAME = "standard_out.txt"
_DEFAULT_OUTPUTS_DIRNAME = "outputs"
_LOG_DESTINATIONS = ("file", "console", "both")


class _Tee:
    """Write to several text streams at once (None streams are ignored)."""

    def __init__(self, *streams: Any) -> None:
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


_EXE_TEMP_DIRNAME = "exe_temp"
_TEMP_DIR_CLEANUP_RETRIES = 5
_TEMP_DIR_CLEANUP_BASE_DELAY = 0.25


def _remove_readonly(func, path, _excinfo):
    """Error handler for shutil.rmtree on Windows read-only files."""
    import os  # noqa: PLC0415
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _is_transient_windows_lock(exc: OSError) -> bool:
    """Return True for the common transient Windows file-lock error."""
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) == 32


def _resolve_temp_dir(
    temp_dir: str | Path | StorageLocation | None,
    cache_loc: StorageLocation | None,
    storage_options: dict[str, Any] | None = None,
) -> StorageLocation | None:
    """Resolve the run-scoped scratch directory (``exe_temp``).

    An explicit ``temp_dir`` wins (local path or fsspec URL). Otherwise it
    follows the cache's scheme: a local cache yields a local ``exe_temp``, a
    remote cache yields a remote ``exe_temp`` under the same prefix. Returns
    ``None`` when neither is available.
    """
    if temp_dir is not None:
        return StorageLocation.parse(temp_dir, storage_options)
    if cache_loc is not None:
        return cache_loc.parent / _EXE_TEMP_DIRNAME
    return None


def _resolve_outputs_dir(
    outputs_dir: str | Path | StorageLocation | None,
    cache_loc: StorageLocation | None,
    storage_options: dict[str, Any] | None = None,
) -> StorageLocation | None:
    """Resolve the user-facing outputs directory.

    Explicit ``outputs_dir`` wins. Otherwise it defaults to a sibling of the
    checkpoint cache directory named ``outputs`` (e.g. ``recipe_cache`` ->
    ``outputs``), following the cache's scheme. Returns ``None`` when neither
    is available.
    """
    if outputs_dir is not None:
        return StorageLocation.parse(outputs_dir, storage_options)
    if cache_loc is not None:
        return cache_loc.parent / _DEFAULT_OUTPUTS_DIRNAME
    return None


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
        regenerate_outputs: bool = False,
        outputs_dir: str | Path | None = None,
        temp_dir: str | Path | None = None,
        log_destination: str = "file",
        checkpoint_mode: CheckpointMode | str | None = None,
        checkpoint_steps: Iterable[str] | None = None,
        checkpoint_format: CheckpointFormat | str | None = None,
        storage_options: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ExecutionResult:
        from aa_recipe_manager.provenance.recorder import ProvenanceRecorder

        if log_destination not in _LOG_DESTINATIONS:
            raise ValueError(
                f"log_destination must be one of {_LOG_DESTINATIONS}, "
                f"got {log_destination!r}"
            )

        pipeline_inputs = dict(inputs or {})
        runtime = RuntimeContext()
        progress = progress or NullProgressCallback()

        # Parse the cache location once; a local path stays local-behaving,
        # an fsspec URL (gs://, ...) routes through StorageLocation.
        cache_loc: StorageLocation | None = (
            StorageLocation.parse(output_dir, storage_options)
            if output_dir is not None
            else None
        )

        checkpoints: CheckpointManager | None = None
        resolved_output_dir: StorageLocation | None = None
        if cache_loc is not None and not no_checkpoints:
            resolved_output_dir = cache_loc
            # Resolve effective format: call-site arg > recipe hint > default "zarr"
            hints = dag.recipe.execution
            effective_format: str = (
                checkpoint_format
                or (hints.checkpoint_format if hints is not None else None)
                or "zarr"
            )
            checkpoints = CheckpointManager(
                cache_loc,
                compute_step_hashes(dag, pipeline_inputs),
                preferred_format=effective_format,
                storage_options=storage_options,
            )

        policy: set[str] = set()
        if checkpoints is not None:
            policy = resolve_checkpoint_policy(
                dag,
                mode=checkpoint_mode,
                extra_step_ids=set(checkpoint_steps or ()),
            )

        result = ExecutionResult(
            output_dir=None
            if resolved_output_dir is None
            else resolved_output_dir.as_context_value()
        )

        # User-facing outputs (images, logs) live in a separate tree from the
        # checkpoint cache. Figures are written to ``<outputs>/images`` (via the
        # execution context's ``artifacts_dir``) and per-step stdout/stderr to
        # ``<outputs>/logs/standard_out.txt``.
        # Use the raw cache location (not the checkpoint-gated resolved_output_dir)
        # so that outputs_dir resolves correctly even when no_checkpoints=True.
        resolved_outputs_loc = _resolve_outputs_dir(
            outputs_dir, cache_loc, storage_options
        )
        result.outputs_dir = (
            None
            if resolved_outputs_loc is None
            else resolved_outputs_loc.as_context_value()
        )

        # An explicit temp_dir is always honored; the sibling-of-cache default
        # follows the checkpoint-gated location (so no_checkpoints keeps the
        # legacy behavior of aa_si_utils falling back to a system temp dir).
        resolved_temp_loc = _resolve_temp_dir(
            temp_dir, resolved_output_dir, storage_options
        )

        log_buffer = io.StringIO()
        log_file_handle = None
        # A local outputs dir streams to standard_out.txt during the run. Object
        # stores cannot append, so a remote outputs dir instead uploads the full
        # buffer once in the finally block (see below); the buffer always
        # captures the text regardless of destination.
        remote_log_loc: StorageLocation | None = None
        want_log_file = (
            resolved_outputs_loc is not None and log_destination in ("file", "both")
        )
        if want_log_file and resolved_outputs_loc.is_local:
            logs_loc = resolved_outputs_loc / LOGS_DIR
            logs_loc.mkdir()
            log_loc = logs_loc / STANDARD_OUT_FILENAME
            log_file_handle = open(log_loc.as_local_path(), "w", encoding="utf-8")
            result.log_file = log_loc.as_context_value()
        elif want_log_file:
            remote_log_loc = (
                resolved_outputs_loc / LOGS_DIR / STANDARD_OUT_FILENAME
            )
            result.log_file = remote_log_loc
        log_sink = _Tee(log_buffer, log_file_handle)

        try:
            self._run_steps(
                dag=dag,
                result=result,
                runtime=runtime,
                pipeline_inputs=pipeline_inputs,
                checkpoints=checkpoints,
                policy=policy,
                resolved_output_dir=resolved_output_dir,
                resolved_outputs_dir=resolved_outputs_loc,
                resolved_temp_dir=resolved_temp_loc,
                force=force,
                skip_sinks=skip_sinks,
                regenerate_outputs=regenerate_outputs,
                progress=progress,
                log_sink=log_sink,
            )
        finally:
            if log_file_handle is not None:
                log_file_handle.close()
            # Upload the full captured log to a remote outputs dir once (object
            # stores cannot append). Runs on failure too, so failed-run logs
            # still land in the bucket. A hard process kill loses the remote log.
            if remote_log_loc is not None:
                try:
                    remote_log_loc.parent.mkdir()
                    remote_log_loc.write_text(log_buffer.getvalue())
                except Exception:  # never mask the original error
                    pass
            self._cleanup_temp_dir(resolved_temp_loc)

        result.console_log = log_buffer.getvalue()
        result.provenance = ProvenanceRecorder.capture(dag, inputs=pipeline_inputs or None)
        return result

    @staticmethod
    def _cleanup_temp_dir(temp_loc: StorageLocation | Path | None) -> None:
        """Remove the run-scoped scratch directory if it exists.

        Windows can briefly hold open NetCDF-backed temp files after the last
        step returns, so the local path retries a few times before surfacing
        the error. Remote scratch has no such locking and is removed in one
        ``fs.rm`` call.
        """
        if temp_loc is None:
            return
        if isinstance(temp_loc, StorageLocation) and not temp_loc.is_local:
            temp_loc.rm(recursive=True)
            return

        temp_dir = Path(os.fspath(temp_loc))
        if not temp_dir.exists():
            return

        last_error: OSError | None = None
        for attempt in range(_TEMP_DIR_CLEANUP_RETRIES):
            try:
                shutil.rmtree(temp_dir, onerror=_remove_readonly)
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                if not temp_dir.exists():
                    return
                if not _is_transient_windows_lock(exc):
                    raise
                last_error = exc
                if attempt == _TEMP_DIR_CLEANUP_RETRIES - 1:
                    break
                gc.collect()
                time.sleep(_TEMP_DIR_CLEANUP_BASE_DELAY * (attempt + 1))

        raise last_error  # type: ignore[misc]

    def _run_steps(
        self,
        *,
        dag: PipelineDAG,
        result: ExecutionResult,
        runtime: RuntimeContext,
        pipeline_inputs: dict[str, Any],
        checkpoints: CheckpointManager | None,
        policy: set[str],
        resolved_output_dir: StorageLocation | None,
        resolved_outputs_dir: StorageLocation | None,
        resolved_temp_dir: StorageLocation | None,
        force: bool,
        skip_sinks: bool,
        regenerate_outputs: bool,
        progress: ProgressCallback,
        log_sink: _Tee,
    ) -> None:
        step_ids = list(dag.topological_order)
        total = len(step_ids)
        execution_plan = plan_execution(
            dag,
            checkpoints,
            force=force,
            regenerate_outputs=regenerate_outputs,
        )
        # Filter blockers by policy: only flag steps that aren't already
        # checkpointed (those in policy get saved, so they aren't really blockers).
        blocking = [s for s in execution_plan.blockers if s not in policy]
        if blocking:
            result.logs.append(
                "resume frontier limited by uncheckpointed step(s): "
                f"{', '.join(blocking)}"
            )

        for index, step_id in enumerate(step_ids, start=1):
            node = dag.nodes[step_id]
            if skip_sinks and node.spec.sink:
                result.logs.append(f"skip sink: {step_id}")
                runtime.record(step_id, {})
                continue

            # Side-effect steps (sinks / no declared outputs) produce on-disk
            # artifacts (plots, logs) rather than cacheable return values, so
            # they cannot be loaded from a checkpoint. They are skipped only
            # via a hash-matching marker (see below).
            is_side_effect = node.spec.sink or not node.spec.outputs

            progress.on_step_start(step_id, index, total)
            start = time.perf_counter()

            try:
                if step_id in execution_plan.pruned:
                    elapsed = time.perf_counter() - start
                    result.pruned_steps.append(step_id)
                    result.logs.append(
                        f"pruned: {step_id} ({elapsed:.3f}s)"
                    )
                    progress.on_step_end(
                        step_id, index, total, skipped=True, elapsed=elapsed
                    )
                    continue

                if step_id in execution_plan.loadable:
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

                if step_id in execution_plan.marker_hits:
                    runtime.record(step_id, {})
                    result.outputs[step_id] = {}
                    result.skipped_steps.append(step_id)
                    elapsed = time.perf_counter() - start
                    result.logs.append(
                        f"sink cache hit: {step_id} ({elapsed:.3f}s)"
                    )
                    progress.on_step_end(
                        step_id, index, total, skipped=True, elapsed=elapsed
                    )
                    continue

                if step_id not in execution_plan.must_run:
                    raise PipelineExecutionError(
                        step_id,
                        f"internal execution planner error for step {step_id!r}",
                    )

                log_sink.write(f"\n=== step {step_id} ({index}/{total}) ===\n")
                log_sink.flush()
                with execution_context(
                    mode="direct",
                    output_dir=resolved_output_dir,
                    step_id=step_id,
                    artifacts_dir=resolved_outputs_dir,
                    temp_dir=resolved_temp_dir,
                ), redirect_stdout(log_sink), redirect_stderr(log_sink):
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

            if checkpoints is not None and outputs and step_id in policy:
                checkpoints.save(step_id, outputs)
                # Replace temp-backed in-memory outputs with their persisted form
                # immediately so downstream steps and final cleanup do not keep
                # Windows NetCDF handles alive under exe_temp.
                outputs = checkpoints.load(step_id)
            runtime.record(step_id, outputs)
            result.outputs[step_id] = outputs
            result.executed_steps.append(step_id)
            if checkpoints is not None and is_side_effect:
                # Record a marker so an unchanged future run can skip
                # regenerating this side-effect step's on-disk artifacts.
                checkpoints.save_marker(step_id)
            elapsed = time.perf_counter() - start
            result.logs.append(f"ran: {step_id} ({elapsed:.3f}s)")
            log_sink.write(f"--- {step_id}: done ({elapsed:.3f}s) ---\n")
            log_sink.flush()
            progress.on_step_end(step_id, index, total, elapsed=elapsed)

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

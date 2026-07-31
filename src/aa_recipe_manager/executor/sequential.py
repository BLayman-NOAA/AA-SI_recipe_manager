# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""SequentialExecutor: default in-process executor with checkpointing."""

from __future__ import annotations

import gc
import io
import json
import os
import shutil
import stat
import time
import warnings
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.base import (
    ExecutionResult,
    NullProgressCallback,
    ProgressCallback,
)
from aa_recipe_manager.executor.engine.backends.inline import InlineBackend
from aa_recipe_manager.executor.engine.context import build_run_context
from aa_recipe_manager.executor.engine.logcapture import install_router
from aa_recipe_manager.executor.engine.runner import PipelineRunner
from aa_recipe_manager.executor.invocation import RuntimeContext
from aa_recipe_manager.storage import StorageLocation

if TYPE_CHECKING:
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore
    from aa_recipe_manager.model.types import (
        CheckpointFormat,
        CheckpointMode,
        PipelineDAG,
    )


LOGS_DIR = "logs"
STANDARD_OUT_FILENAME = "standard_out.txt"
MANIFEST_FILENAME = "manifest.json"
_LOG_DESTINATIONS = ("file", "console", "both")


class _Tee:
    """Write to several text streams at once (None streams are ignored).

    Every write is flushed. A long run that is interrupted — Ctrl-C, an OOM
    kill, a closed terminal — otherwise loses the last (up to 8 KB) buffered
    block, which is exactly the part naming the step that was running when it
    died. Step-level output is low-frequency, so the flush costs nothing.
    """

    def __init__(self, *streams: Any) -> None:
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
            try:
                stream.flush()
            except Exception:  # a closed/detached stream must not kill the run
                pass
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass


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


class SequentialExecutor:
    """Run a PipelineDAG's steps one at a time in topological order.

    The run loop lives in :class:`~aa_recipe_manager.executor.engine.runner.
    PipelineRunner`; this class supplies the default in-process scheduling
    backend and the surrounding run scaffolding (logging, manifest, cleanup).
    Distributed executors subclass it and override :meth:`_make_backend`, so
    they inherit that scaffolding unchanged.
    """

    def _make_backend(self):
        """Return the scheduling backend this executor drives."""
        return InlineBackend()

    def execute(
        self,
        dag: PipelineDAG,
        inputs: dict[str, Any] | None = None,
        user_cache_dir: str | Path | None = None,
        *,
        force: bool = False,
        no_checkpoints: bool = False,
        skip_sinks: bool = False,
        regenerate: str = "auto",
        outputs_dir: str | Path | None = None,
        temp_dir: str | Path | None = None,
        log_destination: str = "file",
        checkpoint_mode: CheckpointMode | str | None = None,
        checkpoint_steps: Iterable[str] | None = None,
        checkpoint_format: CheckpointFormat | str | None = None,
        storage_options: dict[str, Any] | None = None,
        survey_cache_dir: str | Path | None = None,
        cache_write_tier: str = "user",
        progress: ProgressCallback | None = None,
        keep_temp: bool = False,
    ) -> ExecutionResult:
        from aa_recipe_manager.provenance.recorder import (
            ProvenanceRecorder,
            build_raw_inputs_record,
            raw_file_list_step_ids,
        )

        if log_destination not in _LOG_DESTINATIONS:
            raise ValueError(
                f"log_destination must be one of {_LOG_DESTINATIONS}, "
                f"got {log_destination!r}"
            )

        # Wall clock for the whole run, started before the cache/context setup so
        # the reported total is what the user actually waited for.
        run_start = time.perf_counter()

        ctx = build_run_context(
            dag,
            inputs=inputs,
            user_cache_dir=user_cache_dir,
            force=force,
            no_checkpoints=no_checkpoints,
            skip_sinks=skip_sinks,
            regenerate=regenerate,
            outputs_dir=outputs_dir,
            temp_dir=temp_dir,
            checkpoint_mode=checkpoint_mode,
            checkpoint_steps=checkpoint_steps,
            checkpoint_format=checkpoint_format,
            storage_options=storage_options,
            survey_cache_dir=survey_cache_dir,
            cache_write_tier=cache_write_tier,
        )

        pipeline_inputs = ctx.pipeline_inputs
        runtime = RuntimeContext(store=ctx.checkpoints)
        progress = progress or NullProgressCallback()

        run_id = ctx.run_id
        checkpoints = ctx.checkpoints
        resolved_user_cache_dir = ctx.output_loc

        result = ExecutionResult(
            user_cache_dir=None
            if resolved_user_cache_dir is None
            else resolved_user_cache_dir.as_context_value()
        )
        result.run_id = run_id

        # User-facing outputs (images, logs) live in a separate tree from the
        # checkpoint cache. Figures are written to ``<outputs>/images`` (via the
        # execution context's ``artifacts_dir``) and per-step stdout/stderr to
        # ``<outputs>/logs/standard_out.txt``.
        resolved_outputs_loc = ctx.outputs_loc
        result.outputs_dir = (
            None
            if resolved_outputs_loc is None
            else resolved_outputs_loc.as_context_value()
        )
        resolved_temp_loc = ctx.temp_loc

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

        run_started_at = datetime.now(timezone.utc).isoformat()
        status = "completed"
        try:
            # Thread-routed stdout/stderr for the whole run: concurrent tasks each
            # capture their own output instead of fighting over the global stream
            # (see engine/logcapture.py). A no-op for a single-threaded run.
            with install_router():
                PipelineRunner(self._make_backend()).run(
                    ctx=ctx,
                    result=result,
                    runtime=runtime,
                    progress=progress,
                    log_sink=log_sink,
                )
            # Warn-only: compare this environment to the curated provenance
            # of any survey-tier hits. Never blocks or downgrades a hit.
            self._check_curated_environment(
                dag=dag, result=result, store=checkpoints
            )
        except BaseException:
            status = "failed"
            raise
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
            if keep_temp:
                # Diagnostic: leave the run-scoped scratch (per-file zarr
                # intermediates, etc.) in place so a slow step can be profiled
                # against real inputs after the run.
                if resolved_temp_loc is not None:
                    result.logs.append(f"kept temp dir: {resolved_temp_loc}")
            else:
                self._cleanup_temp_dir(resolved_temp_loc)
            result.elapsed_seconds = time.perf_counter() - run_start
            # Best-effort run manifest (written on failure too, status="failed").
            self._write_manifest(
                dag=dag,
                result=result,
                store=checkpoints,
                outputs_loc=resolved_outputs_loc,
                status=status,
                started_at=run_started_at,
                write_tier=cache_write_tier,
            )

        result.console_log = log_buffer.getvalue()
        # Prefer the raw-file list stamped on checkpoints this run hit or wrote:
        # authoritative {name,size} from the producing run, and the path that
        # carries a survey run's raw files into a user run extending its cache.
        # Fall back to a fresh harvest from this run's outputs when nothing was
        # checkpointed (e.g. no_checkpoints).
        raw_record: Any = None
        if checkpoints is not None:
            recovered = checkpoints.recovered_raw_inputs(raw_file_list_step_ids(dag))
            if recovered is not None:
                from aa_recipe_manager.model.types import RawInputsRecord

                raw_record = RawInputsRecord.model_validate(recovered)
        if raw_record is None:
            raw_record = build_raw_inputs_record(
                dag,
                result.outputs,
                pipeline_inputs,
                storage_options,
                run_id=run_id,
            )
        result.provenance = ProvenanceRecorder.capture(
            dag, inputs=pipeline_inputs or None, raw_inputs=raw_record
        )
        return result

    @staticmethod
    def _check_curated_environment(
        *,
        dag: PipelineDAG,
        result: ExecutionResult,
        store: TieredCheckpointStore | None,
    ) -> None:
        """Diff the live environment against curated provenance (warn-only).

        Runs once per distinct provenance ref found on this run's survey-tier
        hits. Prominent mismatches (packages implementing this recipe's ops)
        are surfaced as ``RuntimeWarning``; everything lands in the result and
        the manifest. A missing/unreadable provenance file is a soft warning,
        never an error — the cache hits themselves are unaffected either way.
        """
        if store is None:
            return
        refs = store.survey_hit_provenance_refs()
        if not refs:
            return
        from aa_recipe_manager.provenance.env_check import (
            check_environment_against_provenance,
        )

        survey_root = store.survey_root()
        for ref in sorted(refs):
            prov_loc = survey_root / ref
            try:
                provenance = json.loads(prov_loc.read_text())
            except Exception:
                message = (
                    f"curated provenance file {prov_loc} could not be read; "
                    "skipping the environment check for its cache hits"
                )
                warnings.warn(message, RuntimeWarning, stacklevel=2)
                result.logs.append(f"warning: {message}")
                continue
            for mismatch in check_environment_against_provenance(provenance, dag):
                mismatch.provenance_ref = ref
                result.environment_mismatches.append(mismatch.to_dict())
                line = (
                    f"environment differs from curated run ({ref}): "
                    f"{mismatch.package} curated="
                    f"{mismatch.curated_version} local={mismatch.local_version}"
                )
                if mismatch.severity == "prominent":
                    warnings.warn(
                        f"survey-cache hits were produced with a different "
                        f"version of an op-implementing package — "
                        f"{mismatch.package}: curated "
                        f"{mismatch.curated_version}, local "
                        f"{mismatch.local_version} (results are reused "
                        "anyway; recreate the curated environment from "
                        f"{ref} for exact reproduction)",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    result.logs.append(f"warning: {line}")
                else:
                    result.logs.append(f"note: {line}")

    @staticmethod
    def _write_manifest(
        *,
        dag: PipelineDAG,
        result: ExecutionResult,
        store: TieredCheckpointStore | None,
        outputs_loc: StorageLocation | None,
        status: str,
        started_at: str,
        write_tier: str,
    ) -> None:
        """Write the per-run ``manifest.json`` into the outputs directory.

        The manifest answers "where is my product?" definitively without
        copying anything: per-step disposition (computed / hit user cache /
        hit survey cache / pruned / marker), absolute artifact URIs, and
        timings. Best-effort — a manifest failure never masks the run's own
        outcome.
        """
        if outputs_loc is None:
            return
        hints = dag.recipe.execution
        manifest = {
            "schema_version": 1,
            "run_id": result.run_id,
            "recipe": {"name": dag.recipe.name, "version": dag.recipe.version},
            "cache_epoch": hints.cache_epoch if hints is not None else None,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "status": status,
            "write_tier": write_tier if store is not None else None,
            "tiers": store.tier_roots() if store is not None else {},
            "steps": {
                step_id: record.to_dict()
                for step_id, record in result.step_dispositions.items()
            },
            "environment_mismatches": result.environment_mismatches,
        }
        try:
            outputs_loc.mkdir()
            manifest_loc = outputs_loc / MANIFEST_FILENAME
            manifest_loc.write_text(json.dumps(manifest, indent=2))
            result.manifest_file = manifest_loc.as_context_value()
        except Exception:  # never mask the run's own error
            result.logs.append("warning: failed to write manifest.json")

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


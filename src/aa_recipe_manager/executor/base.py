# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PipelineExecutor protocol and shared execution result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG
    from aa_recipe_manager.storage import StorageLocation


@dataclass
class StepRecord:
    """Per-step disposition for the run manifest.

    ``disposition`` is one of ``computed`` / ``hit-user-cache`` /
    ``hit-survey-cache`` / ``pruned`` / ``marker`` / ``skipped``
    (``skipped`` = excluded by ``--skip-sinks``).
    """

    disposition: str
    step_hash: str | None = None
    tier: str | None = None
    elapsed_seconds: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "step_hash": self.step_hash,
            "tier": self.tier,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "artifacts": self.artifacts,
        }


@dataclass
class ExecutionResult:
    """The result of executing a PipelineDAG.

    Attributes:
        outputs: ``{step_id: {output_name: value}}`` for every executed step.
        provenance: Captured runtime provenance for the run.
        logs: Step-level log entries (one per executed or skipped step).
        skipped_steps: Step ids that were skipped because of a cache hit.
        pruned_steps: Step ids skipped because nothing downstream needed them.
        executed_steps: Step ids that actually invoked their callable.
        output_dir: Directory used for checkpoint serialization, if any.
        outputs_dir: Directory holding user-facing outputs (images, logs).
        log_file: Path to the captured ``standard_out.txt`` log, if written.
        console_log: Captured per-step stdout/stderr text for the run.
        run_id: Unique identifier for this execution (also the artifact
            subdirectory inside each written cache entry).
        step_dispositions: Per-step :class:`StepRecord` powering the manifest.
        manifest_file: Path to the written ``manifest.json``, if any.
        environment_mismatches: Warn-only differences between this run's
            environment and the curated provenance of survey-tier hits.
    """

    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: Any | None = None
    logs: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    pruned_steps: list[str] = field(default_factory=list)
    executed_steps: list[str] = field(default_factory=list)
    # Local runs keep these as plain ``Path``; remote (gs://) runs carry a
    # ``StorageLocation`` (str-safe, fsspec-parseable via ``str(...)``).
    output_dir: "Path | StorageLocation | None" = None
    outputs_dir: "Path | StorageLocation | None" = None
    log_file: "Path | StorageLocation | None" = None
    console_log: str = ""
    run_id: str | None = None
    step_dispositions: dict[str, StepRecord] = field(default_factory=dict)
    manifest_file: "Path | StorageLocation | None" = None
    environment_mismatches: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class ProgressCallback(Protocol):
    """Callback interface invoked by executors as steps make progress."""

    def on_step_start(
        self, step_id: str, index: int, total: int
    ) -> None: ...

    def on_step_end(
        self,
        step_id: str,
        index: int,
        total: int,
        *,
        skipped: bool = False,
        elapsed: float = 0.0,
        error: BaseException | None = None,
    ) -> None: ...


class NullProgressCallback:
    """Default no-op progress callback."""

    def on_step_start(self, step_id: str, index: int, total: int) -> None:
        return None

    def on_step_end(
        self,
        step_id: str,
        index: int,
        total: int,
        *,
        skipped: bool = False,
        elapsed: float = 0.0,
        error: BaseException | None = None,
    ) -> None:
        return None


@runtime_checkable
class PipelineExecutor(Protocol):
    """Interface implemented by all executor backends."""

    def execute(
        self,
        dag: PipelineDAG,
        inputs: dict[str, Any] | None = None,
        output_dir: str | Path | None = None,
        *,
        force: bool = False,
        no_checkpoints: bool = False,
        skip_sinks: bool = False,
        regenerate: str = "auto",
        outputs_dir: str | Path | None = None,
        temp_dir: str | Path | None = None,
        log_destination: str = "file",
        storage_options: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ExecutionResult: ...

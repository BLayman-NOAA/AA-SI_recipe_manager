# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PipelineExecutor protocol and shared execution result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG


@dataclass
class ExecutionResult:
    """The result of executing a PipelineDAG.

    Attributes:
        outputs: ``{step_id: {output_name: value}}`` for every executed step.
        provenance: Captured runtime provenance for the run.
        logs: Step-level log entries (one per executed or skipped step).
        skipped_steps: Step ids that were skipped because of a cache hit.
        executed_steps: Step ids that actually invoked their callable.
        output_dir: Directory used for checkpoint serialization, if any.
    """

    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: Any | None = None
    logs: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    executed_steps: list[str] = field(default_factory=list)
    output_dir: Path | None = None


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
        progress: ProgressCallback | None = None,
    ) -> ExecutionResult: ...

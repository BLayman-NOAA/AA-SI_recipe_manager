# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Execution context helpers for runtime-specific behavior."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from aa_recipe_manager.storage import StorageLocation


@dataclass(frozen=True)
class ExecutionContext:
    mode: str | None = None
    output_dir: Path | StorageLocation | None = None
    step_id: str | None = None
    artifacts_dir: Path | StorageLocation | None = None
    temp_dir: Path | StorageLocation | None = None
    #: Global fsspec storage options for remote (gs://, ...) *input* paths.
    #: Must stay a plain picklable dict so future distributed executors can
    #: re-establish the context inside worker tasks.
    storage_options: Mapping[str, Any] | None = None


_EXECUTION_CONTEXT: ContextVar[ExecutionContext] = ContextVar(
    "aa_recipe_manager_execution_context",
    default=ExecutionContext(),
)


def get_execution_context() -> ExecutionContext:
    """Return the current execution context."""
    return _EXECUTION_CONTEXT.get()


@contextmanager
def execution_context(
    *,
    mode: str | None = None,
    output_dir: str | Path | StorageLocation | None = None,
    step_id: str | None = None,
    artifacts_dir: str | Path | StorageLocation | None = None,
    temp_dir: str | Path | StorageLocation | None = None,
    storage_options: Mapping[str, Any] | None = None,
) -> Iterator[ExecutionContext]:
    """Temporarily set the recipe execution context for the current task.

    Directory values are normalized through :class:`StorageLocation`: local
    paths become plain ``Path`` objects (so consumers using ``pathlib`` are
    unaffected), while fsspec URLs (``gs://`` etc.) are preserved as
    ``StorageLocation`` objects rather than being mangled by ``Path()``.
    """

    def _resolve(value: str | Path | StorageLocation | None):
        if value is None:
            return None
        return StorageLocation.parse(value).as_context_value()

    resolved_output_dir = _resolve(output_dir)
    resolved_artifacts_dir = _resolve(artifacts_dir)
    resolved_temp_dir = _resolve(temp_dir)
    token: Token[ExecutionContext] = _EXECUTION_CONTEXT.set(
        ExecutionContext(
            mode=mode,
            output_dir=resolved_output_dir,
            step_id=step_id,
            artifacts_dir=resolved_artifacts_dir,
            temp_dir=resolved_temp_dir,
            # Empty dicts confuse xarray/zarr ("provided but unused"); publish
            # None instead so consumers can pass the value straight through.
            storage_options=dict(storage_options) if storage_options else None,
        )
    )
    try:
        yield _EXECUTION_CONTEXT.get()
    finally:
        _EXECUTION_CONTEXT.reset(token)
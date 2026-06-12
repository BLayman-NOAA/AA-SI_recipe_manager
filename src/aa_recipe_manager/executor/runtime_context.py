# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Execution context helpers for runtime-specific behavior."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ExecutionContext:
    mode: str | None = None
    output_dir: Path | None = None
    step_id: str | None = None
    artifacts_dir: Path | None = None
    temp_dir: Path | None = None


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
    output_dir: str | Path | None = None,
    step_id: str | None = None,
    artifacts_dir: str | Path | None = None,
    temp_dir: str | Path | None = None,
) -> Iterator[ExecutionContext]:
    """Temporarily set the recipe execution context for the current task."""
    resolved_output_dir = None if output_dir is None else Path(output_dir)
    resolved_artifacts_dir = None if artifacts_dir is None else Path(artifacts_dir)
    resolved_temp_dir = None if temp_dir is None else Path(temp_dir)
    token: Token[ExecutionContext] = _EXECUTION_CONTEXT.set(
        ExecutionContext(
            mode=mode,
            output_dir=resolved_output_dir,
            step_id=step_id,
            artifacts_dir=resolved_artifacts_dir,
            temp_dir=resolved_temp_dir,
        )
    )
    try:
        yield _EXECUTION_CONTEXT.get()
    finally:
        _EXECUTION_CONTEXT.reset(token)
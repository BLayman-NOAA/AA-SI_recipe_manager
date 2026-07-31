# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Lazy references to upstream step outputs, and their shared resolver.

A leaf module (no runtime dependency on ``invocation.py`` or
``engine/*``) so both the closure-building/worker path
(``engine/tasks.py``) and the client-side ``RuntimeContext``
(``invocation.py``) can resolve the same ref types without a circular
import: ``engine/tasks.py`` already imports from ``invocation.py``, so
the shared vocabulary lives here instead of in either of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import PipelineExecutionError

if TYPE_CHECKING:
    from aa_recipe_manager.executor.tiered import CheckpointStore


@dataclass(frozen=True)
class CheckpointRef:
    """An upstream output that lives in the content-addressed cache.

    Re-opened lazily from the store (``xr.open_zarr`` for Datasets, so only
    touched chunks are read). Carries the step id and the instance hash when
    the producer was a mapped/swept instance.
    """

    step_id: str
    output_name: str
    instance_hash: str | None = None


@dataclass(frozen=True)
class ValueRef:
    """An upstream output passed by value.

    Free within a process (the object is shared); across a process boundary
    the value is pickled, so a process backend rejects a closure that still
    holds a ``ValueRef`` for a non-trivial value.
    """

    step_id: str
    output_name: str
    value: Any


@dataclass(frozen=True)
class FoldedCheckpointRef:
    """A mapped/swept member's folded output, backed by per-instance entries.

    A chain member's output as recorded in ``RuntimeContext``/
    ``result.outputs`` is ``{output_name: [instance_0, instance_1, ...]}`` —
    the fold has no single checkpoint entry of its own, only one entry per
    instance (``instance_hashes``, in instance order). Resolving reloads and
    refolds each instance's entry.
    """

    step_id: str
    output_name: str
    instance_hashes: tuple[str, ...]


def resolve_ref(
    ref: CheckpointRef | ValueRef | FoldedCheckpointRef,
    store: CheckpointStore | None,
) -> Any:
    """Resolve a ref to its actual value, loading from ``store`` if needed."""
    if isinstance(ref, ValueRef):
        return ref.value
    if store is None:
        raise PipelineExecutionError(
            ref.step_id,
            f"checkpoint reference for {ref.step_id}.{ref.output_name} "
            "cannot be resolved without a checkpoint store",
        )
    if isinstance(ref, FoldedCheckpointRef):
        return [
            store.load(ref.step_id, instance_hash=h).get(ref.output_name)
            for h in ref.instance_hashes
        ]
    return store.load(ref.step_id, instance_hash=ref.instance_hash).get(
        ref.output_name
    )

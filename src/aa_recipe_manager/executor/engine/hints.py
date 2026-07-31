# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Per-step executor-configuration merging (FR-15.6).

Pipeline-level ``execution.dask_config`` / ``execution.prefect_config`` are the
defaults; a step's own ``execution`` block overrides them on a per-key basis.
The sequential / inline backend ignores both blocks; only the Dask and Prefect
backends consult these.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aa_recipe_manager.executor.engine.schedule import Unit
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG


def _pipeline_block(dag: PipelineDAG, field: str) -> dict[str, Any]:
    hints = dag.recipe.execution
    if hints is None:
        return {}
    return dict(getattr(hints, field) or {})


def _step_block(node: DAGNode, field: str) -> dict[str, Any]:
    hints = node.step.execution
    if hints is None:
        return {}
    return dict(getattr(hints, field) or {})


def resolve_dask_config(dag: PipelineDAG, node: DAGNode) -> dict[str, Any]:
    """Merge pipeline-level ``dask_config`` with a step's overrides.

    Step-level keys win on conflict (``software_architecture.md`` §5.3.1).
    """
    return {**_pipeline_block(dag, "dask_config"), **_step_block(node, "dask_config")}


def resolve_prefect_config(dag: PipelineDAG, node: DAGNode) -> dict[str, Any]:
    """Merge pipeline-level ``prefect_config`` with a step's overrides."""
    return {
        **_pipeline_block(dag, "prefect_config"),
        **_step_block(node, "prefect_config"),
    }


def _resolve_unit(
    dag: PipelineDAG, unit: Unit, resolver
) -> dict[str, Any]:
    """Resolve one unit's config: a step is direct; a chain merges members.

    Chain members merge in execution order (a later member's key wins), and a
    conflicting key across members is surfaced as a warning so a recipe author
    notices that two steps in one fan-out chain disagree on, say, the worker
    scheduler.
    """
    if not unit.is_chain:
        return resolver(dag, dag.nodes[unit.first])
    merged: dict[str, Any] = {}
    for mid in unit.member_ids:
        step_cfg = resolver(dag, dag.nodes[mid])
        for key, value in step_cfg.items():
            if key in merged and merged[key] != value:
                warnings.warn(
                    f"mapped chain {list(unit.member_ids)!r} has conflicting "
                    f"execution config for {key!r} "
                    f"({merged[key]!r} vs {value!r}); using {value!r}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            merged[key] = value
    return merged


def resolve_unit_dask_config(dag: PipelineDAG, unit: Unit) -> dict[str, Any]:
    """Merged ``dask_config`` for a schedulable unit (step or chain)."""
    return _resolve_unit(dag, unit, resolve_dask_config)


def resolve_unit_prefect_config(dag: PipelineDAG, unit: Unit) -> dict[str, Any]:
    """Merged ``prefect_config`` for a schedulable unit (step or chain)."""
    return _resolve_unit(dag, unit, resolve_prefect_config)

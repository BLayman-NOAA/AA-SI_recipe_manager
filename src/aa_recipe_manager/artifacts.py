# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Where a step's checkpointed outputs are, per cache tier.

The entry a recipe's current parameters hash to is looked up in each tier, and
for a mapped or swept step each instance's entry, as the plan preview predicts
them. Only sidecars are read; nothing is written or loaded.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.checkpoint import (
    check_step_ids,
    compute_step_fingerprints,
)
from aa_recipe_manager.executor.engine.context import build_run_context
from aa_recipe_manager.executor.engine.tasks import instance_cache_readable
from aa_recipe_manager.parallel import derive_instance_hash, group_mapped_chains
from aa_recipe_manager.preview import Prober, chain_discriminators, source_label
from aa_recipe_manager.storage import is_remote_url

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from pathlib import Path

    from aa_recipe_manager.executor.checkpoint import (
        CheckpointManager,
        _StepCacheMeta,
    )
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore
    from aa_recipe_manager.model.types import PipelineDAG


@dataclass
class ArtifactInfo:
    """One checkpointed output of a step, or of one instance of it.

    Attributes:
        output: The output name.
        url: An absolute local path (native separators) or a URL such as
            ``gs://...``.
        format: The sidecar's format (``zarr``, ``zarr_da``, ``zarr_tree``,
            ``netcdf``, ``json``, ``pickle``, ...).
        tier: The cache tier it was found in (``user`` or ``survey``).
        step_hash: The entry's hash: the step's, or the instance's.
        instance_index: The instance's position in the current fan out, or
            None for a plain step.
        item: The mapped item the instance ran on (a file URL, for example),
            or None.
        created_at: When the entry was written.
        run_id: The run that wrote the entry.
        size_bytes: Not computed yet; always None.
    """

    output: str
    url: str
    format: str
    tier: str
    step_hash: str
    instance_index: int | None = None
    item: Any = None
    created_at: str | None = None
    run_id: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """The record as a JSON ready dict."""
        return asdict(self)


@dataclass
class StepArtifacts:
    """What the cache tiers hold for one step at its current hash.

    Attributes:
        step_id: The step.
        step_hash: The step's current hash.
        fanned_out: Whether the step is mapped or swept.
        held_by: Tiers holding the step; for a fanned out step, tiers holding
            every instance.
        instances: The expected instance count of a fanned out step, or None
            when it is not known (a plain step, or an uncached fan out source).
        found: Entries found: one per instance with a sidecar, or one for a
            plain step or a marker.
        marker: Whether a side effect step's marker is held.
        note: Why nothing could be looked up, when that is the case.
        artifacts: One record per output of each entry found, in instance
            order. Each entry is read from the first tier in read order that
            holds it.
    """

    step_id: str
    step_hash: str
    fanned_out: bool
    held_by: list[str] = field(default_factory=list)
    instances: int | None = None
    found: int = 0
    marker: bool = False
    note: str | None = None
    artifacts: list[ArtifactInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The answer as a JSON ready dict."""
        return asdict(self)

    def format_text(self) -> str:
        """A heading line and one line per artifact."""
        head = [self.step_id, self.step_hash[:12]]
        if self.fanned_out:
            head.append("mapped")
        head.append(f"held by: {', '.join(self.held_by) or 'none'}")
        if self.fanned_out and self.instances is not None:
            head.append(f"{self.found} of {self.instances} instances found")
        elif self.marker:
            head.append("marker")
        if self.note:
            head.append(self.note)
        lines = ["  ".join(head)]
        for record in self.artifacts:
            row = [record.output, record.format, record.tier]
            if record.instance_index is not None:
                row.append(f"#{record.instance_index}")
            if record.item is not None:
                row.append(_item_label(record.item))
            row.append(record.url)
            lines.append("  " + "  ".join(row))
        return "\n".join(lines)


def step_artifacts(
    dag: PipelineDAG,
    step_id: str,
    *,
    inputs: dict[str, Any] | None = None,
    user_cache_dir: str | Path | None = None,
    survey_cache_dir: str | Path | None = None,
    storage_options: dict[str, Any] | None = None,
    fingerprint_cache: MutableMapping[tuple[str, str], Any] | None = None,
    base_dir: str | Path | None = None,
    instance_probe_limit: int = 32,
) -> StepArtifacts:
    """Find the checkpointed outputs of one step at its current hash.

    Args:
        dag: The pipeline.
        step_id: The step to look up.
        inputs: Pipeline input values.
        user_cache_dir: The user cache tier root. Without it nothing is held.
        survey_cache_dir: The survey cache tier root.
        storage_options: fsspec options for remote tiers and inputs.
        fingerprint_cache: Path fingerprints kept between calls.
        base_dir: Directory that relative local paths are fingerprinted under.
        instance_probe_limit: Above this many instances, which are held is
            read from one listing per tier instead of a probe per instance.

    Returns:
        The :class:`StepArtifacts`.

    Raises:
        UnknownStepError: ``step_id`` is not a step of the recipe.
    """
    check_step_ids(dag, [step_id], "step artifacts")
    node = dag.nodes[step_id]
    ctx = build_run_context(
        dag,
        inputs=inputs,
        user_cache_dir=user_cache_dir,
        survey_cache_dir=survey_cache_dir,
        storage_options=storage_options,
        fingerprint_cache=fingerprint_cache,
        base_dir=base_dir,
    )
    store = ctx.checkpoints
    hashes = ctx.step_hashes
    if store is None:
        hashes = compute_step_fingerprints(
            dag,
            ctx.pipeline_inputs,
            storage_options=storage_options,
            path_fingerprint_cache=fingerprint_cache,
            base_dir=base_dir,
        ).hashes
    result = StepArtifacts(
        step_id=step_id,
        step_hash=hashes.get(step_id, ""),
        fanned_out=node.is_mapped or node.is_swept,
    )
    if store is None:
        result.note = "no cache configured"
    elif result.fanned_out:
        _find_instances(result, dag, store, instance_probe_limit)
    else:
        _find_entry(result, dag, store)
    return result


def _find_entry(
    result: StepArtifacts, dag: PipelineDAG, store: TieredCheckpointStore
) -> None:
    """Fill ``result`` for a plain step from the first tier holding it."""
    step_id = result.step_id
    node = dag.nodes[step_id]
    if node.spec.sink or not node.spec.outputs:
        result.held_by = store.tiers_with_marker(step_id)
        result.marker = bool(result.held_by)
        result.found = int(result.marker)
        return
    result.held_by = store.tiers_holding(step_id)
    for tier in store.read_order:
        if tier not in result.held_by:
            continue
        manager = store.manager(tier)
        meta = manager.read_meta(step_id, result.step_hash)
        if meta is not None:
            result.found = 1
            result.artifacts = _records(manager, meta, tier, None, None)
        return


def _find_instances(
    result: StepArtifacts,
    dag: PipelineDAG,
    store: TieredCheckpointStore,
    limit: int,
) -> None:
    """Fill ``result`` for a mapped or swept step, instance by instance."""
    step_id = result.step_id
    chain = next(c for c in group_mapped_chains(dag) if step_id in c.member_ids)
    discriminators = chain_discriminators(
        dag, chain.source_ref, chain.member_ids, store
    )
    if discriminators is None:
        src = source_label(chain.source_ref)
        result.note = f"instances are known once {src} has run"
        return
    n = len(discriminators)
    result.instances = n
    if not n or not instance_cache_readable(dag.nodes[step_id], result.step_hash):
        return
    instance_hashes = [
        derive_instance_hash(result.step_hash, d) for d in discriminators
    ]
    probe = Prober(store, limit)
    held, _listed = probe.instances_held(step_id, instance_hashes)
    result.held_by = [t for t in probe.tiers if all(held[t])]

    def read(i: int) -> tuple[str, CheckpointManager, _StepCacheMeta] | None:
        for tier in store.read_order:
            if not held[tier][i]:
                continue
            manager = store.manager(tier)
            meta = manager.read_meta(step_id, instance_hashes[i])
            if meta is not None:
                return tier, manager, meta
        return None

    for i, hit in enumerate(probe.map(read, list(range(n)))):
        if hit is None:
            continue
        tier, manager, meta = hit
        result.found += 1
        item = discriminators[i].get("item")
        result.artifacts.extend(_records(manager, meta, tier, i, item))


def _records(
    manager: CheckpointManager,
    meta: _StepCacheMeta,
    tier: str,
    index: int | None,
    item: Any,
) -> list[ArtifactInfo]:
    """One :class:`ArtifactInfo` per output of an entry."""
    return [
        ArtifactInfo(
            output=name,
            url=_absolute(manager.artifact_url(meta, name)),
            format=str(entry.get("format", "")),
            tier=tier,
            step_hash=meta.step_hash,
            instance_index=index,
            item=item,
            created_at=meta.created_at,
            run_id=meta.run_id,
        )
        for name, entry in meta.outputs.items()
    ]


def _absolute(url: str) -> str:
    """A local path made absolute, so another process can open it."""
    return url if is_remote_url(url) else os.path.abspath(url)


def _item_label(item: Any) -> str:
    """A short label for a mapped item: a file name for a path or URL."""
    text = str(item)
    return text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or text

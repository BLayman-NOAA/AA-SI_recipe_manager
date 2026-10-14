# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Plan preview: what a run would do, per step, before it runs.

A preview computes the recipe's step hashes, asks every cache tier which steps
it holds, and plans the run exactly as :func:`plan_execution` would plan it.
For a mapped or swept step it predicts each instance's hash, which needs the
fan out source: when that source is a cached JSON list (file URLs, for
example) it is loaded and every instance is checked. Nothing is written.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.checkpoint import (
    blockers_note,
    check_step_ids,
    compute_step_fingerprints,
    entry_dir_parts,
    plan_execution,
    resolve_checkpoint_policy,
    resolve_target_pins,
    resume_blockers,
)
from aa_recipe_manager.executor.engine.context import build_run_context
from aa_recipe_manager.executor.engine.tasks import (
    instance_cache_readable,
    running_readers,
    skippable_members,
)
from aa_recipe_manager.executor.tiered import SURVEY_TIER, USER_TIER
from aa_recipe_manager.parallel import (
    derive_instance_hash,
    expand_sweep,
    group_mapped_chains,
    instance_discriminator,
)
from aa_recipe_manager.resolver.params import parse_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, MutableMapping
    from pathlib import Path

    from aa_recipe_manager.executor.tiered import TieredCheckpointStore
    from aa_recipe_manager.model.types import PipelineDAG

#: Planned dispositions. ``skipped`` is a mapped member that nothing still
#: running reads; ``unknown`` is a mapped member whose instances cannot be
#: listed before its source runs.
PLANNED = (
    "compute",
    "load-user",
    "load-survey",
    "marker",
    "skipped",
    "pruned",
    "untargeted",
    "unknown",
)
#: What becomes of a step's output: ``saved`` already in a tier,
#: ``will-save`` written by this run, ``memory-only`` kept only in memory.
CHECKPOINT = ("saved", "will-save", "memory-only")

#: The manifest disposition each planned disposition becomes.
PLANNED_TO_DISPOSITION = {
    "compute": "computed",
    "load-user": "hit-user-cache",
    "load-survey": "hit-survey-cache",
    "marker": "marker",
    "skipped": "skipped",
    "pruned": "pruned",
    "untargeted": "untargeted",
}

_PROBE_THREADS = 8


@dataclass
class StepPreview:
    """The predicted outcome of one step.

    Attributes:
        step_id: The step id.
        step_hash: The step's hash; for a fanned out step, the base hash its
            instance hashes derive from.
        held_by: Tiers holding the step. For a fanned out step, the tiers
            holding every instance.
        planned: One of :data:`PLANNED`.
        tier: The tier a load is served from, else None.
        checkpoint: One of :data:`CHECKPOINT`. For a sink, its marker.
        pinned: True when the step is a run target saved by this run only.
        recipe_checkpoint: The step's ``checkpoint`` as the recipe writes it.
        fanned_out: True for a mapped or swept step.
        instances: Instance count of a fanned out step, when known.
        instances_held: Tier to the number of instances it holds.
        note: A short explanation, when there is one.
    """

    step_id: str
    step_hash: str
    held_by: list[str]
    planned: str
    tier: str | None = None
    checkpoint: str = "memory-only"
    pinned: bool = False
    recipe_checkpoint: str | None = None
    fanned_out: bool = False
    instances: int | None = None
    instances_held: dict[str, int] = field(default_factory=dict)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the preview as a JSON ready dict."""
        return {
            "step_id": self.step_id,
            "step_hash": self.step_hash,
            "held_by": list(self.held_by),
            "planned": self.planned,
            "tier": self.tier,
            "checkpoint": self.checkpoint,
            "pinned": self.pinned,
            "recipe_checkpoint": self.recipe_checkpoint,
            "fanned_out": self.fanned_out,
            "instances": self.instances,
            "instances_held": dict(self.instances_held),
            "note": self.note,
        }


@dataclass
class PlanPreview:
    """The predicted outcome of a whole run, one row per step."""

    recipe: str
    version: str
    mode: str
    targets: list[str]
    force_steps: list[str]
    tiers: dict[str, str]
    write_tier: str | None
    steps: list[StepPreview]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def step(self, step_id: str) -> StepPreview:
        """Return one step's preview; raises ``KeyError`` when absent."""
        for preview in self.steps:
            if preview.step_id == step_id:
                return preview
        raise KeyError(step_id)

    def to_dict(self) -> dict[str, Any]:
        """Return the preview as a JSON ready dict."""
        return {
            "recipe": self.recipe,
            "version": self.version,
            "mode": self.mode,
            "targets": list(self.targets),
            "force_steps": list(self.force_steps),
            "tiers": dict(self.tiers),
            "write_tier": self.write_tier,
            "steps": [step.to_dict() for step in self.steps],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }

    def format_text(self) -> str:
        """Render the preview as aligned text, one row per step."""
        header = f"Recipe: {self.recipe} (v{self.version})  mode: {self.mode}"
        if self.targets:
            header += f"  targets: {', '.join(self.targets)}"
        lines = [header]
        if self.tiers:
            tiers = ", ".join(f"{name}={root}" for name, root in self.tiers.items())
            lines.append(f"Tiers: {tiers}")
        else:
            lines.append("Tiers: none")
        rows = [("step", "hash", "held", "planned", "checkpoint", "instances")]
        notes: dict[int, str] = {}
        for step in self.steps:
            held = "".join(tier[0].upper() for tier in step.held_by) or "-"
            if step.instances is None:
                instances = "-"
            else:
                served = max(step.instances_held.values(), default=0)
                instances = f"{served}/{step.instances}"
            checkpoint = step.checkpoint + (" pinned" if step.pinned else "")
            rows.append(
                (
                    step.step_id,
                    step.step_hash[:12],
                    held,
                    step.planned,
                    checkpoint,
                    instances,
                )
            )
            if step.note:
                notes[len(rows) - 1] = step.note
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
        for index, row in enumerate(rows):
            cells = [cell.ljust(width) for cell, width in zip(row, widths)]
            lines.append("  ".join([*cells, row[-1]]).rstrip())
            if index in notes:
                lines.append(f"    {notes[index]}")
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        if self.blockers:
            lines.append(blockers_note(self.blockers))
        return "\n".join(lines)


def plan_preview(
    dag: PipelineDAG,
    *,
    inputs: dict[str, Any] | None = None,
    targets: Iterable[str] | None = None,
    only: bool = False,
    force_steps: Iterable[str] = (),
    force: bool = False,
    regenerate: str = "auto",
    user_cache_dir: str | Path | None = None,
    survey_cache_dir: str | Path | None = None,
    outputs_dir: str | Path | None = None,
    checkpoint_mode: str | None = None,
    checkpoint_steps: Iterable[str] | None = None,
    checkpoint_format: str | None = None,
    pin_targets: bool = True,
    storage_options: dict[str, Any] | None = None,
    fingerprint_cache: MutableMapping[tuple[str, str], Any] | None = None,
    instance_probe_limit: int = 32,
) -> PlanPreview:
    """Predict, per step, what a run with these arguments would do.

    The arguments mean what they mean to :func:`api.execute`, and the run is
    planned with the user tier as the write tier. Raises
    :class:`~aa_recipe_manager.exceptions.UpstreamNotCachedError` when
    ``only`` cannot be satisfied, as the run would.

    Args:
        dag: The pipeline to preview.
        inputs: Pipeline input values.
        targets: Steps to run to.
        only: Run the targets alone.
        force_steps: Steps that recompute even when cached.
        force: Recompute every step.
        regenerate: Artifact regeneration mode.
        user_cache_dir: The user cache tier root.
        survey_cache_dir: The survey cache tier root.
        outputs_dir: The outputs directory, for artifact presence checks.
        checkpoint_mode: Checkpoint mode override.
        checkpoint_steps: Extra steps to checkpoint.
        checkpoint_format: Checkpoint format override.
        pin_targets: Checkpoint the targets, as a run does by default.
        storage_options: fsspec options for remote tiers and inputs.
        fingerprint_cache: Path fingerprints kept between calls.
        instance_probe_limit: Fanned out steps with more instances than this
            are counted from one listing of the step's cache directory per
            tier rather than probed instance by instance.

    Returns:
        The :class:`PlanPreview`.
    """
    targets = tuple(targets or ())
    force_steps = tuple(force_steps or ())
    check_step_ids(dag, targets, "run target")
    check_step_ids(dag, force_steps, "force step")
    pins = set(checkpoint_steps or ())
    warnings: list[str] = []
    if targets and pin_targets:
        pins, pin_warnings = resolve_target_pins(
            dag, targets, pins, checkpoints_enabled=user_cache_dir is not None
        )
        warnings.extend(message for _step, message in pin_warnings)

    ctx = build_run_context(
        dag,
        inputs=inputs,
        user_cache_dir=user_cache_dir,
        force=force,
        regenerate=regenerate,
        outputs_dir=outputs_dir,
        checkpoint_mode=checkpoint_mode,
        checkpoint_steps=pins or None,
        checkpoint_format=checkpoint_format,
        storage_options=storage_options,
        survey_cache_dir=survey_cache_dir,
        targets=targets,
        force_steps=force_steps,
        only=only,
        fingerprint_cache=fingerprint_cache,
    )
    store = ctx.checkpoints
    hashes = ctx.step_hashes
    if store is None:
        warnings.append("no cache configured: every step computes")
        hashes = compute_step_fingerprints(
            dag,
            ctx.pipeline_inputs,
            storage_options=storage_options,
            path_fingerprint_cache=fingerprint_cache,
        ).hashes

    plan = plan_execution(
        dag,
        store,
        force=force,
        regenerate=regenerate,
        outputs_loc=ctx.outputs_loc,
        targets=targets,
        force_steps=force_steps,
        only=only,
    )

    # Pinned steps are the ones only the target pins put in the policy.
    pinned = set()
    if store is not None and targets and pin_targets:
        pinned = ctx.policy - resolve_checkpoint_policy(
            dag, mode=checkpoint_mode, extra_step_ids=checkpoint_steps
        )
    forced = set(dag.topological_order) if force else set(plan.forced)
    probe = _Prober(store, instance_probe_limit)
    previews: dict[str, StepPreview] = {}

    chain_members: set[str] = set()
    for chain in group_mapped_chains(dag):
        chain_members.update(chain.member_ids)
        members = [m for m in chain.member_ids if m not in plan.untargeted]
        if members:
            previews.update(
                _chain_previews(
                    dag, chain.source_ref, members, plan, hashes, forced, probe
                )
            )

    plain = [
        sid for sid in dag.topological_order
        if sid not in chain_members and sid not in plan.untargeted
    ]
    held = probe.held(dag, plain)
    for step_id in dag.topological_order:
        if step_id in plan.untargeted:
            previews[step_id] = _plain_preview(step_id, hashes, "untargeted", None, [])
            continue
        if step_id in chain_members:
            continue
        tier = None
        if step_id in plan.pruned:
            planned = "pruned"
        elif step_id in plan.marker_hits:
            planned, tier = "marker", USER_TIER
        elif step_id in plan.loadable:
            tier = store.hit_tier(step_id) or held[step_id][0]
            planned = f"load-{tier}"
        else:
            planned = "compute"
        previews[step_id] = _plain_preview(
            step_id, hashes, planned, tier, held[step_id]
        )

    steps = []
    for step_id in dag.topological_order:
        preview = previews[step_id]
        node = dag.nodes[step_id]
        is_side_effect = node.spec.sink or not node.spec.outputs
        preview.recipe_checkpoint = node.step.checkpoint
        preview.fanned_out = node.is_mapped or node.is_swept
        preview.pinned = step_id in pinned
        reason = _recompute_reason(
            node, step_id, force, set(force_steps), plan.forced, regenerate
        )
        if preview.held_by and preview.planned == "compute" and reason:
            preview.note = "; ".join(filter(None, [preview.note, reason]))
        if preview.held_by:
            preview.checkpoint = "saved"
        elif preview.planned in ("compute", "unknown") and store is not None and (
            is_side_effect or step_id in ctx.policy
        ):
            preview.checkpoint = "will-save"
        else:
            preview.checkpoint = "memory-only"
        steps.append(preview)

    return PlanPreview(
        recipe=dag.recipe.name,
        version=dag.recipe.version,
        mode="only" if only else "to" if targets else "all",
        targets=list(targets),
        force_steps=sorted(force_steps),
        tiers=store.tier_roots() if store is not None else {},
        write_tier=store.write_tier if store is not None else None,
        steps=steps,
        blockers=resume_blockers(plan, ctx.policy),
        warnings=warnings,
    )


def survey_hits_lost(before: PlanPreview, after: PlanPreview) -> list[str]:
    """Steps held by the survey tier before an edit and not after it.

    Args:
        before: The preview before the edit.
        after: The preview after the edit.

    Returns:
        The step ids, in the order of ``before``.
    """
    held_after = {step.step_id: step.held_by for step in after.steps}
    return [
        step.step_id
        for step in before.steps
        if SURVEY_TIER in step.held_by
        and SURVEY_TIER not in held_after.get(step.step_id, [])
    ]


def _recompute_reason(
    node: Any,
    step_id: str,
    force: bool,
    force_steps: set[str],
    forced: set[str],
    regenerate: str,
) -> str | None:
    """Why a step that a tier holds computes anyway, when there is a reason."""
    if force:
        return "recomputes: the run is forced"
    if step_id in force_steps:
        return "recomputes: forced by --force-step"
    if step_id in forced:
        return "recomputes: an upstream step is forced"
    is_side_effect = node.spec.sink or not node.spec.outputs
    if regenerate == "all" or (regenerate == "sinks" and is_side_effect):
        return f"recomputes: --regenerate {regenerate}"
    if regenerate != "off" and node.step.regenerate in ("always", "if-missing"):
        return f"recomputes: regenerate: {node.step.regenerate}"
    return None


def _plain_preview(
    step_id: str,
    hashes: dict[str, str],
    planned: str,
    tier: str | None,
    held_by: list[str],
) -> StepPreview:
    return StepPreview(
        step_id=step_id,
        step_hash=hashes.get(step_id, ""),
        held_by=list(held_by),
        planned=planned,
        tier=tier,
    )


class _Prober:
    """Ask the cache tiers what they hold, in parallel for remote tiers."""

    def __init__(self, store: TieredCheckpointStore | None, limit: int) -> None:
        self.store = store
        self.limit = limit
        self.tiers = [] if store is None else list(store.tier_roots())
        self.remote = store is not None and any(
            not store.manager(tier).location.is_local for tier in self.tiers
        )

    def map(self, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        if not self.remote or len(items) < 2:
            return [fn(item) for item in items]
        with ThreadPoolExecutor(max_workers=_PROBE_THREADS) as pool:
            return list(pool.map(fn, items))

    def held(self, dag: PipelineDAG, step_ids: list[str]) -> dict[str, list[str]]:
        """Tiers holding each plain step (its marker, for a side effect step)."""
        if self.store is None:
            return {step_id: [] for step_id in step_ids}
        store = self.store

        def one(step_id: str) -> list[str]:
            node = dag.nodes[step_id]
            if node.spec.sink or not node.spec.outputs:
                return store.tiers_with_marker(step_id)
            return store.tiers_holding(step_id)

        return dict(zip(step_ids, self.map(one, step_ids)))

    def instances_held(
        self, step_id: str, instance_hashes: list[str]
    ) -> tuple[dict[str, list[bool]], bool]:
        """Per tier, whether each instance is held, and whether it was listed.

        Up to ``limit`` instances are probed one by one. Beyond that one
        listing per tier is matched against the hash keys, which is a
        prediction: an interrupted write also leaves a key.
        """
        store = self.store
        if len(instance_hashes) <= self.limit:
            pairs = [(t, h) for t in self.tiers for h in instance_hashes]
            found = self.map(
                lambda pair: store.manager(pair[0]).has_checkpoint(
                    step_id, instance_hash=pair[1]
                ),
                pairs,
            )
            n = len(instance_hashes)
            return (
                {t: found[i * n:(i + 1) * n] for i, t in enumerate(self.tiers)},
                False,
            )
        keys = self.map(
            lambda tier: store.manager(tier).entry_keys(step_id), self.tiers
        )
        return (
            {
                tier: [
                    entry_dir_parts(step_id, h)[1] in tier_keys
                    for h in instance_hashes
                ]
                for tier, tier_keys in zip(self.tiers, keys)
            },
            True,
        )


def _map_source_items(
    dag: PipelineDAG, source_ref: str, store: TieredCheckpointStore | None
) -> list[Any] | None:
    """The cached fan out source as a list, or None when it is not known."""
    parsed = parse_ref(source_ref)
    if parsed is None or store is None:
        return None
    src_id, src_out = parsed
    node = dag.nodes.get(src_id)
    if node is None or node.is_mapped or node.is_swept:
        return None
    holding = store.tiers_holding(src_id)
    if not holding:
        return None
    manager = store.manager(holding[0])
    meta = manager.read_meta(src_id)
    entry = (meta.outputs if meta is not None else {}).get(src_out)
    if entry is None or entry.get("format") != "json":
        return None
    value = manager.load(src_id)[src_out]
    return value if isinstance(value, list) else [value]


def _chain_previews(
    dag: PipelineDAG,
    source_ref: str | None,
    members: list[str],
    plan: Any,
    hashes: dict[str, str],
    forced: set[str],
    probe: _Prober,
) -> dict[str, StepPreview]:
    """Predict the members of one mapped or swept chain, instance by instance.

    ``members`` are the chain's members inside the run's target ancestry. As
    in the run, the ones the plan pruned do not run; their holdings are still
    counted so the held column means the same for every step.
    """
    store = probe.store
    active = [m for m in members if m not in plan.pruned]

    def planned_alone(mid: str, otherwise: str) -> str:
        return "pruned" if mid in plan.pruned else otherwise

    if store is None:
        return {
            mid: StepPreview(
                step_id=mid,
                step_hash=hashes.get(mid, ""),
                held_by=[],
                planned=planned_alone(mid, "compute"),
            )
            for mid in members
        }
    if source_ref is not None:
        items = _map_source_items(dag, source_ref, store)
        if items is None:
            src = (parse_ref(source_ref) or (source_ref,))[0]
            return {
                mid: StepPreview(
                    step_id=mid,
                    step_hash=hashes.get(mid, ""),
                    held_by=[],
                    planned=planned_alone(mid, "unknown"),
                    note=f"instances are known once {src} has run",
                )
                for mid in members
            }
    else:
        items = [None]
    swept = [dag.nodes[m] for m in members if dag.nodes[m].is_swept]
    combos: list[dict[str, Any] | None] = (
        list(expand_sweep(swept[0].sweep_declaration)) if swept else [None]
    )

    discriminators = []
    for item in items:
        for combo in combos:
            kwargs: dict[str, Any] = {
                "index": len(discriminators),
                "param_overrides": combo,
            }
            if source_ref is not None:
                kwargs["item"] = item
            discriminators.append(instance_discriminator(**kwargs))
    n = len(discriminators)

    # held[mid][tier][i]: whether ``tier`` holds instance i of ``mid``.
    held: dict[str, dict[str, list[bool]]] = {}
    listed: set[str] = set()
    for mid in members:
        node = dag.nodes[mid]
        if not instance_cache_readable(node, hashes.get(mid)):
            held[mid] = {tier: [False] * n for tier in probe.tiers}
            continue
        instance_hashes = [derive_instance_hash(hashes[mid], d) for d in discriminators]
        held[mid], was_listed = probe.instances_held(mid, instance_hashes)
        if was_listed:
            listed.add(mid)

    read_order = store.read_order
    read_outside = running_readers(active, dag, plan.must_run)
    stats = {mid: {"computed": 0, "cached": 0, "skipped": 0} for mid in members}
    user_served = {mid: False for mid in members}
    for i in range(n):
        cached = {
            mid for mid in active
            if mid not in forced and any(held[mid][t][i] for t in read_order)
        }
        skipped = (
            skippable_members(active, dag, cached, read_outside) if active else set()
        )
        for mid in active:
            if mid in skipped:
                stats[mid]["skipped"] += 1
            elif mid in cached:
                stats[mid]["cached"] += 1
                if USER_TIER in read_order and held[mid][USER_TIER][i]:
                    user_served[mid] = True
            else:
                stats[mid]["computed"] += 1

    previews = {}
    for mid in members:
        counts = {tier: sum(held[mid][tier]) for tier in probe.tiers}
        computed, hits, skipped = (
            stats[mid]["computed"],
            stats[mid]["cached"],
            stats[mid]["skipped"],
        )
        tier = None
        note = None
        if mid in plan.pruned:
            planned = "pruned"
        elif computed == 0 and hits:
            tier = USER_TIER if user_served[mid] else SURVEY_TIER
            planned = f"load-{tier}"
        elif computed == 0 and skipped:
            planned = "skipped"
        else:
            planned = "compute"
            if hits:
                note = f"{hits} of {n} instances cached"
        if mid in listed:
            note = "; ".join(filter(None, [note, "predicted from the cache listing"]))
        previews[mid] = StepPreview(
            step_id=mid,
            step_hash=hashes.get(mid, ""),
            held_by=[t for t in probe.tiers if n and counts[t] == n],
            planned=planned,
            tier=tier,
            instances=n,
            instances_held=counts,
            note=note,
        )
    return previews

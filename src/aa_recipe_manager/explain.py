# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""explain-cache: report exactly why each step will hit or miss the cache.

The chronic pain of every content-addressed cache is "why didn't this hit?"
A miss is simply an absent key, so this module recomputes the recipe's
current fingerprints, probes each tier for the hash, and — on a miss —
scans the tier roots for sidecars with a matching ``step_id``, diffing
their stored fingerprint payloads against the recomputed one to name the
exact field(s) that diverged (a param value, an input checksum, the cache
epoch, an upstream change, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.checkpoint import (
    CheckpointManager,
    _step_upstream,
    compute_step_fingerprints,
)
from aa_recipe_manager.storage import StorageLocation

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG

_ABSENT = object()


@dataclass
class PayloadDiff:
    """One divergent field between a stored and a recomputed payload."""

    path: str  # dotted path, e.g. "fingerprint.resolved_params.factor"
    stored: Any
    current: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "stored": self.stored, "current": self.current}


@dataclass
class CandidateExplanation:
    """The nearest stored entry for a missed step and how it differs."""

    tier: str
    run_id: str | None
    created_at: str | None
    step_hash: str
    differences: list[PayloadDiff] = field(default_factory=list)
    note: str | None = None  # e.g. "no stored fingerprint payload"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "step_hash": self.step_hash,
            "differences": [d.to_dict() for d in self.differences],
            "note": self.note,
        }


@dataclass
class StepCacheExplanation:
    """Hit/miss verdict for one step across the probed tiers."""

    step_id: str
    step_hash: str
    status: str  # "hit" | "marker-hit" | "miss" | "never-cached"
    tier: str | None = None
    candidate: CandidateExplanation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_hash": self.step_hash,
            "status": self.status,
            "tier": self.tier,
            "candidate": None if self.candidate is None else self.candidate.to_dict(),
        }


@dataclass
class CacheExplanation:
    """Full explain-cache report for a recipe against its tier roots."""

    recipe_name: str
    recipe_version: str
    tiers: dict[str, str]  # tier name -> cache root
    steps: list[StepCacheExplanation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe": {"name": self.recipe_name, "version": self.recipe_version},
            "tiers": self.tiers,
            "steps": [s.to_dict() for s in self.steps],
        }

    def format_text(self) -> str:
        lines = [f"Recipe: {self.recipe_name} (v{self.recipe_version})"]
        lines.append(
            "Tiers probed: "
            + ", ".join(f"{name}={root}" for name, root in self.tiers.items())
        )
        lines.append("")
        for step in self.steps:
            short_hash = step.step_hash[:12]
            if step.status in ("hit", "marker-hit"):
                label = "HIT" if step.status == "hit" else "HIT (marker)"
                lines.append(f"{label:<14} [{step.tier}]  {step.step_id}  {short_hash}")
                continue
            if step.status == "never-cached":
                lines.append(f"{'NEVER CACHED':<14} {step.step_id}  {short_hash}")
                continue
            lines.append(f"{'MISS':<14} {step.step_id}  {short_hash}")
            cand = step.candidate
            if cand is not None:
                lines.append(
                    f"    nearest candidate: {cand.tier} tier, "
                    f"run {cand.run_id or '?'}, created {cand.created_at or '?'}"
                )
                if cand.note:
                    lines.append(f"    note: {cand.note}")
                for diff in cand.differences:
                    lines.append(
                        f"      {diff.path}: {diff.stored!r} -> {diff.current!r}"
                    )
        return "\n".join(lines)


def _diff_payload(stored: Any, current: Any, path: str = "") -> list[PayloadDiff]:
    """Recursive structural diff with dotted paths; leaves report both values."""
    if isinstance(stored, dict) and isinstance(current, dict):
        diffs: list[PayloadDiff] = []
        for key in sorted(set(stored) | set(current)):
            sub_path = f"{path}.{key}" if path else str(key)
            sub_stored = stored.get(key, _ABSENT)
            sub_current = current.get(key, _ABSENT)
            if sub_stored is _ABSENT or sub_current is _ABSENT:
                diffs.append(
                    PayloadDiff(
                        path=sub_path,
                        stored="<absent>" if sub_stored is _ABSENT else sub_stored,
                        current="<absent>" if sub_current is _ABSENT else sub_current,
                    )
                )
            else:
                diffs.extend(_diff_payload(sub_stored, sub_current, sub_path))
        return diffs
    if (
        isinstance(stored, list)
        and isinstance(current, list)
        and len(stored) == len(current)
    ):
        diffs = []
        for index, (sub_stored, sub_current) in enumerate(zip(stored, current)):
            diffs.extend(_diff_payload(sub_stored, sub_current, f"{path}[{index}]"))
        return diffs
    if stored != current:
        return [PayloadDiff(path=path, stored=stored, current=current)]
    return []


def _annotate_parent_diffs(
    diffs: list[PayloadDiff], upstream_names: list[str]
) -> list[PayloadDiff]:
    """Rewrite ``parents[i]`` paths to name the upstream step that changed.

    Parent hashes are stored in sorted-upstream-id order, so index ``i`` maps
    to the i-th sorted upstream step of the current DAG. A diverging parent
    hash means "an upstream change" — the interesting explanation lives in
    that parent's own entry, so the annotation points the user there.
    """
    annotated: list[PayloadDiff] = []
    for diff in diffs:
        if diff.path.startswith("parents["):
            index = int(diff.path[len("parents[") : diff.path.index("]")])
            if 0 <= index < len(upstream_names):
                diff = PayloadDiff(
                    path=f"parents[{index}] (upstream change in step "
                    f"{upstream_names[index]!r})",
                    stored=diff.stored,
                    current=diff.current,
                )
        annotated.append(diff)
    return annotated


def explain_cache(
    dag: PipelineDAG,
    *,
    inputs: dict[str, Any] | None = None,
    user_cache_dir: str | Path,
    survey_cache_dir: str | Path | None = None,
    storage_options: dict[str, Any] | None = None,
) -> CacheExplanation:
    """Explain, per step, whether the current recipe would hit each tier.

    Probes tiers in the normal read order ``[user, survey]``. For misses,
    the nearest candidate (same ``step_id``, most recent ``created_at``)
    across all tiers is diffed field-by-field against the recomputed
    fingerprint payload.
    """
    fingerprints = compute_step_fingerprints(
        dag, inputs or {}, storage_options=storage_options
    )
    tiers: list[tuple[str, CheckpointManager]] = [
        (
            "user",
            CheckpointManager(
                StorageLocation.parse(user_cache_dir, storage_options),
                fingerprints.hashes,
                storage_options=storage_options,
            ),
        )
    ]
    if survey_cache_dir is not None:
        tiers.append(
            (
                "survey",
                CheckpointManager(
                    StorageLocation.parse(survey_cache_dir, storage_options),
                    fingerprints.hashes,
                    storage_options=storage_options,
                ),
            )
        )

    # One scan per tier root; reused for every missed step.
    entries_by_tier = {name: manager.iter_entries() for name, manager in tiers}
    upstream = _step_upstream(dag)

    explanation = CacheExplanation(
        recipe_name=dag.recipe.name,
        recipe_version=dag.recipe.version,
        tiers={name: str(manager.location) for name, manager in tiers},
    )

    for step_id in dag.topological_order:
        step_hash = fingerprints.hashes[step_id]
        step_expl = StepCacheExplanation(step_id=step_id, step_hash=step_hash, status="miss")

        for name, manager in tiers:
            if manager.has_checkpoint(step_id):
                step_expl.status = "hit"
                step_expl.tier = name
                break
            if manager.has_marker(step_id):
                step_expl.status = "marker-hit"
                step_expl.tier = name
                break

        if step_expl.status == "miss":
            candidates = [
                (name, meta)
                for name, entries in entries_by_tier.items()
                for _loc, meta in entries
                if meta.step_id == step_id
            ]
            if not candidates:
                step_expl.status = "never-cached"
            else:
                candidates.sort(key=lambda item: item[1].created_at or "", reverse=True)
                tier_name, meta = candidates[0]
                candidate = CandidateExplanation(
                    tier=tier_name,
                    run_id=meta.run_id,
                    created_at=meta.created_at,
                    step_hash=meta.step_hash,
                )
                if meta.fingerprint_payload is None:
                    candidate.note = "no stored fingerprint payload (pre-v2 entry)"
                else:
                    diffs = _diff_payload(
                        meta.fingerprint_payload, fingerprints.payloads[step_id]
                    )
                    candidate.differences = _annotate_parent_diffs(
                        diffs, sorted(upstream.get(step_id, set()))
                    )
                step_expl.candidate = candidate

        explanation.steps.append(step_expl)

    return explanation

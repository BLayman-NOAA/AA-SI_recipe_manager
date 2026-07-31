# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Run-scoped execution context, shared by every executor backend.

:class:`RunContext` is everything a run resolves once up front — the tiered
checkpoint store, the checkpoint policy, per-step hashes, and the three
storage locations (cache, user-facing outputs, scratch). It is built by
:func:`build_run_context` and lives on the client.

:class:`WorkerContext` is the *picklable* subset a distributed task needs to
rebuild that state inside a worker. It deliberately holds no live filesystem
handles: locations travel as strings and are re-parsed by
:meth:`WorkerContext.open_store`, so the same object works for an in-process
thread and a spawned process without a second code path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.checkpoint import (
    PROVENANCE_DIR,
    REGENERATE_MODES,
    CheckpointManager,
    compute_step_fingerprints,
    generate_run_id,
    resolve_checkpoint_policy,
)
from aa_recipe_manager.executor.tiered import (
    CACHE_WRITE_TIERS,
    SURVEY_TIER,
    TieredCheckpointStore,
)
from aa_recipe_manager.storage import StorageLocation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aa_recipe_manager.model.types import (
        CheckpointFormat,
        CheckpointMode,
        PipelineDAG,
    )

EXE_TEMP_DIRNAME = "exe_temp"
DEFAULT_OUTPUTS_DIRNAME = "outputs"


def resolve_temp_dir(
    temp_dir: str | Path | StorageLocation | None,
    cache_loc: StorageLocation | None,
    storage_options: dict[str, Any] | None = None,
) -> StorageLocation | None:
    """Resolve the run-scoped scratch directory (``exe_temp``).

    An explicit ``temp_dir`` wins (local path or fsspec URL). Otherwise it
    follows the cache's scheme: a local cache yields a local ``exe_temp``, a
    remote cache yields a remote ``exe_temp`` under the same prefix. Returns
    ``None`` when neither is available.
    """
    if temp_dir is not None:
        return StorageLocation.parse(temp_dir, storage_options)
    if cache_loc is not None:
        return cache_loc.parent / EXE_TEMP_DIRNAME
    return None


def resolve_outputs_dir(
    outputs_dir: str | Path | StorageLocation | None,
    cache_loc: StorageLocation | None,
    storage_options: dict[str, Any] | None = None,
) -> StorageLocation | None:
    """Resolve the user-facing outputs directory.

    Explicit ``outputs_dir`` wins. Otherwise it defaults to a sibling of the
    checkpoint cache directory named ``outputs`` (e.g. ``recipe_cache`` ->
    ``outputs``), following the cache's scheme. Returns ``None`` when neither
    is available.
    """
    if outputs_dir is not None:
        return StorageLocation.parse(outputs_dir, storage_options)
    if cache_loc is not None:
        return cache_loc.parent / DEFAULT_OUTPUTS_DIRNAME
    return None


def _loc_str(loc: StorageLocation | Path | None) -> str | None:
    """Render a location as a string a worker can re-parse (or ``None``)."""
    if loc is None:
        return None
    if isinstance(loc, StorageLocation):
        return str(loc)
    return os.fspath(loc)


@dataclass(frozen=True)
class WorkerContext:
    """Picklable snapshot a task needs to run a step inside a worker.

    Every field is a plain builtin, a Pydantic model, or ``None`` so the whole
    object survives ``pickle`` for a process backend. ``open_store`` rebuilds
    the :class:`TieredCheckpointStore` on the far side; the result is memoized
    per instance so repeated tasks in one worker do not re-parse the roots.
    """

    dag: PipelineDAG
    pipeline_inputs: dict[str, Any]
    step_hashes: dict[str, str]
    payloads: dict[str, dict[str, Any]]
    run_id: str
    cache_root: str | None
    survey_root: str | None
    write_tier: str
    checkpoint_format: str
    user_cache_dir: str | None
    outputs_dir: str | None
    temp_dir: str | None
    storage_options: dict[str, Any] | None
    recipe_info: dict[str, str]
    force: bool = False
    provenance_ref: str | None = None
    policy: frozenset[str] = frozenset()

    def __getstate__(self) -> dict[str, Any]:
        # A memoized ``_store_cache`` is a live TieredCheckpointStore holding a
        # threading.Lock, which cannot pickle and is inherently per-worker
        # (rebuilt from the roots by ``open_store``). Drop it so this snapshot
        # always survives shipping to a process worker.
        state = self.__dict__.copy()
        state.pop("_store_cache", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Frozen dataclass: bypass the blocking __setattr__ on unpickle.
        self.__dict__.update(state)

    def open_store(self) -> TieredCheckpointStore | None:
        """Rebuild the tiered checkpoint store inside this worker.

        Returns ``None`` when the run has checkpointing disabled, which is the
        same signal the client-side :attr:`RunContext.checkpoints` carries.
        """
        cached = getattr(self, "_store_cache", None)
        if cached is not None:
            return cached
        if self.cache_root is None:
            return None
        user = CheckpointManager(
            self.cache_root,
            self.step_hashes,
            preferred_format=self.checkpoint_format,
            storage_options=self.storage_options,
            payloads=self.payloads,
            run_id=self.run_id,
            recipe_info=self.recipe_info,
        )
        survey: CheckpointManager | None = None
        if self.survey_root is not None:
            survey = CheckpointManager(
                self.survey_root,
                self.step_hashes,
                preferred_format=self.checkpoint_format,
                storage_options=self.storage_options,
                payloads=self.payloads,
                run_id=self.run_id,
                recipe_info=self.recipe_info,
                provenance_ref=self.provenance_ref,
            )
        store = TieredCheckpointStore(
            user=user, survey=survey, write_tier=self.write_tier
        )
        # frozen dataclass: bypass __setattr__ to memoize.
        object.__setattr__(self, "_store_cache", store)
        return store


@dataclass
class RunContext:
    """Everything a run resolves once, before any step executes."""

    dag: PipelineDAG
    pipeline_inputs: dict[str, Any]
    run_id: str

    checkpoints: TieredCheckpointStore | None = None
    policy: set[str] = field(default_factory=set)
    step_hashes: dict[str, str] = field(default_factory=dict)
    payloads: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Raw parsed ``user_cache_dir`` (set even when ``no_checkpoints``).
    cache_loc: StorageLocation | None = None
    #: Checkpoint-gated cache location (``None`` when ``no_checkpoints``).
    output_loc: StorageLocation | None = None
    outputs_loc: StorageLocation | None = None
    temp_loc: StorageLocation | None = None
    survey_loc: StorageLocation | None = None

    force: bool = False
    skip_sinks: bool = False
    regenerate: str = "auto"
    storage_options: dict[str, Any] | None = None
    cache_write_tier: str = "user"
    checkpoint_format: str = "zarr"
    provenance_ref: str | None = None
    recipe_info: dict[str, str] = field(default_factory=dict)

    def worker_context(self) -> WorkerContext:
        """Project this context onto its picklable, worker-side subset."""
        return WorkerContext(
            dag=self.dag,
            pipeline_inputs=dict(self.pipeline_inputs),
            step_hashes=dict(self.step_hashes),
            payloads=dict(self.payloads),
            run_id=self.run_id,
            cache_root=_loc_str(self.output_loc),
            survey_root=_loc_str(self.survey_loc),
            write_tier=self.cache_write_tier,
            checkpoint_format=str(self.checkpoint_format),
            user_cache_dir=_loc_str(self.output_loc),
            outputs_dir=_loc_str(self.outputs_loc),
            temp_dir=_loc_str(self.temp_loc),
            storage_options=dict(self.storage_options)
            if self.storage_options
            else None,
            recipe_info=dict(self.recipe_info),
            force=self.force,
            provenance_ref=self.provenance_ref,
            policy=frozenset(self.policy),
        )


def build_run_context(
    dag: PipelineDAG,
    *,
    inputs: dict[str, Any] | None = None,
    user_cache_dir: str | Path | None = None,
    force: bool = False,
    no_checkpoints: bool = False,
    skip_sinks: bool = False,
    regenerate: str = "auto",
    outputs_dir: str | Path | None = None,
    temp_dir: str | Path | None = None,
    checkpoint_mode: CheckpointMode | str | None = None,
    checkpoint_steps: Iterable[str] | None = None,
    checkpoint_format: CheckpointFormat | str | None = None,
    storage_options: dict[str, Any] | None = None,
    survey_cache_dir: str | Path | None = None,
    cache_write_tier: str = "user",
) -> RunContext:
    """Validate run arguments and resolve the run-scoped execution state.

    Performs the one-time work every backend needs: argument validation, cache
    location parsing, step fingerprinting, construction of the user (and
    optionally survey) :class:`CheckpointManager`, the
    :class:`TieredCheckpointStore`, the checkpoint policy, and the outputs /
    scratch directories.

    A curated (``cache_write_tier="survey"``) run publishes its provenance next
    to the survey cache *before* any step runs, so no sidecar can reference a
    not-yet-written file.
    """
    from aa_recipe_manager.provenance.recorder import ProvenanceRecorder, to_json

    if regenerate not in REGENERATE_MODES:
        raise ValueError(
            f"regenerate must be one of {REGENERATE_MODES}, got {regenerate!r}"
        )
    if cache_write_tier not in CACHE_WRITE_TIERS:
        raise ValueError(
            f"cache_write_tier must be one of {CACHE_WRITE_TIERS}, "
            f"got {cache_write_tier!r}"
        )
    if cache_write_tier == SURVEY_TIER and survey_cache_dir is None:
        raise ValueError(
            "cache_write_tier='survey' requires a survey cache root "
            "(config key survey_cache_dir or --survey-cache-dir)"
        )
    if survey_cache_dir is not None and user_cache_dir is None:
        raise ValueError(
            "survey_cache_dir requires user_cache_dir (the user cache root); "
            "the user tier holds side-effect markers even for curated runs"
        )

    pipeline_inputs = dict(inputs or {})
    run_id = generate_run_id()

    # Parse the cache location once; a local path stays local-behaving, an
    # fsspec URL (gs://, ...) routes through StorageLocation.
    cache_loc: StorageLocation | None = (
        StorageLocation.parse(user_cache_dir, storage_options)
        if user_cache_dir is not None
        else None
    )

    ctx = RunContext(
        dag=dag,
        pipeline_inputs=pipeline_inputs,
        run_id=run_id,
        cache_loc=cache_loc,
        force=force,
        skip_sinks=skip_sinks,
        regenerate=regenerate,
        storage_options=storage_options,
        cache_write_tier=cache_write_tier,
    )

    if cache_loc is not None and not no_checkpoints:
        ctx.output_loc = cache_loc
        # Resolve effective format: call-site arg > recipe hint > default "zarr"
        hints = dag.recipe.execution
        effective_format: str = (
            checkpoint_format
            or (hints.checkpoint_format if hints is not None else None)
            or "zarr"
        )
        if cache_write_tier == SURVEY_TIER and str(effective_format) == "pickle":
            raise ValueError(
                "checkpoint_format='pickle' is not eligible for the shared "
                "survey cache (pickles are not portable across "
                "environments); use 'zarr' instead"
            )
        ctx.checkpoint_format = str(effective_format)
        fingerprints = compute_step_fingerprints(
            dag, pipeline_inputs, storage_options=storage_options
        )
        ctx.step_hashes = fingerprints.hashes
        ctx.payloads = dict(fingerprints.payloads)
        recipe_info = {"name": dag.recipe.name, "version": dag.recipe.version}
        ctx.recipe_info = recipe_info
        user_manager = CheckpointManager(
            cache_loc,
            fingerprints.hashes,
            preferred_format=effective_format,
            storage_options=storage_options,
            payloads=fingerprints.payloads,
            run_id=run_id,
            recipe_info=recipe_info,
        )
        survey_manager: CheckpointManager | None = None
        if survey_cache_dir is not None:
            survey_loc = StorageLocation.parse(survey_cache_dir, storage_options)
            ctx.survey_loc = survey_loc
            # Curated runs publish their provenance (environment, deps,
            # inputs) next to the survey cache at run START — the environment
            # is fully known upfront, and writing it first means sidecars never
            # reference a not-yet-written file. Every sidecar this run writes
            # carries the ref.
            provenance_ref: str | None = None
            if cache_write_tier == SURVEY_TIER:
                provenance_ref = f"{PROVENANCE_DIR}/{dag.recipe.name}@{run_id}.json"
                prov = ProvenanceRecorder.capture(dag, inputs=pipeline_inputs or None)
                prov_loc = survey_loc / provenance_ref
                prov_loc.parent.mkdir()
                prov_loc.write_text(to_json(prov))
            ctx.provenance_ref = provenance_ref
            survey_manager = CheckpointManager(
                survey_loc,
                fingerprints.hashes,
                preferred_format=effective_format,
                storage_options=storage_options,
                payloads=fingerprints.payloads,
                run_id=run_id,
                recipe_info=recipe_info,
                provenance_ref=provenance_ref,
            )
        ctx.checkpoints = TieredCheckpointStore(
            user=user_manager,
            survey=survey_manager,
            write_tier=cache_write_tier,
        )
        ctx.policy = resolve_checkpoint_policy(
            dag,
            mode=checkpoint_mode,
            extra_step_ids=set(checkpoint_steps or ()),
        )

    # User-facing outputs (images, logs) live in a separate tree from the
    # checkpoint cache. Use the raw cache location (not the checkpoint-gated
    # output_loc) so outputs_dir resolves correctly even when no_checkpoints.
    ctx.outputs_loc = resolve_outputs_dir(outputs_dir, cache_loc, storage_options)
    # An explicit temp_dir is always honored; the sibling-of-cache default
    # follows the checkpoint-gated location (so no_checkpoints keeps the legacy
    # behavior of aa_si_utils falling back to a system temp dir).
    ctx.temp_loc = resolve_temp_dir(temp_dir, ctx.output_loc, storage_options)
    return ctx

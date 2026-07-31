# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Pydantic data models for all recipe manager data structures.

All models are pure data containers with no behavior beyond validation.
They are the shared language that all other layers in the package depend on.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Recipe format versions that this package can parse.
SUPPORTED_SCHEMA_VERSIONS = {"1"}

#: How a path-typed input/param folds its target into the step cache hash.
#: ``off`` (default) keeps only the path string; the other modes fold a
#: location-independent listing of the folder's entries and *drop the path*, so
#: the same files under a moved/renamed folder hash identically. Signals:
#: ``names`` (basenames only), ``size`` (names + per-file size, identical local
#: and remote), ``checksum`` (names + content hash: object-store metadata
#: remotely, a byte read locally), ``auto`` (names + size locally, names +
#: checksum remotely — the best cheap signal per backend). All non-``off`` modes
#: exclude mtime.
FingerprintMode = Literal["off", "auto", "names", "size", "checksum"]

#: Declaration blocks reject unknown keys. A key pydantic merely ignored would
#: read as accepted while doing nothing, which is how a recipe still using the
#: removed ``fingerprint_contents`` would silently fall back to path-only cache
#: keying instead of the content fingerprinting it asked for.
_STRICT_DECLARATION = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Shared building-block models
# ---------------------------------------------------------------------------


class PortDeclaration(BaseModel):
    """A single input or output port on a spec."""

    model_config = _STRICT_DECLARATION

    type: str
    description: str | None = None
    required: bool = True
    default: Any = None
    many: bool = False
    expected_variables: list[str] | None = None
    expected_coords: list[str] | None = None
    provenance_role: str | None = None
    """Marks a port whose runtime value carries a named provenance signal.
    ``"raw_file_list"`` tags the list of raw input files a run read, so the
    executor can harvest it into provenance (see ``build_raw_inputs_record``).
    """


class ParamDeclaration(BaseModel):
    """A parameter accepted by a spec."""

    model_config = _STRICT_DECLARATION

    type: str | None = None
    units: str | None = None
    description: str | None = None
    default: Any | None = None
    required: bool = True
    constraints: dict[str, Any] | None = None
    fingerprint_mode: FingerprintMode = "off"
    """How a ``type=='path'`` param folds its target into the step cache key.
    ``off`` (default) hashes only the path string. Non-``off`` modes fold a
    location-independent folder listing and drop the path — see
    :data:`FingerprintMode`. Use on stable read-only inputs (e.g. a raw folder).
    """


class Dependency(BaseModel):
    """An install-time dependency for an implementation."""

    name: str
    version: str  # version range, e.g. ">=0.9,<1.0"
    source: Literal["pypi", "git", "local"]
    url: str | None = None


# ---------------------------------------------------------------------------
# Custom step models
# ---------------------------------------------------------------------------


class CustomSpec(BaseModel):
    """Inline spec and implementation for a custom (unregistered) step.

    When `extends` is set the custom spec inherits inputs, outputs, and params
    from the referenced registry spec, overriding only the declared fields.
    """

    extends: str | None = None
    description: str
    inputs: dict[str, PortDeclaration] | None = None
    outputs: dict[str, PortDeclaration] | None = None
    params: dict[str, ParamDeclaration] | None = None
    callable_path: str
    dependency: Dependency | None = None
    param_map: dict[str, str] | None = None
    output_map: dict[str, str] | None = None
    cache_key: str | None = None
    """Stable cache identity for this custom step. When set, renaming the
    step's callable (or editing its description) stays cache-neutral; bump
    :attr:`version` when the computation itself changes."""
    version: str | None = None
    """Explicit behavior version folded into the step's cache hash."""


# ---------------------------------------------------------------------------
# Execution hint models
# ---------------------------------------------------------------------------


class SweepDeclaration(BaseModel):
    """Parameter-parallel execution for a step.

    Recipes may declare a sweep in the flat form documented in design.md §1.7
    (param names directly under ``sweep:`` alongside an optional ``mode``)::

        sweep:
          min_cluster_size: [500, 1000, 1400, 2000]
          mode: zip

    or the explicit nested form (``param_lists:``). The validator below
    normalizes the flat form into ``param_lists``.
    """

    param_lists: dict[str, list[Any]]
    mode: Literal["zip", "grid"] = "zip"

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_form(cls, data: Any) -> Any:
        """Fold flat ``sweep: {param: [...], mode: ...}`` into ``param_lists``."""
        if not isinstance(data, dict) or "param_lists" in data:
            return data
        mode = data.get("mode", "zip")
        param_lists = {k: v for k, v in data.items() if k != "mode"}
        return {"param_lists": param_lists, "mode": mode}


class StepExecutionHints(BaseModel):
    """Per-step executor overrides, merged with pipeline-level hints."""

    dask_config: dict[str, Any] | None = None
    prefect_config: dict[str, Any] | None = None


CheckpointMode = Literal["eager", "explicit", "terminal", "none"]
"""Recipe/run-level checkpoint policy.

``eager`` writes a checkpoint after every successful step (default).
``explicit`` writes only for steps marked ``checkpoint: always`` or ``checkpoint: save``.
``terminal`` writes only for steps with no downstream consumers.
``none`` writes nothing (equivalent to ``--no-checkpoints``).
A per-step ``Step.checkpoint`` value overrides the mode when set.
"""

CheckpointFormat = Literal["zarr", "netcdf", "pickle"]
"""Serialization format used when writing checkpoints.

``zarr``    – Zarr v2 store (default).  Best for chunked/dask arrays.
``netcdf``  – NetCDF4 file via xarray / echopype APIs.
``pickle``  – Python pickle; last-resort fallback for arbitrary objects.
JSON-serialisable values are always stored as ``.json`` regardless of this
setting.
"""


class ExecutionHints(BaseModel):
    """Pipeline-level annotations that influence execution behavior."""

    split_after: str | None = None
    parallel_branches: bool = False
    output_format: str | None = None
    executor: str | None = None
    checkpoint_mode: CheckpointMode | None = None
    checkpoint_format: CheckpointFormat | None = None
    cache_epoch: str | None = None
    """Salt folded into every step's cache hash. Bumping it deliberately
    invalidates all cached results for this recipe (e.g. after a dependency
    bug is found or an op behavior change shipped without a version bump).
    Must be identical for the curator and every consumer of a shared cache,
    which is why it lives in the recipe rather than per-user config."""
    dask_config: dict[str, Any] | None = None
    prefect_config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class Step(BaseModel):
    """A single step in the pipeline DAG."""

    id: str
    op: str
    description: str | None = None
    inputs: dict[str, str | list[str]] = {}
    params: dict[str, Any] = {}
    depends_on: list[str] | None = None
    implementation_override: str | None = None
    custom_spec: CustomSpec | None = None
    map_over: str | None = None
    collect: str | None = None
    sweep: SweepDeclaration | None = None
    execution: StepExecutionHints | None = None
    checkpoint: Literal["always", "never", "save"] | None = None
    regenerate: Literal["if-missing", "always", "never"] | None = None
    """Whether to regenerate this step's user-facing artifacts (plots, logs,
    reports) when they are absent from the current run's outputs directory.

    ``if-missing`` re-runs the step only when a recorded artifact is missing;
    ``always`` re-runs every run; unset / ``never`` keeps the cached-skip
    behavior. Applies to sink steps (cheap: only the render re-runs, upstream
    loads from cache) and to data steps (the step recomputes, since the artifact
    is a side effect of running). The run-level ``--regenerate`` mode overrides
    this per-step value."""


class IncludeBlock(BaseModel):
    """Metadata for steps contributed by an included recipe."""

    source: str
    step_ids: list[str]


# ---------------------------------------------------------------------------
# Recipe (top-level file model)
# ---------------------------------------------------------------------------


class InputDeclaration(BaseModel):
    """A pipeline-level input slot."""

    model_config = _STRICT_DECLARATION

    type: str
    description: str | None = None
    default: Any | None = None
    required: bool = True
    fingerprint_mode: FingerprintMode = "off"
    """How a ``type=='path'`` pipeline input folds its target into the cache key
    of every step that references it. ``off`` (default) hashes only the path
    string. Non-``off`` modes fold a location-independent folder listing and
    drop the path — see :data:`FingerprintMode`.
    """
    provenance_role: str | None = None
    """Marks a pipeline input carrying a named provenance signal.
    ``"raw_file_list"`` tags a directly-supplied raw file list so a recipe that
    skips the reader step still records what it read.
    """

    @model_validator(mode="after")
    def set_required_from_default(self) -> InputDeclaration:
        if self.default is not None:
            if "required" in self.model_fields_set and self.required is True:
                # The caller explicitly set required=True while also providing a
                # default value, which is contradictory.
                raise ValueError(
                    "'required' cannot be True when a 'default' value is provided."
                )
            self.required = False
        return self


class OutputDeclaration(BaseModel):
    """A pipeline-level output mapped to a specific step's output port."""

    step_id: str
    output_name: str
    description: str | None = None
    save_to: Path | None = None


class Recipe(BaseModel):
    """The top-level container parsed from a YAML/TOML recipe file."""

    name: str
    version: str
    description: str | None = None
    author: str | None = None
    inputs: dict[str, InputDeclaration] = {}
    steps: list[Step] = Field(min_length=1)
    outputs: dict[str, OutputDeclaration] | None = None
    execution: ExecutionHints | None = None
    include_blocks: list[IncludeBlock] = []
    schema_version: str

    @model_validator(mode="after")
    def check_schema_version(self) -> Recipe:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version '{self.schema_version}'. "
                f"This version of aa-recipe-manager only supports: "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return self


# ---------------------------------------------------------------------------
# Registry models
# ---------------------------------------------------------------------------


class Spec(BaseModel):
    """Scientific step specification, the contract for an operation."""

    op: str
    description: str
    category: str | None = None
    sink: bool = False
    inputs: dict[str, PortDeclaration] = {}
    outputs: dict[str, PortDeclaration] = {}
    params: dict[str, ParamDeclaration] = {}
    cache_key: str | None = None
    """Stable cache identity for this op (defaults to ``op`` at fingerprint
    time). Set once when the spec is created and **never auto-updated** —
    renaming the op or moving its callable then stays cache-neutral. Bump
    :attr:`version` instead when the op's *behavior* changes."""
    version: str | None = None
    """Explicit behavior version folded into step cache hashes. Bump this
    (e.g. ``"2"``) when the op's computation changes so cached results from
    the old behavior are invalidated."""


class Implementation(BaseModel):
    """Maps a spec to a real callable."""

    op: str
    key: str
    callable_path: str
    dependency: Dependency | None = None
    param_map: dict[str, str] = {}
    output_map: dict[str, str] = {}
    default: bool = False
    tested_versions: list[str] | None = None
    setup: str | None = None
    teardown: str | None = None
    version: str | None = None
    """Explicit behavior version for *this implementation*, folded into step
    cache hashes. Bump when this implementation's computation changes without
    a spec-level contract change (siblings keep their cached results)."""


# ---------------------------------------------------------------------------
# DAG models
# ---------------------------------------------------------------------------


class DAGNode(BaseModel):
    """A resolved step in the pipeline graph."""

    step: Step
    spec: Spec
    implementation: Implementation | None = None
    resolved_params: dict[str, Any] = {}
    is_mapped: bool = False
    is_collector: bool = False
    is_swept: bool = False
    map_source: str | None = None
    collect_source: str | None = None
    sweep_declaration: SweepDeclaration | None = None


class DAGEdge(BaseModel):
    """A data dependency between two steps."""

    source_step_id: str
    source_output: str
    target_step_id: str
    target_input: str


class PipelineDAG(BaseModel):
    """The fully resolved, validated pipeline graph."""

    recipe: Recipe
    nodes: dict[str, DAGNode] = {}
    edges: list[DAGEdge] = []
    topological_order: list[str] = []


# ---------------------------------------------------------------------------
# Provenance models
# ---------------------------------------------------------------------------


class ResolvedStepInfo(BaseModel):
    """Per-step provenance: which implementation was actually used."""

    op: str
    implementation_key: str
    callable_path: str
    package_name: str
    installed_version: str
    params_used: dict[str, Any] = {}


class RawFileEntry(BaseModel):
    """One raw input file recorded for provenance: basename + size (bytes).

    Deliberately carries no directory path — identity is the file and its data,
    not where it lived. ``size`` is ``None`` when it could not be determined.
    """

    name: str
    size: int | None = None


class RawInputsRecord(BaseModel):
    """The set of raw input files a run read, recorded for data provenance.

    Populated from the reader step's resolved output (``source="resolved"``) or,
    when the reader was a cache hit / pruned on resume, inherited from the
    producing run's checkpoint sidecar (``source="inherited"``, with
    ``origin_run_id`` naming that run — e.g. a curated survey run whose cache a
    user extended).
    """

    files: list[RawFileEntry] = []
    count: int = 0
    digest: str = ""  # sha256 over the sorted (name, size) pairs
    source: Literal["resolved", "inherited"] = "resolved"
    producing_step: str | None = None  # "<step_id>.<port>" or "pipeline_input:<name>"
    origin_run_id: str | None = None  # set only when inherited


class Provenance(BaseModel):
    """Captured runtime environment and execution details for a pipeline run."""

    recipe_hash: str
    recipe_name: str
    recipe_version: str
    timestamp: datetime  # timezone-aware UTC datetime
    python_version: str
    python_version_number: str  # short semver e.g. "3.10.4"
    os_info: str
    inputs: dict[str, Any] = {}  # pipeline-level inputs supplied at runtime
    resolved_steps: dict[str, ResolvedStepInfo] = {}
    resolved_dependencies: dict[str, Any] = {}  # package -> {installed_version, source, url}
    raw_inputs: RawInputsRecord | None = None  # raw files this run read

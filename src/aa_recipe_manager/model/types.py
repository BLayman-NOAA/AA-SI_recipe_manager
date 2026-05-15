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

from pydantic import BaseModel, Field, model_validator

# Recipe format versions that this package can parse.
SUPPORTED_SCHEMA_VERSIONS = {"1"}


# ---------------------------------------------------------------------------
# Shared building-block models
# ---------------------------------------------------------------------------


class PortDeclaration(BaseModel):
    """A single input or output port on a spec."""

    type: str
    description: str | None = None
    required: bool = True
    default: Any = None
    many: bool = False
    expected_variables: list[str] | None = None
    expected_coords: list[str] | None = None


class ParamDeclaration(BaseModel):
    """A parameter accepted by a spec."""

    type: str | None = None
    units: str | None = None
    description: str | None = None
    default: Any | None = None
    required: bool = True
    constraints: dict[str, Any] | None = None


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


# ---------------------------------------------------------------------------
# Execution hint models
# ---------------------------------------------------------------------------


class SweepDeclaration(BaseModel):
    """Parameter-parallel execution for a step."""

    param_lists: dict[str, list[Any]]
    mode: Literal["zip", "grid"] = "zip"


class StepExecutionHints(BaseModel):
    """Per-step executor overrides, merged with pipeline-level hints."""

    dask_config: dict[str, Any] | None = None
    prefect_config: dict[str, Any] | None = None


class ExecutionHints(BaseModel):
    """Pipeline-level annotations that influence execution behavior."""

    split_after: str | None = None
    parallel_branches: bool = False
    output_format: str | None = None
    executor: str | None = None
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


class IncludeBlock(BaseModel):
    """Metadata for steps contributed by an included recipe."""

    source: str
    step_ids: list[str]


# ---------------------------------------------------------------------------
# Recipe (top-level file model)
# ---------------------------------------------------------------------------


class InputDeclaration(BaseModel):
    """A pipeline-level input slot."""

    type: str
    description: str | None = None
    default: Any | None = None
    required: bool = True

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


class Provenance(BaseModel):
    """Captured runtime environment and execution details for a pipeline run."""

    recipe_hash: str
    recipe_name: str
    recipe_version: str
    timestamp: datetime  # timezone-aware UTC datetime
    python_version: str
    os_info: str
    resolved_steps: dict[str, ResolvedStepInfo] = {}
    resolved_dependencies: dict[str, str] = {}  # package -> installed version

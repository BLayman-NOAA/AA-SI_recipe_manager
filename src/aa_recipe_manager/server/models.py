# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Request and response bodies of the client server API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestBody(BaseModel):
    """Base for request bodies: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    dev: bool
    state_dir: str
    docs_url: str


class RecipeEntryModel(BaseModel):
    name: str
    description: str | None
    version: str | None
    path: str
    root: str
    relative: str
    modified: str
    included_by: list[str]
    error: str | None = None


class RecipesResponse(BaseModel):
    roots: list[str]
    recipes: list[RecipeEntryModel]


class OpenSessionRequest(RequestBody):
    path: str


class SessionSummary(BaseModel):
    id: str
    title: str
    origin: str
    created: str


class FileRef(BaseModel):
    path: str
    origin: str
    editable: bool


class InputField(BaseModel):
    name: str
    type: str | None
    description: str | None
    default: Any
    required: bool
    value: Any
    source: Literal["unset", "default", "config", "session"]


class InputsBlock(BaseModel):
    declared: list[InputField]
    values: dict[str, Any]


class RunConfigBlock(BaseModel):
    user_cache_dir: str | None
    survey_cache_dir: str | None
    outputs_dir: str | None
    temp_dir: str | None
    executor: str | None
    dask_scheduler: str | None
    dask_workers: int | None
    storage_options: dict[str, Any] | None
    source: str | None
    overrides: dict[str, Any]


class Problem(BaseModel):
    severity: Literal["error", "warning"]
    message: str
    step: str | None


class ValidationBlock(BaseModel):
    valid: bool
    problems: list[Problem]


class SessionResponse(BaseModel):
    id: str
    created: str
    title: str
    origin: str
    read_only: bool
    version: int
    files: list[FileRef]
    inputs: InputsBlock
    run_config: RunConfigBlock
    graph: dict[str, Any]
    validation: ValidationBlock


class FileTextResponse(BaseModel):
    path: str
    text: str


class SetInputsRequest(RequestBody):
    values: dict[str, Any]


class SetRunConfigRequest(RequestBody):
    """Run settings the session overrides; a missing or null field is not set."""

    user_cache_dir: str | None = None
    survey_cache_dir: str | None = None
    outputs_dir: str | None = None
    temp_dir: str | None = None
    executor: Literal["sequential", "dask", "prefect"] | None = None
    dask_scheduler: str | None = None
    dask_workers: int | None = None
    storage_options: dict[str, Any] | None = None


RunStatus = Literal[
    "queued", "starting", "running", "completed", "failed", "cancelled", "interrupted"
]


class StartRunRequest(RequestBody):
    """What to run: everything, up to the targets, or the targets alone."""

    mode: Literal["all", "to", "only"] = "all"
    targets: list[str] = []
    force: bool = False
    force_steps: list[str] = []


class RunSummary(BaseModel):
    executed: int = 0
    skipped: int = 0
    pruned: int = 0
    untargeted: int = 0
    failed_step: str | None = None
    error_type: str | None = None
    message: str | None = None
    seconds: float | None = None


class RunResponse(BaseModel):
    schema_: int = Field(1, alias="schema")
    id: str
    session: str
    created: str
    started: str | None
    finished: str | None
    status: RunStatus
    pid: int | None
    pid_created: float | None
    exit_code: int | None
    mode: Literal["all", "to", "only"]
    targets: list[str]
    force: bool
    force_steps: list[str]
    executor: str | None
    cancel_requested_at: str | None
    stop_requested_at: str | None = None
    killed: bool
    summary: RunSummary | None
    manifest: str | None
    outputs_dir: str | None
    log: str
    cwd: str | None = None
    last_seq: int


class StepPreviewModel(BaseModel):
    step_id: str
    step_hash: str
    held_by: list[str]
    planned: str
    tier: str | None
    checkpoint: str
    pinned: bool
    recipe_checkpoint: str | None
    fanned_out: bool
    instances: int | None
    instances_held: dict[str, int]
    note: str | None


class PreviewResponse(BaseModel):
    recipe: str
    version: str
    mode: str
    targets: list[str]
    force_steps: list[str]
    tiers: dict[str, str]
    write_tier: str | None
    steps: list[StepPreviewModel]
    blockers: list[str]
    warnings: list[str]


class ArtifactModel(BaseModel):
    output: str
    url: str
    format: str
    tier: str
    step_hash: str
    instance_index: int | None
    item: Any
    created_at: str | None
    run_id: str | None
    size_bytes: int | None


class EchogramBlock(BaseModel):
    available: bool
    reason: str | None
    resolution: dict[str, Any] | None


class StepArtifactsResponse(BaseModel):
    step_id: str
    step_hash: str
    fanned_out: bool
    held_by: list[str]
    instances: int | None
    found: int
    marker: bool
    note: str | None
    artifacts: list[ArtifactModel]
    echogram: EchogramBlock


class LogEntry(BaseModel):
    seq: int
    time: float
    step: str | None
    stream: str
    text: str
    buffered: bool
    instance: Any = None


class LogResponse(BaseModel):
    run_id: str
    step: str | None
    stream: str | None
    entries: list[LogEntry]
    truncated: bool


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Any = None
    step_id: str | None = None

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Request and response bodies of the client server API."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


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
    dirty: bool = False


class DiskConflict(BaseModel):
    """A file changed on disk that the last sync did not reload.

    ``error`` is None when the session has unsaved edits to the file, else
    why its disk text was not taken.
    """

    path: str
    error: str | None
    disk_sha256: str


class HistoryBlock(BaseModel):
    """The undo and redo state; ``seq`` is the newest undoable entry's, else 0."""

    can_undo: bool
    can_redo: bool
    undo_kind: str | None
    redo_kind: str | None
    seq: int
    note: str | None


class InputField(BaseModel):
    """A recipe input and where its value comes from.

    ``required`` is True for an input with no default that a run must be
    given: one declared ``required: true``, or one that feeds a required
    input port or param.
    ``source`` is ``missing`` for a required input with no value, or with a
    null one, and ``unset`` for any other input with none, whose steps then
    use their own defaults.
    """

    name: str
    type: str | None
    description: str | None
    default: Any
    required: bool
    value: Any
    source: Literal["unset", "missing", "default", "config", "session"]

    @field_serializer("default", "value")
    def _values(self, value: Any) -> Any:
        return yaml_nonfinite(value)


class InputsBlock(BaseModel):
    """The declared inputs, and the values shown; NaN is sent as ``.nan``."""

    declared: list[InputField]
    values: dict[str, Any]

    @field_serializer("values")
    def _values(self, value: dict[str, Any]) -> dict[str, Any]:
        return yaml_nonfinite(value)


class RunConfigContents(BaseModel):
    """A run config file as written: its ``inputs:`` and its run settings.

    ``settings`` lists only the settings the file has, null ones included.
    """

    inputs: dict[str, Any]
    settings: dict[str, Any]

    @field_serializer("inputs", "settings")
    def _values(self, value: dict[str, Any]) -> dict[str, Any]:
        return yaml_nonfinite(value)


class RunConfigBlock(BaseModel):
    """The run settings a session's runs get, and the config file they come from.

    The setting fields are the file's values with the session's
    ``overrides`` over them. ``source`` is the file loaded, null when none was
    found or it did not load. ``path`` is the file in use, also when it does
    not load (a chosen file), and ``chosen`` the file chosen for the session,
    null when it is found as the CLI finds it. ``candidate`` is the recipe's
    own ``<stem>.config.yaml``, which Create Config writes. ``error`` says why
    the file did not load. ``contents`` is the file as written, null when
    there is no file or it is not YAML. ``shared_with`` lists the other recipes
    in the folder whose runs use the same file, and ``unused_inputs`` the
    file's inputs the recipe does not declare.
    """

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
    path: str | None
    chosen: str | None
    candidate: str
    error: str | None
    contents: RunConfigContents | None
    shared_with: list[str]
    unused_inputs: list[str]

    @field_serializer("storage_options")
    def _options(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return yaml_nonfinite(value)

    @field_serializer("overrides")
    def _overrides(self, value: dict[str, Any]) -> dict[str, Any]:
        return yaml_nonfinite(value)


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
    dirty: list[str]
    conflicts: list[DiskConflict]
    history: HistoryBlock

    @field_serializer("graph")
    def _graph(self, value: dict[str, Any]) -> dict[str, Any]:
        return yaml_nonfinite(value)


class FileTextResponse(BaseModel):
    path: str
    text: str


class EditRequest(RequestBody):
    """Operations applied in order; ``version`` set means it must be current."""

    operations: list[dict[str, Any]]
    version: int | None = None


class HistoryRequest(RequestBody):
    version: int | None = None


class ReloadRequest(RequestBody):
    """Files to take from disk; null means every file that changed there."""

    files: list[str] | None = None
    version: int | None = None


class SaveRequest(RequestBody):
    overwrite: bool = False


class SaveAsRequest(RequestBody):
    """A Save As; a relative ``path`` is taken from the recipe's folder."""

    path: str
    keep_paths: bool = False
    copy_config: bool = True
    overwrite: bool = False
    dry_run: bool = False


class HistoryEntryModel(BaseModel):
    """One history entry.

    JSON has no NaN or infinity, so a non finite number in ``op`` or
    ``inverse`` (a file can hold ``.nan``) is sent as its YAML spelling,
    ``.nan``, ``.inf`` or ``-.inf``, as in the session's graph.
    """

    seq: int
    time: str
    op: dict[str, Any]
    inverse: dict[str, Any]
    documents: list[str]

    @field_serializer("op", "inverse")
    def _operation(self, value: dict[str, Any]) -> dict[str, Any]:
        return yaml_nonfinite(value)


class EditResponse(BaseModel):
    """The session after an edit, undo, redo or reload.

    ``changed_hashes`` lists the steps whose hash changed, in run order, then
    the steps removed; null when the hashes could not be computed.
    """

    session: SessionResponse
    changed: bool
    changed_hashes: list[str] | None
    entries: list[HistoryEntryModel]


class SyncResponse(BaseModel):
    session: SessionResponse
    reloaded: list[str]
    conflicts: list[DiskConflict]
    entries: list[HistoryEntryModel]


class SaveResponse(BaseModel):
    """The session after a save.

    ``reloaded`` lists the files the sync before the save took from disk.
    """

    session: SessionResponse
    written: list[str]
    reloaded: list[str]


class RewriteModel(BaseModel):
    where: str
    old: str
    new: str
    absolute: bool


class SaveAsResultModel(BaseModel):
    root: str
    written: list[str]
    rewrites: dict[str, list[RewriteModel]]
    absolute: list[str]
    config: str | None
    rehashed: list[str] | None
    existing: list[str]
    dry_run: bool


class SaveAsResponse(BaseModel):
    session: SessionResponse
    result: SaveAsResultModel


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


class ChooseConfigRequest(RequestBody):
    """The run config file to use, as ``--config``; null finds it as the CLI does.

    A relative ``path`` is taken from the recipe's folder.
    """

    path: str | None
    version: int | None = None


class CreateConfigRequest(RequestBody):
    version: int | None = None


class SaveConfigRequest(RequestBody):
    """Edits to the run config file in use, written keeping its comments.

    ``inputs`` is the file's whole ``inputs:`` after the save: names left out
    are removed, and null leaves the inputs as they are. ``settings`` writes
    each run setting it names; a null one is written as null when the file
    has it and not added when it does not. Settings left out are kept.

    ``scope`` ``"recipe"`` writes the edits to a copy of the file at
    ``<recipe stem>.config.yaml`` beside the recipe instead, which the
    recipe then uses for every setting; ``"shared"`` edits the file in use.
    """

    inputs: dict[str, Any] | None = None
    settings: SetRunConfigRequest = Field(default_factory=SetRunConfigRequest)
    scope: Literal["shared", "recipe"] = "shared"
    version: int | None = None


class SaveConfigResponse(BaseModel):
    """The session after a config save.

    ``changed`` is False when the file already held the values.
    ``shared_with`` lists the other recipes in the folder the file applies
    to. ``changed_hashes`` lists the steps whose hash changed, in run order,
    then the steps removed, as for an edit; null when the hashes could not be
    computed. ``cache_changed`` is True when the file's ``user_cache_dir``,
    ``survey_cache_dir`` or ``storage_options`` changed, which can move a step
    to another cache tier without changing its hash.
    """

    session: SessionResponse
    changed: bool
    shared_with: list[str]
    changed_hashes: list[str] | None
    cache_changed: bool


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
    recipe: str | None = None
    last_seq: int


class StartRunResponse(RunResponse):
    """A run just started; ``reloaded`` lists the files taken from disk first."""

    reloaded: list[str]


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


def yaml_nonfinite(value: Any) -> Any:
    """A value with each NaN or infinity replaced by its YAML spelling.

    Without it pydantic would send null, which reads as "no value".
    """
    if isinstance(value, float) and not math.isfinite(value):
        return ".nan" if math.isnan(value) else ".inf" if value > 0 else "-.inf"
    if isinstance(value, dict):
        return {key: yaml_nonfinite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [yaml_nonfinite(item) for item in value]
    return value

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Request and response bodies of the client server API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Any = None
    step_id: str | None = None

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Edit operations from ``aa-recipe edit`` option values.

Pure functions: each raises ValueError with the option's name for a value it
cannot read, and the command turns that into its error exit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from aa_recipe_manager.editing.operations import (
    CHECKPOINT_VALUES,
    Operation,
    ReplaceText,
    SetCheckpoint,
    SetIncludeOverride,
    SetParam,
    SetRecipeInput,
    UnsetIncludeOverride,
    UnsetParam,
    _finite,
    operation_from_dict,
)

if TYPE_CHECKING:
    from aa_recipe_manager.editing.workspace import RecipeWorkspace

#: The ``--checkpoint`` values; ``none`` removes the key.
CHECKPOINT_CHOICES = (*(v for v in CHECKPOINT_VALUES if v is not None), "none")


def split_assignment(text: str, option: str, form: str) -> tuple[str, str]:
    """Split ``KEY=VALUE`` at the first ``=``, stripping both sides.

    Raises:
        ValueError: There is no ``=`` or no key.
    """
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"{option} must be in {form} format, got: {text!r}")
    return key.strip(), value.strip()


def yaml_value(text: str, option: str) -> Any:
    """Read a VALUE as YAML: ``5`` is a number, a bare word a string.

    An empty VALUE is null; ``""`` is the empty string. A date or time stays
    the string typed, since recipe inputs take times as strings.

    Raises:
        ValueError: The text is not valid YAML.
    """
    try:
        value = YAML(typ="safe", pure=True).load(text)
    except YAMLError as exc:
        raise ValueError(f"{option}: VALUE {text!r} is not valid YAML: {exc}") from exc
    return text if isinstance(value, (date, datetime)) else value


def finite_value(text: str, option: str) -> Any:
    """Read a VALUE as ``yaml_value`` does, refusing NaN and infinity.

    Raises:
        ValueError: The text is not valid YAML, or holds NaN or infinity,
            which operations refuse too.
    """
    value = yaml_value(text, option)
    if not _finite(value):
        raise ValueError(
            f"{option}: NaN and infinity cannot be set this way; edit the file's text."
        )
    return value


def param_key(text: str, option: str) -> tuple[str, str, tuple[str | int, ...]]:
    """Split ``STEP.PARAM[.KEY...]``; numeric keys index lists.

    Raises:
        ValueError: There is no PARAM, or a part is empty.
    """
    parts = text.split(".")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"{option} needs STEP.PARAM, got: {text!r}")
    path = tuple(int(part) if part.isdigit() else part for part in parts[2:])
    return parts[0], parts[1], path


def include_key(text: str, option: str) -> tuple[str, str]:
    """Split ``INCLUDE:NAME`` at the last colon.

    Raises:
        ValueError: There is no colon, include or name.
    """
    include, sep, name = text.rpartition(":")
    if not sep or not include or not name:
        raise ValueError(f"{option} needs INCLUDE:NAME, got: {text!r}")
    return include, name


def input_document(workspace: RecipeWorkspace, document: str, name: str) -> str:
    """The label of the file declaring an input, or ``document`` when given.

    Raises:
        ValueError: No file, or more than one, declares the input.
    """
    if document:
        return document
    declaring = []
    for label in workspace.files():
        data = workspace.document(label).data
        declared = data.get("inputs") if isinstance(data, dict) else None
        if isinstance(declared, dict) and name in declared:
            declaring.append(label)
    if not declaring:
        raise ValueError(
            f"--input-default: no file of the recipe declares input {name!r}."
        )
    if len(declaring) > 1:
        raise ValueError(
            f"--input-default: input {name!r} is declared in "
            f"{', '.join(declaring)}; name one as DOCUMENT:{name}=VALUE."
        )
    return declaring[0]


def operations_from_json(text: str, option: str = "--ops") -> list[Operation]:
    """Operations from a JSON list in the wire format.

    Raises:
        ValueError: The text is not a JSON list of valid operations.
    """
    try:
        listed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option}: not valid JSON: {exc}") from exc
    if not isinstance(listed, list):
        raise ValueError(f"{option} must hold a JSON list of operations.")
    ops = []
    for index, item in enumerate(listed):
        try:
            ops.append(operation_from_dict(item))
        except ValueError as exc:
            raise ValueError(f"{option} entry {index}: {exc}") from exc
    return ops


def edit_operations(
    workspace: RecipeWorkspace,
    *,
    sets: Iterable[str] = (),
    unsets: Iterable[str] = (),
    checkpoints: Iterable[str] = (),
    overrides: Iterable[str] = (),
    unset_overrides: Iterable[str] = (),
    input_defaults: Iterable[str] = (),
    replace_texts: Iterable[tuple[str, str]] = (),
) -> list[Operation]:
    """The operations the edit options ask for, in the order they apply.

    Args:
        workspace: The recipe, to find the file declaring an input.
        sets: ``STEP.PARAM=VALUE`` values of ``--set``.
        unsets: ``STEP.PARAM`` values of ``--unset``.
        checkpoints: ``STEP=always|never|save|none`` values.
        overrides: ``INCLUDE:NAME=VALUE`` values of ``--override``.
        unset_overrides: ``INCLUDE:NAME`` values of ``--unset-override``.
        input_defaults: ``[DOCUMENT:]NAME=VALUE`` values.
        replace_texts: ``(document, text)`` pairs, the files already read.

    Raises:
        ValueError: A value cannot be read.
    """
    ops: list[Operation] = []
    for item in sets:
        key, value = split_assignment(item, "--set", "STEP.PARAM=VALUE")
        step, name, path = param_key(key, "--set")
        ops.append(SetParam(step, name, yaml_value(value, "--set"), path=path))
    for item in unsets:
        step, name, path = param_key(item, "--unset")
        ops.append(UnsetParam(step, name, path=path))
    for item in checkpoints:
        step, value = split_assignment(item, "--checkpoint", "STEP=VALUE")
        if value not in CHECKPOINT_CHOICES:
            raise ValueError(
                "--checkpoint value must be always, never, save or none, "
                f"got: {value!r}"
            )
        ops.append(SetCheckpoint(step, None if value == "none" else value))
    for item in overrides:
        key, value = split_assignment(item, "--override", "INCLUDE:NAME=VALUE")
        include, name = include_key(key, "--override")
        ops.append(SetIncludeOverride(include, name, yaml_value(value, "--override")))
    for item in unset_overrides:
        include, name = include_key(item, "--unset-override")
        ops.append(UnsetIncludeOverride(include, name))
    for item in input_defaults:
        key, value = split_assignment(item, "--input-default", "[DOCUMENT:]NAME=VALUE")
        document, _, name = key.rpartition(":")
        ops.append(
            SetRecipeInput(
                input_document(workspace, document, name),
                name,
                "default",
                yaml_value(value, "--input-default"),
            )
        )
    for document, text in replace_texts:
        ops.append(ReplaceText(document, text))
    return ops

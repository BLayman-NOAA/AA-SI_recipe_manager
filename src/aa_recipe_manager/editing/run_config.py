# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""A recipe's run config file: its contents, a template, and edits.

Edits go through :class:`RecipeDocument` and the operations' key helpers, so
the file keeps its comments and every line an edit does not touch.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap

from aa_recipe_manager.config import recipe_config_candidate
from aa_recipe_manager.editing.document import RecipeDocument, _same, write_atomic
from aa_recipe_manager.editing.errors import TargetExists
from aa_recipe_manager.editing.operations import _delete_key, _fresh, _insert_key
from aa_recipe_manager.parser.yaml_reader import _strip_ruamel_types

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Recipe

#: Run settings a config file can hold, in the order the template lists them.
SETTINGS = (
    "user_cache_dir",
    "survey_cache_dir",
    "outputs_dir",
    "temp_dir",
    "executor",
    "dask_scheduler",
    "dask_workers",
    "storage_options",
)
#: The template's comment for each run setting.
SETTING_NOTES = {
    "user_cache_dir": "Checkpoints this user's runs write and read; "
    "./recipe_cache when null.",
    "survey_cache_dir": "The shared survey cache, read before the user cache.",
    "outputs_dir": "Saved outputs and the run manifest; beside the user cache "
    "when null.",
    "temp_dir": "Scratch stores a run writes; beside the user cache when null.",
    "executor": "sequential, dask or prefect; sequential when null.",
    "dask_scheduler": "threads, processes or tcp://host:port, for dask.",
    "dask_workers": "Workers of a local Dask cluster.",
    "storage_options": "Options for gs:// storage, such as a token file.",
}
#: The executors a run config can name.
EXECUTORS = ("sequential", "dask", "prefect")


def config_candidate(recipe_path: str | Path) -> Path:
    """The recipe's own run config, ``<recipe stem>.config.yaml`` beside it."""
    return recipe_config_candidate(recipe_path)


def required_inputs(recipe: Recipe) -> list[tuple[str, str | None]]:
    """``(name, description)`` of each input a run of the recipe must be given.

    These are the inputs dry-run reports as required: declared ``required:
    true``, or feeding a required input port or param of a step.
    """
    from aa_recipe_manager.graph import required_inputs as graph_required_inputs
    from aa_recipe_manager.registry.loader import load_builtin_registry

    names = graph_required_inputs(recipe, load_builtin_registry())
    return [(name, recipe.inputs[name].description) for name in names]


def template(recipe_name: str, required: list[tuple[str, str | None]]) -> str:
    """The text of a new ``<recipe>.config.yaml``.

    Every run setting is listed as null with one comment, and each required
    input is listed with a null value for the user to fill in.

    Args:
        recipe_name: The recipe's file name.
        required: ``(name, description)`` of each required input.
    """
    lines = [
        f"# Run config for {recipe_name}, found beside it by the",
        "# <recipe>.config.yaml rule. aa-recipe run and the client read it.",
        "# Relative folders are taken from the folder a run starts in.",
        "",
    ]
    for name in SETTINGS:
        lines += [f"# {SETTING_NOTES[name]}", f"{name}: null"]
    lines += ["", "# Values for the recipe's inputs. Fill in the required ones."]
    lines.append("inputs:")
    for name, description in required:
        note = " ".join((description or "").split())
        lines += [f"  # Required. {note}".rstrip(), f"  {name}: null"]
    return "\n".join(lines) + "\n"


def create(recipe: Recipe, recipe_path: str | Path, *, overwrite: bool = False) -> Path:
    """Write the recipe's own run config from the template.

    Args:
        recipe: The recipe, for its required inputs.
        recipe_path: The recipe's file; the config goes beside it.
        overwrite: Replace an existing file.

    Returns:
        The file written.

    Raises:
        TargetExists: The file exists and ``overwrite`` is False.
    """
    target = config_candidate(recipe_path)
    if target.exists() and not overwrite:
        raise TargetExists(f"{target.name} exists already.", [target])
    text = template(Path(recipe_path).name, required_inputs(recipe))
    write_atomic(target, text.encode("utf-8"))
    return target


def copy_to(document: RecipeDocument, target: str | Path) -> RecipeDocument:
    """A copy of a config file's text, comments and all, at another path.

    The copy is not written; edit it with ``apply`` and then ``write`` it.

    Raises:
        TargetExists: ``target`` exists already.
    """
    target = Path(target)
    if target.exists():
        raise TargetExists(f"{target.name} exists already.", [target])
    return RecipeDocument.from_text(
        document.text, target, newline=document.newline, bom=document.bom
    )


def contents(document: RecipeDocument) -> dict[str, Any] | None:
    """The file's ``inputs:`` and run settings as written, or None.

    None when the file does not hold a mapping. A setting the file leaves out
    is not listed; one written as null is.
    """
    data = document.plain()
    if data is None:
        return {"inputs": {}, "settings": {}}
    if not isinstance(data, dict):
        return None
    inputs = data.get("inputs") or {}
    return {
        "inputs": dict(inputs) if isinstance(inputs, dict) else {},
        "settings": {name: data[name] for name in SETTINGS if name in data},
    }


def apply(
    document: RecipeDocument,
    inputs: dict[str, Any] | None,
    settings: dict[str, Any],
) -> None:
    """Change the document's inputs and settings, keeping the rest of its text.

    Args:
        document: The config file.
        inputs: The whole ``inputs:`` mapping after the change; names left out
            are removed. None leaves the inputs alone.
        settings: Run settings to write. A null one is written as null when
            the file has it and not added when it does not. Settings not named
            are left alone.

    Raises:
        ValueError: A setting is not a run setting or has the wrong type, the
            file does not hold a mapping, or its ``inputs:`` is not one.
    """
    unknown = sorted(set(settings) - set(SETTINGS))
    if unknown:
        raise ValueError(f"Not a run setting: {', '.join(unknown)}.")
    for name, value in settings.items():
        check_setting(name, value)
    current = document.plain()
    if current is None:
        if not inputs and all(value is None for value in settings.values()):
            return
        text = document.text.rstrip("\n")
        document.replace_text(f"{text}\n{{}}\n" if text else "{}\n")
        current = {}
    if not isinstance(current, dict):
        raise ValueError("The run config file does not hold a mapping.")
    if inputs is not None and not isinstance(current.get("inputs") or {}, dict):
        raise ValueError("The run config file's inputs: is not a mapping.")

    def mutate(data: CommentedMap) -> None:
        if not data:
            data.fa.set_block_style()
        for name, value in settings.items():
            if name in data:
                _assign(data, name, value)
            elif value is not None:
                keys = list(data)
                position = keys.index("inputs") if "inputs" in keys else len(keys)
                _insert_key(data, position, name, value, heading=True, flow=True)
        if inputs is None or (not inputs and not current.get("inputs")):
            return
        if not isinstance(data.get("inputs"), CommentedMap):
            data["inputs"] = CommentedMap()
        _update(data["inputs"], inputs)

    document.edit(mutate)


def check_setting(name: str, value: Any) -> None:
    """Refuse a run setting value ``load_run_config`` would not accept.

    Raises:
        ValueError: The value has the wrong type, or ``executor`` is not one
            of ``EXECUTORS``.
    """
    if value is None:
        return
    if name == "executor" and value not in EXECUTORS:
        raise ValueError(
            f"executor must be sequential, dask or prefect, not {value!r}."
        )
    if name == "dask_workers":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"dask_workers must be an integer, not {value!r}.")
    elif name == "storage_options":
        if not isinstance(value, dict):
            raise ValueError(f"storage_options must be a mapping, not {value!r}.")
    elif not isinstance(value, str):
        raise ValueError(f"{name} must be a string, not {value!r}.")


def _update(mapping: CommentedMap, values: dict[str, Any]) -> None:
    """Make a mapping hold ``values``, changing only the entries that differ."""
    if not mapping:
        mapping.fa.set_block_style()
    for name in [name for name in mapping if name not in values]:
        _delete_key(mapping, name)
    for name, value in values.items():
        _assign(mapping, name, value)


def _assign(mapping: CommentedMap, name: str, value: Any) -> None:
    """Set one entry, leaving it alone when it already holds the value.

    A mapping replacing a mapping is updated in place, so the comments inside
    it stay. A mapping or list replacing a single value is written in flow
    style, so the comment lines after the old value stay after the entry.
    """
    if name not in mapping:
        _insert_key(mapping, len(mapping), name, value)
        return
    old = mapping[name]
    if _same(_strip_ruamel_types(old), value):
        return
    if isinstance(old, CommentedMap) and isinstance(value, dict):
        _update(old, value)
        return
    flow = isinstance(value, (dict, list)) and not isinstance(old, (dict, list))
    mapping[name] = _fresh(value, old, flow=flow)

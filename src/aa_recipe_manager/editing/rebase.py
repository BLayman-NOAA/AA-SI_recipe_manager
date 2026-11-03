# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Relative data paths in a recipe, and rewriting them for a copy elsewhere.

A relative data path resolves against the run's working directory, which is
the root recipe's folder, whichever file of the recipe holds it. A copy of the
recipe in another folder still points at the same files once each such path
is rewritten relative to the new folder. Which values are paths is decided
from declarations (``type: path``), never from what a string looks like.

Only the literal text of some paths enters a step hash. A path read through
a fingerprinted declaration (``fingerprint_mode`` other than ``off``) is
replaced by a location free listing, so rewriting it keeps the hash; a path
read with ``off`` is hashed as written, so rewriting it starts a new cache
entry. :class:`PathUses` and :attr:`PathSite.fingerprinted` tell them apart.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal

from aa_recipe_manager.exceptions import RecipeParseError, SpecNotFoundError
from aa_recipe_manager.graph import step_spec
from aa_recipe_manager.parser.yaml_reader import (
    _is_include_entry,
    _resolve_include_path,
)
from aa_recipe_manager.resolver.params import resolve_input_refs
from aa_recipe_manager.storage import is_remote_url

if TYPE_CHECKING:
    from aa_recipe_manager.editing.document import RecipeDocument
    from aa_recipe_manager.editing.workspace import RecipeWorkspace
    from aa_recipe_manager.model.types import Recipe
    from aa_recipe_manager.registry.registry import Registry

#: Run config keys that name local folders.
CONFIG_FOLDER_KEYS = ("user_cache_dir", "survey_cache_dir", "temp_dir", "outputs_dir")

_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")

Key = tuple[str | int, ...]


@dataclass(frozen=True)
class PathSite:
    """One relative data path in a recipe document.

    Attributes:
        document: The label of the document that holds the value.
        kind: ``param`` (a step param), ``input_default`` (a recipe input's
            default), ``override`` (an include's input override) or ``port``
            (a literal step input).
        where: A readable name for the place, such as ``plan_dives.json_dir``,
            ``inputs.cal_input_folder.default`` or
            ``sub_recipes/x.yaml:seabed_lines_dir``.
        value: The path as written, relative to the root recipe's folder.
        fingerprinted: Whether some step reads the value through a
            fingerprinted declaration and no step hashes its text, so
            rewriting it keeps every hash. False for a value no step reads.
        key: Where the value sits in the document's tree.
    """

    document: str
    kind: Literal["param", "input_default", "override", "port"]
    where: str
    value: str
    fingerprinted: bool
    key: Key


@dataclass(frozen=True)
class Rewrite:
    """One path value rewritten for a copy in another folder.

    Attributes:
        where: The place, as :attr:`PathSite.where` names it, or a run config
            key such as ``user_cache_dir`` or ``inputs.cal_input_folder``.
        old: The value before.
        new: The value after.
        absolute: Whether no relative path exists (another drive), so the
            absolute path was written.
    """

    where: str
    old: str
    new: str
    absolute: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The rewrite as a JSON ready dict."""
        return {
            "where": self.where,
            "old": self.old,
            "new": self.new,
            "absolute": self.absolute,
        }


def is_relative_local(value: Any) -> bool:
    """Whether a value is a relative local path that a copy has to rebase.

    Remote URLs, absolute paths (also ``/x`` on Windows), ``~`` paths, values
    holding a ``${...}`` reference, empty strings and non strings are not.
    """
    if not isinstance(value, str) or not value or "${" in value:
        return False
    if is_remote_url(value) or "://" in value or value.startswith("~"):
        return False
    return not PureWindowsPath(value).anchor


def rebase(value: str, old_base: Path, new_base: Path) -> tuple[str, bool]:
    """Rewrite a relative path so it names the same file from another folder.

    String arithmetic only; the path need not exist. The result uses POSIX
    separators and keeps a leading ``./`` and a trailing ``/`` as written.
    Values :func:`is_relative_local` rejects are returned unchanged.

    Args:
        value: The path, relative to ``old_base``.
        old_base: The folder it resolves against now.
        new_base: The folder it must resolve against.

    Returns:
        The new value, and whether the absolute path had to be written
        because no relative path exists (another Windows drive).
    """
    if not is_relative_local(value):
        return value, False
    target = os.path.join(old_base, value)
    try:
        new = Path(os.path.relpath(target, new_base)).as_posix()
        absolute = False
    except ValueError:
        new = Path(os.path.normpath(target)).as_posix()
        absolute = True
    if not absolute and value.startswith(("./", ".\\")) and not new.startswith("."):
        new = "./" + new
    if value.endswith(("/", "\\")) and not new.endswith("/"):
        new += "/"
    return new, absolute


@dataclass(frozen=True)
class PathUses:
    """How the steps of a recipe fold path strings into their hashes.

    Follows ``executor.checkpoint._step_fingerprint``. A param or pipeline
    input with a ``fingerprint_mode`` other than ``off`` contributes a
    location free listing, and its value is replaced by a placeholder in the
    step's top level value dicts. Every other string in a step's wiring,
    params, resolved params and explicit pipeline inputs is hashed as
    written.

    Attributes:
        hashed: The strings hashed as written, nested ones included.
        fingerprinted: The values read through a fingerprinted declaration.
    """

    hashed: tuple[str, ...]
    fingerprinted: frozenset[str]

    @classmethod
    def of(
        cls,
        recipe: Recipe,
        registry: Registry,
        inputs: Mapping[str, Any] | None = None,
    ) -> PathUses:
        """Collect the uses from a flattened recipe.

        Args:
            recipe: The flattened recipe.
            registry: Where the steps' specs come from.
            inputs: Explicit input values, as for a run.
        """
        explicit = dict(inputs or {})
        values = {
            n: d.default for n, d in recipe.inputs.items() if d.default is not None
        }
        values.update(explicit)
        hashed: list[str] = []
        fingerprinted: set[str] = set()
        for step in recipe.steps:
            resolved = resolve_input_refs(step.params, values)
            texts: list[str] = []
            for value in (*step.inputs.values(), *step.params.values()):
                _strings(value, texts.append)
            for value in resolved.values():
                _strings(value, texts.append)
            referenced = {name for text in texts for name in _INPUT_REF.findall(text)}
            pipeline = {name: explicit.get(name) for name in referenced}
            try:
                spec = step_spec(step, registry)
            except SpecNotFoundError:
                spec = None
            read = [
                resolved.get(name)
                for name, declaration in (spec.params.items() if spec else ())
                if declaration.fingerprint_mode != "off"
            ]
            read += [
                pipeline[name]
                for name in referenced
                if getattr(recipe.inputs.get(name), "fingerprint_mode", "off") != "off"
            ]
            hidden = {str(v) for v in read if isinstance(v, (str, Path))}
            fingerprinted |= hidden
            for mapping in (step.inputs, step.params, resolved, pipeline):
                for value in mapping.values():
                    if not (isinstance(value, (str, Path)) and str(value) in hidden):
                        _strings(value, hashed.append)
        return cls(tuple(hashed), frozenset(fingerprinted))

    def is_fingerprinted(self, value: Any) -> bool:
        """Whether a value is read only through fingerprinted declarations.

        True when some step reads it through a fingerprinted param or input
        and no hashed string contains it, so rewriting it changes no hash.
        """
        return (
            isinstance(value, str)
            and value in self.fingerprinted
            and not any(value in text for text in self.hashed)
        )


def path_input_names(
    recipe: Recipe,
    uses: PathUses,
    values: Mapping[str, Any],
    *,
    fingerprinted_only: bool = False,
) -> set[str]:
    """Names of the recipe inputs whose explicit values a copy rebases.

    Args:
        recipe: The flattened recipe.
        uses: The recipe's path uses, from :meth:`PathUses.of` with
            ``values``.
        values: Explicit input values, as for a run (a run config's
            ``inputs`` and any given on the command line).
        fingerprinted_only: Keep only the inputs whose value is read
            through fingerprinted declarations alone, so no hash changes.

    Returns:
        The inputs declared ``type: path``, filtered as asked.
    """
    names = {name for name, decl in recipe.inputs.items() if decl.type == "path"}
    if not fingerprinted_only:
        return names
    return {name for name in names if uses.is_fingerprinted(values.get(name))}


def path_sites(
    workspace: RecipeWorkspace,
    registry: Registry,
    uses: PathUses | None = None,
) -> list[PathSite]:
    """Every relative data path the recipe's files declare.

    Step params and literal step inputs whose spec declares ``type: path``,
    defaults of recipe inputs declared ``type: path`` in any file, and include
    overrides whose target input the included file declares ``type: path``.
    A step whose op has no spec is skipped.

    Args:
        workspace: The recipe.
        registry: Where the steps' specs come from.
        uses: The recipe's path uses, which decide whether a value is hashed
            as written; computed without explicit inputs when None.

    Returns:
        The sites, by document (root first) in file order.
    """
    recipe = workspace.recipe
    steps = {step.id: step for step in recipe.steps}
    if uses is None:
        uses = PathUses.of(recipe, registry)
    sites: list[PathSite] = []

    def add(kind: str, where: str, value: Any, key: Key) -> None:
        if is_relative_local(value):
            fingerprinted = uses.is_fingerprinted(value)
            sites.append(PathSite(label, kind, where, value, fingerprinted, key))

    for label in workspace.files():
        document = workspace.document(label)
        data = document.data if isinstance(document.data, dict) else {}
        for name, decl in _items(data.get("inputs")):
            if isinstance(decl, dict) and decl.get("type") == "path":
                key = ("inputs", name, "default")
                add("input_default", f"inputs.{name}.default", decl.get("default"), key)
        entries = data.get("steps")
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            at = ("steps", index)
            if _is_include_entry(entry):
                target = _included(workspace, document, entry)
                declared = dict(_items(target.data.get("inputs"))) if target else {}
                for name, value in _items(entry.get("input_overrides")):
                    decl = declared.get(name)
                    if isinstance(decl, dict) and decl.get("type") == "path":
                        where = f"{workspace.label(target)}:{name}"
                        add("override", where, value, (*at, "input_overrides", name))
                continue
            step = steps.get(str(entry.get("id"))) if isinstance(entry, dict) else None
            try:
                spec = step_spec(step, registry) if step is not None else None
            except SpecNotFoundError:
                spec = None
            if spec is None:
                continue
            for name, value in _items(entry.get("params")):
                decl = spec.params.get(name)
                if decl is not None and decl.type == "path":
                    add("param", f"{step.id}.{name}", value, (*at, "params", name))
            for name, value in _items(entry.get("inputs")):
                port = spec.inputs.get(name)
                if port is not None and port.type == "path":
                    where = f"{step.id}.inputs.{name}"
                    add("port", where, value, (*at, "inputs", name))
    return sites


def rebase_document(
    document: RecipeDocument,
    sites: Iterable[PathSite],
    old_base: Path,
    new_base: Path,
    *,
    fingerprinted_only: bool = False,
) -> list[Rewrite]:
    """Rewrite a document's path sites for a copy whose root is in ``new_base``.

    The values change through ``document.edit``, so comments, quotes and
    every other line stay as they were.

    Args:
        document: The document holding the sites (a clone for the copy).
        sites: Its sites, from :func:`path_sites`.
        old_base: The original root folder, which the sites resolve against.
        new_base: The copy's root folder.
        fingerprinted_only: Rewrite only fingerprinted sites, so no hash
            changes; the others keep their text.

    Returns:
        The rewrites made, in the order of ``sites``.
    """
    changes: list[tuple[PathSite, Rewrite]] = []
    for site in sites:
        if fingerprinted_only and not site.fingerprinted:
            continue
        new, absolute = rebase(site.value, old_base, new_base)
        if new != site.value:
            changes.append((site, Rewrite(site.where, site.value, new, absolute)))

    _apply(document, [(site.key, rewrite) for site, rewrite in changes])
    return [rewrite for _, rewrite in changes]


def rebase_run_config(
    document: RecipeDocument,
    old_dir: Path,
    new_dir: Path,
    path_inputs: set[str],
) -> list[Rewrite]:
    """Rewrite a run config's relative local folders and path inputs.

    ``user_cache_dir``, ``survey_cache_dir``, ``temp_dir`` and
    ``outputs_dir`` are rebased, and each ``inputs`` value named in
    ``path_inputs``. Storage options, executor settings and remote values
    are left alone.

    Args:
        document: The config's document (a clone for the copy).
        old_dir: The folder the config's paths resolve against now.
        new_dir: The folder they must resolve against.
        path_inputs: Names of the inputs to rebase: those the recipe
            declares ``type: path``.

    Returns:
        The rewrites made.
    """
    data = document.data
    if not isinstance(data, dict):
        return []
    changes: list[tuple[Key, Rewrite]] = []
    places: list[tuple[Key, str]] = [((key,), key) for key in CONFIG_FOLDER_KEYS]
    places += [
        (("inputs", name), f"inputs.{name}")
        for name, _ in _items(data.get("inputs"))
        if name in path_inputs
    ]
    for key, where in places:
        value = _get(data, key)
        new, absolute = rebase(value, old_dir, new_dir)
        if new != value:
            changes.append((key, Rewrite(where, value, new, absolute)))
    _apply(document, changes)
    return [rewrite for _, rewrite in changes]


def _apply(document: RecipeDocument, changes: list[tuple[Key, Rewrite]]) -> None:
    """Set each rewritten value in one edit of the document."""
    if not changes:
        return

    def mutate(data: Any) -> None:
        for key, rewrite in changes:
            *parents, last = key
            node = data
            for part in parents:
                node = node[part]
            node[last] = rewrite.new

    document.edit(mutate)


def _included(
    workspace: RecipeWorkspace, document: RecipeDocument, entry: dict[str, Any]
) -> RecipeDocument | None:
    """The document an include entry names, or None when it is not loaded."""
    try:
        path = _resolve_include_path(entry["include"], document.path)
    except RecipeParseError:
        return None
    found = workspace.documents.get(path)
    return found if found is not None and isinstance(found.data, dict) else None


def _items(mapping: Any) -> list[tuple[Any, Any]]:
    """A mapping's items, or none when it is not a mapping."""
    return list(mapping.items()) if isinstance(mapping, dict) else []


def _strings(value: Any, visit: Any) -> None:
    """Call ``visit`` with every string inside a nested value."""
    if isinstance(value, (str, Path)):
        visit(str(value))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, visit)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, visit)


def _get(data: Any, key: Key) -> Any:
    """The value at a key path, or None when it is missing."""
    for part in key:
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and isinstance(part, int) and part < len(data):
            data = data[part]
        else:
            return None
    return data

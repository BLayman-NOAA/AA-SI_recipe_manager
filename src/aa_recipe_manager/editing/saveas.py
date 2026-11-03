# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Save As: a recipe under a new name, beside the original or elsewhere.

``RecipeWorkspace.save_as`` dispatches here. Beside the original only the
root and its per recipe run config get a new name. Into another folder the
root and every sub-recipe are copied with the recipe's layout, relative data
paths and the run config are rewritten to reach the same files (``rebase``),
and the workspace continues on the copy.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aa_recipe_manager.config import (
    ENV_VAR,
    default_config_search_paths,
    discover_config_path,
    load_run_config,
    recipe_config_candidate,
)
from aa_recipe_manager.editing.document import RecipeDocument, write_atomic
from aa_recipe_manager.editing.errors import TargetExists, WorkspaceError
from aa_recipe_manager.editing.mirror import mirror
from aa_recipe_manager.editing.rebase import (
    PathUses,
    Rewrite,
    path_input_names,
    path_sites,
    rebase,
    rebase_document,
    rebase_run_config,
)
from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.parser.yaml_reader import build_recipe

if TYPE_CHECKING:
    from aa_recipe_manager.editing.workspace import RecipeWorkspace
    from aa_recipe_manager.model.types import Recipe

logger = logging.getLogger(__name__)


@dataclass
class SaveAsResult:
    """What a Save As wrote and changed.

    Attributes:
        root: The new root recipe file.
        written: Every file written, the run config included, the root last.
        rewrites: The path values rewritten, by the copy's document label (the
            root's is its new file name) and by the run config's file name.
        absolute: Sites given an absolute path because no relative one
            exists (another drive), as ``"<label> <where>"``.
        config: The run config written beside the new root, or None.
        rehashed: Ids of the steps whose hash from the new folder differs
            from the original's from its folder, in run order; None when
            either side's hashes could not be computed.
        existing: Files to write that exist already, which need overwrite.
        dry_run: Whether nothing was written; ``written`` then lists what
            would be.
    """

    root: Path
    written: list[Path]
    rewrites: dict[str, list[Rewrite]] = field(default_factory=dict)
    absolute: list[str] = field(default_factory=list)
    config: Path | None = None
    rehashed: list[str] | None = field(default_factory=list)
    existing: list[Path] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The result as a JSON ready dict, with POSIX file paths."""
        return {
            "root": self.root.as_posix(),
            "written": [path.as_posix() for path in self.written],
            "rewrites": {
                label: [rewrite.to_dict() for rewrite in rewrites]
                for label, rewrites in self.rewrites.items()
            },
            "absolute": list(self.absolute),
            "config": self.config.as_posix() if self.config is not None else None,
            "rehashed": self.rehashed,
            "existing": [path.as_posix() for path in self.existing],
            "dry_run": self.dry_run,
        }


@dataclass
class _ElsewherePlan:
    """A Save As into another folder, worked out but not written.

    Attributes:
        result: The result, with ``written`` listing what would be written.
        clones: Each live document with its rebased copy, the root first.
        config_copy: The rebased run config copy, or None.
        existing: The files to write that exist already.
    """

    result: SaveAsResult
    clones: list[tuple[RecipeDocument, RecipeDocument]]
    config_copy: RecipeDocument | None
    existing: list[Path]


def save_beside(
    workspace: RecipeWorkspace,
    target: Path,
    *,
    copy_config: bool,
    config: Path | None,
    overwrite: bool,
    dry_run: bool = False,
) -> SaveAsResult:
    """Save As in the root origin's folder.

    The root is written to ``target``, dirty sub-recipes to their origins,
    and the recipe's own ``<stem>.config.yaml`` is copied to the new name's,
    unchanged, unless ``config`` names another run config, which then
    applies to the copy as it is. The history is kept. A dry run writes
    nothing and lists what it would write.
    """
    root = workspace.root
    if any(d.origin == target for d in workspace.documents.values() if d is not root):
        raise WorkspaceError(
            f"'{target.name}' is a sub-recipe of this recipe; choose another name."
        )
    _refuse_run_config(target, _config_source(workspace, config))
    source = recipe_config_candidate(root.origin)
    copy = recipe_config_candidate(target)
    copying = (
        copy_config
        and source.is_file()
        and copy != source
        and (config is None or config.resolve() == source.resolve())
    )
    existing = [target] if target.exists() else []
    if copying and copy.exists():
        existing.append(copy)
    if existing and not overwrite and not dry_run:
        _refuse_existing(existing)
    dirty = [d for d in workspace.live_documents()[1:] if d.dirty]
    workspace._check_unchanged(dirty)
    if dry_run:
        return SaveAsResult(
            root=target,
            written=[*(d.origin for d in dirty), *([copy] if copying else []), target],
            config=copy if copying else None,
            existing=existing,
            dry_run=True,
        )
    written = [d.write(d.origin) for d in dirty]
    if copying:
        write_atomic(copy, source.read_bytes())
        written.append(copy)
    if workspace.copy_dir is None:
        del workspace.documents[root.path]
        root.path = target
        workspace.documents = {target: root, **workspace.documents}
    root.origin = target
    written.append(root.write(target))
    return SaveAsResult(
        root=target,
        written=written,
        config=copy if copying else None,
        existing=existing,
    )


def save_elsewhere(
    workspace: RecipeWorkspace,
    target: Path,
    *,
    fingerprinted_only: bool,
    copy_config: bool,
    config: Path | None,
    inputs: dict[str, Any],
    overwrite: bool,
    dry_run: bool = False,
) -> SaveAsResult:
    """Save As into another folder, rewriting relative data paths.

    Args:
        workspace: The workspace, which continues on the copy.
        target: The new root file, resolved.
        fingerprinted_only: Rewrite only paths no hash contains.
        copy_config: Copy the run config beside the new root.
        config: The run config the original uses; discovered as for a run
            from the recipe's folder when None.
        inputs: Explicit input values, applied over the run config's.
        overwrite: Replace existing files where the copy's sub-recipes or
            run config go.
        dry_run: Write nothing and leave the workspace as it is.
    """
    plan = plan_elsewhere(
        workspace,
        target,
        fingerprinted_only=fingerprinted_only,
        copy_config=copy_config,
        config=config,
        inputs=inputs,
    )
    result = plan.result
    if dry_run:
        result.dry_run = True
        return result
    if plan.existing and not overwrite:
        _refuse_existing(plan.existing)
    new_dir = target.parent
    clones = plan.clones
    result.written = mirror([c for _, c in clones[1:]], workspace.root_dir, new_dir)
    if plan.config_copy is not None:
        result.written.append(plan.config_copy.write())
    result.written.append(clones[0][1].write())
    _move_to(workspace, clones, new_dir)
    return result


def plan_elsewhere(
    workspace: RecipeWorkspace,
    target: Path,
    *,
    fingerprinted_only: bool,
    copy_config: bool,
    config: Path | None,
    inputs: dict[str, Any],
) -> _ElsewherePlan:
    """Work out a Save As into another folder without writing anything.

    The arguments are those of ``save_elsewhere``. ``rehashed`` compares the
    original's hashes with those of a recipe built from the rebased copies.

    Raises:
        WorkspaceError: A file to write is one of this recipe's, or the
            target is a run config.
    """
    from aa_recipe_manager.registry.loader import load_builtin_registry

    registry = load_builtin_registry()
    old_dir, new_dir = workspace.base_dir, target.parent
    documents = workspace.live_documents()
    places = [target, *(new_dir / workspace.label(d) for d in documents[1:])]
    source = _config_source(workspace, config)
    _refuse_run_config(target, source)
    copied = copy_config and source is not None and _belongs_to_recipe(source)
    config_target = new_dir / recipe_config_candidate(target).name if copied else None
    existing = _existing_places(workspace, [*places, config_target])

    recipe = workspace.recipe
    original_inputs = _run_inputs(source, inputs)
    known_inputs = inputs if original_inputs is None else original_inputs
    uses = PathUses.of(recipe, registry, known_inputs)
    sites = path_sites(workspace, registry, uses)
    names = path_input_names(
        recipe, uses, known_inputs, fingerprinted_only=fingerprinted_only
    )
    result = SaveAsResult(
        root=target,
        written=[*places[1:], *([config_target] if copied else []), target],
        config=config_target,
        existing=existing,
    )
    clones = []
    for document, place in zip(documents, places):
        label = workspace.label(document)
        clone = RecipeDocument.from_text(
            document.text,
            place if document is workspace.root else document.path,
            origin=place,
            newline=document.newline,
            bom=document.bom,
        )
        done = rebase_document(
            clone,
            [site for site in sites if site.document == label],
            old_dir,
            new_dir,
            fingerprinted_only=fingerprinted_only,
        )
        _record(result, target.name if document is workspace.root else label, done)
        clones.append((document, clone))

    config_copy = None
    if source is not None and config_target is not None:
        original = RecipeDocument.load(source)
        config_copy = RecipeDocument.from_text(
            original.text, config_target, newline=original.newline, bom=original.bom
        )
        done = rebase_run_config(config_copy, old_dir, new_dir, names)
        _record(result, config_target.name, done)

    moved = {
        name: rebase(value, old_dir, new_dir)[0] if name in names else value
        for name, value in inputs.items()
    }
    if config_copy is not None:
        after_inputs = _text_inputs(config_copy.text, moved)
    elif source is not None and not _belongs_to_recipe(source):
        after_inputs = _run_inputs(source, moved)
    else:
        after_inputs = dict(moved)
    before = _hashes(recipe, original_inputs, old_dir)
    copy_recipe = _clone_recipe(clones, target)
    after = None if copy_recipe is None else _hashes(copy_recipe, after_inputs, new_dir)
    if before is None or after is None:
        result.rehashed = None
    else:
        result.rehashed = [s for s, h in after.items() if before.get(s) != h]
    return _ElsewherePlan(result, clones, config_copy, existing)


def _config_source(workspace: RecipeWorkspace, config: Path | None) -> Path | None:
    """The run config the original uses: ``config``, else the one discovered.

    Discovery is as for a run from the recipe's folder.
    """
    if config is not None:
        path = Path(config).resolve()
        if not path.is_file():
            raise WorkspaceError(f"Run config not found: {path}")
        return path
    try:
        found = discover_config_path(
            recipe_path=workspace.root.origin, cwd=workspace.base_dir
        )
    except FileNotFoundError:
        return None
    return found.resolve() if found is not None else None


def _belongs_to_recipe(config: Path) -> bool:
    """Whether a run config is the recipe's to copy.

    The file ``AA_RECIPE_CONFIG`` names and the one in the home folder apply
    to a recipe anywhere, so a copy elsewhere uses them as they are.
    """
    shared = {default_config_search_paths()[-1].resolve()}
    if os.environ.get(ENV_VAR):
        shared.add(Path(os.environ[ENV_VAR]).resolve())
    return config.resolve() not in shared


def _refuse_run_config(target: Path, config: Path | None) -> None:
    """Refuse a root target that is a run config, whatever ``overwrite`` says.

    Args:
        target: The new root file.
        config: The run config the original uses, or None.
    """
    if target.name.endswith(".config.yaml") or target == config:
        raise WorkspaceError(
            f"'{target.name}' is a run config, not a recipe; choose another name."
        )


def _refuse_existing(existing: list[Path]) -> None:
    """Refuse a Save As that would replace existing files."""
    listed = ", ".join(str(p) for p in existing)
    raise TargetExists(
        f"Save As would replace existing files: {listed}. Choose another name "
        "or folder, or allow overwriting.",
        existing,
    )


def _existing_places(
    workspace: RecipeWorkspace, places: list[Path | None]
) -> list[Path]:
    """The places that exist; a place that is a file of this recipe is refused."""
    origins = {d.origin.resolve() for d in workspace.documents.values()}
    chosen = [p for p in places if p is not None]
    clashes = [p for p in chosen if p.resolve() in origins]
    if clashes:
        raise WorkspaceError(
            f"Save As would replace '{clashes[0]}', a file of this recipe; "
            "choose another name or folder."
        )
    return [p for p in chosen if p.exists()]


def _clone_recipe(
    clones: list[tuple[RecipeDocument, RecipeDocument]], target: Path
) -> Recipe | None:
    """The recipe the rebased copies make at ``target``, or None if they do not."""
    by_place = {clone.origin.resolve(): clone for _, clone in clones}
    try:
        return build_recipe(
            clones[0][1].plain(), target, lambda path: by_place[path.resolve()].plain()
        )
    except (RecipeParseError, WorkspaceError, KeyError) as exc:
        logger.debug("The copy at %s does not build a recipe: %s", target, exc)
        return None


def _record(result: SaveAsResult, label: str, rewrites: list[Rewrite]) -> None:
    """Add one document's rewrites to a Save As result."""
    if rewrites:
        result.rewrites[label] = rewrites
        result.absolute += [f"{label} {r.where}" for r in rewrites if r.absolute]


def _move_to(
    workspace: RecipeWorkspace,
    clones: list[tuple[RecipeDocument, RecipeDocument]],
    new_dir: Path,
) -> None:
    """Continue on a copy just written: each document takes its clone's text.

    A working copy keeps its paths and is flushed; the origins move.
    Documents the recipe no longer includes are dropped. The history is
    cleared, because an inverse recorded against the old folder could put
    back a path that no longer resolves.
    """
    documents: dict[Path, RecipeDocument] = {}
    for document, clone in clones:
        document.text = document.saved_text = clone.text
        document.data = clone.data
        document.origin = clone.origin
        if workspace.copy_dir is None:
            document.path = clone.path
        documents[document.path] = document
    workspace.documents = documents
    workspace.base_dir = new_dir
    workspace.history.clear()
    workspace._recipe = workspace.to_recipe()
    workspace.flush()


def _run_inputs(config: Path | None, inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Input values for a run: the run config's, then ``inputs`` over them.

    None when the run config cannot be read.
    """
    if config is None:
        return dict(inputs)
    try:
        loaded = load_run_config(config)
    except (OSError, ValueError) as exc:
        logger.debug("No run config inputs from %s: %s", config, exc)
        return None
    return {**loaded.inputs, **inputs}


def _text_inputs(text: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Input values for a run from a run config's text, as ``_run_inputs`` reads.

    None when the text holds no mapping of inputs.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    loaded = (data.get("inputs") if isinstance(data, dict) else None) or {}
    if not isinstance(loaded, dict):
        return None
    return {**loaded, **inputs}


def _hashes(
    recipe: Recipe, inputs: dict[str, Any] | None, base_dir: Path
) -> dict[str, str] | None:
    """Step hashes from ``base_dir``, or None when they cannot be computed.

    Validation warnings are left to ``RecipeWorkspace.validate``.
    """
    from aa_recipe_manager.api import step_hashes

    if inputs is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            found = step_hashes(recipe, inputs=inputs, base_dir=base_dir)
    except Exception as exc:  # missing data, no credentials, an invalid recipe
        logger.debug("Step hashes from %s failed: %s", base_dir, exc)
        return None
    return {step_id: entry.step_hash for step_id, entry in found.items()}

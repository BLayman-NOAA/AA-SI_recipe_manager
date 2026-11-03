# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Copies of a recipe's documents that keep the folder layout."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from aa_recipe_manager.editing.document import RecipeDocument
from aa_recipe_manager.editing.errors import WorkspaceError


def mirror(
    documents: Iterable[RecipeDocument],
    root_dir: Path,
    dest: Path,
    *,
    repoint: bool = True,
) -> list[Path]:
    """Write documents under ``dest`` at their paths relative to ``root_dir``.

    Relative includes keep resolving to the copies, because the layout is the
    same. Nothing is written when a document lies outside ``root_dir``.

    Args:
        documents: The documents to copy, by their current ``path``.
        root_dir: The folder whose layout is mirrored.
        dest: The folder that receives the copies.
        repoint: Point each document's ``path`` at its copy (a working copy);
            False writes a snapshot and leaves the documents as they were.
            ``origin`` is never changed.

    Returns:
        The paths written, in the order of ``documents``.

    Raises:
        WorkspaceError: A document lies outside ``root_dir``.
    """
    root_dir = Path(root_dir).resolve()
    dest = Path(dest).resolve()
    placed: list[tuple[RecipeDocument, Path]] = []
    for document in documents:
        path = Path(document.path).resolve()
        if not path.is_relative_to(root_dir):
            raise WorkspaceError(
                f"'{path}' lies outside the recipe folder '{root_dir}', so it "
                "cannot be copied with the recipe's layout."
            )
        placed.append((document, dest / path.relative_to(root_dir)))
    written = []
    for document, target in placed:
        document.write(target)
        if repoint:
            document.path = target
        written.append(target)
    return written

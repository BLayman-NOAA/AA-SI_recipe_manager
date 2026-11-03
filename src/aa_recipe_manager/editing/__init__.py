# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The recipe editing model: documents that round trip byte for byte.

A :class:`RecipeDocument` holds one YAML file as its own text and as a ruamel
tree. An untouched document writes back the bytes it was read from; an edit
changes only the lines it has to (:func:`transfer`). A
:class:`RecipeWorkspace` holds a recipe and every file it includes, applies
:class:`Operation` objects with undo and redo, and saves the files, also as
a copy in another folder with its relative data paths rewritten (``rebase``).
A :class:`Journal` keeps a working copy's history on disk, so undo and redo
survive a restart (``RecipeWorkspace.reopen``). ``run_config`` reads, creates
and edits a recipe's run config file the same way.
"""

from aa_recipe_manager.editing import run_config
from aa_recipe_manager.editing.document import RecipeDocument, digest, write_atomic
from aa_recipe_manager.editing.errors import (
    ChangedOnDisk,
    EditError,
    EditRejected,
    TargetExists,
    UnknownTarget,
    WorkspaceError,
)
from aa_recipe_manager.editing.history import History, HistoryEntry
from aa_recipe_manager.editing.journal import HISTORY_LIMIT, Journal
from aa_recipe_manager.editing.mirror import mirror
from aa_recipe_manager.editing.operations import (
    KINDS,
    Operation,
    ReplaceText,
    SetCheckpoint,
    SetIncludeOverride,
    SetParam,
    SetRecipeInput,
    UnsetIncludeOverride,
    UnsetParam,
    UnsetRecipeInput,
    operation_from_dict,
    typed_operations,
    typed_value,
)
from aa_recipe_manager.editing.rebase import (
    PathSite,
    PathUses,
    Rewrite,
    path_sites,
    rebase_document,
    rebase_run_config,
)
from aa_recipe_manager.editing.saveas import SaveAsResult
from aa_recipe_manager.editing.textdiff import transfer
from aa_recipe_manager.editing.workspace import (
    DiskConflict,
    RecipeWorkspace,
    SyncResult,
)

__all__ = [
    "HISTORY_LIMIT",
    "KINDS",
    "ChangedOnDisk",
    "DiskConflict",
    "EditError",
    "EditRejected",
    "History",
    "HistoryEntry",
    "Journal",
    "Operation",
    "PathSite",
    "PathUses",
    "RecipeDocument",
    "RecipeWorkspace",
    "ReplaceText",
    "Rewrite",
    "SaveAsResult",
    "SetCheckpoint",
    "SetIncludeOverride",
    "SetParam",
    "SetRecipeInput",
    "SyncResult",
    "TargetExists",
    "UnknownTarget",
    "UnsetIncludeOverride",
    "UnsetParam",
    "UnsetRecipeInput",
    "WorkspaceError",
    "digest",
    "mirror",
    "operation_from_dict",
    "path_sites",
    "rebase_document",
    "rebase_run_config",
    "run_config",
    "transfer",
    "typed_operations",
    "typed_value",
    "write_atomic",
]

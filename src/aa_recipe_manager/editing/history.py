# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Undo and redo history of a recipe workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.editing.document import digest
from aa_recipe_manager.editing.operations import (
    Operation,
    allow_nonfinite,
    operation_from_dict,
)

if TYPE_CHECKING:
    from aa_recipe_manager.editing.document import RecipeDocument


@dataclass
class HistoryEntry:
    """One applied operation, the operation that undoes it, and the texts.

    Undo and redo put back ``before`` and ``after``, so they give back the
    exact bytes; ``inverse`` is the record of the change, not what undo runs.
    ``to_dict`` leaves the texts out; a ``Journal`` stores them by digest and
    gives them back to ``from_dict``.

    Attributes:
        seq: Sequence number within the workspace, from 1.
        time: When it was applied, ISO 8601 in UTC.
        op: The operation as applied.
        inverse: The operation that undoes it.
        documents: Labels of the documents it changed.
        before: The text of each document it changed, before the change.
        after: The text of each document it changed, after the change.
    """

    seq: int
    time: str
    op: Operation
    inverse: Operation
    documents: list[str]
    before: dict[RecipeDocument, str] = field(
        default_factory=dict, compare=False, repr=False
    )
    after: dict[RecipeDocument, str] = field(
        default_factory=dict, compare=False, repr=False
    )

    def to_dict(self) -> dict[str, Any]:
        """The entry as JSON serializable data."""
        return {
            "seq": self.seq,
            "time": self.time,
            "op": self.op.to_dict(),
            "inverse": self.inverse.to_dict(),
            "documents": list(self.documents),
        }

    def texts(self) -> dict[str, tuple[str, str]]:
        """The before and after text of each document it changed, by label.

        ``before`` and ``after`` hold the documents in the order of
        ``documents``.
        """
        return {
            label: (self.before[document], self.after[document])
            for label, document in zip(self.documents, self.before)
        }

    def digests(self) -> dict[str, dict[str, str]]:
        """The sha256 of each text, as ``{label: {"before", "after"}}``."""
        return {
            label: {"before": digest(before), "after": digest(after)}
            for label, (before, after) in self.texts().items()
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        texts: Mapping[str, tuple[str, str]] | None = None,
        documents: Mapping[str, RecipeDocument] | None = None,
    ) -> HistoryEntry:
        """Rebuild an entry from ``to_dict`` output.

        Args:
            data: The entry's ``to_dict`` output.
            texts: The before and after text of each of its documents, by
                label; with ``documents``, the entry gets them back.
            documents: The document of each label.

        Raises:
            ValueError: An operation in it is malformed.
            KeyError: ``texts`` or ``documents`` lacks one of its labels.
        """
        op = operation_from_dict(data["op"])
        with allow_nonfinite():
            inverse = operation_from_dict(data["inverse"])
        entry = cls(
            seq=int(data["seq"]),
            time=str(data["time"]),
            op=op,
            inverse=inverse,
            documents=[str(label) for label in data["documents"]],
        )
        if texts is not None and documents is not None:
            entry.before = {documents[n]: texts[n][0] for n in entry.documents}
            entry.after = {documents[n]: texts[n][1] for n in entry.documents}
        return entry


@dataclass
class History:
    """Undo and redo stacks; the newest entry is last on each.

    Attributes:
        undo_stack: Entries that can be undone.
        redo_stack: Entries undone since the last new edit.
    """

    undo_stack: list[HistoryEntry] = field(default_factory=list)
    redo_stack: list[HistoryEntry] = field(default_factory=list)

    @property
    def can_undo(self) -> bool:
        """Whether there is an entry to undo."""
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        """Whether there is an entry to redo."""
        return bool(self.redo_stack)

    def push(self, entry: HistoryEntry) -> None:
        """Record a new edit; the redo stack is cleared."""
        self.undo_stack.append(entry)
        self.redo_stack.clear()

    def pop_undo(self) -> HistoryEntry | None:
        """Move the newest undoable entry onto the redo stack and return it."""
        if not self.undo_stack:
            return None
        entry = self.undo_stack.pop()
        self.redo_stack.append(entry)
        return entry

    def pop_redo(self) -> HistoryEntry | None:
        """Move the newest redoable entry back onto the undo stack and return it."""
        if not self.redo_stack:
            return None
        entry = self.redo_stack.pop()
        self.undo_stack.append(entry)
        return entry

    def clear(self) -> None:
        """Forget every entry."""
        self.undo_stack.clear()
        self.redo_stack.clear()

    def to_dict(self) -> dict[str, Any]:
        """Both stacks as JSON serializable data, oldest first."""
        return {
            "undo": [entry.to_dict() for entry in self.undo_stack],
            "redo": [entry.to_dict() for entry in self.redo_stack],
        }

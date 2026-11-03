# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The history record of a working copy, so undo survives a restart.

A journal folder holds ``history.jsonl``, one JSON record per line, and
``texts/<sha256>``, each document text a record names, stored once however
many records name it. An entry's texts are written before its line, so a line
always has its texts. ``compact`` rewrites the file from the live stacks and
deletes the texts nothing names.

Records, each a JSON object with ``record``:

- ``entry``: ``entry`` (``HistoryEntry.to_dict()``) and ``texts``
  (``{label: {"before": sha, "after": sha}}``).
- ``undo``, ``redo``: ``seq``, the entry undone or redone.
- ``saved``: ``origin`` (the root's) and ``saved`` (``{label: sha}`` of each
  document's saved text, null when it has none). One written by ``compact``
  also has ``current``, the digest of each document's text.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aa_recipe_manager.editing.document import digest, write_atomic
from aa_recipe_manager.editing.history import History, HistoryEntry

if TYPE_CHECKING:
    from aa_recipe_manager.editing.document import RecipeDocument

#: Undo entries kept across a reopen.
HISTORY_LIMIT = 500


@dataclass
class Rebuilt:
    """A history folded from a journal.

    Attributes:
        history: The undo and redo stacks, with their texts.
        expected: The digest each document should hold now, by document.
        saved: The digest of each document's saved text, None when the
            record names none.
        origin: The root origin the last saved record names, or None.
        warnings: What could not be restored, one message each.
    """

    history: History
    expected: dict[RecipeDocument, str] = field(default_factory=dict)
    saved: dict[RecipeDocument, str | None] = field(default_factory=dict)
    origin: Path | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Folded:
    """The records folded by label, before any document is looked up.

    Attributes:
        undo: The entry records on the undo stack, oldest first.
        redo: The entry records on the redo stack, the next to redo last.
        current: The digest each entry or ``current`` record leaves, by label,
            in the order they were last written.
        saved: The newest saved record's digests, by label.
        origin: The root origin the newest saved record names, or None.
        warnings: The records that could not be read.
    """

    undo: list[dict[str, Any]] = field(default_factory=list)
    redo: list[dict[str, Any]] = field(default_factory=list)
    current: dict[str, str] = field(default_factory=dict)
    saved: dict[str, str | None] = field(default_factory=dict)
    origin: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def expected(self) -> dict[str, str]:
        """The digest each label's text should be now, the newest last.

        A Save As beside renames the root, so two labels can name one
        document; the later of them holds its text.
        """
        found = {label: sha for label, sha in self.saved.items() if sha is not None}
        for label, sha in self.current.items():
            found.pop(label, None)
            found[label] = sha
        return found


class Journal:
    """``history.jsonl`` and the texts it refers to, next to a working copy.

    Attributes:
        folder: The journal's folder.
        path: Its ``history.jsonl``.
        texts_dir: Its ``texts`` folder.
    """

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "history.jsonl"
        self.texts_dir = self.folder / "texts"

    def exists(self) -> bool:
        """Whether the journal has a record file."""
        return self.path.is_file()

    def record(self, entry: HistoryEntry) -> None:
        """Store the entry's before and after texts, then append its line."""
        self._append(self._entry_record(entry))

    def mark(self, kind: Literal["undo", "redo"], seq: int) -> None:
        """Append that the entry ``seq`` was undone or redone."""
        if kind not in ("undo", "redo"):
            raise ValueError(f"kind must be 'undo' or 'redo', not {kind!r}.")
        self._append({"record": kind, "seq": seq})

    def saved(self, origin: Path, documents: Iterable[tuple[str, str | None]]) -> None:
        """Store each ``(label, saved_text)`` and append a saved record."""
        self._append(self._saved_record(origin, documents))

    def text(self, sha: str) -> str | None:
        """The text stored under a digest; None when it is missing or damaged."""
        try:
            text = (self.texts_dir / sha).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return text if digest(text) == sha else None

    def origin(self) -> Path | None:
        """The root origin the last saved record names, or None."""
        last = self._last_saved()
        return Path(last["origin"]) if last is not None else None

    def rebuild(self, documents: Callable[[str], RecipeDocument | None]) -> Rebuilt:
        """Fold the records into History stacks with their texts.

        An entry whose texts are missing, or whose label ``documents`` cannot
        give, ends its stack there: for undo, it and every older entry are
        dropped; for redo, it and every entry redone after it. A line that
        does not parse is skipped. Each loss is a warning.

        Args:
            documents: The document of a label, or None when there is none.
        """
        folded = self._fold()
        found: dict[str, RecipeDocument | None] = {}

        def document(label: str) -> RecipeDocument | None:
            if label not in found:
                found[label] = documents(label)
            return found[label]

        warnings = folded.warnings
        result = Rebuilt(History(), origin=folded.origin, warnings=warnings)
        for label, sha in folded.saved.items():
            if (doc := document(label)) is not None:
                result.saved[doc] = sha
        for label, sha in folded.expected().items():
            if (doc := document(label)) is not None:
                result.expected[doc] = sha
        undo, redo = folded.undo, folded.redo
        result.history.undo_stack = self._restore(undo, document, warnings, "older")
        result.history.redo_stack = self._restore(redo, document, warnings, "undone")
        return result

    def expected(self) -> dict[str, str]:
        """The digest each document's text should be now, by label."""
        return self._fold().expected()

    def _fold(self) -> _Folded:
        """Fold the records into stacks of entry records and digests by label."""
        records, warnings = self._records()
        folded = _Folded(warnings=warnings)
        undo, redo, current = folded.undo, folded.redo, folded.current

        def move(record: dict[str, Any], side: str) -> None:
            for label, pair in record["texts"].items():
                current.pop(label, None)
                current[label] = pair[side]

        for record in records:
            kind = record.get("record")
            try:
                if kind == "entry":
                    move(record, "after")
                    undo.append(record)
                    redo.clear()
                elif kind in ("undo", "redo"):
                    source, target = (undo, redo) if kind == "undo" else (redo, undo)
                    if not source or source[-1]["entry"]["seq"] != record["seq"]:
                        warnings.append(
                            f"A {kind} record does not match the history; skipped."
                        )
                        continue
                    target.append(source.pop())
                    move(target[-1], "before" if kind == "undo" else "after")
                elif kind == "saved":
                    folded.saved = dict(record["saved"])
                    folded.origin = Path(record["origin"])
                    if "current" in record:
                        current.clear()
                        current.update(record["current"])
            except (KeyError, TypeError, AttributeError):
                warnings.append("A damaged history record was skipped.")
        return folded

    def compact(
        self,
        history: History,
        saved: Iterable[tuple[str, str | None]],
        origin: Path,
        *,
        current: Iterable[tuple[str, str]] | None = None,
        limit: int = HISTORY_LIMIT,
    ) -> None:
        """Rewrite the record from the stacks and delete the texts it no longer names.

        The file gets the newest ``limit`` undo entries, the redo entries with
        their undo marks, and one saved record.

        Args:
            history: The stacks, whose entries hold their texts.
            saved: ``(label, saved_text)`` of each document.
            origin: The root's origin.
            current: ``(label, text)`` of each document, so a text the stacks
                no longer explain is still known.
            limit: How many undo entries to keep.
        """
        undo = history.undo_stack[max(0, len(history.undo_stack) - limit) :]
        records = [self._entry_record(entry) for entry in undo]
        records += [self._entry_record(e) for e in reversed(history.redo_stack)]
        records += [{"record": "undo", "seq": e.seq} for e in history.redo_stack]
        records.append(self._saved_record(origin, saved, current))
        self._rewrite(records)

    def _entry_record(self, entry: HistoryEntry) -> dict[str, Any]:
        """Store an entry's texts and return its record."""
        for before, after in entry.texts().values():
            self._store(before)
            self._store(after)
        return {"record": "entry", "entry": entry.to_dict(), "texts": entry.digests()}

    def _saved_record(
        self,
        origin: Path,
        saved: Iterable[tuple[str, str | None]],
        current: Iterable[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Store the texts of a saved record and return it."""
        record: dict[str, Any] = {
            "record": "saved",
            "origin": str(origin),
            "saved": {label: self._store(text) for label, text in saved},
        }
        if current is not None:
            record["current"] = {label: self._store(text) for label, text in current}
        return record

    def _store(self, text: str | None) -> str | None:
        """Write a text under its digest unless it is there; None for None."""
        if text is None:
            return None
        sha = digest(text)
        place = self.texts_dir / sha
        if not place.is_file():
            write_atomic(place, text.encode("utf-8"))
        return sha

    def _append(self, record: dict[str, Any]) -> None:
        """Append one record as a line, in one write."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()

    def _rewrite(self, records: list[dict[str, Any]]) -> None:
        """Replace the record file and delete the texts it does not name."""
        lines = "".join(json.dumps(record) + "\n" for record in records)
        write_atomic(self.path, lines.encode("utf-8"))
        named = set()
        for record in records:
            for pair in record.get("texts", {}).values():
                named.update(pair.values())
            named.update(record.get("saved", {}).values())
            named.update(record.get("current", {}).values())
        if self.texts_dir.is_dir():
            for place in self.texts_dir.iterdir():
                if place.name not in named:
                    place.unlink(missing_ok=True)

    def _records(self) -> tuple[list[dict[str, Any]], list[str]]:
        """The records in order, and a warning per line that does not parse."""
        if not self.exists():
            return [], []
        records, warnings = [], []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if isinstance(record, dict):
                records.append(record)
            else:
                warnings.append("A damaged history line was skipped.")
        return records, warnings

    def _last_saved(self) -> dict[str, Any] | None:
        """The newest saved record, or None."""
        records, _ = self._records()
        saved = [r for r in records if r.get("record") == "saved"]
        return saved[-1] if saved else None

    def _restore(
        self,
        records: list[dict[str, Any]],
        document: Callable[[str], RecipeDocument | None],
        warnings: list[str],
        which: str,
    ) -> list[HistoryEntry]:
        """Entries with their texts, newest first back, up to the first loss."""
        entries: list[HistoryEntry] = []
        for index in range(len(records) - 1, -1, -1):
            entry = self._entry(records[index], document)
            if entry is None:
                count = index + 1
                edits = "edit" if count == 1 else "edits"
                warnings.append(f"{count} {which} {edits} could not be restored.")
                break
            entries.append(entry)
        entries.reverse()
        return entries

    def _entry(
        self,
        record: dict[str, Any],
        document: Callable[[str], RecipeDocument | None],
    ) -> HistoryEntry | None:
        """An entry with its texts, or None when a text or document is missing."""
        texts, documents = {}, {}
        try:
            for label, pair in record["texts"].items():
                doc = document(label)
                before, after = self.text(pair["before"]), self.text(pair["after"])
                if doc is None or before is None or after is None:
                    return None
                texts[label], documents[label] = (before, after), doc
            return HistoryEntry.from_dict(record["entry"], texts, documents)
        except (KeyError, TypeError, AttributeError, ValueError):
            return None

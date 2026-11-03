# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""A recipe and every file it includes, editable in memory."""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml.error import YAMLError

from aa_recipe_manager.editing.document import (
    RecipeDocument,
    digest,
    disk_text,
    write_atomic,
    yaml_problem,
)
from aa_recipe_manager.editing.errors import (
    ChangedOnDisk,
    EditRejected,
    UnknownTarget,
    WorkspaceError,
)
from aa_recipe_manager.editing.history import History, HistoryEntry
from aa_recipe_manager.editing.journal import HISTORY_LIMIT, Journal
from aa_recipe_manager.editing.mirror import mirror
from aa_recipe_manager.editing.operations import (
    Operation,
    ReplaceText,
    allow_nonfinite,
)
from aa_recipe_manager.editing.saveas import SaveAsResult, save_beside, save_elsewhere
from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
)
from aa_recipe_manager.model.types import Recipe
from aa_recipe_manager.parser.yaml_reader import (
    _is_include_entry,
    _resolve_include_path,
    build_recipe,
)
from aa_recipe_manager.validation import DryRunReport

#: What building the DAG raises for a recipe that does not validate.
_DAG_ERRORS = (
    RecipeValidationError,
    SpecNotFoundError,
    ImplementationNotFoundError,
    AmbiguousImplementationError,
    DependencyVersionError,
    FileNotFoundError,
)


@dataclass
class DiskConflict:
    """A file changed on disk that ``sync_disk`` did not reload.

    Attributes:
        label: The document's label.
        error: Why its disk text was not taken (it does not parse or breaks
            the recipe), or None when the session has unsaved edits to it.
        disk_digest: The sha256 of the origin's text now, or ``""`` when it
            cannot be read.
    """

    label: str
    error: str | None
    disk_digest: str


@dataclass
class SyncResult:
    """What ``sync_disk`` did.

    Attributes:
        reloaded: The ``ReplaceText`` entries of the files reloaded.
        conflicts: The files changed on disk and left as they are.
    """

    reloaded: list[HistoryEntry]
    conflicts: list[DiskConflict]


class RecipeWorkspace:
    """A recipe and every file it includes, editable in memory.

    The documents are flattened exactly as ``load_recipe`` flattens the files
    on disk, through ``build_recipe`` with the documents as its loader. A
    workspace opened with ``copy_to`` keeps a working copy of the files on
    disk: every document's ``path`` is its copy, ``flush`` writes edits there,
    and ``save`` writes them to ``origin``. Validation and fingerprints always
    resolve relative paths against ``base_dir``, the root origin's folder, so
    a working copy hashes like the original.

    Attributes:
        root: The root recipe's document.
        documents: Every document loaded, by resolved ``path``, root first. A
            document an edit stopped including stays here.
        base_dir: The root origin's folder.
        copy_dir: The working copy's folder, or None when editing in memory.
        history: The undo and redo stacks.
        journal: The working copy's history record, or None.
        warnings: What ``reopen`` could not restore; empty otherwise.
    """

    def __init__(
        self,
        root: RecipeDocument,
        *,
        copy_to: Path | None = None,
        journal: Journal | None = None,
    ) -> None:
        """Start a workspace on a loaded root document; ``open`` is the usual way.

        Args:
            root: The root document, with resolved ``path`` and ``origin``.
            copy_to: A folder for a working copy, or None.
            journal: A history record for the working copy; needs ``copy_to``.

        Raises:
            RecipeParseError: The recipe or an include does not parse.
            WorkspaceError: An include lies outside the root recipe's folder,
                ``start_copy`` refuses ``copy_to``, or a journal has no copy.
        """
        if journal is not None and copy_to is None:
            raise WorkspaceError("A journal needs a working copy (copy_to).")
        self._begin(root, None)
        if copy_to is not None:
            self.start_copy(copy_to, journal=journal)

    def _begin(self, root: RecipeDocument, copy_dir: Path | None) -> None:
        """Set the workspace up on a root document and flatten the recipe."""
        self.root = root
        self.base_dir = root.origin.parent
        self.copy_dir = copy_dir
        self.journal: Journal | None = None
        self.warnings: list[str] = []
        self.history = History()
        self._seq = 0
        self._written: dict[Path, str] = {root.path: root.text} if copy_dir else {}
        self.documents: dict[Path, RecipeDocument] = {root.path: root}
        self._recipe = self.to_recipe()

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        copy_to: str | Path | None = None,
        journal: str | Path | Journal | None = None,
    ) -> RecipeWorkspace:
        """Open a recipe and every file it includes.

        Args:
            path: The root recipe file.
            copy_to: A folder to keep a working copy in, mirroring the
                recipe folder's layout, as ``start_copy`` does. The originals
                are not changed until ``save``.
            journal: A folder for the history record (a Journal, or its
                path); needs ``copy_to``. The saved record is written at once.

        Returns:
            The workspace.

        Raises:
            RecipeParseError: The recipe or an include does not parse, as for
                ``load_recipe``.
            WorkspaceError: An include resolves outside the root recipe's
                folder (sub-recipes must live in it or below it),
                ``start_copy`` refuses ``copy_to``, or a journal has no copy.
        """
        origin = Path(path).resolve()
        return cls(
            RecipeDocument.load(origin), copy_to=copy_to, journal=_journal(journal)
        )

    @classmethod
    def reopen(
        cls,
        root_copy: str | Path,
        origin: str | Path,
        *,
        journal: str | Path | Journal | None = None,
    ) -> RecipeWorkspace:
        """Continue a working copy written by ``open(copy_to=)``.

        Every file is read from the copy; ``base_dir`` is the origin's folder.
        With a journal, the undo and redo stacks come back with their texts,
        each document's ``saved_text`` is the text the saved record names, and
        a copy file that differs from what the journal expects, or is missing,
        is rewritten from the journal (the line was written before the copy
        was flushed). A saved text the journal no longer holds is taken from
        the origin when that matches the document, else the document counts
        as unsaved. Without a journal the history starts empty and
        ``saved_text`` is the origin's text now. ``warnings`` lists what was
        restored and what could not be.

        Args:
            root_copy: The root recipe's file in the working copy.
            origin: The root recipe's origin, where Save writes.
            journal: The copy's history record (a Journal, or its folder).

        Raises:
            RecipeParseError: A copy file does not parse.
            WorkspaceError: The journal names another origin, or
                ``root_copy`` is not a copy of ``origin``.
        """
        root_copy, origin = Path(root_copy).resolve(), Path(origin).resolve()
        journal = _journal(journal)
        if root_copy.parent == origin.parent:
            raise WorkspaceError(
                f"'{root_copy}' is in the recipe's own folder, not a working copy."
            )
        named = journal.origin() if journal is not None else None
        if named is not None and named.resolve() != origin:
            raise WorkspaceError(
                f"The history in {journal.folder} belongs to {named}, not {origin}."
            )
        restored = (
            _restore_missing(journal, root_copy, origin) if journal is not None else []
        )
        workspace = cls.__new__(cls)
        workspace._begin(RecipeDocument.load(root_copy, origin), root_copy.parent)
        workspace.root.saved_text = disk_text(origin)
        if journal is None:
            return workspace
        rebuilt = journal.rebuild(workspace._document_for_label)
        workspace.history = rebuilt.history
        del workspace.history.undo_stack[:-HISTORY_LIMIT]
        stacks = workspace.history.undo_stack + workspace.history.redo_stack
        workspace._seq = max((entry.seq for entry in stacks), default=0)
        workspace.warnings = [
            f"{label} was missing from the working copy and was restored from its "
            "history."
            for label in restored
        ]
        workspace.warnings += rebuilt.warnings
        for document, sha in rebuilt.expected.items():
            if digest(document.text) == sha:
                continue
            label = workspace.label(document)
            text = journal.text(sha)
            if text is None:
                workspace.warnings.append(
                    f"{label}: unsaved edits could not be restored from the "
                    "history; the file is as it was last written."
                )
                continue
            document.replace_text(text)
            workspace.warnings.append(
                f"{label} was behind its history and was restored from it."
            )
        for document in workspace.documents.values():
            if document not in rebuilt.saved:
                continue
            sha = rebuilt.saved[document]
            text = journal.text(sha) if sha is not None else None
            if sha is not None and text is None:
                if disk_text(document.origin) == document.text:
                    text = document.text
                else:
                    workspace.warnings.append(
                        f"{workspace.label(document)}: its saved text is missing "
                        "from the history; the file counts as unsaved."
                    )
            document.saved_text = text
        workspace._recipe = workspace.to_recipe()
        workspace.flush()
        workspace.journal = journal
        workspace._compact()
        return workspace

    def start_copy(
        self, folder: str | Path, *, journal: str | Path | Journal | None = None
    ) -> list[Path]:
        """Keep a working copy in a folder, with the recipe folder's layout.

        Every document is written there as it is now and continues there:
        ``flush`` writes later edits and ``save`` still writes to ``origin``.
        A file already there that differs from its original is refused,
        so the edits of an earlier copy are not replaced; an identical file
        is fine, which lets an unchanged copy be opened again. ``reopen``
        continues an edited copy.

        Args:
            folder: The working copy's folder.
            journal: A history record to keep for the copy (a Journal, or its
                folder). It is written at once with the history so far.

        Returns:
            The paths written.

        Raises:
            WorkspaceError: ``folder`` is the recipe's folder, the workspace
                keeps a copy already, or a file there differs from its
                original.
        """
        folder = Path(folder).resolve()
        if folder == self.base_dir:
            raise WorkspaceError(
                f"copy_to must be another folder than the recipe's ({self.base_dir})."
            )
        if self.copy_dir is not None:
            raise WorkspaceError(f"The workspace keeps a copy in {self.copy_dir}.")
        documents = list(self.documents.values())
        self._check_copy_folder(documents, folder)
        written = mirror(documents, self.base_dir, folder)
        self.copy_dir = folder
        self.documents = {document.path: document for document in documents}
        self._written.update({document.path: document.text for document in documents})
        self.journal = _journal(journal)
        self._compact()
        return written

    @property
    def recipe(self) -> Recipe:
        """The recipe as flattened after the newest successful change.

        Shared, so do not change it; ``to_recipe`` builds a fresh one.
        """
        return self._recipe

    @property
    def root_dir(self) -> Path:
        """The folder the root document lives in now (the copy, or the origin's)."""
        return self.root.path.parent

    def label(self, document: RecipeDocument) -> str:
        """A document's label: the root's file name, else its path from the root."""
        if document is self.root:
            return self.root.origin.name
        return document.label(self.root_dir)

    def files(self) -> list[str]:
        """Labels of the recipe's files, the root first.

        The includes follow in the order of ``Recipe.include_blocks``, which
        is the graph's ``includes``.
        """
        return [
            self.label(self.root),
            *(block.path for block in self._recipe.include_blocks),
        ]

    def document(self, label: str) -> RecipeDocument:
        """The document with a label.

        Raises:
            UnknownTarget: No document has that label.
        """
        if label == self.label(self.root):
            return self.root
        document = self.documents.get((self.root_dir / label).resolve())
        if document is None:
            raise UnknownTarget(f"No document '{label}' in the recipe.")
        return document

    def step_location(self, step_id: str) -> tuple[RecipeDocument, int]:
        """The document that declares a step and its index in that file's steps.

        Raises:
            UnknownTarget: No file of the recipe declares the step.
        """
        for document in self.live_documents():
            for index, entry in enumerate(_steps(document)):
                if (
                    isinstance(entry, dict)
                    and not _is_include_entry(entry)
                    and str(entry.get("id")) == step_id
                ):
                    return document, index
        raise UnknownTarget(f"No step '{step_id}' in the recipe.")

    def include_location(self, include: str) -> tuple[RecipeDocument, int]:
        """The document holding an include entry and the entry's index.

        Args:
            include: The included file's path relative to the root's folder.

        Raises:
            UnknownTarget: No file of the recipe includes it.
        """
        wanted = (self.root_dir / include).resolve()
        for document in self.live_documents():
            for index, entry in enumerate(_steps(document)):
                if not _is_include_entry(entry) or not isinstance(
                    entry["include"], str
                ):
                    continue
                if _resolve_include_path(entry["include"], document.path) == wanted:
                    return document, index
        raise UnknownTarget(f"No include '{include}' in the recipe.")

    def to_recipe(self) -> Recipe:
        """Flatten the documents as they are now into a validated Recipe.

        Raises:
            RecipeParseError: A document does not parse into a recipe.
            WorkspaceError: An include resolves outside the root's folder.
        """
        return build_recipe(self.root.plain(), self.root.path, self._raw_for)

    def validate(
        self, inputs: dict[str, Any] | None = None, *, check_versions: bool = False
    ) -> DryRunReport:
        """Validate the recipe as edited, as ``aa-recipe dry-run`` would.

        Relative paths are checked under ``base_dir``. The ``UserWarning``s
        raised while validating, such as a param of the wrong type, become
        the report's warnings, as do a param the op does not declare and an
        include override no sub-recipe input matches. Never raises.

        Args:
            inputs: Input values, as for a run.
            check_versions: Check installed package versions.
        """
        from aa_recipe_manager.api import dry_run

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            report = dry_run(
                self._recipe,
                inputs=inputs,
                check_versions=check_versions,
                base_dir=self.base_dir,
            )
        found = []
        for warning in caught:
            if issubclass(warning.category, UserWarning):
                found.append(str(warning.message))
            else:
                warnings.warn_explicit(
                    warning.message, warning.category, warning.filename, warning.lineno
                )
        found += _undeclared_names(self._recipe)
        report.warnings += [w for w in dict.fromkeys(found) if w not in report.warnings]
        return report

    def apply(
        self, op: Operation, *, inputs: dict[str, Any] | None = None
    ) -> HistoryEntry | None:
        """Apply an operation and record it in the history.

        A result that no longer parses into a recipe (bad YAML, duplicate step
        ids, a model rejection) is rolled back. Validation errors are kept
        and shown by ``validate``, except for a strict operation
        (``SetCheckpoint``), which is rolled back when it adds DAG errors.

        Args:
            op: The operation.
            inputs: Input values for the strict check, as for a run.

        Returns:
            The history entry, or None when the operation changed no text
            (setting a value it already has), which is not recorded.

        Raises:
            UnknownTarget: The operation names something that does not exist.
            ValueError: The operation asks for something unsupported, such as
                removing a list entry.
            EditRejected: The result was rolled back; ``problems`` says why.
            OSError: The journal could not be written; the edit is rolled back.
        """
        before = self._dag_errors(self._recipe, inputs) if op.strict else None
        text = op.target(self).text
        document, inverse = self._run(op, before, inputs)
        if document.text == text:
            return None
        entry = HistoryEntry(
            seq=self._seq + 1,
            time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            op=op,
            inverse=inverse,
            documents=[self.label(document)],
            before={document: text},
            after={document: document.text},
        )
        if self.journal is not None:
            try:
                self.journal.record(entry)
            except BaseException:
                self._restore({document: text})
                raise
        self._seq = entry.seq
        self.history.push(entry)
        return entry

    def undo(self) -> HistoryEntry | None:
        """Undo the newest edit; None when there is nothing to undo.

        The documents it changed get back their texts from before it, byte
        for byte.
        """
        if not self.history.can_undo:
            return None
        self._restore(self.history.undo_stack[-1].before)
        entry = self.history.pop_undo()
        if self.journal is not None:
            self.journal.mark("undo", entry.seq)
        return entry

    def redo(self) -> HistoryEntry | None:
        """Apply the newest undone edit again; None when there is none.

        The documents it changed get back their texts from after it.
        """
        if not self.history.can_redo:
            return None
        self._restore(self.history.redo_stack[-1].after)
        entry = self.history.pop_redo()
        if self.journal is not None:
            self.journal.mark("redo", entry.seq)
        return entry

    def check_disk(self) -> list[str]:
        """Labels of live documents whose origin no longer holds ``saved_text``."""
        return [self.label(d) for d in self.live_documents() if not d.saved_in()]

    def reload(self, labels: Iterable[str] | None = None) -> list[HistoryEntry]:
        """Take the origins' current texts into documents, as undoable edits.

        Each file becomes one ``ReplaceText`` entry, and its origin's text its
        ``saved_text``; a file whose text is already the origin's records
        no entry.

        Args:
            labels: The documents to reload; ``check_disk()`` when None.

        Returns:
            The entries recorded.

        Raises:
            UnknownTarget: No document has one of the labels.
            EditRejected: A file's origin cannot be read, does not parse, or
                breaks the recipe, naming the file; files before it in the
                list stay reloaded.
        """
        entries = []
        try:
            for label in self.check_disk() if labels is None else list(labels):
                document = self.document(label)
                try:
                    fresh = RecipeDocument.load(document.origin)
                    entry = self.apply(ReplaceText(label, fresh.text))
                except (RecipeParseError, EditRejected) as exc:
                    cause = exc.__cause__
                    if isinstance(cause, YAMLError):
                        raise EditRejected([yaml_problem(cause, label)]) from exc
                    problems = getattr(exc, "problems", None) or [str(exc)]
                    raise EditRejected([f"{label}: {p}" for p in problems]) from exc
                document.saved_text = fresh.text
                document.newline, document.bom = fresh.newline, fresh.bom
                if entry is not None:
                    entries.append(entry)
        finally:
            self._record_saved()
        return entries

    def sync_disk(self) -> SyncResult:
        """Reload the files changed on disk that have no unsaved edits here.

        Each file ``check_disk`` lists whose text is still its ``saved_text``
        is reloaded as ``reload`` does. A file with unsaved edits, or whose
        disk text ``reload`` rejects, is a conflict and is left as it is.
        """
        result = SyncResult([], [])
        for label in self.check_disk():
            document = self.document(label)
            text = disk_text(document.origin)
            sha = digest(text) if text is not None else ""
            if document.dirty:
                result.conflicts.append(DiskConflict(label, None, sha))
                continue
            try:
                result.reloaded += self.reload([label])
            except EditRejected as exc:
                error = "; ".join(
                    p.removeprefix(f"{label}: ").removeprefix(f"{label}, ")
                    for p in exc.problems
                )
                result.conflicts.append(DiskConflict(label, error, sha))
        return result

    def dirty(self) -> list[str]:
        """Labels of the recipe's files whose text differs from their origin's.

        A document an edit stopped including is left out; it is kept only so
        that undoing that edit brings it back as it was.
        """
        return [self.label(d) for d in self.live_documents() if d.dirty]

    def flush(self) -> list[Path]:
        """Write the working copy's documents that changed since their last write.

        Returns:
            The paths written; none when editing in memory.
        """
        written = []
        for document in self.documents.values():
            if document.path == document.origin:
                continue
            if self._written.get(document.path) != document.text:
                written.append(document.write())
                self._written[document.path] = document.text
        return written

    def save(self, *, overwrite: bool = False) -> list[Path]:
        """Write every dirty file of the recipe to its origin, the root last.

        Args:
            overwrite: Write files whose origin changed on disk anyway.

        Returns:
            The origins written.

        Raises:
            ChangedOnDisk: An origin to write no longer holds what the
                workspace last read or wrote there, and ``overwrite`` is
                False; nothing is written. ``reload`` takes the disk's text.
        """
        documents = [d for d in self.live_documents()[1:] if d.dirty]
        if self.root.dirty:
            documents.append(self.root)
        if not overwrite:
            self._check_unchanged(documents)
        written = [document.write(document.origin) for document in documents]
        if written:
            self._record_saved()
        return written

    def save_as(
        self,
        path: str | Path,
        *,
        paths: Literal["all", "fingerprinted"] = "all",
        copy_config: bool = True,
        config: str | Path | None = None,
        inputs: dict[str, Any] | None = None,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> SaveAsResult:
        """Save the recipe under a new name; Save then follows it.

        Into the root origin's folder, only the root is written there, with
        the recipe's own ``<stem>.config.yaml`` copied to the new name's
        unless ``config`` names another run config, which then applies to
        the copy as it is; dirty sub-recipes are written to their origins as
        ``save`` does, and the history is kept.

        Into another folder, the root and every sub-recipe are copied with
        the recipe's layout, and relative data paths are rewritten so the
        copy reads and writes the same files; data is not copied. The run
        config the original uses is copied beside the new root as
        ``<stem>.config.yaml``, with its relative folders and path inputs
        rewritten too, unless it is the shared one ``AA_RECIPE_CONFIG`` names
        or the one in the home folder. The originals are left as they are,
        the workspace continues on the copy and the history is cleared.

        Args:
            path: The new root file.
            paths: ``"all"`` rewrites every relative data path, so the copy
                runs as is; steps whose path is hashed as written then hash
                differently (``rehashed``). ``"fingerprinted"`` rewrites only
                the paths whose text no hash contains, so every hash stays
                and the copy shares the caches; the other paths must then be
                provided beside the copy.
            copy_config: Copy the run config.
            config: The run config the original uses, as for a run; found
                as a run from the recipe's folder would find it when None.
            inputs: Explicit input values, as for a run, applied over each
                side's run config when comparing hashes.
            overwrite: Replace an existing file at ``path``, an existing run
                config, or existing files where the copy's sub-recipes go.
                A run config at ``path`` is never replaced.
            dry_run: Compute the result (rewrites, config, rehashed,
                existing) and write nothing; the workspace is unchanged.

        Returns:
            What was written and rewritten, or would be on a dry run.

        Raises:
            TargetExists: ``path`` or a copied file exists, ``overwrite`` is
                False and this is not a dry run.
            ChangedOnDisk: A dirty sub-recipe saved in place changed on disk
                since the workspace read it; nothing is written.
            WorkspaceError: ``path`` is the recipe's own file, ``path`` or a
                copied file would replace a file of this recipe, ``path`` is
                a run config (its name ends in
                ``.config.yaml``, or it is the one the original uses), or
                ``config`` does not exist.
            ValueError: ``paths`` is neither ``"all"`` nor
                ``"fingerprinted"``.
        """
        if paths not in ("all", "fingerprinted"):
            raise ValueError(f"paths must be 'all' or 'fingerprinted', not {paths!r}.")
        target = Path(path).resolve()
        if target == self.root.origin:
            raise WorkspaceError(f"'{target.name}' is the recipe's own file; use Save.")
        beside = target.parent == self.base_dir
        if beside:
            result = save_beside(
                self,
                target,
                copy_config=copy_config,
                config=Path(config) if config is not None else None,
                overwrite=overwrite,
                dry_run=dry_run,
            )
        else:
            result = save_elsewhere(
                self,
                target,
                fingerprinted_only=paths == "fingerprinted",
                copy_config=copy_config,
                config=Path(config) if config is not None else None,
                inputs=dict(inputs or {}),
                overwrite=overwrite,
                dry_run=dry_run,
            )
        if dry_run:
            return result
        if beside:
            self._record_saved()
        else:
            self._compact()
        return result

    def revert(self) -> None:
        """Reload every document from its origin, clear the history and flush.

        Raises:
            RecipeParseError: An origin no longer parses into a recipe.
        """
        for document in self.documents.values():
            fresh = RecipeDocument.load(document.origin)
            document.text = document.saved_text = fresh.text
            document.data = fresh.data
            document.newline, document.bom = fresh.newline, fresh.bom
        self.history.clear()
        self._recipe = self.to_recipe()
        self.flush()
        self._compact()

    def live_documents(self) -> list[RecipeDocument]:
        """The documents the recipe includes now, root first."""
        paths = [
            (self.root_dir / block.path).resolve()
            for block in self._recipe.include_blocks
        ]
        return [self.root, *(self.documents[path] for path in paths)]

    def snapshot(self, folder: str | Path) -> Path:
        """Write the recipe's files under a folder with its layout, as they are now.

        The documents keep their paths, so the snapshot does not follow later
        edits.

        Returns:
            The root's copy.
        """
        documents = self.live_documents()
        return mirror(documents, self.root_dir, Path(folder), repoint=False)[0]

    def _run(
        self,
        op: Operation,
        before: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> tuple[RecipeDocument, Operation]:
        """Apply an operation, rolling its document back if the recipe breaks.

        Args:
            op: The operation.
            before: The DAG errors before it, to reject one that adds errors;
                None skips that check.
            inputs: Input values for that check.

        Returns:
            The document changed and the inverse operation.
        """
        document = op.target(self)
        text, recipe = document.text, self._recipe
        try:
            with allow_nonfinite():
                inverse = op.apply(self)
            self._recipe = self.to_recipe()
            if before is not None:
                errors = self._dag_errors(self._recipe, inputs)
                added = [error for error in errors if error not in before]
                if added:
                    raise EditRejected(added)
        except BaseException as exc:
            if document.text != text:
                document.replace_text(text)
            self._recipe = recipe
            if isinstance(exc, (RecipeParseError, WorkspaceError)):
                raise EditRejected([str(exc)]) from exc
            raise
        return document, inverse

    def _check_unchanged(self, documents: list[RecipeDocument]) -> None:
        """Refuse to write origins that changed on disk since the last read.

        Raises:
            ChangedOnDisk: Naming every such origin.
        """
        changed = [d.origin for d in documents if not d.saved_in()]
        if changed:
            raise ChangedOnDisk(changed)

    def _check_copy_folder(self, documents: list[RecipeDocument], folder: Path) -> None:
        """Refuse a copy folder holding a file that differs from its original."""
        clashes = []
        for document in documents:
            place = folder / document.origin.relative_to(self.base_dir)
            if place.exists() and not document.saved_in(place):
                clashes.append(place)
        if clashes:
            listed = ", ".join(str(place) for place in clashes)
            raise WorkspaceError(
                f"The copy folder already holds files that differ from the "
                f"originals: {listed}. Copying again would replace those edits; "
                "edit the copy directly, or choose an empty folder. Files identical "
                "to the originals are fine."
            )

    def _restore(self, texts: dict[RecipeDocument, str]) -> None:
        """Give documents back texts they held, parsing them again."""
        for document, text in texts.items():
            document.replace_text(text)
        self._recipe = self.to_recipe()

    def _dag_errors(self, recipe: Recipe, inputs: dict[str, Any] | None) -> list[str]:
        """The errors building the recipe's DAG reports, one per problem.

        Its warnings are left to ``validate``.
        """
        from aa_recipe_manager.parser.dag_builder import build_dag
        from aa_recipe_manager.registry.loader import load_builtin_registry

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                build_dag(
                    recipe,
                    load_builtin_registry(),
                    input_values=inputs,
                    check_versions=False,
                    base_dir=self.base_dir,
                )
        except RecipeValidationError as exc:
            return list(exc.errors)
        except _DAG_ERRORS as exc:
            return [str(exc)]
        return []

    def _raw_for(self, path: Path) -> dict[str, Any]:
        """The loader ``build_recipe`` calls for each included file."""
        document = self.documents.get(path)
        if document is None:
            document = self._load_include(path)
        raw = document.plain()
        if not isinstance(raw, dict):
            raise RecipeParseError(
                f"Expected a YAML mapping at the top level of '{path}'."
            )
        return raw

    def _load_include(self, path: Path) -> RecipeDocument:
        """Load an included file not seen before.

        When copying, a file the copy holds already is read from there, with
        its origin's text as ``saved_text``; any other is read from its origin
        and copied.
        """
        if not path.is_relative_to(self.root_dir):
            label = Path(os.path.relpath(path, self.root_dir)).as_posix()
            raise WorkspaceError(
                f"Include '{label}' resolves outside the recipe folder "
                f"'{self.root_dir}'; sub-recipes must be in the recipe's folder "
                "or below it."
            )
        origin = self.base_dir / path.relative_to(self.root_dir)
        copied = self.copy_dir is not None and path.is_file()
        source = path if copied else origin
        if not source.is_file():
            raise RecipeParseError(f"Included recipe file not found: {origin}")
        try:
            document = RecipeDocument.load(source, origin)
        except RecipeParseError as exc:
            raise RecipeParseError(
                f"Failed to read included recipe '{source}': {exc.__cause__ or exc}"
            ) from exc
        if copied:
            document.saved_text = disk_text(origin)
            self._written[document.path] = document.text
        elif self.copy_dir is not None:
            self._mirror(document)
        self.documents[document.path] = document
        if self.copy_dir is not None:
            self._record_saved()
        return document

    def _document_for_label(self, label: str) -> RecipeDocument | None:
        """A document by label, loading one only the working copy still holds.

        So an entry for an include the recipe no longer has is restored too.
        """
        try:
            return self.document(label)
        except UnknownTarget:
            pass
        if self.copy_dir is None or not (self.copy_dir / label).is_file():
            return None
        try:
            document = RecipeDocument.load(self.copy_dir / label, self.base_dir / label)
        except RecipeParseError:
            return None
        document.saved_text = disk_text(document.origin)
        self.documents[document.path] = document
        self._written[document.path] = document.text
        return document

    def _record_saved(self) -> None:
        """Append every document's saved text to the journal."""
        if self.journal is not None:
            self.journal.saved(self.root.origin, self._saved_texts())

    def _compact(self) -> None:
        """Rewrite the journal from the history and the documents as they are."""
        if self.journal is not None:
            self.journal.compact(
                self.history,
                self._saved_texts(),
                self.root.origin,
                current=[(self.label(d), d.text) for d in self.documents.values()],
            )

    def _saved_texts(self) -> list[tuple[str, str | None]]:
        """``(label, saved_text)`` of every document."""
        return [(self.label(d), d.saved_text) for d in self.documents.values()]

    def _mirror(self, document: RecipeDocument) -> None:
        """Write a document loaded from its origin into the working copy."""
        self._check_copy_folder([document], self.copy_dir)
        mirror([document], self.base_dir, self.copy_dir)
        self._written[document.path] = document.text


def _journal(journal: str | Path | Journal | None) -> Journal | None:
    """A Journal, given one or its folder."""
    if journal is None or isinstance(journal, Journal):
        return journal
    return Journal(journal)


def _restore_missing(journal: Journal, root_copy: Path, origin: Path) -> list[str]:
    """Write the working copy files the journal expects that are missing.

    Each is written from the journal's text in its original's newline style,
    the newest first, so a root renamed by a Save As beside gets its text
    under the label it has now.
    A missing root whose text the journal lacks is copied from its origin, so
    the copy can still be opened; ``reopen`` then warns that it differs.

    Returns:
        The labels restored from the journal.
    """
    copy_dir = root_copy.parent
    restored = []
    for label, sha in reversed(journal.expected().items()):
        is_root = label == origin.name
        place = root_copy if is_root else (copy_dir / label).resolve()
        if place.exists() or not place.is_relative_to(copy_dir):
            continue
        original = origin if is_root else origin.parent / label
        text = journal.text(sha)
        if text is None:
            if is_root and original.is_file():
                write_atomic(place, original.read_bytes())
            continue
        try:
            like = RecipeDocument.load(original)
            newline, bom = like.newline, like.bom
        except RecipeParseError:
            newline, bom = "\n", False
        RecipeDocument.from_text(text, place, newline=newline, bom=bom).write()
        restored.insert(0, label)
    return restored


def _undeclared_names(recipe: Recipe) -> list[str]:
    """Warnings for params and include overrides that match nothing declared.

    A param is checked only against a registered spec that declares params;
    a spec with none (``compute_sv``) passes its callable's keywords through,
    and a custom step's spec is the recipe's own. An override is flagged when
    the sub-recipe does not declare the input and no step of it uses one.
    """
    from aa_recipe_manager.registry.loader import load_builtin_registry

    registry = load_builtin_registry()
    found = []
    for step in recipe.steps:
        if step.op == "custom" or not registry.has_spec(step.op):
            continue
        declared = registry.get_spec(step.op).params
        found += [
            f"Step '{step.id}': op '{step.op}' has no param '{name}'."
            for name in step.params
            if declared and name not in declared
        ]
    for block in recipe.include_blocks:
        found += [
            f"Include '{block.path or block.source}': override '{name}' is not an "
            "input of the sub-recipe."
            for name in block.overrides
            if name not in block.inputs and not block.bindings.get(name)
        ]
    return found


def _steps(document: RecipeDocument) -> list[Any]:
    """A document's ``steps`` list, or an empty one."""
    data = document.data
    steps = data.get("steps") if isinstance(data, dict) else None
    return steps if isinstance(steps, list) else []

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Journal: the history record of a working copy."""

from __future__ import annotations

import json

import pytest

from aa_recipe_manager.editing import (
    Journal,
    RecipeWorkspace,
    SetParam,
    digest,
)
from aa_recipe_manager.editing import journal as journal_module

from .conftest import copy_fixture
from .test_operations import NESTED


def _workspace(tmp_path, *, journal: bool = True) -> RecipeWorkspace:
    """The two level include fixture, with a working copy and a journal."""
    (tmp_path / "src").mkdir()
    root = copy_fixture(tmp_path / "src", "with_sub.yaml")
    return RecipeWorkspace.open(
        root,
        copy_to=tmp_path / "work",
        journal=tmp_path / "journal" if journal else None,
    )


def _reopen(tmp_path) -> RecipeWorkspace:
    return RecipeWorkspace.reopen(
        tmp_path / "work" / "with_sub.yaml",
        tmp_path / "src" / "with_sub.yaml",
        journal=tmp_path / "journal",
    )


def _blobs(journal: Journal) -> set[str]:
    return {path.name for path in journal.texts_dir.iterdir()}


def test_record_writes_the_texts_before_the_line(tmp_path, monkeypatch):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    entry = ws.apply(SetParam("leaf_add", "k", 9))
    journal = Journal(tmp_path / "journal")
    real, written = journal_module.write_atomic, []

    def fail_after_one(path, data):
        if written:
            raise OSError("disk full")
        written.append(path)
        real(path, data)

    monkeypatch.setattr(journal_module, "write_atomic", fail_after_one)
    with pytest.raises(OSError, match="disk full"):
        journal.record(entry)
    assert not journal.path.exists()
    assert len(_blobs(journal)) == 1
    assert journal.rebuild(ws._document_for_label).history.undo_stack == []

    monkeypatch.undo()
    journal.record(entry)
    [line] = journal.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record == {
        "record": "entry",
        "entry": json.loads(json.dumps(entry.to_dict())),
        "texts": entry.digests(),
    }
    pair = record["texts"]["sub/leaf.yaml"]
    leaf = ws.document("sub/leaf.yaml")
    assert journal.text(pair["before"]) == entry.before[leaf]
    assert journal.text(pair["after"]) == entry.after[leaf] == leaf.text


def test_shared_texts_are_stored_once(tmp_path):
    ws = RecipeWorkspace.open(copy_fixture(tmp_path, "with_sub.yaml"))
    journal = Journal(tmp_path / "journal")

    journal.record(ws.apply(SetParam("leaf_add", "k", 9)))
    journal.record(ws.apply(SetParam("leaf_add", "k", 2)))

    assert len(_blobs(journal)) == 3


def test_rebuild_gives_the_stacks_held_in_memory(tmp_path):
    ws = _workspace(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.undo()
    ws.apply(SetParam("seed", "n", 4))
    ws.apply(SetParam("leaf_add", "k", 2))
    ws.undo()
    ws.redo()
    ws.undo()

    rebuilt = Journal(tmp_path / "journal").rebuild(ws._document_for_label)

    history = rebuilt.history
    assert [e.seq for e in history.undo_stack] == [2]
    assert [e.seq for e in history.redo_stack] == [3]
    assert history.undo_stack == ws.history.undo_stack
    assert history.redo_stack == ws.history.redo_stack
    for mine, theirs in zip(
        history.undo_stack + history.redo_stack,
        ws.history.undo_stack + ws.history.redo_stack,
    ):
        assert (mine.before, mine.after) == (theirs.before, theirs.after)
    assert rebuilt.expected == {d: digest(d.text) for d in ws.documents.values()}
    assert rebuilt.saved == {d: digest(d.saved_text) for d in ws.documents.values()}
    assert rebuilt.origin == ws.root.origin
    assert rebuilt.warnings == []


def test_a_broken_trailing_line_is_dropped_with_a_warning(tmp_path):
    ws = _workspace(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.flush()
    with open(ws.journal.path, "a", encoding="utf-8") as stream:
        stream.write('{"record": "ent')

    rebuilt = ws.journal.rebuild(ws._document_for_label)
    assert rebuilt.warnings == ["A damaged history line was skipped."]
    assert [e.seq for e in rebuilt.history.undo_stack] == [1]

    reopened = _reopen(tmp_path)
    assert reopened.warnings == ["A damaged history line was skipped."]
    assert reopened.history.can_undo
    lines = ws.journal.path.read_text(encoding="utf-8").splitlines()
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert _reopen(tmp_path).warnings == []


def _three_edits(tmp_path) -> tuple[RecipeWorkspace, str]:
    """Edits to leaf, root and leaf, flushed; returns the root edit's after digest."""
    ws = _workspace(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    root_edit = ws.apply(SetParam("seed", "n", 4))
    ws.apply(SetParam("leaf_add", "k", 2))
    ws.flush()
    return ws, root_edit.digests()["with_sub.yaml"]["after"]


def test_a_missing_text_ends_the_undo_stack_at_that_entry(tmp_path):
    ws, root_after = _three_edits(tmp_path)
    leaf_before_last = ws.history.undo_stack[-1].before[ws.document("sub/leaf.yaml")]
    (ws.journal.texts_dir / root_after).unlink()

    rebuilt = ws.journal.rebuild(ws._document_for_label)
    assert [e.seq for e in rebuilt.history.undo_stack] == [3]
    assert rebuilt.warnings == ["2 older edits could not be restored."]

    reopened = _reopen(tmp_path)
    assert reopened.warnings == ["2 older edits could not be restored."]
    assert reopened.root.text == ws.root.text
    assert _reopen(tmp_path).warnings == []
    assert reopened.undo().seq == 3
    assert reopened.document("sub/leaf.yaml").text == leaf_before_last
    assert not reopened.history.can_undo


def test_a_missing_text_ends_the_redo_stack_at_that_entry(tmp_path):
    ws, root_after = _three_edits(tmp_path)
    while ws.undo() is not None:
        pass
    (ws.journal.texts_dir / root_after).unlink()

    rebuilt = ws.journal.rebuild(ws._document_for_label)

    assert rebuilt.history.undo_stack == []
    assert [e.seq for e in rebuilt.history.redo_stack] == [1]
    assert rebuilt.warnings == ["2 undone edits could not be restored."]


def test_compact_keeps_the_newest_entries_and_the_redo_stack(tmp_path):
    ws = _workspace(tmp_path)
    for value in range(1, 6):
        ws.apply(SetParam("leaf_add", "k", value))
    ws.undo()
    ws.undo()
    documents = list(ws.documents.values())

    ws.journal.compact(
        ws.history,
        [(ws.label(d), d.saved_text) for d in documents],
        ws.root.origin,
        current=[(ws.label(d), d.text) for d in documents],
        limit=1,
    )

    rebuilt = ws.journal.rebuild(ws._document_for_label)
    assert [e.seq for e in rebuilt.history.undo_stack] == [3]
    assert [e.seq for e in rebuilt.history.redo_stack] == [5, 4]
    assert [e.after for e in rebuilt.history.redo_stack] == [
        e.after for e in ws.history.redo_stack
    ]
    needed = {d.saved_text for d in documents} | {d.text for d in documents}
    for entry in [ws.history.undo_stack[-1], *ws.history.redo_stack]:
        for before, after in entry.texts().values():
            needed |= {before, after}
    assert _blobs(ws.journal) == {digest(text) for text in needed}
    assert rebuilt.expected == {d: digest(d.text) for d in documents}


def test_revert_leaves_one_saved_record(tmp_path):
    ws = _workspace(tmp_path)
    ws.apply(SetParam("leaf_add", "k", 9))
    ws.save()
    ws.apply(SetParam("leaf_add", "k", 2))

    ws.revert()

    [line] = ws.journal.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["record"] == "saved"
    texts = {ws.label(d): digest(d.saved_text) for d in ws.documents.values()}
    assert record["saved"] == record["current"] == texts
    assert _blobs(ws.journal) == set(texts.values())


def test_an_inverse_holding_nan_survives_the_journal(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    root = src / "nested.yaml"
    root.write_text(NESTED.replace("LEG: 1", "LEG: .nan"), encoding="utf-8")
    original = root.read_text(encoding="utf-8")
    ws = RecipeWorkspace.open(root, copy_to=tmp_path / "work", journal=tmp_path / "j")
    ws.apply(SetParam("pick", "filters", 2, path=("LEG",)))
    ws.flush()

    reopened = RecipeWorkspace.reopen(
        tmp_path / "work" / "nested.yaml", root, journal=tmp_path / "j"
    )

    [entry] = reopened.history.undo_stack
    assert entry.inverse.value != entry.inverse.value
    reopened.undo()
    assert reopened.root.text == original

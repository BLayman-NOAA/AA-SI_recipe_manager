# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for RecipeDocument: byte identical round trips and minimal edits."""

from __future__ import annotations

import codecs
import difflib
import logging
import os
import subprocess
import sys

import pytest
from ruamel.yaml import YAML

from aa_recipe_manager.editing import EditRejected, RecipeDocument
from aa_recipe_manager.editing import document as document_module
from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.parser.yaml_reader import _new_yaml, _strip_ruamel_types

from .conftest import HAVE_EXAMPLES, REFERENCE_YAML, copy_fixture

#: A recipe with the cosmetics a dump loses: a leading blank line, trailing
#: whitespace, an empty value and no final newline.
RECIPE = (
    "\n"
    "# A recipe.\n"
    "recipe:\n"
    "  name: demo   \n"
    '  version: "1.0"\n'
    "\n"
    "steps:\n"
    "  - id: a\n"
    "    op: noop\n"
    "    params:\n"
    "      k: 1    # the constant\n"
    "      label: 'single'\n"
    "      empty: null\n"
    "      blank:\n"
    "\n"
    "  - id: b\n"
    "    op: noop"
)


def _safe_load(text: str):
    return YAML(typ="safe").load(text)


def _changed_lines(old: str, new: str) -> list[str]:
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), n=0)
    return [line for line in diff if line[:1] in "+-" and line[:3] not in "+++---"]


def _first_step(data):
    steps = data.get("steps") if isinstance(data, dict) else None
    return next((s for s in steps or [] if isinstance(s, dict) and "id" in s), None)


def _step_edit(data):
    """A mutation and its inverse on the first step, else on the top level."""
    if not isinstance(data, dict):
        return None, None
    step = _first_step(data)
    if step is None:

        def mutate(tree):
            tree["zz_new"] = 1

        def undo(tree):
            del tree["zz_new"]

        return mutate, undo
    had_params = "params" in step
    old_checkpoint = step.get("checkpoint")

    def mutate(tree):
        step = _first_step(tree)
        step["checkpoint"] = "never" if old_checkpoint == "always" else "always"
        if step.get("params") is None:
            step["params"] = {}
        step["params"]["zz_new"] = "x"

    def undo(tree):
        step = _first_step(tree)
        if old_checkpoint is None:
            del step["checkpoint"]
        else:
            step["checkpoint"] = old_checkpoint
        if had_params:
            del step["params"]["zz_new"]
        else:
            del step["params"]

    return mutate, undo


def test_every_reference_file_round_trips_byte_for_byte(reference_yaml):
    raw = reference_yaml.read_bytes()
    doc = RecipeDocument.load(reference_yaml)
    assert doc.to_bytes() == raw
    assert not doc.dirty
    assert doc.plain() == _strip_ruamel_types(_new_yaml().load(doc.text))


def test_every_reference_file_takes_a_minimal_reversible_edit(reference_yaml, caplog):
    doc = RecipeDocument.load(reference_yaml)
    raw = reference_yaml.read_bytes()
    mutate, undo = _step_edit(doc.data)
    if mutate is None:
        pytest.skip("not a mapping")
    with caplog.at_level(logging.WARNING):
        doc.edit(mutate)
        assert _safe_load(doc.text) == _safe_load(doc.dump())
        assert len(_changed_lines(doc.saved_text, doc.text)) <= 4
        doc.edit(undo)
    assert not caplog.records
    assert doc.to_bytes() == raw


@pytest.mark.skipif(
    not HAVE_EXAMPLES or os.name != "nt",
    reason="CRLF files come from a Windows checkout of the example recipes",
)
def test_reference_set_includes_crlf_files():
    crlf = [path for path in REFERENCE_YAML if b"\r\n" in path.read_bytes()]
    assert crlf
    for path in crlf:
        assert RecipeDocument.load(path).newline == "\r\n"


def test_newline_and_bom_survive_an_edit(tmp_path):
    crlf = tmp_path / "crlf.yaml"
    crlf.write_bytes(b"a: 1\r\nb: 2  \r\nc: 3")
    bom = tmp_path / "bom.yaml"
    bom.write_bytes(codecs.BOM_UTF8 + b"a: 1\nb: 2\n")

    doc = RecipeDocument.load(crlf)
    assert (doc.newline, doc.bom) == ("\r\n", False)
    doc.edit(lambda data: data.__setitem__("a", 5))
    assert doc.to_bytes() == b"a: 5\r\nb: 2  \r\nc: 3"

    doc = RecipeDocument.load(bom)
    assert (doc.newline, doc.bom) == ("\n", True)
    assert doc.to_bytes() == bom.read_bytes()
    doc.edit(lambda data: data.__setitem__("b", 7))
    assert doc.to_bytes() == codecs.BOM_UTF8 + b"a: 1\nb: 7\n"


def test_edit_changes_only_the_edited_lines(tmp_path):
    path = tmp_path / "recipe.yaml"
    path.write_text(RECIPE, encoding="utf-8", newline="\n")
    doc = RecipeDocument.load(path)
    doc.edit(lambda data: data["steps"][0]["params"].__setitem__("label", "two"))

    assert _changed_lines(RECIPE, doc.text) == [
        "-      label: 'single'\n",
        "+      label: 'two'\n",
    ]
    assert doc.text == RECIPE.replace("'single'", "'two'")
    assert doc.dirty


def test_edit_keeps_quote_style_and_comment_column():
    text = "a:\n  q: \"x\"       # note\n  s: 'y'\n  n: 1   # count\n"
    doc = RecipeDocument.from_text(text, "q.yaml")

    def mutate(data):
        data["a"]["q"] = "longer"
        data["a"]["s"] = "z"
        data["a"]["n"] = 22

    doc.edit(mutate)
    assert doc.text == "a:\n  q: \"longer\"  # note\n  s: 'z'\n  n: 22  # count\n"


def test_none_is_written_as_null():
    doc = RecipeDocument.from_text("a:\n  k: 1\n", "n.yaml")
    doc.edit(lambda data: data["a"].__setitem__("k", None))
    assert doc.text == "a:\n  k: null\n"
    doc.edit(lambda data: data["a"].__setitem__("m", [None, 1]))
    assert doc.text == "a:\n  k: null\n  m:\n    - null\n    - 1\n"


def test_untouched_empty_value_stays_empty():
    doc = RecipeDocument.from_text("a:\n  k:\n  j: 1\n", "e.yaml")
    doc.edit(lambda data: data["a"].__setitem__("j", 2))
    assert doc.text == "a:\n  k:\n  j: 2\n"


def test_replace_text_rejects_invalid_yaml_and_keeps_the_document():
    doc = RecipeDocument.from_text("a: 1\n", "r.yaml")
    with pytest.raises(EditRejected) as excinfo:
        doc.replace_text("a: [1,\n")
    assert excinfo.value.problems
    assert doc.text == "a: 1\n"
    assert doc.data == {"a": 1}

    doc.replace_text("a: 2\r\nb: 3\r\n")
    assert doc.text == "a: 2\nb: 3\n"
    assert doc.plain() == {"a": 2, "b": 3}
    assert doc.dirty


def test_a_failing_mutation_leaves_the_document_unchanged():
    doc = RecipeDocument.from_text("a:\n  k: 1\n", "f.yaml")

    def mutate(data):
        data["a"]["k"] = 2
        raise ValueError("no")

    with pytest.raises(ValueError):
        doc.edit(mutate)
    assert doc.plain() == {"a": {"k": 1}}
    assert doc.text == "a:\n  k: 1\n"


def test_a_failed_transfer_falls_back_to_the_whole_dump(monkeypatch, caplog):
    monkeypatch.setattr(document_module, "transfer", lambda text, b, a: text)
    doc = RecipeDocument.from_text("a:   \n  k: 1\n", "t.yaml")
    with caplog.at_level(logging.WARNING):
        doc.edit(lambda data: data["a"].__setitem__("k", 2))
    assert doc.text == "a:\n  k: 2\n"
    assert "rewriting the whole file" in caplog.text


def test_write_is_atomic_and_updates_saved_text(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    original = root.read_bytes()
    copy = tmp_path / "work" / "with_sub.yaml"
    doc = RecipeDocument.load(root)
    doc.path = copy
    doc.edit(lambda data: data["inputs"]["n"].__setitem__("default", 4))

    assert doc.write() == copy
    assert doc.dirty
    assert root.read_bytes() == original
    assert copy.read_bytes() == doc.to_bytes()

    assert doc.write(root) == root
    assert not doc.dirty
    assert root.read_bytes() == doc.to_bytes()
    assert not list(tmp_path.rglob("*.tmp"))


def test_load_errors_use_the_parser_messages(tmp_path):
    with pytest.raises(RecipeParseError, match="Recipe file not found"):
        RecipeDocument.load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [1,\n", encoding="utf-8")
    with pytest.raises(RecipeParseError, match="Failed to read"):
        RecipeDocument.load(bad)


def test_from_text_keeps_origin_newline_and_bom(tmp_path):
    doc = RecipeDocument.from_text(
        "a: 1\r\n",
        tmp_path / "clone" / "a.yaml",
        origin=tmp_path / "a.yaml",
        newline="\r\n",
        bom=True,
    )
    assert doc.text == "a: 1\n"
    assert doc.origin == tmp_path / "a.yaml"
    assert doc.to_bytes() == codecs.BOM_UTF8 + b"a: 1\r\n"


def test_label_is_posix_relative_to_the_root_folder(tmp_path):
    root = copy_fixture(tmp_path, "with_sub.yaml")
    doc = RecipeDocument.load(tmp_path / "sub" / "leaf.yaml")
    assert doc.label(root.parent) == "sub/leaf.yaml"
    assert RecipeDocument.load(root).label(root.parent) == "with_sub.yaml"


def test_edit_of_a_file_in_another_indent_style():
    text = "steps:\n- id: a\n  params:\n    k: 1\n- id: b\n  op: noop\n"
    doc = RecipeDocument.from_text(text, "offset0.yaml")

    def mutate(data):
        data["steps"][0]["params"]["j"] = 2
        data["steps"][0]["checkpoint"] = "always"

    doc.edit(mutate)
    assert doc.text == (
        "steps:\n- id: a\n  params:\n    k: 1\n    j: 2\n  checkpoint: always\n"
        "- id: b\n  op: noop\n"
    )


def test_editing_does_not_import_the_server():
    code = (
        "import sys, aa_recipe_manager.editing; "
        "print(any(m.startswith('aa_recipe_manager.server') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for carrying dump line changes onto a file's own text."""

from __future__ import annotations

from ruamel.yaml import YAML

from aa_recipe_manager.editing.textdiff import transfer

BEFORE = """\
recipe:
  name: demo
steps:
  - id: a
    op: noop
    params:
      k: 1
      j: 2
"""


def _safe_load(text: str):
    return YAML(typ="safe").load(text)


def test_unchanged_dumps_return_the_text_itself():
    text = "\n" + BEFORE.replace("k: 1", "k: 1   ")
    assert transfer(text, BEFORE, BEFORE) is text


def test_one_replaced_line():
    after = BEFORE.replace("k: 1", "k: 5")
    assert transfer(BEFORE, BEFORE, after) == after


def test_insert_at_the_end():
    after = BEFORE + "      z: 3\n"
    assert transfer(BEFORE, BEFORE, after) == after


def test_insert_at_the_end_of_a_file_without_a_final_newline():
    text = BEFORE.rstrip("\n")
    after = BEFORE + "      z: 3\n"
    assert transfer(text, BEFORE, after) == after


def test_delete():
    after = BEFORE.replace("      j: 2\n", "")
    assert transfer(BEFORE, BEFORE, after) == after


def test_cosmetic_differences_are_kept():
    text = (
        "\n"
        "recipe:   \n"
        "  name: demo  # the name\n"
        "  extra:\n"
        "steps:\n"
        "  - id: a\n"
        "    op: noop\n"
        "    params:\n"
        "      k: 1\n"
        "      j: 2"
    )
    before = (
        "recipe:\n"
        "  name: demo  # the name\n"
        "  extra: null\n"
        "steps:\n"
        "  - id: a\n"
        "    op: noop\n"
        "    params:\n"
        "      k: 1\n"
        "      j: 2\n"
    )
    after = before.replace("op: noop", "op: other")
    assert transfer(text, before, after) == text.replace("op: noop", "op: other")


def test_reindents_for_a_text_with_sequence_offset_zero():
    text = """\
steps:
- id: a
  params:
    lst:
    - 1
  inputs: {}
"""
    before = """\
steps:
  - id: a
    params:
      lst:
        - 1
    inputs: {}
"""
    after = """\
steps:
  - id: a
    params:
      lst:
        - 1
        - 2
      zz: x
    inputs: {}
  - id: b
"""
    expected = """\
steps:
- id: a
  params:
    lst:
    - 1
    - 2
    zz: x
  inputs: {}
- id: b
"""
    result = transfer(text, before, after)
    assert result == expected
    assert _safe_load(result) == _safe_load(after)


def test_edit_inside_a_line_for_line_cosmetic_block():
    text = "a:\n  x: 1  \n  y:\n  z: 3  \nb: 4\n"
    before = "a:\n  x: 1\n  y: null\n  z: 3\nb: 4\n"
    after = before.replace("y: null", "y: 2")
    assert transfer(text, before, after) == "a:\n  x: 1  \n  y: 2\n  z: 3  \nb: 4\n"


def test_edit_inside_a_folded_scalar_replaces_the_whole_run():
    text = "a:\n  note: one\n    two\n  k: 1\nb: 2\n"
    before = "a:\n  note: one two\n  k: 1\nb: 2\n"
    after = "a:\n  note: one three\n  k: 1\nb: 2\n"
    assert transfer(text, before, after) == after
    inserted = "a:\n  note: one two\n  new: 0\n  k: 1\nb: 2\n"
    assert transfer(text, before, inserted) == (
        "a:\n  note: one\n    two\n  new: 0\n  k: 1\nb: 2\n"
    )


def test_insert_inside_a_folded_run_is_widened_to_the_run():
    text = "x:\n  a: one\n    two\n  b:\nc: 1\n"
    before = "x:\n  a: one two\n  b: null\nc: 1\n"
    after = "x:\n  a: one two\n  new: 0\n  b: null\nc: 1\n"
    assert transfer(text, before, after) == after


def test_the_inverse_change_restores_the_text():
    text = "\n" + BEFORE.replace("k: 1", "k: 1  # keep")
    before = BEFORE.replace("k: 1", "k: 1  # keep")
    after = before.replace("k: 1", "k: 7").replace("      j: 2\n", "")
    edited = transfer(text, before, after)
    assert edited == "\n" + after
    assert transfer(edited, after, before) == text

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Carry the line changes between two YAML dumps onto a file's own text.

A recipe file is edited by mutating its ruamel tree, but a ruamel dump of the
tree is not byte identical to the file: it drops trailing whitespace, a
missing final newline and a leading blank line, and writes ``null`` where the
file may have an empty value. So the file keeps its own text, and an edit is
the set of lines that differ between a dump before and a dump after the
mutation, spliced onto that text.
"""

from __future__ import annotations

from difflib import SequenceMatcher


def transfer(text: str, before: str, after: str) -> str:
    """Carry the line changes between two dumps onto the file's own text.

    ``before`` and ``after`` are dumps of a document before and after one
    mutation; ``text`` is the file's text (LF), which differs from ``before``
    only cosmetically. Lines equal in both dumps stay as ``text`` has them;
    replaced and inserted lines come from ``after``, re-indented by the
    indentation difference between ``text`` and ``before`` at the nearest
    aligned line of the same depth. An edit that touches a run of ``text``
    lines with no line for line match in ``before`` (a plain scalar folded
    over several lines, say) replaces the whole run. An edit on the last line
    of a file without a final newline adds one.

    Args:
        text: The file's text with LF newlines.
        before: The dump before the mutation.
        after: The dump after the mutation.

    Returns:
        The edited text, or ``text`` itself when ``before == after``.
    """
    if before == after:
        return text
    text_lines = text.splitlines(True)
    before_lines = before.splitlines(True)
    after_lines = after.splitlines(True)
    alignment = _Alignment(before_lines, text_lines)
    out = list(text_lines)
    groups = _change_groups(before_lines, after_lines, alignment)
    for i1, i2, j1, j2 in reversed(groups):
        if i1 == i2:
            start = end = alignment.insert_point(i1)
        else:
            start, end = alignment.start[i1], alignment.end[i2 - 1]
        new = [alignment.reindent(line, i1, i2) for line in after_lines[j1:j2]]
        if new and start == len(out) and out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out[start:end] = new
    return "".join(out)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_anchor(line: str) -> bool:
    """Whether a line's indentation says something about the YAML structure."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


class _Alignment:
    """Where each line of a dump sits in the file's text.

    Attributes:
        start: For each dump line, the first text line it covers.
        end: For each dump line, one past the last text line it covers.
        single: For each dump line, whether it matches exactly one text line.
        block: For each dump line inside a run with no line for line match,
            that run's ``(first, past last)`` dump indices, else None.
    """

    def __init__(self, before: list[str], text: list[str]) -> None:
        self.before = before
        self.text = text
        n = len(before)
        self.start = [0] * n
        self.end = [0] * n
        self.single = [False] * n
        self.block: list[tuple[int, int] | None] = [None] * n
        keys_before = [line.rstrip() for line in before]
        keys_text = [line.rstrip() for line in text]
        matcher = SequenceMatcher(None, keys_before, keys_text, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert":
                continue
            if i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    self.start[i1 + k] = j1 + k
                    self.end[i1 + k] = j1 + k + 1
                    self.single[i1 + k] = True
            else:
                for k in range(i1, i2):
                    self.start[k], self.end[k] = j1, j2
                    self.block[k] = (i1, i2)
        self.uniform = all(
            _indent(text[self.start[k]]) == _indent(before[k])
            for k in range(n)
            if self.single[k] and _is_anchor(before[k])
        )

    def insert_point(self, i: int) -> int:
        """The text line before which lines inserted at dump index ``i`` go."""
        if i < len(self.before):
            return self.start[i]
        return self.end[-1] if self.before else len(self.text)

    def reindent(self, line: str, i1: int, i2: int) -> str:
        """Shift a dump line by the text's indentation offset near ``[i1, i2)``."""
        if self.uniform or not line.strip():
            return line
        depth = _indent(line)
        nearest = None
        candidates = [*range(i1, i2), *range(i1 - 1, -1, -1)]
        candidates += range(i2, len(self.before))
        for k in candidates:
            if not (self.single[k] and _is_anchor(self.before[k])):
                continue
            delta = _indent(self.text[self.start[k]]) - _indent(self.before[k])
            if _indent(self.before[k]) == depth:
                nearest = delta
                break
            if nearest is None:
                nearest = delta
        if not nearest:
            return line
        return " " * max(0, depth + nearest) + line.lstrip(" ")


def _change_groups(
    before: list[str], after: list[str], alignment: _Alignment
) -> list[tuple[int, int, int, int]]:
    """The changed ranges between two dumps, as ``(i1, i2, j1, j2)`` tuples.

    A change that touches a text run without a line for line match is widened
    to the whole run, so the run is replaced at once and none of it is lost.
    """
    ops: list[tuple[str, int, int, int, int]] = []
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ops.append((tag, i1 + k, i1 + k + 1, j1 + k, j1 + k + 1))
        else:
            ops.append((tag, i1, i2, j1, j2))
    spans: list[list[int]] = []
    for index, op in enumerate(ops):
        if op[0] == "equal":
            continue
        low, high = index, index
        while True:
            need_low, need_high = ops[low][1], ops[high][2]
            for block in _blocks_touched(alignment, need_low, need_high):
                need_low = min(need_low, block[0])
                need_high = max(need_high, block[1])
            if ops[low][1] <= need_low and ops[high][2] >= need_high:
                break
            while ops[low][1] > need_low:
                low -= 1
            while ops[high][2] < need_high:
                high += 1
        if spans and low <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], high)
        else:
            spans.append([low, high])
    return [(ops[lo][1], ops[hi][2], ops[lo][3], ops[hi][4]) for lo, hi in spans]


def _blocks_touched(alignment: _Alignment, i1: int, i2: int) -> list[tuple[int, int]]:
    """The unmatched text runs a change of dump lines ``[i1, i2)`` reaches into."""
    blocks = [alignment.block[k] for k in range(i1, i2) if alignment.block[k]]
    if i1 == i2 and 0 < i1 < len(alignment.block):
        inside = alignment.block[i1]
        if inside is not None and inside == alignment.block[i1 - 1]:
            blocks.append(inside)
    return blocks

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""One YAML file as an editable document that round trips byte for byte."""

from __future__ import annotations

import codecs
import hashlib
import io
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.representer import RoundTripRepresenter

from aa_recipe_manager.editing.errors import EditRejected
from aa_recipe_manager.editing.textdiff import transfer
from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.parser.yaml_reader import _strip_ruamel_types

logger = logging.getLogger(__name__)

#: Attempts at the final rename before a write gives up.
REPLACE_ATTEMPTS = 20


class _Representer(RoundTripRepresenter):
    """The round trip representer, writing None as ``null``.

    A new string that would read back as another type (``"5"``, a timestamp)
    is written in double quotes, as the recipes quote strings.
    """


def _represent_none(representer: RoundTripRepresenter, data: None) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _represent_str(representer: RoundTripRepresenter, data: str) -> Any:
    tag = "tag:yaml.org,2002:str"
    resolved = representer.dumper.resolver.resolve(ScalarNode, data, (True, False))
    style = None if resolved == tag else '"'
    return representer.represent_scalar(tag, data, style=style)


_Representer.add_representer(type(None), _represent_none)
_Representer.add_representer(str, _represent_str)


def _yaml() -> YAML:
    """A fresh round trip parser and dumper per call, in the house style.

    List items are indented under their key, lines are never rewrapped, and
    None is written as ``null``. ruamel parsers are not thread safe, hence
    one per call.
    """
    yaml = YAML()
    yaml.Representer = _Representer
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def _normalise(text: str) -> str:
    """Text with LF newlines and no leading BOM character."""
    return text.removeprefix(codecs.BOM_UTF8.decode()).replace("\r\n", "\n")


def _same(a: Any, b: Any) -> bool:
    """Equality of plain YAML values in which NaN equals NaN."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_same, a, b))
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return type(a) is type(b) and a == b


def yaml_problem(exc: YAMLError, label: str) -> str:
    """A YAML parse error as one line naming the file and the line.

    Args:
        exc: The error ruamel raised.
        label: The file's name as the user knows it.

    Returns:
        For example ``sub/x.yaml, line 3: expected ',' or ']', but got
        '<stream end>' (while parsing a flow sequence at line 2)``.
    """
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if problem is None or mark is None:
        return f"{label}: {exc}"
    message = f"{label}, line {mark.line + 1}: {problem}"
    context = getattr(exc, "context", None)
    context_mark = getattr(exc, "context_mark", None)
    if context and context_mark is not None:
        message += f" ({context} at line {context_mark.line + 1})"
    return message


def digest(text: str) -> str:
    """The sha256 hex digest of a document text (LF, UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def disk_text(path: str | Path) -> str | None:
    """A file's text as a document holds it (LF, no BOM); None if unreadable."""
    try:
        return _normalise(Path(path).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return None


def write_atomic(path: Path, data: bytes) -> None:
    """Write a file atomically: ``<name>.tmp`` beside it, then a rename.

    Missing parent folders are created. On Windows a reader or a virus
    scanner holding the old file open makes the rename fail for a moment, so
    it is retried.

    Args:
        path: The file to write.
        data: Its new content.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise


class RecipeDocument:
    """One YAML file kept as text and as a ruamel tree, in step with each other.

    The text is what gets written, so a document that was never edited writes
    back exactly the bytes it was read from. An edit mutates the tree and
    carries only the changed lines of its dump onto the text (``transfer``),
    so comments, quotes, blank lines and trailing whitespace elsewhere stay as
    they were. The newline style comes from the first line; a file that mixes
    CRLF and LF is written back with one style throughout.

    Attributes:
        path: Where the document lives now (the working copy, or the origin).
        origin: Where Save writes.
        newline: ``"\\r\\n"`` or ``"\\n"``, from the first line of the file.
        bom: Whether the file started with a UTF-8 BOM.
        text: The document's text with LF newlines; the authoritative form.
        data: The ruamel tree (CommentedMap for a recipe), parsed from the
            text and mutated by operations.
        saved_text: ``text`` as last read from or written to ``origin``. None
            only when a reopened working copy's journal names a saved text
            that is missing; the document is then dirty.
    """

    def __init__(
        self,
        text: str,
        path: Path,
        origin: Path | None,
        newline: str,
        bom: bool,
    ) -> None:
        path = Path(path)
        self.path = path
        self.origin = Path(origin) if origin is not None else path
        self.newline = newline
        self.bom = bom
        self.text = _normalise(text)
        self.data = _yaml().load(self.text)
        self.saved_text: str | None = self.text

    @classmethod
    def load(cls, path: str | Path, origin: str | Path | None = None) -> RecipeDocument:
        """Read a document from a file.

        Args:
            path: The file to read.
            origin: Where Save writes; defaults to ``path``.

        Raises:
            RecipeParseError: When the file is missing or is not valid YAML,
                with the messages ``load_recipe`` uses.
        """
        path = Path(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise RecipeParseError(f"Recipe file not found: {path}") from None
        first_line = raw.split(b"\n", 1)[0]
        newline = "\r\n" if first_line.endswith(b"\r") and b"\n" in raw else "\n"
        try:
            return cls(
                raw.decode("utf-8-sig"),
                path,
                origin,
                newline,
                raw.startswith(codecs.BOM_UTF8),
            )
        except (UnicodeDecodeError, YAMLError) as exc:
            raise RecipeParseError(f"Failed to read '{path}': {exc}") from exc

    @classmethod
    def from_text(
        cls,
        text: str,
        path: str | Path,
        origin: str | Path | None = None,
        *,
        newline: str = "\n",
        bom: bool = False,
    ) -> RecipeDocument:
        """Make a document from text, for a file not yet on disk or a clone.

        Args:
            text: The document's text; CRLF is normalised to LF.
            path: Where the document lives.
            origin: Where Save writes; defaults to ``path``.
            newline: The newline written to disk.
            bom: Whether a UTF-8 BOM is written.

        Raises:
            RecipeParseError: When the text is not valid YAML.
        """
        try:
            return cls(text, Path(path), origin, newline, bom)
        except YAMLError as exc:
            raise RecipeParseError(f"Failed to read '{path}': {exc}") from exc

    @property
    def dirty(self) -> bool:
        """Whether the text differs from what ``origin`` last held."""
        return self.text != self.saved_text

    def saved_in(self, path: str | Path | None = None) -> bool:
        """Whether a file, ``origin`` by default, holds ``saved_text``.

        Newline style and a BOM are not compared; a missing file holds nothing.
        """
        text = disk_text(path if path is not None else self.origin)
        return text is not None and text == self.saved_text

    def label(self, root_dir: Path) -> str:
        """The document's path relative to ``root_dir``, as POSIX."""
        return Path(os.path.relpath(self.path, root_dir)).as_posix()

    def plain(self) -> dict[str, Any]:
        """The document as plain Python types, a fresh copy on every call."""
        return _strip_ruamel_types(self.data)

    def edit(self, mutate: Callable[[Any], None]) -> None:
        """Mutate the tree and carry the change onto the text.

        Only the lines whose dump changed are replaced. The tree is kept as
        mutated rather than parsed again from the new text, so ruamel's
        comment placement stays where it was and the inverse mutation gives
        back the original bytes. ``mutate`` should insert fresh values, not
        objects the caller keeps changing.

        If the spliced text would not read back as the mutated tree (possible
        only for a file written far from the house style), the whole dump is
        used instead, which is valid but restyles the file.

        Args:
            mutate: Called with ``data``; changes it in place. If it raises,
                the tree is parsed again from the unchanged text.
        """
        before = self.dump()
        try:
            mutate(self.data)
        except BaseException:
            self.data = _yaml().load(self.text)
            raise
        after = self.dump()
        if after == before:
            return
        text = transfer(self.text, before, after)
        if not self._reads_as_data(text):
            logger.warning(
                "Line transfer did not reproduce the edit of %s; rewriting the "
                "whole file.",
                self.path,
            )
            text = after
        self.text = text

    def _reads_as_data(self, text: str) -> bool:
        """Whether ``text`` parses to the same values as the tree."""
        try:
            parsed = _yaml().load(text)
        except YAMLError:
            return False
        return _same(_strip_ruamel_types(parsed), _strip_ruamel_types(self.data))

    def replace_text(self, text: str, label: str | None = None) -> None:
        """Replace the whole text, parsing it first.

        Args:
            text: The new text; CRLF is normalised to LF.
            label: The name a parse error gives the file; the file name when
                None.

        Raises:
            EditRejected: When the text is not valid YAML; nothing changes.
        """
        text = _normalise(text)
        try:
            data = _yaml().load(text)
        except YAMLError as exc:
            problem = yaml_problem(exc, label or self.path.name)
            raise EditRejected([problem]) from exc
        self.text, self.data = text, data

    def dump(self) -> str:
        """A ruamel dump of the tree in the house style, with LF newlines."""
        buffer = io.StringIO()
        _yaml().dump(self.data, buffer)
        return buffer.getvalue()

    def to_bytes(self) -> bytes:
        """The text as the file holds it: BOM, newline style, UTF-8."""
        data = self.text.replace("\n", self.newline).encode("utf-8")
        return codecs.BOM_UTF8 + data if self.bom else data

    def write(self, path: str | Path | None = None) -> Path:
        """Write the document atomically.

        Args:
            path: Where to write; defaults to ``path``. Writing to ``origin``
                makes the document clean.

        Returns:
            The path written.
        """
        target = Path(path) if path is not None else self.path
        write_atomic(target, self.to_bytes())
        if target.resolve() == self.origin.resolve():
            self.saved_text = self.text
        return target

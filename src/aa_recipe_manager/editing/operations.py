# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Edit operations: serializable changes to a recipe workspace.

Each operation is a frozen value object with a ``kind``. ``apply`` changes the
workspace's documents and returns the operation that undoes the change.
Operations address steps by id, includes by their path relative to the root
recipe's folder (``IncludeBlock.path``, the graph's ``includes[].source``) and
documents by the same label, the root being its file name.

The ``position`` field of the ``Set`` operations says where a key that does
not exist yet goes in its mapping, as an index; when the enclosing
``params:`` or ``input_overrides:`` block is missing too, it places the new
block instead. Left out, a new key is appended, or a new block follows the
house key order. The inverses of the ``Unset`` operations use it to put a key
back where it was.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.scalarstring import ScalarString
from ruamel.yaml.tokens import CommentToken

from aa_recipe_manager.editing.errors import UnknownTarget
from aa_recipe_manager.model.types import InputDeclaration
from aa_recipe_manager.plain_values import json_safe

if TYPE_CHECKING:
    from aa_recipe_manager.editing.document import RecipeDocument
    from aa_recipe_manager.editing.workspace import RecipeWorkspace

#: The order of a step's keys in the house style; new keys follow it.
STEP_KEY_ORDER = (
    "id",
    "op",
    "description",
    "map_over",
    "collect",
    "sweep",
    "depends_on",
    "checkpoint",
    "regenerate",
    "dispose_outputs",
    "inputs",
    "params",
    "implementation_override",
    "custom_spec",
    "execution",
)

#: The order of an include entry's keys.
INCLUDE_KEY_ORDER = ("include", "map_over", "input_overrides")

#: The values ``SetCheckpoint`` accepts; None removes the key.
CHECKPOINT_VALUES = ("always", "never", "save", None)

#: Fields of an operation that must be strings.
_TEXT_FIELDS = ("step", "name", "include", "document", "field", "text")

_MISSING = object()

#: Set while operations may hold NaN or infinity: inverses and rebuilt entries.
_NONFINITE_ALLOWED: ContextVar[bool] = ContextVar("nonfinite_allowed", default=False)


@contextmanager
def allow_nonfinite() -> Iterator[None]:
    """Let operations built inside hold NaN or infinity.

    An inverse puts back the value a file held, which may be ``.nan``, and a
    history rebuilt from its record holds such inverses too.
    """
    token = _NONFINITE_ALLOWED.set(True)
    try:
        yield
    finally:
        _NONFINITE_ALLOWED.reset(token)


@dataclass(frozen=True)
class Operation:
    """One change to a workspace, with its inverse.

    Attributes:
        kind: The operation's name in the wire format.
        strict: Whether the workspace rejects an application that adds DAG
            validation errors.
    """

    kind: ClassVar[str] = ""
    strict: ClassVar[bool] = False

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if field.name in _TEXT_FIELDS and not isinstance(value, str):
                raise ValueError(f"{self.kind}: '{field.name}' must be a string.")
            if field.name == "path":
                if isinstance(value, (str, bytes)) or not _is_sequence(value):
                    raise ValueError(f"{self.kind}: 'path' must be a list.")
                value = tuple(value)
                if not all(_is_path_item(item) for item in value):
                    raise ValueError(
                        f"{self.kind}: 'path' holds keys (strings) and list "
                        "indexes (non negative integers) only."
                    )
                object.__setattr__(self, "path", value)
            if (
                field.name == "value"
                and not _NONFINITE_ALLOWED.get()
                and not _finite(value)
            ):
                raise ValueError(
                    f"{self.kind}: NaN and infinity cannot be set through an "
                    "operation; edit the YAML text."
                )
            if field.name == "value" and isinstance(value, (dict, list, tuple)):
                object.__setattr__(self, "value", _detach(value))
            if field.name == "position" and value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(
                        f"{self.kind}: 'position' must be a non negative integer."
                    )

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        """The document the operation changes.

        Raises:
            UnknownTarget: The step, include or document does not exist.
        """
        raise NotImplementedError

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        """Change the workspace's documents.

        Returns:
            The operation that undoes this one.

        Raises:
            UnknownTarget: The operation names something that does not exist.
        """
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """The operation in the wire format, JSON serializable."""
        data: dict[str, Any] = {"kind": self.kind}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            data[field.name] = json_safe(value)
        return data


@dataclass(frozen=True)
class SetParam(Operation):
    """Set a step parameter in the file that declares the step.

    Attributes:
        step: The step id.
        name: The parameter name.
        value: The new value; ``${inputs.x}`` strings are written as given.
        path: Keys and list indexes into the parameter's value, to set one
            entry inside it (``("CRUISE_NAME",)`` for ``filters.CRUISE_NAME``).
        position: Where a new key goes in its mapping, or a new ``params:``
            block in the step; appended (or in key order) when None.
    """

    kind: ClassVar[str] = "set_param"

    step: str
    name: str
    value: Any
    path: tuple[str | int, ...] = ()
    position: int | None = None

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.step_location(self.step)[0]

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document, index = workspace.step_location(self.step)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            step = data["steps"][index]
            where = f"step '{self.step}'"
            if not self.path:
                found["old"] = _set_in_block(
                    step,
                    "params",
                    STEP_KEY_ORDER,
                    self.name,
                    self.value,
                    self.position,
                    where,
                )
                return
            container, key = _walk(
                _block(step, "params", where), (self.name, *self.path), where
            )
            found["old"] = _set_entry(container, key, self.value, self.position, where)

        document.edit(mutate)
        if found["old"] is _MISSING:
            return UnsetParam(self.step, self.name, self.path)
        return SetParam(self.step, self.name, found["old"], self.path)


@dataclass(frozen=True)
class UnsetParam(Operation):
    """Remove a step parameter, or one entry inside its value.

    An emptied ``params:`` block is removed with its last parameter. A path
    ending in a list index raises ValueError: set the whole list instead.

    Attributes:
        step: The step id.
        name: The parameter name.
        path: Keys into the parameter's value, to remove one entry inside it.
    """

    kind: ClassVar[str] = "unset_param"

    step: str
    name: str
    path: tuple[str | int, ...] = ()

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.step_location(self.step)[0]

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document, index = workspace.step_location(self.step)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            step = data["steps"][index]
            where = f"step '{self.step}'"
            if not self.path:
                found["old"], found["position"] = _unset_in_block(
                    step, "params", self.name, where
                )
                return
            container, key = _walk(
                _block(step, "params", where), (self.name, *self.path), where
            )
            if not isinstance(container, dict):
                raise ValueError(
                    f"Cannot remove a list entry of '{self.name}' in {where}; "
                    "set the whole list instead."
                )
            if key not in container:
                raise UnknownTarget(f"No '{_dotted(self.name, self.path)}' in {where}.")
            found["position"] = list(container).index(key)
            found["old"] = _detach(container[key])
            _delete_key(container, key)

        document.edit(mutate)
        return SetParam(
            self.step, self.name, found["old"], self.path, found["position"]
        )


@dataclass(frozen=True)
class SetCheckpoint(Operation):
    """Set or remove a step's ``checkpoint`` key.

    Strict: the workspace rejects the change when it adds DAG validation
    errors, such as removing ``checkpoint: always`` a fan in needs (FR-28).

    Attributes:
        step: The step id.
        value: ``"always"``, ``"never"``, ``"save"``, or None to remove it.
        position: Where a new key goes; the house key order when None.
    """

    kind: ClassVar[str] = "set_checkpoint"
    strict: ClassVar[bool] = True

    step: str
    value: str | None
    position: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.value not in CHECKPOINT_VALUES:
            raise ValueError(
                f"set_checkpoint: value must be always, never, save or null, "
                f"not {self.value!r}."
            )

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.step_location(self.step)[0]

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document, index = workspace.step_location(self.step)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            step = data["steps"][index]
            found["old"] = _detach(step.get("checkpoint"))
            found["position"] = (
                list(step).index("checkpoint") if "checkpoint" in step else None
            )
            if self.value is None:
                if "checkpoint" in step:
                    _delete_key(step, "checkpoint")
            elif "checkpoint" in step:
                step["checkpoint"] = self.value
            elif self.position is not None:
                _insert_key(step, self.position, "checkpoint", self.value)
            else:
                _insert_ordered(step, "checkpoint", self.value, STEP_KEY_ORDER)

        document.edit(mutate)
        return SetCheckpoint(self.step, found["old"], found["position"])


@dataclass(frozen=True)
class SetIncludeOverride(Operation):
    """Set one ``input_overrides`` entry on an include entry.

    The entry is edited in the file that holds it, so an override on
    ``sub/leaf.yaml`` included by ``sub/chain.yaml`` changes ``sub/chain.yaml``.

    Attributes:
        include: The included file's path relative to the root's folder.
        name: The included recipe's input.
        value: The new value.
        position: Where a new key goes, or a new ``input_overrides:`` block
            in the entry; appended (or in key order) when None.
    """

    kind: ClassVar[str] = "set_include_override"

    include: str
    name: str
    value: Any
    position: int | None = None

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.include_location(self.include)[0]

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document, index = workspace.include_location(self.include)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            found["old"] = _set_in_block(
                data["steps"][index],
                "input_overrides",
                INCLUDE_KEY_ORDER,
                self.name,
                self.value,
                self.position,
                f"include '{self.include}'",
            )

        document.edit(mutate)
        if found["old"] is _MISSING:
            return UnsetIncludeOverride(self.include, self.name)
        return SetIncludeOverride(self.include, self.name, found["old"])


@dataclass(frozen=True)
class UnsetIncludeOverride(Operation):
    """Remove one override; an emptied ``input_overrides:`` goes with it.

    Attributes:
        include: The included file's path relative to the root's folder.
        name: The included recipe's input.
    """

    kind: ClassVar[str] = "unset_include_override"

    include: str
    name: str

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.include_location(self.include)[0]

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document, index = workspace.include_location(self.include)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            found["old"], found["position"] = _unset_in_block(
                data["steps"][index],
                "input_overrides",
                self.name,
                f"include '{self.include}'",
            )

        document.edit(mutate)
        return SetIncludeOverride(
            self.include, self.name, found["old"], found["position"]
        )


@dataclass(frozen=True)
class SetRecipeInput(Operation):
    """Set one field of an input a document declares.

    Attributes:
        document: The declaring document's label.
        name: The input name.
        field: A field of an input declaration, such as ``default``.
        value: The new value.
        position: Where a new key goes; appended when None.
    """

    kind: ClassVar[str] = "set_recipe_input"

    document: str
    name: str
    field: str
    value: Any
    position: int | None = None

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.document(self.document)

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document = workspace.document(self.document)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            declaration = _declaration(data, self.document, self.name, self.field)
            found["old"] = _set_entry(
                declaration,
                self.field,
                self.value,
                self.position,
                f"input '{self.name}'",
            )

        document.edit(mutate)
        if found["old"] is _MISSING:
            return UnsetRecipeInput(self.document, self.name, self.field)
        return SetRecipeInput(self.document, self.name, self.field, found["old"])


@dataclass(frozen=True)
class UnsetRecipeInput(Operation):
    """Remove one field of an input a document declares.

    Attributes:
        document: The declaring document's label.
        name: The input name.
        field: A field of an input declaration, such as ``default``.
    """

    kind: ClassVar[str] = "unset_recipe_input"

    document: str
    name: str
    field: str

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.document(self.document)

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document = workspace.document(self.document)
        found: dict[str, Any] = {}

        def mutate(data: Any) -> None:
            declaration = _declaration(data, self.document, self.name, self.field)
            if self.field not in declaration:
                raise UnknownTarget(
                    f"Input '{self.name}' in '{self.document}' has no '{self.field}'."
                )
            found["position"] = list(declaration).index(self.field)
            found["old"] = _detach(declaration[self.field])
            _delete_key(declaration, self.field)

        document.edit(mutate)
        return SetRecipeInput(
            self.document, self.name, self.field, found["old"], found["position"]
        )


@dataclass(frozen=True)
class ReplaceText(Operation):
    """Replace a document's whole text.

    Attributes:
        document: The document's label.
        text: The new text; CRLF is normalised to LF.
    """

    kind: ClassVar[str] = "replace_text"

    document: str
    text: str

    def target(self, workspace: RecipeWorkspace) -> RecipeDocument:
        return workspace.document(self.document)

    def apply(self, workspace: RecipeWorkspace) -> Operation:
        document = workspace.document(self.document)
        old = document.text
        document.replace_text(self.text, self.document)
        return ReplaceText(self.document, old)


#: Every operation class by its ``kind``.
KINDS: dict[str, type[Operation]] = {
    cls.kind: cls
    for cls in (
        SetParam,
        UnsetParam,
        SetCheckpoint,
        SetIncludeOverride,
        UnsetIncludeOverride,
        SetRecipeInput,
        UnsetRecipeInput,
        ReplaceText,
    )
}


def operation_from_dict(data: Mapping[str, Any]) -> Operation:
    """Build an operation from its wire format.

    Args:
        data: A mapping with ``kind`` and the operation's fields.

    Returns:
        The operation.

    Raises:
        ValueError: The kind is unknown, a required field is missing, a field
            is not the operation's, or a value has the wrong type.
    """
    if not isinstance(data, Mapping):
        raise ValueError("An operation must be a mapping with a 'kind'.")
    kind = data.get("kind")
    cls = KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise ValueError(
            f"Unknown operation kind {kind!r}; expected one of {', '.join(KINDS)}."
        )
    fields = {field.name: field for field in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(fields) - {"kind"})
    if unknown:
        raise ValueError(f"{kind}: unknown field(s) {', '.join(unknown)}.")
    missing = [
        name
        for name, field in fields.items()
        if name not in data and field.default is dataclasses.MISSING
    ]
    if missing:
        raise ValueError(f"{kind}: missing field(s) {', '.join(missing)}.")
    return cls(**{name: data[name] for name in fields if name in data})


def typed_value(value: Any, declared: str | None) -> Any:
    """A value as the recipe's YAML would hold it for the declared type.

    JSON and the command line do not keep ``1800.0`` apart from ``1800``, and
    the two hash differently, so a whole number for a ``float`` becomes a
    float.

    Args:
        value: The value given.
        declared: The declared type of what it sets, or None when unknown.
    """
    if declared == "float" and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def typed_operations(
    workspace: RecipeWorkspace,
    ops: list[Operation],
    graph: dict[str, Any] | None = None,
) -> list[Operation]:
    """Operations with a whole number made a float where a float is declared.

    That is the value of a param set without a path, an include override or
    an input default whose declared type is ``float``.

    Args:
        workspace: The recipe the operations will apply to.
        ops: The operations.
        graph: The recipe's graph document (``api.recipe_graph``) for the
            declared types; built from the workspace when None.
    """
    if not any(isinstance(getattr(op, "value", None), int) for op in ops):
        return list(ops)
    if graph is None:
        from aa_recipe_manager.api import recipe_graph

        graph = recipe_graph(
            workspace.recipe,
            source=workspace.root.origin.name,
            allow_unknown_ops=True,
        )
    root = workspace.label(workspace.root)
    typed = []
    for op in ops:
        value = getattr(op, "value", None)
        made = typed_value(value, _declared_type(op, graph, root))
        typed.append(op if made is value else dataclasses.replace(op, value=made))
    return typed


def _declared_type(op: Operation, graph: dict[str, Any], root: str) -> str | None:
    """The declared type of what an operation sets, from the recipe graph."""
    if isinstance(op, SetParam) and not op.path:
        for node in graph["nodes"]:
            if node["kind"] == "step" and node["id"] == op.step:
                types = {param["name"]: param["type"] for param in node["params"]}
                return types.get(op.name)
    elif isinstance(op, SetIncludeOverride):
        return _include_input_type(graph, op.include, op.name)
    elif isinstance(op, SetRecipeInput) and op.field == "default":
        if op.document != root:
            return _include_input_type(graph, op.document, op.name)
        for node in graph["nodes"]:
            if node["kind"] == "input" and node["id"] == f"in:{op.name}":
                return node["type"]
    return None


def _include_input_type(graph: dict[str, Any], source: str, name: str) -> str | None:
    """The declared type of a sub-recipe's input, from the graph's includes."""
    for block in graph["includes"]:
        if block["source"] == source:
            types = {field["name"]: field["type"] for field in block["inputs"]}
            return types.get(name)
    return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _finite(value: Any) -> bool:
    """Whether no float in a value, at any depth, is NaN or infinite."""
    if isinstance(value, dict):
        return all(_finite(key) and _finite(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def _is_path_item(item: Any) -> bool:
    return isinstance(item, str) or (
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
    )


def _dotted(name: str, path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in (name, *path))


def _declaration(data: Any, label: str, name: str, field: str) -> CommentedMap:
    """The mapping that declares input ``name`` in a document's tree."""
    if field not in InputDeclaration.model_fields:
        raise UnknownTarget(
            f"'{field}' is not a field of an input declaration; expected one of "
            f"{', '.join(InputDeclaration.model_fields)}."
        )
    inputs = data.get("inputs") if isinstance(data, dict) else None
    declaration = inputs.get(name) if isinstance(inputs, dict) else None
    if not isinstance(declaration, dict):
        raise UnknownTarget(f"'{label}' does not declare an input '{name}'.")
    return declaration


def _block(owner: CommentedMap, key: str, where: str) -> CommentedMap:
    """The mapping under ``key``, which must exist."""
    block = owner.get(key)
    if not isinstance(block, dict):
        raise UnknownTarget(f"No {key} in {where}.")
    return block


def _walk(
    container: Any, keys: tuple[str | int, ...], where: str
) -> tuple[Any, str | int]:
    """The container holding the last of ``keys``, and that key."""
    for depth, key in enumerate(keys[:-1]):
        if isinstance(container, dict) and isinstance(key, str) and key in container:
            container = container[key]
        elif (
            isinstance(container, list)
            and isinstance(key, int)
            and key < len(container)
        ):
            container = container[key]
        else:
            raise UnknownTarget(
                f"No '{_dotted(keys[0], keys[1 : depth + 1])}' in {where}."
            )
    if not isinstance(container, (dict, list)):
        raise UnknownTarget(
            f"'{_dotted(keys[0], keys[1:-1])}' in {where} is not a container."
        )
    return container, keys[-1]


def _set_entry(
    container: Any, key: str | int, value: Any, position: int | None, where: str
) -> Any:
    """Set ``container[key]``; returns the old value, or _MISSING."""
    if isinstance(container, list):
        if not isinstance(key, int) or key >= len(container):
            raise UnknownTarget(f"No list entry {key!r} in {where}.")
        old = _detach(container[key])
        container[key] = _fresh(value, container[key])
        return old
    if not isinstance(key, str):
        raise UnknownTarget(f"A mapping in {where} has no entry {key!r}.")
    if key in container:
        old = _detach(container[key])
        container[key] = _fresh(value, container[key])
        return old
    _insert_key(container, len(container) if position is None else position, key, value)
    return _MISSING


def _set_in_block(
    owner: CommentedMap,
    block_key: str,
    order: tuple[str, ...],
    name: str,
    value: Any,
    position: int | None,
    where: str,
) -> Any:
    """Set ``owner[block_key][name]``, adding the block when it is missing.

    ``position`` places a new key in the block, or a new block in ``owner``.
    """
    block = owner.get(block_key)
    if block is None:
        if block_key in owner:
            owner[block_key] = _fresh({name: value})
        elif position is not None:
            _insert_key(owner, position, block_key, {name: value})
        else:
            _insert_ordered(owner, block_key, {name: value}, order)
        return _MISSING
    if not isinstance(block, dict):
        raise UnknownTarget(f"{block_key} in {where} is not a mapping.")
    return _set_entry(block, name, value, position, where)


def _unset_in_block(
    owner: CommentedMap, block_key: str, name: str, where: str
) -> tuple[Any, int]:
    """Delete ``owner[block_key][name]`` and an emptied block.

    Returns:
        The old value and where it was: the key's index in the block, or the
        block's index in ``owner`` when the block went too.
    """
    block = owner.get(block_key)
    if not isinstance(block, dict) or name not in block:
        raise UnknownTarget(f"No {block_key} entry '{name}' in {where}.")
    position = list(block).index(name)
    old = _detach(block[name])
    carry = _delete_key(block, name)
    if not block:
        position = list(owner).index(block_key)
        _delete_key(owner, block_key, carry)
    return old, position


def _fresh(value: Any, like: Any = None, *, flow: bool = False) -> Any:
    """A new tree value for ``value``, styled like the value it replaces.

    Containers become new ruamel containers, flow style when the replaced
    container was or ``flow`` is set; a list of scalars is flow style too
    unless it replaces a block list. A string replacing a quoted string keeps
    the quotes.
    """
    if isinstance(value, dict):
        fresh: Any = CommentedMap()
        for key, item in value.items():
            fresh[key] = _fresh(item)
    elif isinstance(value, (list, tuple)):
        fresh = CommentedSeq(_fresh(item) for item in value)
        scalars = not any(isinstance(item, (dict, list, tuple)) for item in value)
        block = isinstance(like, CommentedSeq) and not like.fa.flow_style()
        if scalars and not block:
            fresh.fa.set_flow_style()
    elif (
        isinstance(value, str)
        and not isinstance(value, ScalarString)
        and isinstance(like, ScalarString)
    ):
        return type(like)(value)
    else:
        return value
    styled = isinstance(like, (CommentedMap, CommentedSeq)) and like.fa.flow_style()
    if flow or styled:
        fresh.fa.set_flow_style()
    return fresh


def _detach(value: Any) -> Any:
    """A tree value as plain containers and strings.

    Numbers keep ruamel's scalar types, which remember how they were written
    (``1e-3``, ``0x1F``), so an inverse writes them back the same way.
    """
    if isinstance(value, dict):
        return {_detach(key): _detach(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_detach(item) for item in value]
    if isinstance(value, str):
        return str(value)
    return value


def _insert_ordered(
    mapping: CommentedMap, key: str, value: Any, order: tuple[str, ...]
) -> None:
    """Insert a key after the last existing key that ranks before it.

    In a mapping that follows ``order`` this is also before the first key
    that ranks after it; in one that does not (``execution`` before
    ``inputs``), a new ``params`` still follows ``inputs``. Keys missing from
    ``order`` rank last.
    """
    rank = {name: index for index, name in enumerate(order)}
    mine = rank.get(key, len(order))
    earlier = [
        index
        for index, existing in enumerate(mapping)
        if rank.get(existing, len(order)) < mine
    ]
    _insert_key(mapping, earlier[-1] + 1 if earlier else 0, key, value)


def _insert_key(
    mapping: CommentedMap,
    position: int,
    key: str,
    value: Any,
    *,
    heading: bool = False,
    flow: bool = False,
) -> None:
    """Insert a key at ``position``.

    A key appended after an entry that carries trailing blank lines or
    comment lines (the blank line that separates two steps) would land after
    them, so they move to the new key and the new line goes where a person
    would put it. ``_delete_key`` hands them back.

    Args:
        mapping: The mapping.
        position: Where the key goes.
        key: The key.
        value: Its value.
        heading: Move those lines below the new key when it goes before
            another key too, for lines that head what follows it.
        flow: Write a mapping or list value in flow style.
    """
    position = min(position, len(mapping))
    tail = ""
    if position and (heading or position == len(mapping)):
        tail = _take_tail(mapping, list(mapping)[position - 1])
    mapping.insert(position, key, _fresh(value, flow=flow))
    if tail:
        _give_tail(mapping, key, tail)


def _delete_key(mapping: CommentedMap, key: str, carry: str = "") -> str:
    """Delete a key, handing its trailing lines to the entry before it.

    Args:
        mapping: The mapping.
        key: The key to delete.
        carry: Trailing lines from a deleted child to pass on as well.

    Returns:
        The trailing lines that had nowhere to go (``key`` was first), for the
        caller to pass on when it deletes the emptied mapping.
    """
    keys = list(mapping)
    index = keys.index(key)
    tail = _take_tail(mapping, key) + carry
    del mapping[key]
    if tail and index == 0:
        return tail
    if tail:
        _give_tail(mapping, keys[index - 1], tail)
    return ""


def _deepest(container: Any, key: Any) -> tuple[Any, Any]:
    """The container and key where ruamel keeps an entry's trailing comment.

    That is the entry itself for a scalar or flow value, else the last entry
    of its block value, recursively.
    """
    value = container[key]
    while (
        isinstance(value, (CommentedMap, CommentedSeq))
        and len(value)
        and not value.fa.flow_style()
    ):
        container = value
        key = (
            next(reversed(value)) if isinstance(value, CommentedMap) else len(value) - 1
        )
        value = container[key]
    return container, key


def _comment_slot(container: Any) -> int:
    """Index of the end of line comment in an entry of ``container.ca.items``."""
    return 2 if isinstance(container, CommentedMap) else 0


def _token(container: Any, key: Any) -> CommentToken | None:
    entry = container.ca.items.get(key)
    slot = _comment_slot(container)
    if not isinstance(entry, list) or len(entry) <= slot:
        return None
    token = entry[slot]
    return token if isinstance(token, CommentToken) else None


def _take_tail(mapping: CommentedMap, key: str) -> str:
    """Remove and return the lines after the first of an entry's trailing token.

    ruamel keeps an end of line comment, and the blank and comment lines that
    follow it, in one token on the deepest last entry. The first line stays
    with the entry; the rest is returned.
    """
    container, deep_key = _deepest(mapping, key)
    token = _token(container, deep_key)
    if token is None:
        return ""
    value = token.value
    end = value.find("\n")
    if end < 0 or end == len(value) - 1:
        return ""
    token.value = value[: end + 1]
    return value[end + 1 :]


def _give_tail(mapping: CommentedMap, key: str, tail: str) -> bool:
    """Attach trailing lines to an entry; False when its token cannot take them."""
    container, deep_key = _deepest(mapping, key)
    token = _token(container, deep_key)
    if token is not None:
        if not token.value.endswith("\n"):
            return False
        token.value = token.value + tail
        return True
    entry = container.ca.items.get(deep_key)
    slot = _comment_slot(container)
    if entry is None:
        entry = container.ca.items[deep_key] = [None, None, None, None]
    elif not isinstance(entry, list) or len(entry) <= slot or entry[slot] is not None:
        return False
    entry[slot] = CommentToken("\n" + tail, CommentMark(0))
    return True

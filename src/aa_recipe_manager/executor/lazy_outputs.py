# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""``LazyStepOutputs``: a step's output dict with some values evicted.

Used only for steps whose output has been checkpointed and evicted from
live memory during a run (see ``executor/engine/runner.py``'s
``_evict``). A step that is never evicted keeps a plain ``dict`` in
``ExecutionResult.outputs`` — this wrapper is introduced lazily, only
where needed.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.refs import (
    CheckpointRef,
    FoldedCheckpointRef,
    resolve_ref,
)

if TYPE_CHECKING:
    from aa_recipe_manager.executor.tiered import CheckpointStore


class LazyStepOutputs(MutableMapping):
    """A step's ``{output_name: value}`` dict, transparently reloading
    evicted entries from the checkpoint store on access.

    Reads and equality behave exactly like the plain dict this replaces;
    ``.raw(name)`` peeks at the underlying slot (a ref or a real value)
    without resolving it, for tests/introspection.
    """

    def __init__(self, raw: dict[str, Any], store: CheckpointStore | None) -> None:
        self._raw = raw
        self._store = store

    def __getitem__(self, name: str) -> Any:
        value = self._raw[name]
        if isinstance(value, (CheckpointRef, FoldedCheckpointRef)):
            return resolve_ref(value, self._store)
        return value

    def __setitem__(self, name: str, value: Any) -> None:
        self._raw[name] = value

    def __delitem__(self, name: str) -> None:
        del self._raw[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __contains__(self, name: object) -> bool:
        return name in self._raw

    def raw(self, name: str) -> Any:
        """The underlying slot for ``name``, without resolving a ref."""
        return self._raw[name]

    def __repr__(self) -> str:
        return f"LazyStepOutputs({self._raw!r})"

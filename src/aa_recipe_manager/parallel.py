# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Fan-out / fan-in primitives shared by the executor and code generator.

Stage 8 adds ``map_over`` (segment-parallel), ``sweep`` (parameter-parallel),
and ``collect`` (fan-in). This module holds the pure helpers both the
sequential executor and the notebook backend consume so their semantics stay
identical:

* :func:`group_mapped_chains` — partition a DAG's steps into mapped chains
  (consecutive steps sharing a ``map_over`` source) and single-step sweeps.
* :func:`expand_sweep` — turn a :class:`SweepDeclaration` into the ordered
  list of per-invocation param dicts (``zip`` or ``grid``).
* :func:`instance_discriminator` / :func:`derive_instance_hash` — per-instance
  content addressing: an instance's checkpoint hash is the step's base hash
  folded with a discriminator (the sweep params, and the mapped item value
  when JSON-serializable, else its ordinal index).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG, SweepDeclaration


# ---------------------------------------------------------------------------
# Mapped-chain grouping
# ---------------------------------------------------------------------------


@dataclass
class MappedChain:
    """A run of steps that fan out together.

    ``source_ref`` is the ``${step.output}`` a map chain iterates over, or
    ``None`` for a pure ``sweep`` step (whose cardinality comes from its
    ``sweep`` block, not a runtime list). ``member_ids`` lists the chain's
    steps in execution order.
    """

    source_ref: str | None
    member_ids: list[str] = field(default_factory=list)


def group_mapped_chains(dag: PipelineDAG) -> list[MappedChain]:
    """Partition ``dag.topological_order`` into mapped chains and sweeps.

    Consecutive steps declaring the same ``map_over`` source form one chain
    (design.md §1.6); a swept-but-not-mapped step forms its own single-member
    chain. Non-mapped, non-swept steps (including collectors) are not chains
    and are absent from the result.
    """
    chains: list[MappedChain] = []
    current: MappedChain | None = None
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.is_mapped:
            src = node.map_source
            if current is not None and current.source_ref == src:
                current.member_ids.append(step_id)
            else:
                current = MappedChain(source_ref=src, member_ids=[step_id])
                chains.append(current)
        elif node.is_swept:
            current = None
            chains.append(MappedChain(source_ref=None, member_ids=[step_id]))
        else:
            current = None
    return chains


# ---------------------------------------------------------------------------
# Sweep expansion
# ---------------------------------------------------------------------------


def expand_sweep(sweep: SweepDeclaration) -> list[dict[str, Any]]:
    """Return the ordered per-invocation param dicts for a sweep.

    ``zip`` pairs the lists positionally (all lists must be equal length —
    enforced by the DAG validator); ``grid`` takes the cartesian product in
    declaration order.
    """
    names = list(sweep.param_lists.keys())
    lists = [sweep.param_lists[name] for name in names]
    if not names:
        return []
    if sweep.mode == "grid":
        combos = itertools.product(*lists)
    else:  # "zip"
        combos = zip(*lists)
    return [dict(zip(names, values)) for values in combos]


# ---------------------------------------------------------------------------
# Per-instance content addressing
# ---------------------------------------------------------------------------


_NO_ITEM = object()
_UNSERIALIZABLE = object()


def _json_native(value: Any) -> Any:
    """Return ``value`` if built only from JSON-native types, else a sentinel.

    Used so a mapped item participates in content addressing *only* when it is
    deterministically serializable (e.g. a file-path string). Non-native
    values (xarray Datasets, EchoData, …) fall back to the ordinal index —
    ``repr``/``str`` of those is not stable across runs, so it must never enter
    a content hash.
    """
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        out = [_json_native(v) for v in value]
        return _UNSERIALIZABLE if any(v is _UNSERIALIZABLE for v in out) else out
    if isinstance(value, dict):
        out_d: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                return _UNSERIALIZABLE
            nv = _json_native(v)
            if nv is _UNSERIALIZABLE:
                return _UNSERIALIZABLE
            out_d[k] = nv
        return out_d
    return _UNSERIALIZABLE


def instance_discriminator(
    *,
    index: int,
    item: Any = _NO_ITEM,
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the per-instance discriminator folded into the instance hash.

    Prefers content (the sweep params and a JSON-native mapped item) so
    identical work dedupes across runs and — for hashable items like file
    paths — becomes independently survey-tier addressable (global_cache_plan
    §13). Falls back to the ordinal ``index`` when the item is not
    JSON-native.
    """
    disc: dict[str, Any] = {}
    if param_overrides:
        disc["params"] = param_overrides
    if item is not _NO_ITEM:
        native = _json_native(item)
        if native is _UNSERIALIZABLE:
            disc["item_index"] = index
        else:
            disc["item"] = native
    if not disc:
        disc["index"] = index
    return disc


def derive_instance_hash(base_hash: str, discriminator: dict[str, Any]) -> str:
    """Content-address one instance as ``H(base_step_hash + discriminator)``."""
    payload = json.dumps(discriminator, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{base_hash}\x00{payload}".encode()).hexdigest()

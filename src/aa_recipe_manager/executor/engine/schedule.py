# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Unit graph and wavefront readiness for the pipeline runner.

A *unit* is the granularity the runner schedules: either a single step or a
whole mapped/swept chain (all its members). Units are derived from
``dag.topological_order`` plus the mapped-chain grouping from
:func:`~aa_recipe_manager.parallel.group_mapped_chains`, so the ordering the
sequential executor relied on is preserved: at concurrency 1 the wavefront
degenerates to topological order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aa_recipe_manager.parallel import group_mapped_chains

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG


@dataclass(frozen=True)
class Unit:
    """One schedulable unit of a run.

    ``member_ids`` is a single-element tuple for a plain step and the full
    member list for a mapped/swept chain. ``order`` is the unit's position in
    topological order (its first member's index), used to break ties so a
    single-concurrency run is deterministic and byte-identical to the old
    sequential loop. ``map_source`` is the chain's ``map_over`` reference (or
    ``None`` for a plain step or a pure sweep).
    """

    unit_id: str
    member_ids: tuple[str, ...]
    is_chain: bool
    order: int
    map_source: str | None = None

    @property
    def first(self) -> str:
        return self.member_ids[0]


@dataclass
class UnitGraph:
    """Units plus their dependency edges, keyed by ``unit_id``."""

    units: dict[str, Unit] = field(default_factory=dict)
    #: ``unit_id -> set(unit_id)`` this unit waits on.
    deps: dict[str, set[str]] = field(default_factory=dict)
    #: ``unit_id -> set(unit_id)`` waiting on this unit.
    dependents: dict[str, set[str]] = field(default_factory=dict)
    #: Units in topological order (for deterministic single-concurrency runs).
    order: list[str] = field(default_factory=list)
    #: ``step_id -> unit_id`` (every member of a chain maps to its chain unit).
    _step_to_unit: dict[str, str] = field(default_factory=dict)

    def unit_of_step(self, step_id: str) -> Unit:
        return self.units[self._step_to_unit[step_id]]


def build_unit_graph(dag: PipelineDAG) -> UnitGraph:
    """Collapse a DAG's steps into schedulable units and wire their edges.

    Consecutive steps sharing a ``map_over`` source become one chain unit
    (matching the sequential executor's mapped-chain grouping); every other
    step is its own unit. Dependency edges are lifted from ``dag.edges`` and
    ``depends_on`` and collapsed to the unit level (an intra-chain edge never
    becomes a unit self-dependency).
    """
    chains = group_mapped_chains(dag)
    chain_of: dict[str, str] = {}
    for chain in chains:
        uid = f"chain:{chain.member_ids[0]}"
        for mid in chain.member_ids:
            chain_of[mid] = uid

    graph = UnitGraph()
    order_index = {sid: i for i, sid in enumerate(dag.topological_order)}

    # Materialize units in topological order.
    seen: set[str] = set()
    for step_id in dag.topological_order:
        uid = chain_of.get(step_id, f"step:{step_id}")
        if uid in seen:
            graph._step_to_unit[step_id] = uid
            continue
        seen.add(uid)
        if uid.startswith("chain:"):
            chain = next(c for c in chains if f"chain:{c.member_ids[0]}" == uid)
            members = tuple(chain.member_ids)
            unit = Unit(
                unit_id=uid,
                member_ids=members,
                is_chain=True,
                order=order_index[members[0]],
                map_source=chain.source_ref,
            )
        else:
            unit = Unit(
                unit_id=uid,
                member_ids=(step_id,),
                is_chain=False,
                order=order_index[step_id],
            )
        graph.units[uid] = unit
        graph.deps[uid] = set()
        graph.dependents[uid] = set()
        for mid in unit.member_ids:
            graph._step_to_unit[mid] = uid

    # Collapse step edges (data + depends_on) to unit edges.
    def _add_edge(src_step: str, dst_step: str) -> None:
        src = graph._step_to_unit[src_step]
        dst = graph._step_to_unit[dst_step]
        if src == dst:
            return
        graph.deps[dst].add(src)
        graph.dependents[src].add(dst)

    for edge in dag.edges:
        _add_edge(edge.source_step_id, edge.target_step_id)
    for step_id in dag.topological_order:
        for dep in dag.nodes[step_id].step.depends_on or []:
            if dep in graph._step_to_unit:
                _add_edge(dep, step_id)

    graph.order = sorted(graph.units, key=lambda uid: graph.units[uid].order)
    return graph


class WavefrontScheduler:
    """Tracks which units are ready as their dependencies complete.

    Ready units are returned in topological order so a concurrency-1 backend
    reproduces the sequential order exactly. Higher-concurrency backends pull
    the whole ready frontier at once.
    """

    def __init__(self, graph: UnitGraph) -> None:
        self._graph = graph
        self._remaining: dict[str, set[str]] = {
            uid: set(deps) for uid, deps in graph.deps.items()
        }
        self._done: set[str] = set()
        self._dispatched: set[str] = set()

    def ready(self) -> list[Unit]:
        """Units whose dependencies are all done and not yet dispatched."""
        out = [
            self._graph.units[uid]
            for uid in self._graph.order
            if uid not in self._dispatched and not self._remaining[uid]
        ]
        return out

    def mark_dispatched(self, unit: Unit) -> None:
        self._dispatched.add(unit.unit_id)

    def mark_done(self, unit: Unit) -> None:
        """Record a unit as complete and unblock its dependents."""
        self._done.add(unit.unit_id)
        self._dispatched.add(unit.unit_id)
        for dep in self._graph.dependents.get(unit.unit_id, ()):
            self._remaining[dep].discard(unit.unit_id)

    def all_done(self) -> bool:
        return len(self._done) == len(self._graph.units)

    def pending_count(self) -> int:
        return len(self._graph.units) - len(self._done)

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PipelineRunner: the one run loop shared by every executor backend.

The runner owns everything true of a run regardless of where its steps
execute: planning (:func:`plan_execution`), the wavefront schedule, client-side
disposition bookkeeping (pruned / cache-hit / marker / skipped), folding
mapped-chain fan-out back into lists, and the run manifest. A
:class:`~aa_recipe_manager.executor.engine.backends.base.SchedulerBackend`
supplies only *where* a data task runs.

At concurrency 1 (the :class:`InlineBackend`) the wavefront degenerates to
topological order, so the ordered ``executed_steps`` / ``skipped_steps`` /
``pruned_steps`` lists, the run log, and the manifest are byte-identical to the
old single-threaded ``SequentialExecutor``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor.base import StepRecord
from aa_recipe_manager.executor.checkpoint import plan_execution
from aa_recipe_manager.executor.engine.hints import (
    resolve_unit_dask_config,
    resolve_unit_prefect_config,
)
from aa_recipe_manager.executor.engine.logcapture import capture_output
from aa_recipe_manager.executor.engine.schedule import (
    Unit,
    UnitGraph,
    WavefrontScheduler,
    build_unit_graph,
)
from aa_recipe_manager.executor.engine.step import execute_step
from aa_recipe_manager.executor.engine.tasks import (
    TASK_LOG_ATTR,
    ChainInstanceTask,
    CheckpointRef,
    StepTask,
    TaskClosure,
    ValueRef,
)
from aa_recipe_manager.executor.invocation import RuntimeContext
from aa_recipe_manager.executor.lazy_outputs import LazyStepOutputs
from aa_recipe_manager.executor.refs import FoldedCheckpointRef
from aa_recipe_manager.executor.runtime_context import execution_context
from aa_recipe_manager.parallel import expand_sweep
from aa_recipe_manager.resolver.params import extract_edge_refs, parse_ref

if TYPE_CHECKING:
    from aa_recipe_manager.executor.base import ExecutionResult, ProgressCallback
    from aa_recipe_manager.executor.engine.backends.base import SchedulerBackend
    from aa_recipe_manager.executor.engine.context import RunContext
    from aa_recipe_manager.executor.engine.tasks import MemberResult, TaskResult
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG


def _fold_instance_outputs(
    spec_outputs: dict[str, Any],
    per_instance: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse a mapped member's per-instance output dicts into lists.

    ``{output_name: [instance_0_value, instance_1_value, ...]}`` for each
    declared output, so a downstream ``collect`` step receives the fan-in list
    (design.md §1.6). Members with no declared outputs fold to ``{}``.
    """
    if not spec_outputs:
        return {}
    return {
        out_name: [inst.get(out_name) for inst in per_instance]
        for out_name in spec_outputs
    }


@dataclass
class _EvictionPlan:
    """Per-run bookkeeping for evicting checkpointed step outputs.

    Built once from the DAG's edges (see ``PipelineRunner._plan_eviction``);
    ``remaining_consumers``/``evicted`` are mutated as units complete.
    """

    #: step_id -> its checkpoint-eligible output port names.
    producer_ports: dict[str, list[str]]
    #: (step_id, output_name) -> count of distinct consuming units not yet done.
    remaining_consumers: dict[tuple[str, str], int]
    #: unit_id -> ports it consumes, for O(1) lookup as each unit finishes.
    consumer_of_unit: dict[str, list[tuple[str, str]]]
    evicted: set[tuple[str, str]] = field(default_factory=set)


class PipelineRunner:
    """Execute a planned DAG through a scheduling backend."""

    def __init__(self, backend: SchedulerBackend) -> None:
        self._backend = backend

    # -- public entry point --------------------------------------------------

    def run(
        self,
        *,
        ctx: RunContext,
        result: ExecutionResult,
        runtime: RuntimeContext,
        progress: ProgressCallback,
        log_sink: Any,
    ) -> None:
        dag = ctx.dag
        self._ctx = ctx
        self._dag = dag
        self._result = result
        self._runtime = runtime
        self._progress = progress
        self._log_sink = log_sink
        self._checkpoints = ctx.checkpoints
        self._policy = ctx.policy
        self._step_hashes = ctx.step_hashes
        self._pipeline_inputs = ctx.pipeline_inputs
        # Memoized raw-input record stamped onto sidecars; resolved lazily once
        # the reader step's output lands in result.outputs (see _raw_inputs).
        self._raw_inputs_dict: dict[str, Any] | None = None
        self._raw_inputs_found = False
        # Eviction bookkeeping (see _plan_eviction / _evict_if_ready): steps
        # actually checkpointed this run, and captured instance hashes for a
        # chain member whose folded output is being evicted.
        self._durable: set[str] = set()
        self._chain_instance_hashes: dict[tuple[str, str], tuple[str, ...]] = {}

        step_ids = list(dag.topological_order)
        self._total = len(step_ids)
        self._step_index = {sid: i for i, sid in enumerate(step_ids, start=1)}

        self._plan = plan_execution(
            dag,
            self._checkpoints,
            force=ctx.force,
            regenerate=ctx.regenerate,
            outputs_loc=ctx.outputs_loc,
        )
        blocking = [s for s in self._plan.blockers if s not in self._policy]
        if blocking:
            result.logs.append(
                "resume frontier limited by uncheckpointed step(s): "
                f"{', '.join(blocking)}"
            )

        self._wctx = ctx.worker_context()
        # The inline backend shares the client's heap, so its worker must reuse
        # the *same* store object rather than rebuild one from URLs — that keeps
        # save/load state (and any test monkeypatch) consistent between worker
        # and client. Every other backend (including threaded Dask, which sets
        # in_process=False so threads and processes behave alike) rebuilds its
        # own store from the picklable roots.
        if self._backend.in_process and ctx.checkpoints is not None:
            object.__setattr__(self._wctx, "_store_cache", ctx.checkpoints)

        graph = build_unit_graph(dag)
        self._unit_graph = graph
        self._eviction = self._plan_eviction(dag, graph)

        self._backend.start(self._wctx)
        try:
            self._announce_backend()
            self._wavefront(graph)
        finally:
            self._backend.close()

    def _plan_eviction(
        self, dag: PipelineDAG, graph: UnitGraph
    ) -> _EvictionPlan | None:
        """Precompute, once per run, when each checkpoint-eligible step's
        output can be freed from live memory.

        A ``(step_id, output_name)`` port becomes evictable once every unit
        that reads it (per ``dag.edges``, mapped onto the schedule's unit
        graph so a whole mapped/swept chain retires together) has finished —
        see ``_evict_if_ready``. Returns ``None`` (disabling eviction
        entirely) whenever checkpointing itself is off, so the feature costs
        nothing under ``no_checkpoints=True``.
        """
        if self._checkpoints is None:
            return None

        # A step is a candidate whenever it will *actually* end up durable
        # this run — either because this run's policy says to write it
        # (self._policy), or because it's already a cache hit regardless of
        # this run's policy (self._plan.loadable: e.g. written by an earlier
        # run under a different checkpoint_mode). Policy membership alone
        # would miss the latter, real, case.
        candidates = self._policy | self._plan.loadable
        producer_ports: dict[str, list[str]] = {}
        for step_id in candidates:
            node = dag.nodes.get(step_id)
            if node is None or node.spec.sink or not node.spec.outputs:
                continue
            producer_ports[step_id] = list(node.spec.outputs)
        if not producer_ports:
            return None

        consumer_units: dict[tuple[str, str], set[str]] = {
            (step_id, out): set()
            for step_id, outs in producer_ports.items()
            for out in outs
        }
        for edge in dag.edges:
            # depends_on edges carry source_output="" (pure ordering, no data
            # read) and must not count as consumption.
            if not edge.source_output:
                continue
            key = (edge.source_step_id, edge.source_output)
            if key not in consumer_units:
                continue
            consumer_units[key].add(graph.unit_of_step(edge.target_step_id).unit_id)

        remaining_consumers = {key: len(units) for key, units in consumer_units.items()}
        consumer_of_unit: dict[str, list[tuple[str, str]]] = {}
        for key, units in consumer_units.items():
            for uid in units:
                consumer_of_unit.setdefault(uid, []).append(key)

        return _EvictionPlan(
            producer_ports=producer_ports,
            remaining_consumers=remaining_consumers,
            consumer_of_unit=consumer_of_unit,
        )

    # -- eviction --------------------------------------------------------------

    def _mark_done(self, sched: WavefrontScheduler, unit: Unit) -> None:
        """Mark ``unit`` complete, then evict any now-unneeded output.

        Replaces every direct ``sched.mark_done(unit)`` call so eviction
        fires uniformly regardless of which disposition (pruned / cache-hit /
        marker / skip-sink / freshly computed / client-executed) produced the
        completion.
        """
        sched.mark_done(unit)
        if self._eviction is not None:
            self._evict_if_ready(unit)

    def _evict_if_ready(self, unit: Unit) -> None:
        """After ``unit`` finishes, evict any producer port whose last
        consumer just ran, or that never had a consumer to begin with."""
        plan = self._eviction
        for key in plan.consumer_of_unit.get(unit.unit_id, ()):
            plan.remaining_consumers[key] -= 1
            if plan.remaining_consumers[key] == 0:
                self._try_evict(*key)
        for step_id in unit.member_ids:
            for out in plan.producer_ports.get(step_id, ()):
                if plan.remaining_consumers.get((step_id, out), -1) == 0:
                    self._try_evict(step_id, out)

    def _try_evict(self, step_id: str, output_name: str) -> None:
        """Evict ``(step_id, output_name)``, using *its own* producing unit
        to decide plain-vs-chain — not whichever unit's completion happened
        to trigger this check (a consumer's unit, for the decrement path)."""
        plan = self._eviction
        key = (step_id, output_name)
        if key in plan.evicted or step_id not in self._durable:
            return
        if not self._runtime.has_step(step_id) or not self._runtime.has_output(
            step_id, output_name
        ):
            return
        producer_unit = self._unit_graph.unit_of_step(step_id)
        hash_key: tuple[str, str] | None = None
        if producer_unit.is_chain:
            # A member's instance hashes are shared by all of its output ports,
            # so read without consuming: every port needs the same tuple.
            hash_key = (producer_unit.unit_id, step_id)
            instance_hashes = self._chain_instance_hashes.get(hash_key)
            if instance_hashes is None:
                return
            ref = FoldedCheckpointRef(step_id, output_name, instance_hashes)
        else:
            ref = CheckpointRef(step_id, output_name)
        self._evict(step_id, output_name, ref)
        plan.evicted.add(key)
        if hash_key is not None and all(
            (step_id, out) in plan.evicted
            for out in plan.producer_ports.get(step_id, ())
        ):
            self._chain_instance_hashes.pop(hash_key, None)

    def _evict(
        self, step_id: str, output_name: str, ref: CheckpointRef | FoldedCheckpointRef
    ) -> None:
        """Drop the live object for one output port, keeping a lazy ref.

        ``RuntimeContext`` and ``result.outputs`` are two separate dicts that
        happen to share the same value references (``RuntimeContext.record``
        only shallow-copies its outer dict) — both must be mutated, or the
        object stays alive via whichever side wasn't touched.
        """
        self._runtime.evict(step_id, output_name, ref)
        outer = self._result.outputs.get(step_id)
        if outer is None:
            return
        if not isinstance(outer, LazyStepOutputs):
            outer = LazyStepOutputs(dict(outer), self._checkpoints)
            self._result.outputs[step_id] = outer
        outer[output_name] = ref

    def _announce_backend(self) -> None:
        """Record what is actually executing this run, in the run log.

        A run that later looks slow, or is interrupted with no summary, leaves
        this line behind — so "was it even running concurrently?" is answerable
        from the log rather than from the command the user believes they typed.
        """
        backend = self._backend
        name = type(backend).__name__.replace("Backend", "").lower()
        concurrency = getattr(backend, "max_concurrency", 1)
        line = f"executor: {name} (max {concurrency} concurrent task(s))"
        dashboard = getattr(backend, "dashboard_link", None)
        if callable(dashboard):
            link = dashboard()
            if link:
                line += f"\ndashboard: {link}"
        self._result.logs.append(line)
        self._log_sink.write(f"{line}\n")
        self._log_sink.flush()

    # -- wavefront loop ------------------------------------------------------

    def _wavefront(self, graph) -> None:
        sched = WavefrontScheduler(graph)
        backend = self._backend
        in_flight: dict[Any, tuple[Unit, Any]] = {}
        pending: list[tuple[Unit, Any]] = []
        # unit_id -> {"remaining": int, "results": list[(order, TaskResult)]}
        chain_state: dict[str, dict[str, Any]] = {}
        #: Units already announced to the progress callback (see step 2).
        started: set[str] = set()

        while not sched.all_done():
            progressed = False

            # 1. Expand every newly ready unit: client-handle the cheap
            #    dispositions immediately, or queue its data tasks.
            for unit in sched.ready():
                sched.mark_dispatched(unit)
                progressed = True
                tasks = self._open_unit(unit, sched)
                if tasks is None:
                    continue  # fully client-handled
                chain_state[unit.unit_id] = {
                    "remaining": len(tasks),
                    "n_tasks": len(tasks),
                    "results": [],
                    "start": time.perf_counter(),
                }
                for task in tasks:
                    pending.append((unit, task))
                if not tasks:
                    # A unit with no instances (e.g. a ``map_over`` over an empty
                    # list, or an empty ``sweep``): fold to empty outputs now,
                    # since no task completion will ever trigger finalization.
                    self._finalize_unit(unit, chain_state.pop(unit.unit_id))
                    self._mark_done(sched, unit)

            # 2. Submit queued tasks up to the concurrency limit. A unit is
            #    announced (progress + log header) when its first task is
            #    actually submitted, not when it was expanded — otherwise every
            #    ready unit would report "started" before any of them ran, and
            #    a progress reporter could not pair starts with ends.
            while pending and len(in_flight) < backend.max_concurrency:
                unit, task = pending.pop(0)
                if unit.unit_id not in started:
                    started.add(unit.unit_id)
                    self._start_unit(unit, chain_state[unit.unit_id]["n_tasks"])
                handle = backend.submit(task, config=self._task_config(unit))
                in_flight[handle] = (unit, task)
                progressed = True

            # 3. Wait for one task to complete, then re-evaluate readiness.
            if in_flight:
                handle = next(backend.as_completed(list(in_flight)))
                unit, task = in_flight.pop(handle)
                try:
                    task_result = backend.result(handle)
                except BaseException as exc:
                    # Propagate the worker's exception unchanged: execute_step
                    # already raises a fully attributed PipelineExecutionError,
                    # and a store-policy error (e.g. pickle-in-survey-tier) must
                    # surface as its own type, just as it did in the old loop.
                    self._on_unit_error(unit, exc, chain_state)
                    raise
                self._collect_task(unit, task, task_result, chain_state)
                if chain_state[unit.unit_id]["remaining"] == 0:
                    self._finalize_unit(unit, chain_state.pop(unit.unit_id))
                    self._mark_done(sched, unit)
                progressed = True

            if not progressed and not sched.all_done():
                raise PipelineExecutionError(
                    "<scheduler>",
                    "execution stalled with unresolved units "
                    f"({sched.pending_count()} pending)",
                )

    # -- unit opening --------------------------------------------------------

    def _open_unit(
        self, unit: Unit, sched: WavefrontScheduler
    ) -> list[Any] | None:
        """Client-handle a cheap unit (returns ``None``) or return its tasks.

        A step unit is one task; a chain unit expands to one task per instance.
        Pruned / cache-hit / marker / skip-sink dispositions and side-effect
        (sink) steps are handled entirely on the client here.
        """
        if unit.is_chain:
            return self._open_chain(unit, sched)
        return self._open_step(unit, sched)

    def _open_step(self, unit: Unit, sched: WavefrontScheduler) -> list[Any] | None:
        step_id = unit.first
        node = self._dag.nodes[step_id]
        result = self._result
        plan = self._plan

        if self._ctx.skip_sinks and node.spec.sink:
            result.logs.append(f"skip sink: {step_id}")
            self._runtime.record(step_id, {})
            result.step_dispositions[step_id] = StepRecord(
                disposition="skipped",
                step_hash=self._step_hashes.get(step_id),
            )
            self._mark_done(sched, unit)
            return None

        index = self._step_index[step_id]
        total = self._total

        if step_id in plan.pruned:
            self._progress.on_step_start(step_id, index, total)
            result.pruned_steps.append(step_id)
            result.logs.append(f"pruned: {step_id} (0.000s)")
            result.step_dispositions[step_id] = StepRecord(
                disposition="pruned",
                step_hash=self._step_hashes.get(step_id),
                elapsed_seconds=0.0,
            )
            self._progress.on_step_end(
                step_id, index, total, skipped=True, elapsed=0.0
            )
            self._mark_done(sched, unit)
            return None

        if step_id in plan.loadable:
            self._progress.on_step_start(step_id, index, total)
            start = time.perf_counter()
            outputs = self._checkpoints.load(step_id)
            tier = self._checkpoints.hit_tier(step_id) or "user"
            self._durable.add(step_id)
            self._runtime.record(step_id, outputs)
            result.outputs[step_id] = outputs
            result.skipped_steps.append(step_id)
            elapsed = time.perf_counter() - start
            result.logs.append(f"cache hit: {step_id} [{tier}] ({elapsed:.3f}s)")
            result.step_dispositions[step_id] = StepRecord(
                disposition=f"hit-{tier}-cache",
                step_hash=self._step_hashes.get(step_id),
                tier=tier,
                elapsed_seconds=elapsed,
                artifacts=self._checkpoints.artifact_urls(step_id),
            )
            self._progress.on_step_end(
                step_id, index, total, skipped=True, elapsed=elapsed
            )
            self._mark_done(sched, unit)
            return None

        if step_id in plan.marker_hits:
            self._progress.on_step_start(step_id, index, total)
            self._runtime.record(step_id, {})
            result.outputs[step_id] = {}
            result.skipped_steps.append(step_id)
            result.logs.append(f"sink cache hit: {step_id} (0.000s)")
            result.step_dispositions[step_id] = StepRecord(
                disposition="marker",
                step_hash=self._step_hashes.get(step_id),
                tier="user",
                elapsed_seconds=0.0,
            )
            self._progress.on_step_end(
                step_id, index, total, skipped=True, elapsed=0.0
            )
            self._mark_done(sched, unit)
            return None

        if step_id not in plan.must_run:
            raise PipelineExecutionError(
                step_id, f"internal execution planner error for step {step_id!r}"
            )

        is_side_effect = node.spec.sink or not node.spec.outputs
        if is_side_effect or node.is_collector:
            # Sinks / no-output steps write artifacts to the shared outputs dir
            # (one matplotlib context, no cross-worker figure contention), and a
            # collector is a fan-in join over the whole per-instance list — both
            # run on the client so large folded values never cross the wire.
            self._run_client_step(node, is_side_effect=is_side_effect)
            self._mark_done(sched, unit)
            return None

        # NB: progress/log announcement happens at submit time (_start_unit).
        checkpoint = self._checkpoints is not None and step_id in self._policy
        closure = self._build_closure([step_id])
        return [
            StepTask(
                step_id=step_id,
                closure=closure,
                checkpoint=checkpoint,
                write_token=self._write_token(),
                raw_inputs=self._raw_inputs(),
            )
        ]

    def _open_chain(self, unit: Unit, sched: WavefrontScheduler) -> list[Any] | None:
        member_ids = list(unit.member_ids)
        members = [self._dag.nodes[mid] for mid in member_ids]
        result = self._result
        total = self._total

        # A downstream cached terminal pruned this chain: skip all members.
        if any(mid in self._plan.pruned for mid in member_ids):
            for mid in member_ids:
                idx = self._step_index.get(mid, 0)
                self._progress.on_step_start(mid, idx, total)
                result.pruned_steps.append(mid)
                result.step_dispositions[mid] = StepRecord(
                    disposition="pruned", step_hash=self._step_hashes.get(mid)
                )
                result.logs.append(f"pruned: {mid} (mapped chain)")
                self._progress.on_step_end(mid, idx, total, skipped=True, elapsed=0.0)
            self._mark_done(sched, unit)
            return None

        swept_members = [m for m in members if m.is_swept]
        if len(members) > 1 and swept_members:
            raise PipelineExecutionError(
                member_ids[0],
                "sweep within a multi-step mapped chain is not supported; keep "
                "the sweep on a single step (map_over + sweep on one step is "
                "allowed).",
            )

        # NB: progress/log announcement happens at submit time (_start_unit).

        # Resolve the fan-out source (single-item transparency: a non-list
        # source runs the chain exactly once).
        has_item = unit.map_source is not None
        if has_item:
            parsed = parse_ref(unit.map_source)
            if parsed is None:
                raise PipelineExecutionError(
                    member_ids[0],
                    f"map_over source {unit.map_source!r} is not a "
                    "${step.output} reference",
                )
            src_id, src_out = parsed
            try:
                source_val = self._runtime.get(src_id, src_out)
            except KeyError as exc:
                raise PipelineExecutionError(
                    member_ids[0],
                    f"map_over source {unit.map_source!r} is unavailable: {exc}",
                    original=exc,
                ) from exc
            source_list = source_val if isinstance(source_val, list) else [source_val]
        else:
            source_list = [None]

        swept_member = swept_members[0] if swept_members else None
        combos: list[dict[str, Any] | None] = (
            list(expand_sweep(swept_member.sweep_declaration))
            if swept_member is not None
            else [None]
        )

        checkpoint_members = frozenset(
            mid for mid in member_ids if mid in self._policy
        )
        closure = self._build_closure(member_ids)

        tasks: list[Any] = []
        flat = 0
        for item in source_list:
            for combo in combos:
                tasks.append(
                    ChainInstanceTask(
                        member_ids=tuple(member_ids),
                        instance_index=flat,
                        item=item,
                        combo=combo,
                        has_item=has_item,
                        closure=closure,
                        checkpoint_members=checkpoint_members,
                        write_token=self._write_token(),
                        raw_inputs=self._raw_inputs(),
                    )
                )
                flat += 1
        return tasks

    def _start_unit(self, unit: Unit, n_tasks: int) -> None:
        """Announce a unit as it begins: progress callback + run-log header.

        Called once per unit, when its *first* task is submitted, so every
        ``on_step_start`` is followed by that step's ``on_step_end`` rather than
        every ready unit reporting "started" up front.
        """
        total = self._total
        for mid in unit.member_ids:
            self._progress.on_step_start(mid, self._step_index.get(mid, 0), total)
        if unit.is_chain:
            self._log_sink.write(
                f"\n=== mapped chain {list(unit.member_ids)} x "
                f"{n_tasks} instance(s) ===\n"
            )
        else:
            step_id = unit.first
            self._log_sink.write(
                f"\n=== step {step_id} ({self._step_index[step_id]}/{total}) ===\n"
            )
        self._log_sink.flush()

    def _write_token(self) -> str | None:
        """A per-task artifact-dir token for concurrent same-hash writers.

        ``None`` for in-process backends so the inline run keeps the old
        ``<hash>/<run_id>/`` layout byte-for-byte; a short unique token
        otherwise so two workers of one run never share an artifact key.
        """
        if self._backend.in_process:
            return None
        from uuid import uuid4

        return uuid4().hex[:8]

    def _raw_inputs(self) -> dict[str, Any] | None:
        """The run's raw input file list, resolved lazily and memoized.

        Returns ``None`` until the reader step's output is available in
        ``result.outputs`` (so the reader's own task and any step built before it
        finalizes carry nothing); every task built afterward stamps the same
        record onto its checkpoint sidecar. Recomputed cheaply each call while
        unresolved, then cached once found.
        """
        if not self._raw_inputs_found:
            from aa_recipe_manager.provenance.recorder import build_raw_inputs_record

            record = build_raw_inputs_record(
                self._dag,
                self._result.outputs,
                self._pipeline_inputs,
                self._ctx.storage_options,
                run_id=self._ctx.run_id,
            )
            if record is not None:
                self._raw_inputs_dict = record.model_dump(mode="json")
                self._raw_inputs_found = True
        return self._raw_inputs_dict

    # -- client-executed steps (sinks + collectors) --------------------------

    def _run_client_step(self, node: DAGNode, *, is_side_effect: bool) -> None:
        """Run a step in-process on the client (sink or fan-in collector).

        Byte-identical to the old ``_run_steps`` must-run branch: same
        ``execution_context``, stdout/stderr redirect, checkpoint save+reload
        for data steps, side-effect marker for sinks, and disposition.
        """
        step_id = node.step.id
        index = self._step_index[step_id]
        total = self._total
        result = self._result
        self._progress.on_step_start(step_id, index, total)
        start = time.perf_counter()
        self._log_sink.write(f"\n=== step {step_id} ({index}/{total}) ===\n")
        self._log_sink.flush()
        artifact_paths: list[str] = []

        saved = False
        save_seconds = 0.0
        # self._checkpoints.save runs inside this execution_context (not just
        # execute_step) so a remote checkpoint's staged-upload scratch space
        # (checkpoint.py's _stage_parent_dir) sees this step's own temp_dir
        # rather than silently falling back to the system default --
        # get_execution_context() returns the zero-value context once this
        # `with` exits.
        with execution_context(
            mode="direct",
            user_cache_dir=self._ctx.output_loc,
            step_id=step_id,
            artifacts_dir=self._ctx.outputs_loc,
            temp_dir=self._ctx.temp_loc,
            storage_options=self._ctx.storage_options,
            artifact_sink=artifact_paths,
        ), capture_output(self._log_sink):
            try:
                outputs = execute_step(node, self._runtime, self._pipeline_inputs)
            except PipelineExecutionError as exc:
                elapsed = time.perf_counter() - start
                self._progress.on_step_end(
                    step_id, index, total, elapsed=elapsed, error=exc
                )
                raise
            except Exception as exc:
                elapsed = time.perf_counter() - start
                wrapped = PipelineExecutionError(
                    step_id,
                    f"step {step_id!r} failed during execution: {exc}",
                    original=exc,
                )
                self._progress.on_step_end(
                    step_id, index, total, elapsed=elapsed, error=wrapped
                )
                raise wrapped from exc

            if (
                not is_side_effect
                and self._checkpoints is not None
                and outputs
                and step_id in self._policy
            ):
                save_start = time.perf_counter()
                self._checkpoints.save(
                    step_id,
                    outputs,
                    artifacts=artifact_paths,
                    raw_inputs=self._raw_inputs(),
                )
                save_seconds = time.perf_counter() - save_start
                outputs = self._checkpoints.load(step_id)
                saved = True
                self._durable.add(step_id)
        self._runtime.record(step_id, outputs)
        result.outputs[step_id] = outputs
        result.executed_steps.append(step_id)
        if is_side_effect and self._checkpoints is not None:
            self._checkpoints.save_marker(
                step_id, artifacts=artifact_paths, raw_inputs=self._raw_inputs()
            )
        elapsed = time.perf_counter() - start
        result.step_dispositions[step_id] = StepRecord(
            disposition="computed",
            step_hash=self._step_hashes.get(step_id),
            tier=self._checkpoints.write_tier if saved else None,
            elapsed_seconds=elapsed,
            save_seconds=save_seconds,
            artifacts=self._checkpoints.artifact_urls(step_id) if saved else {},
        )
        result.logs.append(f"ran: {step_id} ({elapsed:.3f}s)")
        self._log_sink.write(
            f"--- {step_id}: done ({elapsed:.3f}s"
            f"{_save_note(save_seconds)}) ---\n"
        )
        self._log_sink.flush()
        self._progress.on_step_end(step_id, index, total, elapsed=elapsed)

    # -- closure construction ------------------------------------------------

    def _build_closure(self, member_ids: list[str]) -> TaskClosure:
        """Collect the out-of-chain upstream outputs a unit's members read.

        In-process backends pass every value by reference (:class:`ValueRef`);
        cross-process backends prefer a lazy :class:`CheckpointRef` for any
        upstream already in the cache so large data never crosses the wire.
        """
        member_set = set(member_ids)
        wanted: set[tuple[str, str]] = set()
        for mid in member_ids:
            node = self._dag.nodes[mid]
            for src_step, src_out, _t, _i in extract_edge_refs(node.step):
                if src_step not in member_set:
                    wanted.add((src_step, src_out))
            # ``collect: ${S.out}`` also feeds a value the runner resolved.
            if node.step.collect is not None:
                parsed = parse_ref(node.step.collect)
                if parsed is not None and parsed[0] not in member_set:
                    wanted.add(parsed)

        in_process = self._backend.in_process
        store = self._checkpoints
        refs: dict[tuple[str, str], CheckpointRef | ValueRef] = {}
        for step_id, out_name in wanted:
            if (
                not in_process
                and store is not None
                and store.has_checkpoint(step_id)
            ):
                refs[(step_id, out_name)] = CheckpointRef(step_id, out_name)
            else:
                try:
                    value = self._runtime.get(step_id, out_name)
                except KeyError:
                    # Not produced (e.g. an optional/pruned upstream): let the
                    # worker's kwarg builder decide whether it is required.
                    continue
                refs[(step_id, out_name)] = ValueRef(step_id, out_name, value)
        return TaskClosure(refs=refs)

    def _task_config(self, unit: Unit) -> dict[str, Any]:
        """Backend config for a unit: merged Dask/Prefect per-step overrides.

        The inline backend ignores this; Dask reads ``dask_config`` (scheduler,
        resources, retries) and Prefect reads ``prefect_config`` (retries,
        timeout, tags). Resolved lazily so a recipe with no ``execution`` blocks
        costs nothing.
        """
        return {
            "dask_config": resolve_unit_dask_config(self._dag, unit),
            "prefect_config": resolve_unit_prefect_config(self._dag, unit),
        }

    def _on_unit_error(
        self, unit: Unit, exc: BaseException, chain_state: dict | None = None
    ) -> None:
        """Report a failing step: elapsed time, captured output, progress.

        The task's own stdout is only reachable through the exception (see
        ``tasks.attach_task_log``), so it is flushed to the log sink here.
        Without it the step that failed contributes nothing to
        ``standard_out.txt``, which is where "how far did it get" lives.
        """
        step_id = getattr(exc, "step_id", None) or unit.first
        index = self._step_index.get(step_id, 0)
        state = (chain_state or {}).get(unit.unit_id) or {}
        start = state.get("start")
        elapsed = time.perf_counter() - start if start is not None else 0.0

        log_text = getattr(exc, TASK_LOG_ATTR, None)
        self._log_sink.write(f"\n=== step {step_id} FAILED ===\n")
        if log_text:
            self._log_sink.write(log_text)
        self._log_sink.write(f"--- {step_id}: {type(exc).__name__}: {exc} ---\n")
        self._log_sink.flush()

        self._progress.on_step_end(
            step_id, index, self._total, elapsed=elapsed, error=exc
        )

    # -- result collection ---------------------------------------------------

    def _collect_task(
        self, unit: Unit, task: Any, task_result: TaskResult, chain_state: dict
    ) -> None:
        state = chain_state[unit.unit_id]
        state["remaining"] -= 1
        for member in task_result.members:
            state["results"].append((getattr(task, "instance_index", 0), member))
        if task_result.log_text:
            # Instances finish out of order and their output lands here in
            # completion order, so fence each block with the instance it came
            # from — otherwise a fan-out's log is unattributable.
            if unit.is_chain:
                index = getattr(task, "instance_index", 0)
                label = f"{unit.first} [instance {index + 1}/{state['n_tasks']}]"
                self._log_sink.write(f"\n--- {label} ---\n")
            self._log_sink.write(task_result.log_text)
            self._log_sink.flush()

    def _finalize_unit(self, unit: Unit, state: dict) -> None:
        if unit.is_chain:
            self._finalize_chain(unit, state)
        else:
            self._finalize_step(unit, state)

    def _finalize_step(self, unit: Unit, state: dict) -> None:
        step_id = unit.first
        index = self._step_index[step_id]
        total = self._total
        result = self._result
        # A step unit produced exactly one member result.
        _order, member = state["results"][0]
        outputs = self._member_outputs(member)
        self._runtime.record(step_id, outputs)
        result.outputs[step_id] = outputs
        result.executed_steps.append(step_id)
        saved = member.checkpointed
        if saved:
            self._durable.add(step_id)
        result.step_dispositions[step_id] = StepRecord(
            disposition="computed",
            step_hash=self._step_hashes.get(step_id),
            tier=member.tier if saved else None,
            elapsed_seconds=member.elapsed,
            save_seconds=member.save_seconds,
            artifacts=self._checkpoints.artifact_urls(step_id) if saved else {},
        )
        result.logs.append(f"ran: {step_id} ({member.elapsed:.3f}s)")
        self._log_sink.write(
            f"--- {step_id}: done ({member.elapsed:.3f}s"
            f"{_save_note(member.save_seconds)}) ---\n"
        )
        self._log_sink.flush()
        self._progress.on_step_end(step_id, index, total, elapsed=member.elapsed)

    def _finalize_chain(self, unit: Unit, state: dict) -> None:
        result = self._result
        total = self._total
        member_ids = list(unit.member_ids)

        # Reassemble per-member, per-instance outputs in instance order.
        by_instance: dict[int, dict[str, MemberResult]] = {}
        for inst_index, member in state["results"]:
            by_instance.setdefault(inst_index, {})[member.step_id] = member
        instance_order = sorted(by_instance)

        member_outputs: dict[str, list[dict[str, Any]]] = {m: [] for m in member_ids}
        member_stats: dict[str, list[int]] = {m: [0, 0] for m in member_ids}
        for inst_index in instance_order:
            for mid in member_ids:
                member = by_instance[inst_index][mid]
                member_outputs[mid].append(self._member_outputs(member))
                if member.disposition == "computed":
                    member_stats[mid][0] += 1
                else:
                    member_stats[mid][1] += 1

        for mid in member_ids:
            node = self._dag.nodes[mid]
            folded = _fold_instance_outputs(node.spec.outputs, member_outputs[mid])
            self._runtime.record(mid, folded)
            result.outputs[mid] = folded
            if instance_order and all(
                by_instance[i][mid].checkpointed for i in instance_order
            ):
                self._durable.add(mid)
                self._chain_instance_hashes[(unit.unit_id, mid)] = tuple(
                    by_instance[i][mid].instance_hash for i in instance_order
                )
            computed, hits = member_stats[mid]
            if computed:
                result.executed_steps.append(mid)
            else:
                result.skipped_steps.append(mid)
            if computed == 0 and hits:
                tier = (
                    self._checkpoints.hit_tier(mid) if self._checkpoints else None
                ) or "user"
                disposition = f"hit-{tier}-cache"
                write_tier = None
            else:
                disposition = "computed"
                write_tier = (
                    self._checkpoints.write_tier
                    if (self._checkpoints and computed)
                    else None
                )
            save_total = sum(
                by_instance[i][mid].save_seconds for i in instance_order
            )
            # Time attributed to THIS member, per instance and summed, rather
            # than the whole chain's wall time -- reporting the chain total on
            # every member made each step look as slow as the entire fan-out.
            # Under a concurrent backend instances overlap, so the sum exceeds
            # the chain's wall time; it measures work done per step, and the
            # spread separates "all files slow" from "one file dominated".
            instance_times = tuple(
                by_instance[i][mid].elapsed for i in instance_order
            )
            member_elapsed = sum(instance_times)
            result.step_dispositions[mid] = StepRecord(
                disposition=disposition,
                step_hash=self._step_hashes.get(mid),
                tier=write_tier,
                elapsed_seconds=member_elapsed,
                save_seconds=save_total,
                instance_seconds=instance_times,
            )
            result.logs.append(
                f"mapped {mid}: {computed + hits} instance(s) "
                f"({computed} computed, {hits} cached)"
            )
            # Chains need the same closing line a plain step gets, or the run log
            # has no record of when the fan-out finished.
            self._log_sink.write(
                f"--- {mid}: done ({_instance_note(instance_times)}"
                f"{_save_note(save_total)}, "
                f"{computed + hits} instance(s): "
                f"{computed} computed, {hits} cached) ---\n"
            )
            self._log_sink.flush()
            self._progress.on_step_end(
                mid, self._step_index.get(mid, 0), total,
                skipped=(computed == 0), elapsed=member_elapsed,
                instance_seconds=instance_times,
            )

    def _member_outputs(self, member: MemberResult) -> dict[str, Any]:
        """Resolve a member result to its output dict.

        Checkpointed outputs are reloaded from the client's store (lazy
        zarr-backed values); everything else is carried by value.
        """
        if member.checkpointed and self._checkpoints is not None:
            return self._checkpoints.load(
                member.step_id, instance_hash=member.instance_hash
            )
        return member.inline_outputs or {}


def _instance_note(instance_seconds: tuple[float, ...]) -> str:
    """Render a fanned-out step's total plus its per-instance spread."""
    total = sum(instance_seconds)
    if len(instance_seconds) < 2:
        return f"{total:.3f}s"
    mean = total / len(instance_seconds)
    return (
        f"{total:.3f}s total, avg {mean:.3f}s, "
        f"min {min(instance_seconds):.3f}s, max {max(instance_seconds):.3f}s"
    )


def _save_note(save_seconds: float) -> str:
    """Render the checkpoint-write share of a step's time, when it is material.

    A step that spends most of its wall clock uploading to a bucket needs a
    different fix (chunking, a local temp dir) than one that is genuinely slow
    to compute, so the split is worth a few characters in the run log.
    """
    if save_seconds < 0.05:
        return ""
    return f", {save_seconds:.3f}s writing checkpoint"

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Picklable units of work and the worker functions that run them.

A *task* is the atom a :class:`~aa_recipe_manager.executor.engine.backends.
base.SchedulerBackend` schedules. There are two kinds:

* :class:`StepTask` — one non-mapped data step.
* :class:`ChainInstanceTask` — one instance of a mapped/swept chain (every
  member run in order for a single fan-out element), so all of an instance's
  intermediates stay on one worker.

Everything a task needs to run travels with it: a :class:`TaskClosure` of the
upstream values its members read (as a lazy :class:`CheckpointRef` when the
value is already in the cache, else a by-value :class:`ValueRef`), plus a
:class:`~aa_recipe_manager.executor.engine.context.WorkerContext`. The worker
functions are module-level so a process backend can pickle them; an in-process
backend calls them directly and the refs simply hold live objects.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.executor.engine.logcapture import capture_output
from aa_recipe_manager.executor.engine.step import execute_step
from aa_recipe_manager.executor.invocation import RuntimeContext, _ElementContext
from aa_recipe_manager.executor.refs import CheckpointRef, ValueRef, resolve_ref
from aa_recipe_manager.executor.runtime_context import execution_context
from aa_recipe_manager.executor.disposal import dispose_step_outputs
from aa_recipe_manager.parallel import (
    derive_instance_hash,
    instance_discriminator,
)

if TYPE_CHECKING:
    from aa_recipe_manager.executor.engine.context import WorkerContext
    from aa_recipe_manager.executor.tiered import CheckpointStore

__all__ = [
    "CheckpointRef",
    "ValueRef",
    "TaskClosure",
    "StepTask",
    "ChainInstanceTask",
    "MemberResult",
    "TaskResult",
]


# ---------------------------------------------------------------------------
# Upstream value references (the data plane)
# ---------------------------------------------------------------------------
#
# CheckpointRef / ValueRef live in executor/refs.py (re-exported here for
# backward compatibility with existing import sites) so that module can be
# imported by both this module and executor/invocation.py without a cycle.


@dataclass
class TaskClosure:
    """The out-of-chain upstream outputs a task's members read.

    ``refs`` maps ``(step_id, output_name)`` to the ref that resolves it. Built
    on the client from the live runtime and the store; rebuilt into a
    :class:`RuntimeContext` inside the worker by :meth:`materialize`.
    """

    refs: dict[tuple[str, str], CheckpointRef | ValueRef] = field(
        default_factory=dict
    )

    def value_ref_steps(self) -> list[str]:
        """Step ids still carried by value (a process backend must reject these
        when the value is not a small builtin)."""
        return sorted(
            {ref.step_id for ref in self.refs.values() if isinstance(ref, ValueRef)}
        )

    def heavy_value_ref_steps(self) -> list[str]:
        """Upstream steps whose by-value payload cannot cross a process boundary.

        A small JSON-native value (a path string, a params dict) pickles
        cheaply and is allowed; anything else (an ``xarray`` Dataset, an
        ``EchoData``) must instead reach the worker as a checkpoint reference,
        so a process backend rejects the task and names the offending step.
        """
        from aa_recipe_manager.parallel import _UNSERIALIZABLE, _json_native

        heavy: set[str] = set()
        for ref in self.refs.values():
            if isinstance(ref, ValueRef) and _json_native(ref.value) is _UNSERIALIZABLE:
                heavy.add(ref.step_id)
        return sorted(heavy)

    def materialize(
        self, store: CheckpointStore | None
    ) -> RuntimeContext:
        """Rebuild a :class:`RuntimeContext` holding every upstream this task
        reads. ``CheckpointRef`` entries load lazily from ``store``."""
        runtime = RuntimeContext()
        loaded: dict[str, dict[str, Any]] = {}
        for (step_id, output_name), ref in self.refs.items():
            bucket = loaded.setdefault(step_id, {})
            bucket[output_name] = resolve_ref(ref, store)
        for step_id, bucket in loaded.items():
            runtime.record(step_id, bucket)
        return runtime


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepTask:
    """One non-mapped data step, scheduled as a single unit."""

    step_id: str
    closure: TaskClosure
    checkpoint: bool
    write_token: str | None = None
    #: The run's raw input file list (RawInputsRecord dumped to a dict), stamped
    #: onto this step's checkpoint sidecar. ``None`` until the reader resolves it.
    raw_inputs: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChainInstanceTask:
    """One instance of a mapped/swept chain (all members, one element)."""

    member_ids: tuple[str, ...]
    instance_index: int
    item: Any
    combo: dict[str, Any] | None
    has_item: bool
    closure: TaskClosure
    #: Member step ids whose per-instance output should be checkpointed.
    checkpoint_members: frozenset[str] = frozenset()
    write_token: str | None = None
    #: The run's raw input file list (see :class:`StepTask`).
    raw_inputs: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class MemberResult:
    """Per-(member, instance) outcome returned to the client.

    Carries references, not large values: when ``checkpointed`` the client
    reloads the output from the store (lazy); otherwise ``inline_outputs`` holds
    the by-value result.
    """

    step_id: str
    disposition: str
    tier: str | None
    elapsed: float
    artifacts: list[str]
    checkpointed: bool
    instance_hash: str | None = None
    inline_outputs: dict[str, Any] | None = None
    #: Seconds of ``elapsed`` spent writing the checkpoint rather than computing.
    #: Against a bucket this is often most of a step's time — without the split,
    #: "slow step" and "slow upload" are indistinguishable.
    save_seconds: float = 0.0


@dataclass
class TaskResult:
    """What a worker returns for one task.

    A :class:`StepTask` yields exactly one :class:`MemberResult`; a
    :class:`ChainInstanceTask` yields one per member (for its single instance).
    ``log_text`` is the task's captured stdout/stderr, written to the run log by
    the client so concurrent tasks never scribble over a shared stream.
    """

    members: list[MemberResult]
    log_text: str = ""
    #: Scratch paths this instance's disposal removed. Reported on the chain's
    #: completion line: disposal that silently does nothing looks exactly like
    #: disposal that is working, so the count has to be visible.
    disposed: int = 0


# ---------------------------------------------------------------------------
# Worker functions (module-level so a process backend can pickle them)
# ---------------------------------------------------------------------------


def _task_temp_dir(wctx: WorkerContext, discriminator: str) -> str | None:
    """Give each task its own scratch subdir so concurrent steps never collide.

    Returns a string path/URL (``exe_temp/<discriminator>``) or ``None`` when
    the run has no scratch dir.
    """
    if wctx.temp_dir is None:
        return None
    sep = "/" if "://" in wctx.temp_dir or "/" in wctx.temp_dir else "\\"
    base = wctx.temp_dir.rstrip("/\\")
    return f"{base}{sep}{discriminator}"


#: Attribute holding a failed task's captured stdout, read by the runner.
TASK_LOG_ATTR = "aa_task_log"


def attach_task_log(exc: BaseException, text: str) -> None:
    """Stash a failed task's captured output on the exception it raised.

    A task hands back its :class:`TaskResult` (and with it ``log_text``) only on
    success, so the output of the step that actually failed would otherwise be
    dropped -- exactly the output needed to tell how far it got.
    """
    if not text or getattr(exc, TASK_LOG_ATTR, None):
        return
    try:
        setattr(exc, TASK_LOG_ATTR, text)
    except AttributeError:  # an exception type using __slots__
        pass


def run_step_task(task: StepTask, wctx: WorkerContext) -> TaskResult:
    """Execute one non-mapped data step inside a worker."""
    node = wctx.dag.nodes[task.step_id]
    store = wctx.open_store()
    runtime = task.closure.materialize(store)

    log_buffer = io.StringIO()
    artifact_paths: list[str] = []
    start = time.perf_counter()
    checkpointed = False
    tier: str | None = None
    save_seconds = 0.0
    # store.save runs inside the same execution_context as the op itself (not
    # just execute_step) so a remote checkpoint's staged-upload scratch space
    # (checkpoint.py's _stage_parent_dir) sees this task's own temp_dir rather
    # than silently falling back to the system default -- get_execution_context()
    # returns the zero-value context once this `with` exits.
    try:
        with execution_context(
            mode="direct",
            user_cache_dir=wctx.user_cache_dir,
            step_id=task.step_id,
            artifacts_dir=wctx.outputs_dir,
            temp_dir=_task_temp_dir(wctx, task.step_id),
            storage_options=wctx.storage_options,
            artifact_sink=artifact_paths,
        ), capture_output(log_buffer):
            outputs = execute_step(node, runtime, wctx.pipeline_inputs)

            if task.checkpoint and store is not None and outputs:
                save_start = time.perf_counter()
                store.save(
                    task.step_id,
                    outputs,
                    artifacts=artifact_paths,
                    write_token=task.write_token,
                    raw_inputs=task.raw_inputs,
                )
                save_seconds = time.perf_counter() - save_start
                checkpointed = True
                tier = store.write_tier
    except BaseException as exc:
        attach_task_log(exc, log_buffer.getvalue())
        raise
    elapsed = time.perf_counter() - start
    return TaskResult(
        members=[
            MemberResult(
                step_id=task.step_id,
                disposition="computed",
                tier=tier,
                elapsed=elapsed,
                artifacts=artifact_paths,
                checkpointed=checkpointed,
                inline_outputs=None if checkpointed else outputs,
                save_seconds=save_seconds,
            )
        ],
        log_text=log_buffer.getvalue(),
    )


def run_chain_instance(task: ChainInstanceTask, wctx: WorkerContext) -> TaskResult:
    """Execute one instance of a mapped/swept chain inside a worker.

    Ports the per-instance inner loop of the sequential ``_run_mapped_chain``:
    an :class:`_ElementContext` isolates within-chain references to this element,
    each member is checkpointed at its own instance hash, and a cached instance
    is reused rather than recomputed.
    """
    store = wctx.open_store()
    parent = task.closure.materialize(store)
    elem_ctx = _ElementContext(parent, item=task.item)

    log_buffer = io.StringIO()
    members: list[MemberResult] = []
    disposed = 0
    try:
        _run_chain_members(task, wctx, store, elem_ctx, log_buffer, members)
    except BaseException as exc:
        attach_task_log(exc, log_buffer.getvalue())
        raise
    finally:
        # Per instance, not per chain: _finalize_chain only runs once every
        # instance is done, so disposing there would hold every downloaded file
        # for the length of the fan-out, which is the thing this exists to
        # avoid. Runs on the failure path too, so a crashed instance does not
        # strand its scratch.
        disposed = _dispose_instance(task, wctx, elem_ctx, log_buffer)
    return TaskResult(
        members=members, log_text=log_buffer.getvalue(), disposed=disposed
    )


def _dispose_instance(
    task: ChainInstanceTask,
    wctx: WorkerContext,
    elem_ctx: "_ElementContext",
    log_buffer: io.StringIO,
) -> int:
    """Delete this instance's disposable outputs, returning how many paths went.

    Safe to run as soon as the instance's members are done: a disposable port
    is validated to be uncheckpointed, and a collector reads a chain's outputs
    only through the checkpointed ports it fans in on.
    """
    removed = 0
    for mid in task.member_ids:
        member = wctx.dag.nodes[mid]
        removed += dispose_step_outputs(member, elem_ctx.own_outputs(mid))
    if removed:
        with capture_output(log_buffer):
            print(f"disposed {removed} path(s) for instance {task.instance_index}")
    return removed


def _instance_discriminator_kwargs(task: ChainInstanceTask) -> dict[str, Any]:
    """Discriminator inputs identifying one instance within its chain."""
    disc: dict[str, Any] = {
        "index": task.instance_index,
        "param_overrides": task.combo,
    }
    if task.has_item:
        disc["item"] = task.item
    return disc


def _member_instance_hash(
    task: ChainInstanceTask, wctx: WorkerContext, store: Any, mid: str
) -> str | None:
    """This instance's checkpoint address for member ``mid``, or ``None``.

    ``None`` whenever the member is not checkpointed at all: no store, no base
    hash, not among the chain's ``checkpoint_members``, or a side-effect step
    with no outputs to store.
    """
    if store is None or mid not in task.checkpoint_members:
        return None
    base_hash = wctx.step_hashes.get(mid)
    if not base_hash:
        return None
    member = wctx.dag.nodes[mid]
    if member.spec.sink or not member.spec.outputs:
        return None
    return derive_instance_hash(
        base_hash, instance_discriminator(**_instance_discriminator_kwargs(task))
    )


def _resumable_members(
    task: ChainInstanceTask, wctx: WorkerContext, store: Any
) -> set[str]:
    """Members this instance can skip because nothing that still runs reads them.

    A chain instance runs its members in order and consults the cache one
    member at a time, so a chain whose cached members all sit near the end
    re-ran every earlier member on every run even when those cached members
    were hits for every instance. On a survey-scale fan-out that is most of the
    cost of the run: the checkpoint is found, but only after everything that
    produced it has been recomputed to reach it.

    Need is propagated backwards from the members that must produce a value:
    the ones read outside the chain, the ones a recipe names in its ``outputs``
    block, sinks, output-less steps, steps that regenerate artifacts, and the
    last member. A member holding a checkpoint for this instance is loaded
    rather than computed, so it needs no inputs and need stops there. Whatever
    is left unneeded is skipped.

    The invariant that makes this safe is about reference resolution, not cost.
    A skipped member is never recorded into the element context, and
    ``_ElementContext.get`` falls through to the parent when a step is absent
    from its store - which for a chain member is another instance's value or
    the whole fanned-out list, silently. So a member may only be skipped when
    every consumer of it is itself skipped or is a cache hit, since a hit loads
    its outputs and never resolves its inputs. Propagating need through
    uncached members is exactly that condition. It relies on every reference
    producing a DAG edge: ``extract_edge_refs`` covers ``inputs`` and
    ``params`` including nested lists and dicts, and ``depends_on``,
    ``map_over`` and ``collect`` are edged separately by the DAG builder.
    """
    if store is None or wctx.force:
        return set()
    member_ids = list(task.member_ids)
    inside = set(member_ids)

    cached = set()
    for mid in member_ids:
        inst_hash = _member_instance_hash(task, wctx, store, mid)
        if inst_hash is not None and store.has_checkpoint(
            mid, instance_hash=inst_hash
        ):
            cached.add(mid)
    if not cached:
        return set()

    producers: dict[str, set[str]] = {}
    needed = {member_ids[-1]}
    for edge in wctx.dag.edges:
        if edge.source_step_id not in inside:
            continue
        if edge.target_step_id in inside:
            producers.setdefault(edge.target_step_id, set()).add(edge.source_step_id)
        else:
            needed.add(edge.source_step_id)
    declared = getattr(wctx.dag.recipe, "outputs", None) or {}
    for output in declared.values():
        step_id = getattr(output, "step_id", None)
        if step_id in inside:
            needed.add(step_id)
    for mid in member_ids:
        member = wctx.dag.nodes[mid]
        # A step whose value nothing reads may still be the point of the run.
        # Sinks and output-less steps exist only for their side effects, and a
        # regenerate policy is a step saying outright that running it writes
        # artifacts.
        if member.spec.sink or not member.spec.outputs:
            needed.add(mid)
        elif member.step.regenerate not in (None, "never"):
            needed.add(mid)

    for mid in reversed(member_ids):
        if mid in needed and mid not in cached:
            needed |= producers.get(mid, set())
    return inside - needed


def chain_external_readers(member_ids, dag) -> set[str]:
    """Chain members whose outputs something outside the chain reads.

    Membership is by DAG edge, plus any member a recipe names in its
    ``outputs`` block, which is a reader the edges do not show.
    """
    inside = set(member_ids)
    read = {
        edge.source_step_id
        for edge in dag.edges
        if edge.source_step_id in inside and edge.target_step_id not in inside
    }
    declared = getattr(dag.recipe, "outputs", None) or {}
    for output in declared.values():
        step_id = getattr(output, "step_id", None)
        if step_id in inside:
            read.add(step_id)
    return read


def _externally_read_members(
    task: ChainInstanceTask, wctx: WorkerContext
) -> set[str]:
    """Chain members whose outputs something outside the chain reads.

    The client keeps a MemberResult per member per instance until the chain
    finalizes, and an uncheckpointed member carries its output by value. On a
    survey-scale fan-out that is the dominant cost of the run: measured at 76
    MiB per instance on HB1603's per-file chain, which is 57 GiB by instance
    766 - and every byte of it for members nothing outside the chain ever looks
    at, since within the chain each member reads the previous one's value out
    of the element context in the worker.

    Membership is by DAG edge, plus any member a recipe names in its ``outputs``
    block, which is a reader the edges do not show.

    Only *heavy* outputs are dropped on the strength of this: a small
    JSON-native result (a count, a path, a params dict) costs nothing to carry
    and ``result.outputs`` is expected to hold it. The rule is the one
    :meth:`TaskClosure.heavy_value_ref_steps` already uses in the other
    direction.
    """
    return chain_external_readers(task.member_ids, wctx.dag)


def _empty_upstream(
    member: Any, task: ChainInstanceTask, wctx: WorkerContext, elem_ctx: _ElementContext
) -> bool:
    """True when a data input of this member is an in-chain output of None.

    An op may declare an instance empty by returning None: select_ping_time_range
    does with allow_empty, for a file the window does not touch. Nothing
    downstream of it can run on None, so every later member of the chain that
    reads it is empty for the same instance, and reports None outputs without
    being invoked. The fan-in at the end drops them.

    Only ``inputs`` count, not ``params``, and only an output that was actually
    recorded for this instance: a member skipped by _resumable_members has no
    record, and is not empty.
    """
    inside = set(task.member_ids)
    data_ports = set(member.step.inputs)
    for edge in wctx.dag.edges:
        if edge.target_step_id != member.step.id or edge.source_step_id not in inside:
            continue
        if edge.target_input not in data_ports or not edge.source_output:
            continue
        produced = elem_ctx.own_outputs(edge.source_step_id)
        if (
            produced is not None
            and edge.source_output in produced
            and produced[edge.source_output] is None
        ):
            return True
    return False


def _inline_outputs(
    out: dict[str, Any] | None, checkpointed: bool, externally_read: bool
) -> dict[str, Any] | None:
    """What of a member's result travels back to the client, and is then kept.

    Nothing when the member is checkpointed (the client reloads it lazily), and
    nothing when a heavy result has no reader outside the chain. A small
    JSON-native result is carried either way; see _externally_read_members.
    """
    from aa_recipe_manager.parallel import _UNSERIALIZABLE, _json_native

    if checkpointed:
        return None
    if externally_read or _json_native(out) is not _UNSERIALIZABLE:
        return out or {}
    return None


def _members_read_inside_chain(
    task: ChainInstanceTask, wctx: WorkerContext, skippable: set[str]
) -> set[str]:
    """Members whose value another member of this chain still has to read.

    A cache hit is recorded into the element context so a later member can
    resolve a reference to it, and that recording is the only reason the worker
    loads it at all: the client reloads every checkpointed member from the
    store when it folds the chain in (``_member_outputs``). So a hit that no
    surviving member reads was being opened from the bucket twice and used
    once. On HB1603 both fanned-in members are in exactly that position, which
    is 6644 wasted zarr opens over GCS per run.

    A skipped consumer reads nothing, so it does not count. A consumer that
    turns out to be a hit reads nothing either, but that is not known until the
    loop reaches it, so it is counted here and the load is kept.
    """
    inside = set(task.member_ids)
    return {
        edge.source_step_id
        for edge in wctx.dag.edges
        if edge.source_step_id in inside
        and edge.target_step_id in inside
        and edge.target_step_id not in skippable
    }


def _run_chain_members(
    task: ChainInstanceTask,
    wctx: WorkerContext,
    store: Any,
    elem_ctx: _ElementContext,
    log_buffer: io.StringIO,
    members: list[MemberResult],
) -> None:
    """Run every member of one chain instance, appending to ``members``."""
    skippable = _resumable_members(task, wctx, store)
    externally_read = _externally_read_members(task, wctx)
    read_in_chain = _members_read_inside_chain(task, wctx, skippable)
    with capture_output(log_buffer):
        for mid in task.member_ids:
            if mid in skippable:
                # Nothing still to run reads this member (see
                # _resumable_members), so its output is never materialized. It
                # still reports a result: the client indexes every member of
                # every instance when it folds the chain in.
                members.append(
                    MemberResult(
                        step_id=mid,
                        disposition="skipped",
                        tier=None,
                        elapsed=0.0,
                        artifacts=[],
                        checkpointed=False,
                    )
                )
                continue
            member = wctx.dag.nodes[mid]
            disc_kwargs = _instance_discriminator_kwargs(task)
            inst_hash = _member_instance_hash(task, wctx, store, mid)

            start = time.perf_counter()
            if (
                inst_hash is not None
                and not wctx.force
                and store.has_checkpoint(mid, instance_hash=inst_hash)
            ):
                if mid in read_in_chain:
                    out = store.load(mid, instance_hash=inst_hash)
                    elem_ctx.record(mid, out or {})
                members.append(
                    MemberResult(
                        step_id=mid,
                        disposition="hit",
                        tier=store.hit_tier(mid) or "user",
                        elapsed=time.perf_counter() - start,
                        artifacts=[],
                        checkpointed=True,
                        instance_hash=inst_hash,
                    )
                )
                continue

            if _empty_upstream(member, task, wctx, elem_ctx):
                out = {port: None for port in (member.spec.outputs or {})}
                elem_ctx.record(mid, out)
                members.append(
                    MemberResult(
                        step_id=mid,
                        disposition="computed",
                        tier=None,
                        elapsed=0.0,
                        artifacts=[],
                        checkpointed=False,
                        inline_outputs=out,
                    )
                )
                continue

            artifact_paths: list[str] = []
            checkpointed = False
            save_seconds = 0.0
            # store.save runs inside this execution_context too -- see the
            # matching comment in run_step_task for why.
            with execution_context(
                mode="direct",
                user_cache_dir=wctx.user_cache_dir,
                step_id=mid,
                artifacts_dir=wctx.outputs_dir,
                temp_dir=_task_temp_dir(
                    wctx, f"{mid}-{task.instance_index}"
                ),
                storage_options=wctx.storage_options,
                artifact_sink=artifact_paths,
            ):
                out = execute_step(
                    member, elem_ctx, wctx.pipeline_inputs,
                    param_overrides=task.combo,
                )

                if inst_hash is not None and out and any(
                    value is not None for value in out.values()
                ):
                    save_start = time.perf_counter()
                    store.save(
                        mid,
                        out,
                        artifacts=artifact_paths,
                        instance_hash=inst_hash,
                        instance_index=task.instance_index,
                        instance_discriminator=instance_discriminator(**disc_kwargs),
                        write_token=task.write_token,
                        raw_inputs=task.raw_inputs,
                    )
                    save_seconds = time.perf_counter() - save_start
                    checkpointed = True
            elem_ctx.record(mid, out or {})
            members.append(
                MemberResult(
                    step_id=mid,
                    disposition="computed",
                    tier=store.write_tier if checkpointed else None,
                    elapsed=time.perf_counter() - start,
                    artifacts=artifact_paths,
                    checkpointed=checkpointed,
                    instance_hash=inst_hash,
                    inline_outputs=_inline_outputs(
                        out, checkpointed, mid in externally_read
                    ),
                    save_seconds=save_seconds,
                )
            )

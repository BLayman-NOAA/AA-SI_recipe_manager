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
    """Members this instance can skip because a later member loads from cache.

    A chain instance runs its members in order and consults the cache one
    member at a time, so a chain whose only checkpointed member is its last one
    re-ran every earlier member on every run even when that last member was a
    hit for every instance. On a survey-scale fan-out that is most of the cost
    of the run: the checkpoint is found, but only after everything that
    produced it has been recomputed to reach it.

    This finds the latest member holding a checkpoint for this instance and
    returns the members before it, whose outputs nothing still to run can
    observe. It gives up altogether, returning an empty set, unless every one
    of those members is consumed only at or before that frontier: an output
    read from outside the chain, or by a member that still has to run, has to
    be computed.
    """
    if store is None or wctx.force:
        return set()
    member_ids = list(task.member_ids)
    frontier = -1
    for index in range(len(member_ids) - 1, 0, -1):
        inst_hash = _member_instance_hash(task, wctx, store, member_ids[index])
        if inst_hash is not None and store.has_checkpoint(
            member_ids[index], instance_hash=inst_hash
        ):
            frontier = index
            break
    if frontier < 1:
        return set()
    skippable = set(member_ids[:frontier])
    reachable = set(member_ids[: frontier + 1])
    for edge in wctx.dag.edges:
        if edge.source_step_id in skippable and edge.target_step_id not in reachable:
            return set()
    return skippable


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

                if inst_hash is not None and out:
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
                    inline_outputs=None if checkpointed else (out or {}),
                    save_seconds=save_seconds,
                )
            )

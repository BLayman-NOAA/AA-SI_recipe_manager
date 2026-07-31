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
    with capture_output(log_buffer):
        for mid in task.member_ids:
            member = wctx.dag.nodes[mid]
            base_hash = wctx.step_hashes.get(mid)
            is_side_effect = member.spec.sink or not member.spec.outputs
            disc_kwargs: dict[str, Any] = {
                "index": task.instance_index,
                "param_overrides": task.combo,
            }
            if task.has_item:
                disc_kwargs["item"] = task.item
            want_ckpt = (
                store is not None
                and base_hash
                and mid in task.checkpoint_members
                and not is_side_effect
            )
            inst_hash = (
                derive_instance_hash(
                    base_hash, instance_discriminator(**disc_kwargs)
                )
                if want_ckpt
                else None
            )

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
    return TaskResult(members=members, log_text=log_buffer.getvalue())

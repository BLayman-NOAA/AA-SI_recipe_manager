# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""BatchExecutor: run one recipe across many input sets (UC-6).

Wraps any :class:`~aa_recipe_manager.executor.base.PipelineExecutor` and runs
its DAG once per input set — a folder of raw files, the rows of a CSV manifest,
or explicit lists.

**Cache sharing (deliberate deviation from `software_architecture.md` §5.4).**
The sketch gives each run its own ``user_cache_dir/run_NNNN/``. Under the Stage 7
content-addressed cache that would defeat cross-run dedupe: two input sets that
share, say, a calibration step would each recompute it. So the batch runner uses
**one shared cache root** (``user_cache_dir``) and a **per-set outputs directory**
(``outputs_dir/<label>/``) for that set's images, logs, and manifest. Identical
upstream work is computed once and reused across every set; only the differing
tail runs per set. A top-level ``batch_manifest.json`` indexes the per-set runs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.storage import StorageLocation

if TYPE_CHECKING:
    from aa_recipe_manager.executor.base import ExecutionResult, PipelineExecutor
    from aa_recipe_manager.model.types import PipelineDAG

BATCH_MANIFEST_FILENAME = "batch_manifest.json"

#: Builds a DAG for one input set. A recipe's ``${inputs.x}`` *params* are baked
#: into each step's cache fingerprint at build time, so the DAG is rebuilt per
#: set — that is what makes each set's input-dependent steps address distinct
#: cache entries while input-independent steps still dedupe across sets.
DagFactory = Callable[[dict[str, Any]], "PipelineDAG"]


@dataclass
class InputSet:
    """One run's inputs plus the label naming its outputs subdirectory."""

    label: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchResult:
    """Aggregate result of a batch run."""

    results: list[ExecutionResult] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    manifest_file: StorageLocation | Path | None = None
    # Wall clock for the whole batch — measured, not summed from the per-run
    # times, so it stays correct if runs are ever overlapped.
    elapsed_seconds: float = 0.0

    def __len__(self) -> int:
        return len(self.results)


class BatchExecutor:
    """Run a DAG across a collection of input sets, sharing one cache root."""

    def __init__(self, executor: PipelineExecutor) -> None:
        self._executor = executor

    def execute_batch(
        self,
        dag_factory: DagFactory,
        input_sets: list[InputSet],
        *,
        user_cache_dir: str | Path | None = None,
        outputs_dir: str | Path | None = None,
        storage_options: dict[str, Any] | None = None,
        progress: Any = None,
        **execute_kwargs: Any,
    ) -> BatchResult:
        """Run one recipe once per input set, sharing one checkpoint cache.

        ``dag_factory(inputs)`` builds the DAG for a given set (see
        :data:`DagFactory`). ``user_cache_dir`` is the single shared cache; each
        set's user-facing outputs go under ``outputs_dir/<label>/``. Remaining
        keyword arguments are forwarded to the wrapped executor's ``execute``.
        """
        if not input_sets:
            raise ValueError("execute_batch requires at least one input set")
        labels = [s.label for s in input_sets]
        if len(set(labels)) != len(labels):
            raise ValueError(f"input-set labels must be unique; got {labels!r}")

        outputs_root = (
            StorageLocation.parse(outputs_dir, storage_options)
            if outputs_dir is not None
            else None
        )

        batch_start = time.perf_counter()
        batch = BatchResult(labels=labels)
        last_dag: PipelineDAG | None = None
        for input_set in input_sets:
            set_outputs = (
                str(outputs_root / input_set.label)
                if outputs_root is not None
                else None
            )
            dag = dag_factory(input_set.inputs)
            last_dag = dag
            result = self._executor.execute(
                dag,
                inputs=input_set.inputs or None,
                user_cache_dir=user_cache_dir,
                outputs_dir=set_outputs,
                storage_options=storage_options,
                progress=progress,
                **execute_kwargs,
            )
            batch.results.append(result)

        batch.elapsed_seconds = time.perf_counter() - batch_start
        batch.manifest_file = self._write_batch_manifest(
            batch, last_dag, outputs_root, input_sets
        )
        return batch

    @staticmethod
    def _write_batch_manifest(
        batch: BatchResult,
        dag: PipelineDAG | None,
        outputs_root: StorageLocation | None,
        input_sets: list[InputSet],
    ) -> StorageLocation | None:
        """Index the per-set runs at the top of the outputs tree (best-effort)."""
        if outputs_root is None or dag is None:
            return None
        manifest = {
            "schema_version": 1,
            "recipe": {"name": dag.recipe.name, "version": dag.recipe.version},
            "elapsed_seconds": round(batch.elapsed_seconds, 3),
            "runs": [
                {
                    "label": s.label,
                    "run_id": r.run_id,
                    "inputs": s.inputs,
                    "elapsed_seconds": round(r.elapsed_seconds, 3),
                    "manifest": str(r.manifest_file) if r.manifest_file else None,
                    "outputs_dir": str(r.outputs_dir) if r.outputs_dir else None,
                }
                for s, r in zip(input_sets, batch.results)
            ],
        }
        try:
            outputs_root.mkdir()
            loc = outputs_root / BATCH_MANIFEST_FILENAME
            loc.write_text(json.dumps(manifest, indent=2))
            return loc
        except Exception:  # never mask a completed batch
            return None


# ---------------------------------------------------------------------------
# Input-set builders
# ---------------------------------------------------------------------------


def input_sets_from_folder(
    folder: str | Path,
    input_name: str,
    *,
    pattern: str = "*.raw",
    storage_options: dict[str, Any] | None = None,
) -> list[InputSet]:
    """One input set per file matching ``pattern`` under ``folder``.

    ``folder`` may be a local path or an fsspec URL (``gs://…``); the matched
    file's URL is passed as ``input_name`` and its stem labels the run.
    """
    loc = StorageLocation.parse(folder, storage_options)
    matches = sorted(loc.glob(pattern), key=lambda m: m.name)
    if not matches:
        raise ValueError(f"no files matching {pattern!r} under {folder!r}")
    sets: list[InputSet] = []
    for match in matches:
        label = Path(match.name).stem
        sets.append(InputSet(label=label, inputs={input_name: str(match)}))
    return sets


def input_sets_from_csv(path: str | Path) -> list[InputSet]:
    """One input set per row of a CSV manifest (header = input names).

    A ``label`` column, if present, names each run; otherwise rows are labelled
    ``row_0000``, ``row_0001``, ….
    """
    import csv

    rows: list[InputSet] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            label = row.pop("label", None) or f"row_{i:04d}"
            rows.append(InputSet(label=label, inputs=dict(row)))
    if not rows:
        raise ValueError(f"CSV manifest {path!r} has no data rows")
    return rows


def input_sets_from_lists(
    input_name: str,
    values: list[str],
    *,
    labels: list[str] | None = None,
) -> list[InputSet]:
    """One input set per value, binding it to ``input_name``."""
    if labels is not None and len(labels) != len(values):
        raise ValueError("labels and values must be the same length")
    out: list[InputSet] = []
    for i, value in enumerate(values):
        label = labels[i] if labels is not None else f"set_{i:04d}"
        out.append(InputSet(label=label, inputs={input_name: value}))
    return out

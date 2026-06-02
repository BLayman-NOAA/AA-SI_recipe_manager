# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""CheckpointManager: step-output checkpointing and resume support.

Each call to :meth:`save` writes one cached file per output and a per-step
metadata sidecar that records the step hash, output filenames, and how
each output was serialized. :meth:`load` reads them back. Step-hash
mismatch invalidates a cached step entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import shutil
import stat
import time
import warnings
from contextlib import contextmanager
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import (
        CheckpointFormat,
        CheckpointMode,
        DAGNode,
        PipelineDAG,
        Step,
    )

# Matches ${inputs.name} anywhere within a string (mirrors resolver.params).
_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")


META_SUFFIX = "__cache_meta.json"
DEFAULT_OUTPUT_ROOT = "recipe_cache"
ZARR_DATA_DIR = "zarr_data"
JSON_DATA_DIR = "json_data"
CACHE_METADATA_DIR = "cache_metadata"
IMAGE_DATA_DIR = "images"
OTHER_DATA_DIR = "other"


CleanMode = Literal["intermediate", "all", "stale"]

_DEFAULT_CHECKPOINT_MODE: CheckpointMode = "explicit"


def _referenced_pipeline_inputs(node: DAGNode) -> set[str]:
    """Names of pipeline inputs (``${inputs.x}``) referenced by a step.

    Scans the step's input wiring, raw params, and resolved params so that a
    change to a referenced pipeline-input *value* participates in the step's
    cache hash without dragging in unrelated inputs.
    """
    names: set[str] = set()

    def scan(value: Any) -> None:
        if isinstance(value, str):
            names.update(_INPUT_REF.findall(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                scan(item)
        elif isinstance(value, dict):
            for item in value.values():
                scan(item)

    for value in node.step.inputs.values():
        scan(value)
    for value in node.step.params.values():
        scan(value)
    for value in node.resolved_params.values():
        scan(value)
    return names


def _path_fingerprint(path_value: Any) -> dict[str, Any] | None:
    """Return a deterministic, stat-only fingerprint for a path-like value.

    Files are fingerprinted by size and nanosecond mtime. Directories are
    fingerprinted by a sorted top-level listing containing each entry's name,
    kind, size, and nanosecond mtime. This catches common local-input changes
    (added/removed/replaced files) without reading large file contents.
    """
    if path_value is None:
        return None
    if not isinstance(path_value, (str, Path)):
        path_value = str(path_value)
    path = Path(path_value)
    if not path.exists():
        return {"path": str(path), "kind": "missing"}
    stat_result = path.stat()
    if path.is_file():
        return {
            "path": str(path),
            "kind": "file",
            "size": stat_result.st_size,
            "mtime_ns": stat_result.st_mtime_ns,
        }
    if path.is_dir():
        entries: list[dict[str, Any]] = []
        for entry in sorted(path.iterdir(), key=lambda item: item.name):
            entry_stat = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "kind": "dir" if entry.is_dir() else "file",
                    "size": entry_stat.st_size if entry.is_file() else None,
                    "mtime_ns": entry_stat.st_mtime_ns,
                }
            )
        return {
            "path": str(path),
            "kind": "dir",
            "mtime_ns": stat_result.st_mtime_ns,
            "entries": entries,
        }
    return {
        "path": str(path),
        "kind": "other",
        "mtime_ns": stat_result.st_mtime_ns,
    }


def _step_fingerprint(
    node: DAGNode,
    pipeline_inputs: dict[str, Any],
    input_declarations: dict[str, Any],
) -> dict[str, Any]:
    """Content fingerprint of a single step's definition.

    Captures everything that can change a step's outputs *locally*: its op,
    input/param wiring, resolved params, mapping/sweep declarations, the
    resolved implementation, and the values of any pipeline inputs the step
    references. Upstream data dependencies are folded in separately via the
    Merkle parent hashes in :func:`compute_step_hashes`.
    """
    step = node.step
    impl = node.implementation
    referenced = _referenced_pipeline_inputs(node)
    resolved_inputs = {name: pipeline_inputs.get(name) for name in sorted(referenced)}
    pipeline_input_paths = {
        name: _path_fingerprint(resolved_inputs[name])
        for name in sorted(referenced)
        if getattr(input_declarations.get(name), "fingerprint_contents", False)
    }
    param_paths = {
        name: _path_fingerprint(node.resolved_params.get(name))
        for name, declaration in sorted(node.spec.params.items())
        if getattr(declaration, "fingerprint_contents", False)
    }
    return {
        "op": step.op,
        "inputs": step.inputs,
        "params": step.params,
        "resolved_params": node.resolved_params,
        "map_over": step.map_over,
        "collect": step.collect,
        "sweep": step.sweep.model_dump() if step.sweep is not None else None,
        "callable_path": impl.callable_path if impl is not None else None,
        "implementation_key": impl.key if impl is not None else None,
        "param_map": impl.param_map if impl is not None else None,
        "output_map": impl.output_map if impl is not None else None,
        "custom_spec": (
            step.custom_spec.model_dump() if step.custom_spec is not None else None
        ),
        "pipeline_inputs": resolved_inputs,
        "pipeline_input_paths": pipeline_input_paths,
        "param_paths": param_paths,
    }


def _step_upstream(dag: PipelineDAG) -> dict[str, set[str]]:
    """Map each step id to the set of step ids that feed it (data + ordering)."""
    upstream: dict[str, set[str]] = {sid: set() for sid in dag.topological_order}
    for edge in dag.edges:
        upstream.setdefault(edge.target_step_id, set()).add(edge.source_step_id)
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        for dep in node.step.depends_on or []:
            upstream.setdefault(step_id, set()).add(dep)
    return upstream


def compute_step_hashes(
    dag: PipelineDAG,
    pipeline_inputs: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Per-step Merkle hashes used for content-addressed checkpoint validation.

    Each step's hash combines a fingerprint of the step's own definition (see
    :func:`_step_fingerprint`) with the hashes of every upstream step that
    feeds one of its inputs. Because steps are visited in topological order,
    every parent hash is available before its children are computed.

    The practical effect: editing a step (e.g. changing a param on a late ML
    step) only changes that step's hash and the hashes of its descendants.
    Unaffected upstream checkpoints keep the same hash and are reused on the
    next run, instead of the whole recipe being treated as new.
    """
    pipeline_inputs = pipeline_inputs or {}
    input_declarations = dag.recipe.inputs
    upstream = _step_upstream(dag)
    hashes: dict[str, str] = {}
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        fingerprint = _step_fingerprint(node, pipeline_inputs, input_declarations)
        parents = sorted(p for p in upstream.get(step_id, set()) if p in hashes)
        parent_hashes = [hashes[p] for p in parents]
        payload = json.dumps(
            {"fingerprint": fingerprint, "parents": parent_hashes},
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        hashes[step_id] = hashlib.sha256(payload).hexdigest()
    return hashes


@dataclass
class ExecutionPlan:
    """Plan describing which steps run, load, or can be fully pruned.

    ``blockers`` lists intermediate must-run steps that lack a checkpoint,
    limiting how far upstream the resume frontier can reach. Non-empty when
    a partial cache exists but some intermediate step cannot be skipped.
    """

    must_run: set[str]
    loadable: set[str]
    marker_hits: set[str]
    pruned: set[str]
    blockers: list[str]


def plan_execution(
    dag: PipelineDAG,
    checkpoints: CheckpointManager | None,
    *,
    force: bool = False,
    regenerate_outputs: bool = False,
) -> ExecutionPlan:
    """Plan minimal correct execution for a DAG given current checkpoints.

    The planner walks the DAG backward from the required goal steps (terminal
    steps, sinks, no-output steps, and any recipe-declared outputs). When a
    required step has a valid checkpoint, that checkpoint becomes the frontier
    and its ancestors are not pulled in. When a required side-effect step has a
    valid marker, the step is skipped and its ancestors are likewise pruned.
    """
    if checkpoints is None:
        return ExecutionPlan(
            must_run=set(dag.topological_order),
            loadable=set(),
            marker_hits=set(),
            pruned=set(),
            blockers=[],
        )

    upstream = _step_upstream(dag)
    terminal, _intermediate = classify_steps(dag)
    recipe_output_steps = {
        output.step_id for output in (dag.recipe.outputs or {}).values()
    }
    needed: set[str] = {
        step_id
        for step_id in dag.topological_order
        if (
            step_id in terminal
            or dag.nodes[step_id].spec.sink
            or not dag.nodes[step_id].spec.outputs
            or step_id in recipe_output_steps
        )
    }
    must_run: set[str] = set()
    loadable: set[str] = set()
    marker_hits: set[str] = set()
    pruned: set[str] = set()

    for step_id in reversed(dag.topological_order):
        if step_id not in needed:
            pruned.add(step_id)
            continue

        node = dag.nodes[step_id]
        is_side_effect = node.spec.sink or not node.spec.outputs
        parents = upstream.get(step_id, set())

        if force:
            must_run.add(step_id)
            needed.update(parents)
            continue

        if not is_side_effect and checkpoints.has_checkpoint(step_id):
            loadable.add(step_id)
            continue

        if (
            is_side_effect
            and not regenerate_outputs
            and checkpoints.has_marker(step_id)
        ):
            marker_hits.add(step_id)
            continue

        must_run.add(step_id)
        needed.update(parents)

    # Compute which must-run intermediate steps are limiting the resume frontier.
    # These are steps that have no checkpoint but sit between a pruned/loadable
    # upstream and a goal step, meaning adding checkpoint:always to them would
    # let future runs skip more work.
    terminal, _intermediate = classify_steps(dag)
    recipe_output_steps = {
        output.step_id for output in (dag.recipe.outputs or {}).values()
    }
    goal_steps: set[str] = {
        step_id
        for step_id in dag.topological_order
        if (
            step_id in terminal
            or dag.nodes[step_id].spec.sink
            or not dag.nodes[step_id].spec.outputs
            or step_id in recipe_output_steps
        )
    }
    blockers: list[str] = [
        step_id
        for step_id in dag.topological_order
        if (
            step_id in must_run
            and step_id not in goal_steps
            and not dag.nodes[step_id].spec.sink
            and dag.nodes[step_id].spec.outputs
        )
    ]

    return ExecutionPlan(
        must_run=must_run,
        loadable=loadable,
        marker_hits=marker_hits,
        pruned=pruned,
        blockers=blockers,
    )



def classify_steps(dag: PipelineDAG) -> tuple[set[str], set[str]]:
    """Partition a DAG's step ids into ``(terminal, intermediate)`` sets.

    A step is *intermediate* if any of its outputs is consumed by another
    step's input or param. All other steps are *terminal* (leaf nodes).
    Steps that produce no outputs (sinks) are treated as terminal because
    they have nothing worth distinguishing as intermediate.
    """
    consumed: set[str] = set()
    for edge in dag.edges:
        consumed.add(edge.source_step_id)
    terminal: set[str] = set()
    intermediate: set[str] = set()
    for step_id in dag.topological_order:
        if step_id in consumed:
            intermediate.add(step_id)
        else:
            terminal.add(step_id)
    return terminal, intermediate


def _resolve_recipe_mode(dag: PipelineDAG) -> CheckpointMode:
    hints = dag.recipe.execution
    if hints is not None and hints.checkpoint_mode is not None:
        return hints.checkpoint_mode
    return _DEFAULT_CHECKPOINT_MODE


def explicit_checkpoint_steps(dag: PipelineDAG) -> set[str]:
    """Step ids explicitly marked ``checkpoint: always`` in the recipe."""
    return {
        node.step.id
        for node in dag.nodes.values()
        if node.step.checkpoint == "always"
    }


def resolve_checkpoint_policy(
    dag: PipelineDAG,
    *,
    mode: CheckpointMode | str | None = None,
    extra_step_ids: Iterable[str] | None = None,
) -> set[str]:
    """Return the set of step ids whose outputs should be persisted.

    Combines (in priority order):
        * per-step ``Step.checkpoint`` ("always" forces save, "never" blocks)
        * ad-hoc ``extra_step_ids`` (force save)
        * the recipe / call-site ``mode``
            - ``eager``   : every step
            - ``explicit``: only steps marked ``checkpoint: always``
            - ``terminal``: only steps with no downstream consumers
            - ``none``    : empty set

    Sink steps and steps with no declared outputs are excluded because they
    have nothing to persist. Unknown ids in ``extra_step_ids`` or ids that
    resolve to a sink / no-output step raise ``ValueError`` so user typos do
    not silently produce an empty pin.
    """
    effective_mode = mode or _resolve_recipe_mode(dag)
    if effective_mode not in {"eager", "explicit", "terminal", "none"}:
        raise ValueError(
            f"unknown checkpoint_mode {effective_mode!r}; expected one of "
            "'eager', 'explicit', 'terminal', 'none'"
        )
    extras = set(extra_step_ids or ())
    unknown = extras - set(dag.nodes)
    if unknown:
        raise ValueError(
            "unknown step id(s) passed to checkpoint pin: "
            f"{sorted(unknown)}; valid ids: {sorted(dag.nodes)}"
        )
    bad_pins = {
        sid
        for sid in extras
        if dag.nodes[sid].spec.sink or not dag.nodes[sid].spec.outputs
    }
    if bad_pins:
        raise ValueError(
            "cannot pin checkpoint on sink or no-output step(s): "
            f"{sorted(bad_pins)}"
        )
    terminal, _intermediate = classify_steps(dag)

    policy: set[str] = set()
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.spec.sink or not node.spec.outputs:
            continue
        per_step = node.step.checkpoint
        if per_step == "never":
            continue
        if per_step == "always" or step_id in extras:
            policy.add(step_id)
            continue
        if effective_mode == "eager":
            policy.add(step_id)
        elif effective_mode == "terminal" and step_id in terminal:
            policy.add(step_id)
        # "explicit" and "none" fall through with no auto-include.
    return policy


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _coerce_echodata_bool_attrs(echodata: Any) -> None:
    """Convert Python bool dataset attributes to int in-place on each DataTree node.

    Older versions of the netCDF4 C library reject the 'b1' (bool) dtype for
    group attributes (e.g., ``Provenance.is_combined``). Writing 0/1 integers
    is semantically equivalent for all downstream truthiness checks.

    EchoData.__getitem__ returns a *copy* via ``node.to_dataset()``, so we must
    write the modified dataset back through each DataTree node directly.
    """
    tree = getattr(echodata, "_tree", None)
    if tree is None:
        return
    for node in tree.subtree:
        ds = getattr(node, "dataset", None)
        if ds is None:
            continue
        changed = False
        new_attrs: dict[str, Any] = {}
        for key, val in ds.attrs.items():
            if type(val) is bool or (hasattr(val, "dtype") and val.dtype.kind == "b"):
                new_attrs[key] = int(val)
                changed = True
            else:
                new_attrs[key] = val
        if changed:
            node.dataset = ds.assign_attrs(new_attrs)


def _is_echodata(value: Any) -> bool:
    """Return True when ``value`` looks like an echopype ``EchoData``."""
    cls = value.__class__
    return cls.__name__ == "EchoData" and cls.__module__.startswith("echopype")


@contextmanager
def _zarr_write_warnings_suppressed():
    """Context manager that silences expected Zarr V3 interim warnings.

    Zarr V3 raises ``UnstableSpecificationWarning`` for fixed-length UTF-32
    string dtypes (used by echopype's EchoData metadata) and
    ``ZarrUserWarning`` for consolidated metadata, neither of which affects
    correctness for our read-back path.  These are suppressed at the write
    site rather than globally so that genuine zarr warnings elsewhere remain
    visible.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*FixedLengthUTF32.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=".*Consolidated metadata.*",
            category=UserWarning,
        )
        yield


def _remove_existing_output(path: Path) -> None:
    """Remove an existing checkpoint artifact before rewriting it.

    On Windows, Zarr metadata replacement can fail when rewriting an existing
    local store in place. Clearing the prior artifact first keeps checkpoint
    saves idempotent across reruns.

    An ``onerror`` handler clears the read-only bit on any files that resist
    deletion (Windows sometimes marks zarr metadata files as read-only).
    """
    if not path.exists():
        return
    if path.is_dir():
        def _on_error(func: Any, fpath: Any, _exc_info: Any) -> None:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        shutil.rmtree(path, onerror=_on_error)
        return
    try:
        path.unlink()
    except PermissionError:
        os.chmod(str(path), stat.S_IWRITE)
        path.unlink()


def _write_zarr_with_retry(
    write_fn: "Any",
    zarr_path: Path,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> None:
    """Retry a zarr write on Windows PermissionError.

    zarr-python v3 uses atomic writes: data is written to a ``.partial``
    temporary file which is then renamed to the final path.  On Windows,
    antivirus software or the file-indexing service can transiently hold an
    exclusive lock on the ``.partial`` file between the write and rename
    steps, causing a ``PermissionError`` ([WinError 5]).  Retrying after a
    brief delay — with a full cleanup of any partially-written state — is
    the most robust mitigation without modifying zarr's storage internals.
    """
    last_exc: PermissionError | None = None
    for attempt in range(max_retries):
        _remove_existing_output(zarr_path)
        try:
            write_fn()
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))  # 1 s, 2 s
    raise last_exc  # type: ignore[misc]


def _checkpoint_output_category(
    value: Any,
    preferred_format: str,
) -> str:
    """Return the output subdirectory for a checkpointed value."""
    import xarray as xr

    if _is_echodata(value):
        return ZARR_DATA_DIR if preferred_format == "zarr" else OTHER_DATA_DIR
    if isinstance(value, (xr.DataArray, xr.Dataset)):
        return ZARR_DATA_DIR if preferred_format == "zarr" else OTHER_DATA_DIR
    if _is_json_safe(value):
        return JSON_DATA_DIR
    return OTHER_DATA_DIR


def _checkpoint_artifact_stem(step_id: str, out_name: str) -> str:
    """Return the original stem for on-disk checkpoint artifacts."""
    return f"{step_id}_{out_name}"


def _serialize_output(
    value: Any,
    base_path: Path,
    preferred_format: str = "zarr",
) -> tuple[Path, str]:
    """Write ``value`` to disk, returning the file path and a format tag.

    Dispatch order:
    1. EchoData  → echodata_zarr (default) / echodata_netcdf / pickle
    2. xr.DataArray → zarr_da / netcdf_da / pickle  (wrapped in single-var Dataset)
    3. xr.Dataset   → zarr / netcdf / pickle
    4. JSON-safe    → json  (always, regardless of preferred_format)
    5. fallback     → pickle
    """
    import xarray as xr

    if _is_echodata(value):
        if preferred_format == "netcdf":
            nc_path = base_path.with_suffix(".nc")
            _remove_existing_output(nc_path)
            _coerce_echodata_bool_attrs(value)
            value.to_netcdf(save_path=nc_path, compress=False, overwrite=True)
            return nc_path, "echodata_netcdf"
        if preferred_format == "pickle":
            pkl_path = base_path.with_suffix(".pkl")
            _remove_existing_output(pkl_path)
            with pkl_path.open("wb") as fh:
                pickle.dump(value, fh)
            return pkl_path, "pickle"
        # default: zarr
        zarr_path = base_path.with_suffix(".zarr")

        def _write_echodata() -> None:
            with _zarr_write_warnings_suppressed():
                value.to_zarr(
                    save_path=zarr_path,
                    overwrite=True,
                    compress=False,
                    zarr_format=2,
                )

        _write_zarr_with_retry(_write_echodata, zarr_path)
        return zarr_path, "echodata_zarr"

    if isinstance(value, xr.DataArray):
        ds = value.to_dataset(name="_da")
        if preferred_format == "netcdf":
            nc_path = base_path.with_suffix(".nc")
            _remove_existing_output(nc_path)
            ds.to_netcdf(str(nc_path))
            return nc_path, "netcdf_da"
        if preferred_format == "pickle":
            pkl_path = base_path.with_suffix(".pkl")
            _remove_existing_output(pkl_path)
            with pkl_path.open("wb") as fh:
                pickle.dump(value, fh)
            return pkl_path, "pickle"
        # default: zarr
        zarr_path = base_path.with_suffix(".zarr")

        def _write_da() -> None:
            with _zarr_write_warnings_suppressed():
                ds.to_zarr(
                    str(zarr_path),
                    mode="w",
                    compute=True,
                    align_chunks=True,
                    zarr_format=2,
                )

        _write_zarr_with_retry(_write_da, zarr_path)
        return zarr_path, "zarr_da"

    if isinstance(value, xr.Dataset):
        if preferred_format == "netcdf":
            nc_path = base_path.with_suffix(".nc")
            _remove_existing_output(nc_path)
            value.to_netcdf(str(nc_path))
            return nc_path, "netcdf"
        if preferred_format == "pickle":
            pkl_path = base_path.with_suffix(".pkl")
            _remove_existing_output(pkl_path)
            with pkl_path.open("wb") as fh:
                pickle.dump(value, fh)
            return pkl_path, "pickle"
        # default: zarr
        zarr_path = base_path.with_suffix(".zarr")

        def _write_ds() -> None:
            with _zarr_write_warnings_suppressed():
                value.to_zarr(
                    str(zarr_path),
                    mode="w",
                    compute=True,
                    align_chunks=True,
                    zarr_format=2,
                )

        _write_zarr_with_retry(_write_ds, zarr_path)
        return zarr_path, "zarr"

    if _is_json_safe(value):
        json_path = base_path.with_suffix(".json")
        _remove_existing_output(json_path)
        json_path.write_text(
            json.dumps(value, indent=2, default=str), encoding="utf-8"
        )
        return json_path, "json"

    pkl_path = base_path.with_suffix(".pkl")
    _remove_existing_output(pkl_path)
    with pkl_path.open("wb") as fh:
        pickle.dump(value, fh)
    return pkl_path, "pickle"


def _is_json_safe(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool, type(None))):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_json_safe(v) for k, v in value.items()
        )
    return False


def _deserialize_output(path: Path, fmt: str) -> Any:
    if fmt == "echodata_netcdf":
        from echopype.echodata.echodata import EchoData

        return EchoData.from_file(str(path))
    if fmt == "echodata_zarr":
        import echopype as ep

        return ep.open_converted(str(path), chunks={})
    if fmt == "netcdf":
        import xarray as xr

        return xr.open_dataset(str(path))
    if fmt == "netcdf_da":
        import xarray as xr

        return xr.open_dataset(str(path))["_da"]
    if fmt == "zarr":
        import xarray as xr

        return xr.open_dataset(str(path), engine="zarr", chunks={})
    if fmt == "zarr_da":
        import xarray as xr

        return xr.open_dataset(str(path), engine="zarr", chunks={})["_da"]
    if fmt == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if fmt == "pickle":
        with path.open("rb") as fh:
            return pickle.load(fh)
    raise ValueError(f"unknown checkpoint format: {fmt!r}")


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------


@dataclass
class _StepCacheMeta:
    step_id: str
    step_hash: str
    outputs: dict[str, dict[str, str]]  # out_name -> {"path": str, "format": str}
    marker: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _StepCacheMeta:
        # Accept both current format (step_hash) and legacy format (recipe_hash
        # only). Legacy sidecars missing step_hash are treated as stale.
        step_hash = data.get("step_hash") or data.get("recipe_hash", "")
        return cls(
            step_id=data["step_id"],
            step_hash=step_hash,
            outputs=data.get("outputs", {}),
            marker=bool(data.get("marker", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_hash": self.step_hash,
            "marker": self.marker,
            "outputs": self.outputs,
        }


class CheckpointManager:
    """Read/write per-step checkpoints under an output directory.

    Cache validity is keyed on a per-step hash (see :func:`compute_step_hashes`)
    so that editing one step only invalidates that step and its descendants —
    upstream checkpoints stay reusable.
    """

    def __init__(
        self,
        output_dir: str | Path,
        hashes: dict[str, str],
        *,
        preferred_format: CheckpointFormat | str = "zarr",
    ) -> None:
        self.output_dir = Path(output_dir)
        self._hashes = dict(hashes)
        self.preferred_format = preferred_format

    def _meta_path(self, step_id: str) -> Path:
        return self.output_dir / CACHE_METADATA_DIR / f"{step_id}{META_SUFFIX}"

    def _iter_meta_paths(self) -> list[Path]:
        meta_dir = self.output_dir / CACHE_METADATA_DIR
        if not meta_dir.exists():
            return []
        return sorted(meta_dir.glob(f"*{META_SUFFIX}"))

    def _read_meta(self, step_id: str) -> _StepCacheMeta | None:
        path = self._meta_path(step_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return _StepCacheMeta.from_dict(data)

    def has_checkpoint(self, step_id: str) -> bool:
        meta = self._read_meta(step_id)
        if meta is None or meta.marker:
            return False
        expected = self._hashes.get(step_id)
        if expected is None or meta.step_hash != expected:
            return False
        for entry in meta.outputs.values():
            if not Path(entry["path"]).exists():
                return False
        return True

    def has_marker(self, step_id: str) -> bool:
        """Return True when a hash-matching *side-effect* marker exists.

        Markers are written by :meth:`save_marker` for sink / no-output steps
        that produce on-disk side effects (e.g. plots) rather than cacheable
        return values. A matching marker means the step already ran for the
        current per-step hash, so its side-effect outputs should already exist
        on disk and the step can be skipped.
        """
        meta = self._read_meta(step_id)
        if meta is None or not meta.marker:
            return False
        expected = self._hashes.get(step_id)
        return expected is not None and meta.step_hash == expected

    def save_marker(self, step_id: str) -> None:
        """Record that a side-effect (sink / no-output) step ran for this hash.

        Writes a metadata-only sidecar (no serialized outputs) so a later run
        with an unchanged per-step hash can skip re-generating the step's
        on-disk side effects.
        """
        step_hash = self._hashes.get(step_id, "")
        meta = _StepCacheMeta(step_id=step_id, step_hash=step_hash, outputs={}, marker=True)
        meta_path = self._meta_path(step_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    def save(self, step_id: str, outputs: dict[str, Any]) -> None:
        out_meta: dict[str, dict[str, str]] = {}
        for out_name, value in outputs.items():
            category_dir = self.output_dir / _checkpoint_output_category(
                value, str(self.preferred_format)
            )
            category_dir.mkdir(parents=True, exist_ok=True)
            base = category_dir / _checkpoint_artifact_stem(step_id, out_name)
            path, fmt = _serialize_output(value, base, self.preferred_format)
            out_meta[out_name] = {"path": str(path), "format": fmt}
        step_hash = self._hashes.get(step_id, "")
        meta = _StepCacheMeta(step_id=step_id, step_hash=step_hash, outputs=out_meta)
        meta_path = self._meta_path(step_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    def load(self, step_id: str) -> dict[str, Any]:
        meta = self._read_meta(step_id)
        if meta is None:
            raise FileNotFoundError(f"no checkpoint metadata for step {step_id!r}")
        expected = self._hashes.get(step_id)
        if expected is None or meta.step_hash != expected:
            raise ValueError(
                f"checkpoint for step {step_id!r} was written from a "
                "different recipe (step-hash mismatch)"
            )
        loaded: dict[str, Any] = {}
        for out_name, entry in meta.outputs.items():
            loaded[out_name] = _deserialize_output(Path(entry["path"]), entry["format"])
        return loaded

    def clean(
        self,
        dag: PipelineDAG,
        mode: CleanMode = "intermediate",
        *,
        dry_run: bool = False,
    ) -> list[Path]:
        """Remove checkpoint files according to ``mode``; returns removed paths.

        Modes:
            ``intermediate``: drop checkpoints for intermediate steps only.
                Steps explicitly marked ``checkpoint: always`` in the recipe
                are protected and kept.
            ``all``: drop every checkpoint and sidecar in the directory.
                ``checkpoint: always`` marks are **not** protected.
            ``stale``: drop only checkpoints whose hash does not match the
                current recipe's per-step hash. ``checkpoint: always`` marks
                are **not** protected because mismatched hashes are unusable
                anyway.
        """
        if not self.output_dir.exists():
            return []
        _terminal, intermediate = classify_steps(dag)
        protected = explicit_checkpoint_steps(dag)
        removed: list[Path] = []
        for meta_path in self._iter_meta_paths():
            step_id = meta_path.name[: -len(META_SUFFIX)]
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta = _StepCacheMeta.from_dict(data)
            if mode == "intermediate":
                if step_id not in intermediate:
                    continue
                if step_id in protected:
                    continue
            if mode == "stale":
                expected = self._hashes.get(step_id)
                if expected is not None and meta.step_hash == expected:
                    continue

            for entry in meta.outputs.values():
                removed.append(Path(entry["path"]))
            removed.append(meta_path)

        if dry_run:
            return removed
        for path in removed:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except FileNotFoundError:
                continue
        return removed

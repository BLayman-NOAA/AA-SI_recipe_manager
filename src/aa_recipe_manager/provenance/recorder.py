# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""ProvenanceRecorder: captures runtime environment details."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.storage import is_remote_url

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import (
        PipelineDAG,
        Provenance,
        RawFileEntry,
        RawInputsRecord,
    )

#: Marks the port/input whose runtime value is the run's raw input file list.
RAW_FILE_LIST_ROLE = "raw_file_list"
#: Recipe pipeline-input reference, e.g. ``${inputs.raw_folder}``.
_PIPELINE_INPUT_REF = re.compile(r"^\$\{inputs\.([^}]+)\}$")


def _installed_version(package_name: str) -> str:
    try:
        return pkg_version(package_name)
    except PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Raw input file provenance
# ---------------------------------------------------------------------------


def _raw_basename(path: Any) -> str:
    """Basename of a local path or fsspec URL (handles / and \\ separators)."""
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _digest_raw_files(entries: list[RawFileEntry]) -> str:
    """Deterministic sha256 over the sorted ``(name, size)`` pairs.

    Location-independent (names only, no directory) and portable across
    environments (size is the same signal everywhere). Reused on resume so an
    inherited digest is comparable to a freshly resolved one.
    """
    hasher = hashlib.sha256()
    for entry in entries:
        hasher.update(f"{entry.name}\t{entry.size}\n".encode())
    return hasher.hexdigest()


def _raw_file_sizes(
    paths: list[Any], storage_options: dict[str, Any] | None = None
) -> dict[str, int | None]:
    """Best-effort per-file byte size, keyed by the original path string.

    Local files are ``os.stat``-ed; remote files are grouped by parent and read
    from a single ``fs.ls`` per parent (matched by basename). Never raises —
    unresolved sizes come back ``None`` so provenance capture cannot break a run.
    """
    sizes: dict[str, int | None] = {}
    remote_by_parent: dict[str, list[str]] = {}
    for path in paths:
        sp = str(path)
        if is_remote_url(sp):
            parent = sp.replace("\\", "/").rstrip("/").rsplit("/", 1)[0]
            remote_by_parent.setdefault(parent, []).append(sp)
        else:
            try:
                sizes[sp] = os.stat(sp).st_size
            except OSError:
                sizes[sp] = None
    for parent, group in remote_by_parent.items():
        by_base: dict[str, int | None] = {}
        try:
            fs, fs_parent = __import__(
                "fsspec.core", fromlist=["url_to_fs"]
            ).url_to_fs(parent, **(storage_options or {}))
            for info in fs.ls(fs_parent, detail=True):
                by_base[_raw_basename(info["name"])] = info.get("size")
        except Exception:  # missing creds / network / driver — degrade to None
            by_base = {}
        for sp in group:
            sizes[sp] = by_base.get(_raw_basename(sp))
    return sizes


def _resolve_tagged_input_value(
    node: Any,
    port: str,
    result_outputs: dict[str, dict[str, Any]],
    pipeline_inputs: dict[str, Any],
) -> Any:
    """Resolve a tagged step *input* port's runtime value from the wiring.

    Follows a ``${step.output}`` edge into ``result_outputs`` or a
    ``${inputs.name}`` reference into ``pipeline_inputs``; a literal value is
    returned as-is. Lets a recipe that reads raw files directly (no reader step
    that materializes the list as an output) still be harvested.
    """
    from aa_recipe_manager.resolver.params import parse_ref

    wiring = node.step.inputs.get(port)
    ref = parse_ref(wiring)
    if ref is not None:
        src_step, src_out = ref
        return (result_outputs.get(src_step) or {}).get(src_out)
    if isinstance(wiring, str):
        match = _PIPELINE_INPUT_REF.match(wiring.strip())
        if match:
            return pipeline_inputs.get(match.group(1))
    return wiring


def _is_raw_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0


def build_raw_inputs_record(
    dag: PipelineDAG,
    result_outputs: dict[str, dict[str, Any]],
    pipeline_inputs: dict[str, Any],
    storage_options: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> RawInputsRecord | None:
    """Harvest the raw input file list a run read into a ``RawInputsRecord``.

    Sources, in preference order (topological, so the earliest reader wins):
    a step *output* port tagged ``provenance_role: raw_file_list`` (e.g.
    ``initial_setup.raw_file_paths``); a tagged step *input* port resolved
    through its wiring (covers recipes that skip the reader); a tagged pipeline
    *input*. Returns ``None`` when nothing is tagged/produced.

    Records only basenames + sizes (no directory), sorted, with a digest and
    ``origin_run_id`` set to ``run_id`` (this run originally resolved the list).
    """
    from aa_recipe_manager.model.types import RawFileEntry, RawInputsRecord

    raw_paths: Any = None
    producing_step: str | None = None
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        for out_port, decl in (node.spec.outputs or {}).items():
            if getattr(decl, "provenance_role", None) == RAW_FILE_LIST_ROLE:
                value = (result_outputs.get(step_id) or {}).get(out_port)
                if _is_raw_list(value):
                    raw_paths, producing_step = value, f"{step_id}.{out_port}"
                    break
        if raw_paths is not None:
            break
        for in_port, decl in (node.spec.inputs or {}).items():
            if getattr(decl, "provenance_role", None) == RAW_FILE_LIST_ROLE:
                value = _resolve_tagged_input_value(
                    node, in_port, result_outputs, pipeline_inputs
                )
                if _is_raw_list(value):
                    raw_paths, producing_step = value, f"{step_id}.{in_port}"
                    break
        if raw_paths is not None:
            break
    if raw_paths is None:
        for name, decl in (dag.recipe.inputs or {}).items():
            if getattr(decl, "provenance_role", None) == RAW_FILE_LIST_ROLE:
                value = pipeline_inputs.get(name)
                if _is_raw_list(value):
                    raw_paths, producing_step = value, f"pipeline_input:{name}"
                    break
    if raw_paths is None:
        return None

    sizes = _raw_file_sizes(list(raw_paths), storage_options)
    entries = sorted(
        (
            RawFileEntry(name=_raw_basename(path), size=sizes.get(str(path)))
            for path in raw_paths
        ),
        key=lambda entry: entry.name,
    )
    return RawInputsRecord(
        files=entries,
        count=len(entries),
        digest=_digest_raw_files(entries),
        source="resolved",
        producing_step=producing_step,
        origin_run_id=run_id,
    )


def raw_file_list_step_ids(dag: PipelineDAG) -> tuple[str, ...]:
    """Step ids that carry the raw-file-list role, in topological order.

    Passed to ``TieredCheckpointStore.recovered_raw_inputs`` as the preferred
    (raw lineage) steps so recovery picks the authoritative record.
    """
    ids: list[str] = []
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        ports = {**(node.spec.outputs or {}), **(node.spec.inputs or {})}
        if any(
            getattr(decl, "provenance_role", None) == RAW_FILE_LIST_ROLE
            for decl in ports.values()
        ):
            ids.append(step_id)
    return tuple(ids)


class ProvenanceRecorder:
    """Captures runtime environment from a PipelineDAG."""

    @staticmethod
    def capture(
        dag: PipelineDAG,
        recipe_path: Path | str | None = None,
        inputs: dict[str, Any] | None = None,
        raw_inputs: RawInputsRecord | None = None,
    ) -> Provenance:
        """Produce a Provenance object from the current runtime environment.

        If recipe_path is provided the recipe hash is computed from the file
        content. Otherwise the hash is computed from the serialized Recipe model.
        ``raw_inputs`` records the raw input files this run read (see
        :func:`build_raw_inputs_record`).
        """
        from aa_recipe_manager.model.types import Provenance, ResolvedStepInfo

        # Recipe hash.
        if recipe_path is not None:
            file_bytes = Path(recipe_path).read_bytes()
            recipe_hash = hashlib.sha256(file_bytes).hexdigest()
        else:
            model_bytes = dag.recipe.model_dump_json().encode()
            recipe_hash = hashlib.sha256(model_bytes).hexdigest()

        # Collect dependencies from all nodes, preserving source/url metadata.
        dep_versions: dict[str, Any] = {}
        for step_id in dag.topological_order:
            node = dag.nodes[step_id]
            if node.implementation and node.implementation.dependency:
                dep = node.implementation.dependency
                if dep.name not in dep_versions:
                    entry: dict[str, Any] = {
                        "installed_version": _installed_version(dep.name),
                        "source": dep.source,
                    }
                    if dep.url:
                        entry["url"] = dep.url
                    dep_versions[dep.name] = entry

        # Build per-step provenance.
        resolved_steps: dict[str, ResolvedStepInfo] = {}
        for step_id in dag.topological_order:
            node = dag.nodes[step_id]
            if node.implementation is None:
                continue
            dep = node.implementation.dependency
            resolved_steps[step_id] = ResolvedStepInfo(
                op=node.spec.op,
                implementation_key=node.implementation.key,
                callable_path=node.implementation.callable_path,
                package_name=dep.name,
                installed_version=_installed_version(dep.name),
                params_used=node.resolved_params,
            )

        return Provenance(
            recipe_hash=recipe_hash,
            recipe_name=dag.recipe.name,
            recipe_version=dag.recipe.version,
            timestamp=datetime.now(timezone.utc),
            python_version=sys.version,
            python_version_number=platform.python_version(),
            os_info=platform.platform(),
            inputs=dict(inputs) if inputs else {},
            resolved_steps=resolved_steps,
            resolved_dependencies=dep_versions,
            raw_inputs=raw_inputs,
        )

    @staticmethod
    def capture_environment(package_names: list[str] | None = None) -> dict[str, Any]:
        """Capture the runtime environment without a DAG.

        Returns a flat dict with Python version, platform, timestamp, and
        installed versions of any requested packages. Suitable for embedding in
        generated notebook provenance cells.
        """
        result: dict[str, Any] = {
            "python_version": sys.version,
            "python_version_number": platform.python_version(),
            "os_info": platform.platform(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if package_names:
            result["installed_packages"] = {
                pkg: _installed_version(pkg) for pkg in package_names
            }
        return result


def to_dict(prov: Provenance) -> dict[str, Any]:
    """Serialize a Provenance object to a plain dict."""
    return prov.model_dump(mode="python")


def to_json(prov: Provenance) -> str:
    """Serialize a Provenance object to a JSON string."""
    return prov.model_dump_json(indent=2)


def to_yaml(prov: Provenance) -> str:
    """Serialize a Provenance object to a YAML string.

    Excludes ``resolved_steps`` — those are redundant when the recipe file is
    available. The written fields are sufficient to reproduce the run:
    recipe identity (name, version, hash), runtime environment, inputs
    supplied at execution time, and pinned dependency versions.
    """
    import io

    from ruamel.yaml import YAML

    data = prov.model_dump(mode="json", exclude={"resolved_steps"})
    # Drop inputs section entirely when empty to keep the file clean.
    if not data.get("inputs"):
        data.pop("inputs", None)
    # Drop raw_inputs when nothing was recorded.
    if not data.get("raw_inputs"):
        data.pop("raw_inputs", None)
    yaml = YAML()
    yaml.default_flow_style = False
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def to_netcdf_attrs(prov: Provenance) -> dict[str, str]:
    """Return a flat dict of string attributes suitable for NetCDF global attrs.

    All keys are prefixed with 'provenance_'. Nested structures are
    JSON-serialized to ensure every value is a plain string.
    """
    attrs: dict[str, str] = {
        "provenance_recipe_hash": prov.recipe_hash,
        "provenance_recipe_name": prov.recipe_name,
        "provenance_recipe_version": prov.recipe_version,
        "provenance_timestamp": prov.timestamp.isoformat(),
        "provenance_python_version": prov.python_version,
        "provenance_os_info": prov.os_info,
    }
    if prov.resolved_dependencies:
        attrs["provenance_resolved_dependencies"] = json.dumps(
            prov.resolved_dependencies
        )
    if prov.raw_inputs is not None:
        # Count + digest only — never the full file list (avoids a multi-KB
        # global attr; the list lives in the YAML/JSON provenance and sidecars).
        attrs["provenance_raw_input_count"] = str(prov.raw_inputs.count)
        attrs["provenance_raw_inputs_digest"] = prov.raw_inputs.digest
    return attrs

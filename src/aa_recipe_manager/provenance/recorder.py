# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""ProvenanceRecorder: captures runtime environment details."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG, Provenance


def _installed_version(package_name: str) -> str:
    try:
        return pkg_version(package_name)
    except PackageNotFoundError:
        return "unknown"


class ProvenanceRecorder:
    """Captures runtime environment from a PipelineDAG."""

    @staticmethod
    def capture(dag: PipelineDAG, recipe_path: Path | str | None = None) -> Provenance:
        """Produce a Provenance object from the current runtime environment.

        If recipe_path is provided the recipe hash is computed from the file
        content. Otherwise the hash is computed from the serialized Recipe model.
        """
        from aa_recipe_manager.model.types import Provenance, ResolvedStepInfo

        # Recipe hash.
        if recipe_path is not None:
            file_bytes = Path(recipe_path).read_bytes()
            recipe_hash = hashlib.sha256(file_bytes).hexdigest()
        else:
            model_bytes = dag.recipe.model_dump_json().encode()
            recipe_hash = hashlib.sha256(model_bytes).hexdigest()

        # Collect dependencies from all nodes.
        dep_versions: dict[str, str] = {}
        for step_id in dag.topological_order:
            node = dag.nodes[step_id]
            if node.implementation and node.implementation.dependency:
                pkg = node.implementation.dependency.name
                if pkg not in dep_versions:
                    dep_versions[pkg] = _installed_version(pkg)

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
            os_info=platform.platform(),
            resolved_steps=resolved_steps,
            resolved_dependencies=dep_versions,
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
    """Serialize a Provenance object to a YAML string."""
    import io

    from ruamel.yaml import YAML

    data = prov.model_dump(mode="json")
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
    return attrs

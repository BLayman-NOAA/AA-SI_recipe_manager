# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Files written beside a generated flow: registry entry, YAML and requirements."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG
    from aa_recipe_manager.resolver.dependencies import ResolvedDependencies


def flow_slug(recipe_name: str) -> str:
    """Return the flow key for a recipe name: lower case letters, digits and _."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", recipe_name).strip("_").lower()
    if not slug:
        slug = "recipe"
    if slug[0].isdigit():
        slug = f"recipe_{slug}"
    return slug


def module_name(slug: str) -> str:
    """Return the generated module's file name."""
    return f"flows_{slug}.py"


def _registry_entry(dag: PipelineDAG, slug: str) -> str:
    description = (dag.recipe.description or dag.recipe.name).strip()
    return "\n".join([
        "# Add to FLOW_REGISTRY in src/echodataflow/deployment/flow_registry.py:",
        f'    "{slug}": FlowRegistration(',
        f'        entrypoint="echodataflow/flows/{module_name(slug)}:flow_{slug}",',
        f"        description={json.dumps(description)},",
        "    ),",
        "",
    ])


def _params_yaml(dag: PipelineDAG, slug: str, required: list[str]) -> str:
    lines = [
        "# Parameter config fragment for echodataflow-deploy run --param-config.",
        "flows:",
        f"  {slug}:",
    ]
    for name, decl in dag.recipe.inputs.items():
        if name in required:
            lines.append(f"    {name}: null  # required: set before deploying")
        elif decl.default is not None:
            lines.append(f"    {name}: {json.dumps(decl.default, default=str)}")
    lines.append(f"    output_dir: {json.dumps(f'outputs/{slug}')}")
    return "\n".join(lines) + "\n"


def _deploy_yaml(slug: str) -> str:
    return "\n".join([
        "# Deploy config fragment for echodataflow-deploy run --deploy-spec.",
        "# No schedule: the flow is run once, on demand.",
        "flows:",
        f"  {slug}:",
        f"    deployment_name: {slug.replace('_', '-')}",
        "    # task_runner:",
        "    #   type: dask",
        "    #   cluster_kwargs:",
        "    #     n_workers: 4",
        "    #     threads_per_worker: 1",
        "",
    ])


def _readme(dag: PipelineDAG, slug: str) -> str:
    return "\n".join([
        f"# {module_name(slug)}",
        "",
        f"Generated from recipe `{dag.recipe.name}` (version {dag.recipe.version}).",
        "",
        "To add it to echodataflow:",
        "",
        f"1. Copy `{module_name(slug)}` into `src/echodataflow/flows/`.",
        "2. Add the entry in `flow_registry_entry.txt` to `FLOW_REGISTRY`.",
        "3. Install the packages in `requirements.txt` in the flow's environment.",
        f"4. Merge `params.{slug}.yaml` and `deploy.{slug}.yaml` into your configs,",
        "   or pass them to `echodataflow-deploy run` to deploy this flow alone.",
        "",
        "The flow runs the recipe once, end to end. It keeps no ledger and does",
        "not resume; rerunning it recomputes everything.",
        "",
    ])


def render_sidecars(
    dag: PipelineDAG,
    slug: str,
    required: list[str],
    resolved_deps: ResolvedDependencies,
) -> dict[str, str]:
    """Return the sidecar files, by file name.

    Args:
        dag: The recipe's DAG.
        slug: The flow key.
        required: Recipe inputs a run must be given.
        resolved_deps: The recipe's resolved dependencies.

    Returns:
        File name to text.
    """
    requirements = resolved_deps.to_requirements_txt()
    return {
        "flow_registry_entry.txt": _registry_entry(dag, slug),
        f"params.{slug}.yaml": _params_yaml(dag, slug, required),
        f"deploy.{slug}.yaml": _deploy_yaml(slug),
        "requirements.txt": requirements + "\n" if requirements else "",
        "README.md": _readme(dag, slug),
    }

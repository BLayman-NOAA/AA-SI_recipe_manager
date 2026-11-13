# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""echodataflow flow generation backend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.generator.echodataflow.plan import (
    UnsupportedRecipeError,
    plan_flow,
)
from aa_recipe_manager.generator.echodataflow.render import render_flow_module
from aa_recipe_manager.generator.echodataflow.sidecars import (
    flow_slug,
    module_name,
    render_sidecars,
)
from aa_recipe_manager.validation import required_inputs

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG
    from aa_recipe_manager.resolver.dependencies import ResolvedDependencies


class EchodataflowBackend:
    """Write an echodataflow flow module and its sidecar files to a folder."""

    def generate(
        self,
        dag: PipelineDAG,
        resolved_deps: ResolvedDependencies,
        output_path: Path,
        options: dict[str, Any] | None = None,
    ) -> Path:
        """Generate the flow.

        Args:
            dag: The recipe's DAG.
            resolved_deps: Its resolved dependencies, for requirements.txt.
            output_path: Folder to write into, created if missing.
            options: Unused; accepted for the backend protocol.

        Returns:
            The path of the generated module.

        Raises:
            UnsupportedRecipeError: If the recipe cannot be compiled faithfully.
        """
        slug = flow_slug(dag.recipe.name)
        required = required_inputs(dag)
        source = render_flow_module(dag, plan_flow(dag), slug, required)
        try:
            compile(source, module_name(slug), "exec")
        except SyntaxError as exc:
            raise UnsupportedRecipeError(
                f"generated module is not valid Python at line {exc.lineno}: "
                f"{(exc.text or '').strip()}"
            ) from exc

        # Everything is rendered before anything is written, so a failure
        # leaves no half written folder behind.
        sidecars = render_sidecars(dag, slug, required, resolved_deps)
        output_path.mkdir(parents=True, exist_ok=True)
        module = output_path / module_name(slug)
        module.write_text(source, encoding="utf-8")
        for name, text in sidecars.items():
            (output_path / name).write_text(text, encoding="utf-8")
        return module

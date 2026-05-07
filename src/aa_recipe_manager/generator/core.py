# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""CodeGenerator wrapper: registry injection and backend dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG
    from aa_recipe_manager.registry.registry import Registry
    from aa_recipe_manager.resolver.dependencies import ResolvedDependencies


@runtime_checkable
class CodeGeneratorBackend(Protocol):
    """Protocol that all code generation backends must satisfy."""

    def generate(
        self,
        dag: PipelineDAG,
        resolved_deps: ResolvedDependencies,
        output_path: Path,
        options: dict[str, Any] | None = None,
    ) -> Path:
        """Generate code from the DAG and write it to output_path."""
        ...


def build_variable_name_map(dag: PipelineDAG) -> dict[tuple[str, str], str]:
    """Pre-compute output variable names for every (step_id, output_name) pair.

    Unique output names use just the output_name. When two or more steps
    produce outputs with the same name, all of those outputs are qualified as
    {step_id}_{output_name}.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for node in dag.nodes.values():
        for output_name in node.spec.outputs:
            counts[output_name] += 1

    name_map: dict[tuple[str, str], str] = {}
    for step_id, node in dag.nodes.items():
        for output_name in node.spec.outputs:
            if counts[output_name] > 1:
                name_map[(step_id, output_name)] = f"{step_id}_{output_name}"
            else:
                name_map[(step_id, output_name)] = output_name
    return name_map


class CodeGenerator:
    """Dispatches DAG code generation to a named backend.

    The default backend is 'notebook'. Additional backends can be registered
    via register_backend().
    """

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._backends: dict[str, CodeGeneratorBackend] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        from aa_recipe_manager.generator.backends.notebook import NotebookBackend
        from aa_recipe_manager.generator.backends.script import ScriptBackend

        self.register_backend("notebook", NotebookBackend())
        self.register_backend("script", ScriptBackend())

    def register_backend(self, name: str, backend: CodeGeneratorBackend) -> None:
        self._backends[name] = backend

    def generate(
        self,
        dag: PipelineDAG,
        output_path: str | Path,
        backend: str = "notebook",
        options: dict[str, Any] | None = None,
    ) -> Path:
        """Generate code from dag using the named backend."""
        from aa_recipe_manager.resolver.dependencies import resolve_dependencies

        if backend not in self._backends:
            available = ", ".join(sorted(self._backends))
            raise ValueError(
                f"Unknown backend '{backend}'. Available: {available}"
            )
        resolved_deps = resolve_dependencies(dag)
        output_path = Path(output_path)
        return self._backends[backend].generate(
            dag, resolved_deps, output_path, options
        )

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Python script code generation backend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.generator.backends.notebook import _build_notebook_cells

if TYPE_CHECKING:
	from aa_recipe_manager.model.types import PipelineDAG
	from aa_recipe_manager.resolver.dependencies import ResolvedDependencies


def _markdown_to_comments(source: str) -> str:
	lines = source.splitlines()
	if not lines:
		return "#"
	return "\n".join(f"# {line}" if line else "#" for line in lines)


class ScriptBackend:
	"""Generates a runnable Python script from a validated PipelineDAG."""

	def generate(
		self,
		dag: PipelineDAG,
		resolved_deps: ResolvedDependencies,
		output_path: Path,
		options: dict[str, Any] | None = None,
	) -> Path:
		cells = _build_notebook_cells(dag, resolved_deps, options)
		parts: list[str] = []
		for cell in cells:
			if cell.cell_type == "markdown":
				parts.append(_markdown_to_comments(cell.source))
			else:
				parts.append(cell.source)

		content = "\n\n\n".join(part for part in parts if part.strip())
		if content:
			content += "\n"

		output_path.parent.mkdir(parents=True, exist_ok=True)
		output_path.write_text(content, encoding="utf-8")
		return output_path

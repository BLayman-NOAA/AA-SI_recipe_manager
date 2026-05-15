# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Dry-run validation engine: validate a PipelineDAG without executing or generating code."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from packaging.specifiers import SpecifierSet

from aa_recipe_manager.resolver.params import resolve_input_refs

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Dependency, Implementation, ParamDeclaration, PipelineDAG


@dataclass
class DryRunStepInfo:
    """Resolved information about one step, collected during dry-run."""

    step_id: str
    op: str
    implementation_key: str | None
    callable_path: str | None
    package_name: str | None
    installed_version: str | None
    version_status: str  # "ok", "warning", "error", or "no_impl"
    params: dict[str, Any]
    param_specs: dict[str, ParamDeclaration]


@dataclass
class DryRunReport:
    """Result of a dry-run validation pass."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolved_steps: list[DryRunStepInfo] = field(default_factory=list)
    dag_diagram: str | None = None
    recipe_label: str = "Recipe"

    def format_text(self) -> str:
        """Return a human-readable summary of the dry-run report."""
        lines: list[str] = []

        lines.append(self.recipe_label)

        if self.resolved_steps:
            lines.append(f"  Steps ({len(self.resolved_steps)}, in order):")
            for i, step in enumerate(self.resolved_steps, 1):
                if step.version_status == "no_impl":
                    impl_str = "no implementation"
                    status_icon = ""
                elif step.version_status == "ok":
                    status_icon = "OK"
                    impl_str = f"{step.implementation_key} ({step.package_name}=={step.installed_version})"
                elif step.version_status == "warning":
                    status_icon = "WARN"
                    impl_str = f"{step.implementation_key} ({step.package_name}=={step.installed_version})"
                else:
                    status_icon = "ERROR"
                    impl_str = f"{step.implementation_key or '?'} ({step.package_name or '?'})"

                status_part = f" [{status_icon}]" if status_icon else ""
                op_display = (
                    f"{step.op} (custom / unregistered)"
                    if step.op == "custom"
                    else step.op
                )
                lines.append(
                    f"    {i}. {step.step_id:<20} op: {op_display:<30} {impl_str}{status_part}"
                )

                for param_name, value in step.params.items():
                    pspec = step.param_specs.get(param_name)
                    type_str = f", {pspec.type}" if pspec and pspec.type else ""
                    units_str = f", {pspec.units}" if pspec and pspec.units else ""
                    lines.append(
                        f"         {param_name}: {value!r}{type_str}{units_str}"
                    )

        if self.warnings:
            lines.append(f"\n  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    - {w}")
        else:
            lines.append("\n  Warnings:\n    None")

        if self.errors:
            lines.append(f"\n  Errors ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"    - {e}")
        else:
            lines.append("\n  Errors:\n    None")

        if self.is_valid:
            lines.append("\nDry-run complete. No issues found.")
        else:
            lines.append(f"\nDry-run failed. {len(self.errors)} error(s) found.")

        return "\n".join(lines)


class DryRunEngine:
    """Validates a PipelineDAG and produces a structured DryRunReport."""

    def run(
        self,
        dag: PipelineDAG,
        inputs: dict[str, Any] | None = None,
        visualize: bool = False,
        check_versions: bool = True,
    ) -> DryRunReport:
        """Validate the DAG and return a DryRunReport.

        Does not execute any pipeline steps or write any files.
        """
        errors: list[str] = []
        report_warnings: list[str] = []
        resolved_steps: list[DryRunStepInfo] = []
        input_values = {
            name: decl.default
            for name, decl in dag.recipe.inputs.items()
            if decl.default is not None
        }
        if inputs:
            input_values.update(inputs)

        for step_id in dag.topological_order:
            node = dag.nodes[step_id]
            impl = node.implementation
            param_specs = dict(node.spec.params) if node.spec.params else {}
            params = resolve_input_refs(node.step.params, input_values)

            if impl is None:
                step_info = DryRunStepInfo(
                    step_id=step_id,
                    op=node.spec.op,
                    implementation_key=None,
                    callable_path=None,
                    package_name=None,
                    installed_version=None,
                    version_status="no_impl",
                    params=params,
                    param_specs=param_specs,
                )
                resolved_steps.append(step_info)
                continue

            dep = impl.dependency
            version_status, installed_version = self._check_version(
                impl, dep, check_versions, report_warnings, errors
            )

            step_info = DryRunStepInfo(
                step_id=step_id,
                op=node.spec.op,
                implementation_key=impl.key,
                callable_path=impl.callable_path,
                package_name=dep.name,
                installed_version=installed_version,
                version_status=version_status,
                params=params,
                param_specs=param_specs,
            )
            resolved_steps.append(step_info)

        dag_diagram: str | None = None
        if visualize:
            dag_diagram = self._build_mermaid(dag)

        is_valid = len(errors) == 0
        report = DryRunReport(
            is_valid=is_valid,
            errors=errors,
            warnings=report_warnings,
            resolved_steps=resolved_steps,
            dag_diagram=dag_diagram,
        )
        recipe = dag.recipe
        report.recipe_label = f"Recipe: {recipe.name} (v{recipe.version})"
        return report

    def _check_version(
        self,
        impl: Implementation,
        dep: Dependency,
        check_versions: bool,
        report_warnings: list[str],
        errors: list[str],
    ) -> tuple[str, str | None]:
        """Return (version_status, installed_version) for an implementation dependency."""
        if not check_versions:
            return "ok", None

        try:
            installed = importlib.metadata.version(dep.name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(
                f"Dependency '{dep.name}' required by implementation '{impl.key}' "
                f"(op '{impl.op}') is not installed."
            )
            return "error", None

        if installed not in SpecifierSet(dep.version):
            errors.append(
                f"Installed '{dep.name}' ({installed}) is outside the declared range "
                f"'{dep.version}' for implementation '{impl.key}' (op '{impl.op}')."
            )
            return "error", installed

        if impl.tested_versions and installed not in impl.tested_versions:
            report_warnings.append(
                f"Installed '{dep.name}' ({installed}) is not in the tested versions "
                f"{impl.tested_versions} for implementation '{impl.key}' (op '{impl.op}'). "
                "Results may differ from tested behavior."
            )
            return "warning", installed

        return "ok", installed

    def _build_mermaid(self, dag: PipelineDAG) -> str:
        """Build a Mermaid graph TD string from the DAG."""
        lines = ["graph TD"]

        for step_id, node in dag.nodes.items():
            label = f"{step_id}\\n({node.spec.op})"
            lines.append(f'    {step_id}["{label}"]')

        for edge in dag.edges:
            src = edge.source_step_id
            tgt = edge.target_step_id
            output = edge.source_output
            if output:
                lines.append(f'    {src} -->|"{output}"| {tgt}')
            else:
                lines.append(f"    {src} --> {tgt}")

        return "\n".join(lines)


__all__ = ["DryRunEngine", "DryRunReport", "DryRunStepInfo"]

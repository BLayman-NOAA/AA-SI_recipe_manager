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

            self._check_sweep_purity(step_id, node, report_warnings)

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

    def _check_sweep_purity(
        self, step_id: str, node: Any, report_warnings: list[str]
    ) -> None:
        """Warn if a swept step looks impure (FR-18.7).

        A swept step declaring the same type name for both an input and an
        output suggests in-place mutation rather than a pure ``data in ->
        results out`` function. This is a heuristic, so it warns (never errors);
        the system cannot know for certain whether the callable mutates.
        """
        if node.step.sweep is None:
            return
        input_types = {
            port.type for port in node.spec.inputs.values() if port.type
        }
        for out_name, out_port in node.spec.outputs.items():
            if out_port.type and out_port.type in input_types:
                report_warnings.append(
                    f"Step '{step_id}': swept step declares type "
                    f"'{out_port.type}' as both an input and output "
                    f"('{out_name}'), suggesting in-place mutation; swept steps "
                    "should be pure (data in -> results out) (FR-18.7)."
                )
                return

    def _build_mermaid(self, dag: PipelineDAG) -> str:
        """Build a Mermaid graph TD string from the DAG.

        Solid arrows are data: an output, or a param reference, feeding an
        input. Each mapped chain is drawn as one box, entered by a single
        dotted ``map_over`` arrow from the list it fans out over, and the
        members that read the item itself get a solid ``(item)`` arrow. A
        collector is entered by one dotted ``collect`` arrow. Membership in a
        chain is therefore shown by the box, not by an arrow per member.
        """
        from aa_recipe_manager.parallel import group_mapped_chains

        chains = [c for c in group_mapped_chains(dag) if c.member_ids]
        chain_of = {m: i for i, c in enumerate(chains) for m in c.member_ids}

        def declare(step_id: str, indent: str = "    ") -> str:
            node = dag.nodes[step_id]
            tags = []
            if node.is_mapped:
                tags.append("map")
            if node.is_swept:
                tags.append("sweep")
            if node.is_collector:
                tags.append("collect")
            suffix = f"\\n[{' + '.join(tags)}]" if tags else ""
            return f'{indent}{step_id}["{step_id}\\n({node.spec.op}){suffix}"]'

        lines = ["graph TD"]
        drawn_chains: set[int] = set()
        for step_id in dag.nodes:
            index = chain_of.get(step_id)
            if index is None:
                lines.append(declare(step_id))
                continue
            if index in drawn_chains:
                continue
            drawn_chains.add(index)
            chain = chains[index]
            if chain.source_ref:
                title = f"map_over: {_ref_text(chain.source_ref)}, one instance per item"
            else:
                title = "sweep, one instance per parameter set"
            lines.append(f'    subgraph chain_{index} ["{title}"]')
            lines.extend(declare(m, "        ") for m in chain.member_ids)
            lines.append("    end")

        # A collector names the same value twice, as ``collect:`` and as an
        # input; one dotted arrow stands for both.
        collected = {
            (e.source_step_id, e.source_output, e.target_step_id)
            for e in dag.edges
            if e.target_input == "__collect__"
        }
        fanned_out: set[int] = set()
        for edge in dag.edges:
            src, tgt, output = edge.source_step_id, edge.target_step_id, edge.source_output
            if edge.target_input == "__map_over__":
                index = chain_of.get(tgt)
                if index is None or index in fanned_out:
                    continue
                fanned_out.add(index)
                lines.append(f'    {src} -. "map_over: {output}" .-> chain_{index}')
            elif edge.target_input == "__collect__":
                lines.append(f'    {src} -. "collect: {output}" .-> {tgt}')
            elif (src, output, tgt) in collected:
                continue
            elif output:
                lines.append(f'    {src} -->|"{output}"| {tgt}')
            else:
                lines.append(f"    {src} --> {tgt}")

        for index, chain in enumerate(chains):
            if not chain.source_ref:
                continue
            src, output = _ref_text(chain.source_ref).split(".", 1)
            for member in chain.member_ids:
                step = dag.nodes[member].step
                if _uses_item(step.inputs) or _uses_item(step.params):
                    lines.append(f'    {src} -->|"{output} (item)"| {member}')
        return "\n".join(lines)


def _ref_text(ref: str) -> str:
    """``${step.output}`` as ``step.output``."""
    return ref.strip()[2:-1] if ref.strip().startswith("${") else ref.strip()


def _uses_item(value: Any) -> bool:
    """True when a wiring value reads the mapped item anywhere inside it."""
    if isinstance(value, str):
        return "${_item}" in value
    if isinstance(value, dict):
        return any(_uses_item(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_uses_item(v) for v in value)
    return False


__all__ = ["DryRunEngine", "DryRunReport", "DryRunStepInfo"]

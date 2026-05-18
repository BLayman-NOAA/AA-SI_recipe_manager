# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Jupyter notebook code generation backend."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import nbformat

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG
    from aa_recipe_manager.resolver.dependencies import ResolvedDependencies


_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")
_STEP_REF = re.compile(r"\$\{(\w+)\.(\w+)\}")


def _md_cell(source: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(source)


def _code_cell(source: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(source)


# ---------------------------------------------------------------------------
# Input value rendering
# ---------------------------------------------------------------------------

def _render_input_value(raw: Any, var_name_map: dict[tuple[str, str], str]) -> str:
    """Render a step input value as a Python expression string.

    Handles:
      - "${inputs.x}"       -> "x"
      - "${step_id.out}"    -> var_name_map[(step_id, out)]
      - list of refs        -> "[v1, v2, v3]"
      - plain literal       -> repr(value)
    """
    if isinstance(raw, list):
        items = [_render_single_ref(v, var_name_map) for v in raw]
        return "[" + ", ".join(items) + "]"
    return _render_single_ref(raw, var_name_map)


def _render_single_ref(raw: Any, var_name_map: dict[tuple[str, str], str]) -> str:
    if not isinstance(raw, str):
        return repr(raw)
    m_input = _INPUT_REF.fullmatch(raw)
    if m_input:
        return m_input.group(1)
    m_step = _STEP_REF.fullmatch(raw)
    if m_step:
        step_id, output_name = m_step.group(1), m_step.group(2)
        return var_name_map.get((step_id, output_name), f"{step_id}_{output_name}")
    return repr(raw)


def _render_param_value(raw: Any, var_name_map: dict[tuple[str, str], str]) -> str:
    """Render a param value, resolving ${inputs.x} references if present."""
    if not isinstance(raw, str):
        return repr(raw)
    # Full-match ${inputs.x}
    m = _INPUT_REF.fullmatch(raw)
    if m:
        return m.group(1)
    # Partial interpolation: "some/${inputs.x}/path" -> f-string
    if "${inputs." in raw:
        result = _INPUT_REF.sub(r"{\1}", raw)
        return f'f"{result}"'
    return repr(raw)


# ---------------------------------------------------------------------------
# Output extraction rendering
# ---------------------------------------------------------------------------

def _render_extraction(rule: str, result_var: str = "_result") -> str:
    """Translate an output_map rule to a Python expression.

    Rules (from §3.5 of software_architecture.md):
      __return__    -> result_var (the return value itself)
      [N]           -> result_var[N]
      ['key']       -> result_var['key']
      .attr         -> result_var.attr
      name          -> result_var['name']
      [0]['key']    -> result_var[0]['key']   (chained)
    """
    if rule == "__return__":
        return result_var
    if rule.startswith("[") or rule.startswith("."):
        return f"{result_var}{rule}"
    # bare identifier
    return f"{result_var}['{rule}']"


# ---------------------------------------------------------------------------
# Cell builders
# ---------------------------------------------------------------------------

def _build_title_cell(dag: PipelineDAG) -> nbformat.NotebookNode:
    recipe = dag.recipe
    lines = [f"# {recipe.name}"]
    if recipe.description:
        lines.append("")
        lines.append(recipe.description)
    meta: list[str] = []
    if recipe.author:
        meta.append(f"**Author:** {recipe.author}")
    if recipe.version:
        meta.append(f"**Version:** {recipe.version}")
    if meta:
        lines.append("")
        lines.extend(meta)
    return _md_cell("\n".join(lines))


def _build_deps_cell(
    resolved_deps: ResolvedDependencies,
) -> nbformat.NotebookNode | None:
    if not resolved_deps.packages:
        return None
    lines = ["# Uncomment the lines below to install required packages."]
    for line in resolved_deps.to_requirements_txt().splitlines():
        lines.append(f"# %pip install {line}")
    return _code_cell("\n".join(lines))


def _build_callable_aliases(dag: PipelineDAG) -> dict[str, str]:
    """Assign stable callable aliases, qualifying only when basenames collide."""
    paths: list[str] = []
    basename_counts: dict[str, int] = {}

    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.implementation is None:
            continue
        path = node.implementation.callable_path
        if path in paths:
            continue
        paths.append(path)
        basename = path.rsplit(".", 1)[-1]
        basename_counts[basename] = basename_counts.get(basename, 0) + 1

    aliases: dict[str, str] = {}
    for path in paths:
        basename = path.rsplit(".", 1)[-1]
        if basename_counts[basename] == 1:
            aliases[path] = basename
            continue
        aliases[path] = path.replace(".", "_")
    return aliases


def _collect_imports(dag: PipelineDAG, callable_aliases: dict[str, str]) -> list[str]:
    """Collect unique import lines for all callable_paths using safe aliases."""
    seen: set[str] = set()
    imports: list[str] = []
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.implementation is None:
            continue
        path = node.implementation.callable_path
        alias = callable_aliases[path]
        if "." in path:
            module, name = path.rsplit(".", 1)
            if alias == name:
                stmt = f"from {module} import {name}"
            else:
                stmt = f"from {module} import {name} as {alias}"
        else:
            stmt = f"import {path}"
        if stmt not in seen:
            seen.add(stmt)
            imports.append(stmt)
    return imports


def _build_imports_cell(
    dag: PipelineDAG,
    callable_aliases: dict[str, str],
    include_provenance: bool = True,
    include_tracker: bool = True,
) -> nbformat.NotebookNode:
    imports = _collect_imports(dag, callable_aliases)
    lines = imports + [""]
    if include_tracker:
        lines.append(
            "from aa_recipe_manager.tracker.pipeline_tracker import PipelineTracker"
        )
    if include_provenance:
        lines.append(
            "from aa_recipe_manager.provenance.recorder import ProvenanceRecorder"
        )
    return _code_cell("\n".join(lines))


def _build_tracker_init_cell(dag: PipelineDAG) -> nbformat.NotebookNode:
    import json as _json

    # The tracker needs the resolved recipe shape. For modular recipes, the
    # source YAML contains include directives rather than the flattened steps
    # emitted into the notebook.
    recipe_json = _json.dumps(
        _json.loads(dag.recipe.model_dump_json()), ensure_ascii=False
    )
    src = (
        "import json as _json\n"
        f"_recipe_dict = _json.loads({repr(recipe_json)})\n"
        "tracker = PipelineTracker(_recipe_dict)"
    )
    return _code_cell(src)


def _build_inputs_cell(
    dag: PipelineDAG,
    cache_aware: bool = False,
) -> nbformat.NotebookNode:
    recipe = dag.recipe
    if not recipe.inputs and not cache_aware:
        return _code_cell("# No pipeline inputs declared.")
    lines: list[str] = []
    for name, decl in recipe.inputs.items():
        comment = f"  # {decl.type}" if decl.type else ""
        if decl.default is not None:
            lines.append(f"{name} = {repr(decl.default)}{comment}")
        else:
            lines.append(f"{name} = None  # TODO: set this value{comment}")
    if cache_aware:
        lines.append(
            '_recipe_manager_cache_dir = "outputs"  # Generated step output cache'
        )
        lines.append("_recipe_manager_step_signatures = {}")
    return _code_cell("\n".join(lines))


def _indent_lines(lines: list[str], prefix: str = "    ") -> list[str]:
    return [f"{prefix}{line}" if line else prefix for line in lines]


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _build_step_markdown_source(
    step_id: str,
    node: DAGNode,
    source: str | None = None,
) -> str:
    title = f"### Step: `{step_id}`"
    if source:
        title = f"{title} (from {source})"
    op_label = f"`{node.spec.op}`"
    if node.spec.op == "custom":
        op_label = f"{op_label} (custom / unregistered)"
    md_lines = [title, f"**Op:** {op_label}"]
    if node.spec.description:
        md_lines.append("")
        md_lines.append(node.spec.description.strip())
    if node.spec.params:
        md_lines.append("")
        md_lines.append("**Parameters:**")
        for pname, pdecl in node.spec.params.items():
            parts = [f"`{pname}`"]
            if pdecl.type:
                parts.append(pdecl.type)
            if pdecl.units:
                parts.append(f"({pdecl.units})")
            if pdecl.description:
                parts.append(f"- {pdecl.description}")
            md_lines.append("- " + " ".join(parts))
    return "\n".join(md_lines)


def _build_scalar_params(node: DAGNode) -> dict[str, Any]:
    return {
        k: v
        for k, v in node.resolved_params.items()
        if isinstance(v, (int, float, str, bool, type(None)))
        or (
            isinstance(v, list)
            and all(isinstance(i, (int, float, str, bool, type(None))) for i in v)
        )
    }


def _param_var_name(step_id: str, param_name: str) -> str:
    """Generate a scoped variable name for a step parameter."""
    return f"_{step_id}__{param_name}"


def _build_param_var_declarations(
    step_id: str,
    node: DAGNode,
) -> tuple[dict[str, str], list[str]]:
    """Return (param_var_names, declaration_lines) for scalar params.

    Generates ``_{step_id}__{param_name} = value`` variables so that a
    parameter value only needs to be edited in one place and both
    ``tracker.step(params={...})`` and the function-call kwargs stay in sync.
    """
    scalar_params = _build_scalar_params(node)
    if not scalar_params:
        return {}, []
    param_var_names = {
        param_name: _param_var_name(step_id, param_name)
        for param_name in scalar_params
    }
    decl_lines = ["# --- Parameters ---"]
    for param_name, var_name in param_var_names.items():
        value_expr = _render_param_value(scalar_params[param_name], {})
        decl_lines.append(f"{var_name} = {value_expr}")
    decl_lines.append("")
    return param_var_names, decl_lines


def _wrap_tracker_step(
    step_id: str,
    node: DAGNode,
    body_lines: list[str] | None = None,
    param_var_names: dict[str, str] | None = None,
) -> list[str]:
    scalar_params = _build_scalar_params(node)
    if param_var_names and scalar_params:
        header = (
            f"with tracker.step({repr(step_id)}, "
            f"op={repr(node.spec.op)}, params={{"
        )
        param_items = [
            f"    {repr(k)}: {param_var_names[k]},"
            for k in scalar_params
            if k in param_var_names
        ]
        lines: list[str] = [header, *param_items, "}):"
        ]
    else:
        lines = [
            (
                f"with tracker.step({repr(step_id)}, "
                f"op={repr(node.spec.op)}, "
                f"params={repr(scalar_params)}):"
            )
        ]
    return [*lines, *_indent_lines(body_lines or ["pass"])]


def _build_step_body_lines(
    step_id: str,
    node: DAGNode,
    var_name_map: dict[tuple[str, str], str],
    callable_aliases: dict[str, str],
    param_var_names: dict[str, str] | None = None,
) -> list[str]:
    if node.implementation is None:
        return [f"# TODO: no implementation found for op '{node.spec.op}'"]

    impl = node.implementation
    callable_name = callable_aliases[impl.callable_path]
    step = node.step

    param_map = impl.param_map or {}
    kwargs: list[tuple[str, str]] = []

    for port_name, raw_value in step.inputs.items():
        callable_arg = param_map.get(port_name, port_name)
        value_expr = _render_input_value(raw_value, var_name_map)
        kwargs.append((callable_arg, value_expr))

    for param_name, raw_value in node.resolved_params.items():
        callable_arg = param_map.get(param_name, param_name)
        if param_var_names and param_name in param_var_names:
            value_expr = param_var_names[param_name]
        else:
            value_expr = _render_param_value(raw_value, var_name_map)
        kwargs.append((callable_arg, value_expr))

    resolved_keys = set(node.resolved_params.keys())
    for param_name, raw_value in step.params.items():
        if param_name not in resolved_keys:
            callable_arg = param_map.get(param_name, param_name)
            if param_var_names and param_name in param_var_names:
                value_expr = param_var_names[param_name]
            else:
                value_expr = _render_param_value(raw_value, var_name_map)
            kwargs.append((callable_arg, value_expr))

    if len(kwargs) <= 2:
        args_str = ", ".join(f"{k}={v}" for k, v in kwargs)
        call_expr = f"{callable_name}({args_str})"
    else:
        inner = ",\n".join(f"    {k}={v}" for k, v in kwargs)
        call_expr = f"{callable_name}(\n{inner},\n)"

    output_map = impl.output_map or {}
    outputs = list(node.spec.outputs.keys())

    code_lines: list[str] = []

    if node.spec.sink or not outputs:
        code_lines.append(call_expr)
    elif len(outputs) == 1:
        out_name = outputs[0]
        var_name = var_name_map.get((step_id, out_name), out_name)
        rule = output_map.get(out_name, "__return__")
        if rule == "__return__":
            code_lines.append(f"{var_name} = {call_expr}")
        else:
            code_lines.append(f"_result = {call_expr}")
            expr = _render_extraction(rule)
            code_lines.append(f"{var_name} = {expr}")
    else:
        code_lines.append(f"_result = {call_expr}")
        for out_name in outputs:
            var_name = var_name_map.get((step_id, out_name), out_name)
            rule = output_map.get(out_name, out_name)
            expr = _render_extraction(rule)
            code_lines.append(f"{var_name} = {expr}")

    return code_lines


def _build_step_execution_lines(
    step_id: str,
    node: DAGNode,
    var_name_map: dict[tuple[str, str], str],
    callable_aliases: dict[str, str],
    include_tracker: bool = True,
) -> list[str]:
    if node.implementation is None:
        return [f"# TODO: no implementation found for op '{node.spec.op}'"]

    param_var_names, decl_lines = _build_param_var_declarations(step_id, node)
    body_lines = _build_step_body_lines(
        step_id, node, var_name_map, callable_aliases, param_var_names=param_var_names
    )
    if not include_tracker:
        return decl_lines + body_lines
    return decl_lines + _wrap_tracker_step(
        step_id, node, body_lines, param_var_names=param_var_names
    )


def _collect_dependency_step_ids(node: DAGNode) -> list[str]:
    dependency_ids = set(node.step.depends_on or [])
    for raw_value in node.step.inputs.values():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            if isinstance(value, str):
                match = _STEP_REF.fullmatch(value)
                if match:
                    dependency_ids.add(match.group(1))
    return sorted(dependency_ids)


def _collect_referenced_input_names(node: DAGNode) -> list[str]:
    input_names: set[str] = set()

    def _collect(value: Any) -> None:
        if isinstance(value, str):
            input_names.update(_INPUT_REF.findall(value))
        elif isinstance(value, list):
            for item in value:
                _collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                _collect(item)

    for raw_value in node.step.inputs.values():
        _collect(raw_value)
    for raw_value in node.step.params.values():
        _collect(raw_value)

    return sorted(input_names)


def _render_dependency_signature_item(step_id: str) -> str:
    return (
        f"{repr(step_id)}: "
        f"_recipe_manager_step_signatures.get({repr(step_id)})"
    )


def _build_cache_aware_step_lines(
    step_id: str,
    node: DAGNode,
    var_name_map: dict[tuple[str, str], str],
    callable_aliases: dict[str, str],
    include_tracker: bool = True,
) -> list[str]:
    param_var_names, decl_lines = _build_param_var_declarations(step_id, node)
    body_lines = _build_step_body_lines(
        step_id,
        node,
        var_name_map,
        callable_aliases,
        param_var_names=param_var_names,
    )
    outputs = list(node.spec.outputs.keys())
    if node.implementation is None or node.spec.sink or not outputs:
        if node.implementation is None:
            return [f"# TODO: no implementation found for op '{node.spec.op}'"]
        return _build_step_execution_lines(
            step_id,
            node,
            var_name_map,
            callable_aliases,
            include_tracker=include_tracker,
        )

    cache_targets = [
        (out_name, var_name_map.get((step_id, out_name), out_name))
        for out_name in outputs
    ]
    dependency_ids = _collect_dependency_step_ids(node)
    input_names = _collect_referenced_input_names(node)
    input_signature_items = ", ".join(
        f"{repr(name)}: {name}" for name in input_names
    )
    dependency_signature_items = ", ".join(
        _render_dependency_signature_item(name) for name in dependency_ids
    )
    static_signature = _json_safe(
        {
            "step_id": step_id,
            "op": node.spec.op,
            "implementation_key": node.implementation.key,
            "callable_path": node.implementation.callable_path,
            "step": node.step.model_dump(mode="json"),
            "resolved_params": node.resolved_params,
            "outputs": outputs,
            "output_map": node.implementation.output_map or {},
        }
    )

    code_lines = decl_lines + [
        "from pathlib import Path as _Path",
        "import pickle as _pickle",
        "import hashlib as _hashlib",
        "import json as _json",
        "_cache_dir = _Path(_recipe_manager_cache_dir)",
        "_cache_dir.mkdir(parents=True, exist_ok=True)",
        f"_cache_meta_path = _cache_dir / {repr(f'{step_id}__cache_meta.json')}",
        "_cache_meta = {}",
        "if _cache_meta_path.exists():",
        "    try:",
        "        with _cache_meta_path.open('r', encoding='utf-8') as _f:",
        "            _cache_meta = _json.load(_f)",
        "    except Exception:",
        "        _cache_meta = {}",
        f"_signature_payload = {repr(static_signature)}",
        f"_signature_payload['inputs'] = {{{input_signature_items}}}",
        (
            f"_signature_payload['dependencies'] = "
            f"{{{dependency_signature_items}}}"
        ),
        (
            "_signature_text = _json.dumps("
            "_signature_payload, sort_keys=True, default=str)"
        ),
        (
            "_step_signature = _hashlib.sha256("
            "_signature_text.encode('utf-8')).hexdigest()"
        ),
    ]

    if len(cache_targets) == 1:
        out_name, var_name = cache_targets[0]
        cache_file = f"{step_id}_{out_name}.pkl"
        code_lines.append(f"_cache_path = _cache_dir / {repr(cache_file)}")
        code_lines.append(
            "if _cache_path.exists() "
            "and _cache_meta.get('signature') == _step_signature:"
        )
        code_lines.append("    with _cache_path.open('rb') as _f:")
        code_lines.append(f"        {var_name} = _pickle.load(_f)")
        if include_tracker:
            code_lines.extend(
                _indent_lines(
                    _wrap_tracker_step(
                        step_id,
                        node,
                        param_var_names=param_var_names,
                    ),
                    prefix="    ",
                )
            )
        code_lines.append(
            f"    _recipe_manager_step_signatures[{repr(step_id)}] = _step_signature"
        )
        code_lines.append(
            f"    print({repr(f'Loaded {step_id}.{out_name} from cache')})"
        )
        code_lines.append("else:")
        if include_tracker:
            code_lines.extend(
                _indent_lines(
                    _wrap_tracker_step(
                        step_id,
                        node,
                        body_lines,
                        param_var_names=param_var_names,
                    )
                )
            )
        else:
            code_lines.extend(_indent_lines(body_lines))
        code_lines.append("    with _cache_path.open('wb') as _f:")
        code_lines.append(f"        _pickle.dump({var_name}, _f)")
        code_lines.append(
            "    with _cache_meta_path.open('w', encoding='utf-8') as _f:"
        )
        code_lines.append(
            "        _json.dump({'signature': _step_signature}, _f, indent=2)"
        )
        code_lines.append(
            f"    _recipe_manager_step_signatures[{repr(step_id)}] = _step_signature"
        )
        return code_lines

    code_lines.append("_cache_paths = {")
    for out_name, _ in cache_targets:
        cache_file = f"{step_id}_{out_name}.pkl"
        code_lines.append(f"    {repr(out_name)}: _cache_dir / {repr(cache_file)},")
    code_lines.append("}")
    code_lines.append(
        "if all(_path.exists() for _path in _cache_paths.values()) "
        "and _cache_meta.get('signature') == _step_signature:"
    )
    for out_name, var_name in cache_targets:
        code_lines.append(
            f"    with _cache_paths[{repr(out_name)}].open('rb') as _f:"
        )
        code_lines.append(f"        {var_name} = _pickle.load(_f)")
    if include_tracker:
        code_lines.extend(
            _indent_lines(
                _wrap_tracker_step(
                    step_id,
                    node,
                    param_var_names=param_var_names,
                ),
                prefix="    ",
            )
        )
    code_lines.append(
        f"    _recipe_manager_step_signatures[{repr(step_id)}] = _step_signature"
    )
    code_lines.append(f"    print({repr(f'Loaded cached outputs for {step_id}')})")
    code_lines.append("else:")
    if include_tracker:
        code_lines.extend(
            _indent_lines(
                _wrap_tracker_step(
                    step_id,
                    node,
                    body_lines,
                    param_var_names=param_var_names,
                )
            )
        )
    else:
        code_lines.extend(_indent_lines(body_lines))
    for out_name, var_name in cache_targets:
        code_lines.append(
            f"    with _cache_paths[{repr(out_name)}].open('wb') as _f:"
        )
        code_lines.append(f"        _pickle.dump({var_name}, _f)")
    code_lines.append("    with _cache_meta_path.open('w', encoding='utf-8') as _f:")
    code_lines.append(
        "        _json.dump({'signature': _step_signature}, _f, indent=2)"
    )
    code_lines.append(
        f"    _recipe_manager_step_signatures[{repr(step_id)}] = _step_signature"
    )
    return code_lines


def _build_step_cells(
    step_id: str,
    node: DAGNode,
    var_name_map: dict[tuple[str, str], str],
    callable_aliases: dict[str, str],
    cache_aware: bool = False,
    include_tracker: bool = True,
    source: str | None = None,
) -> list[nbformat.NotebookNode]:
    cells: list[nbformat.NotebookNode] = []

    cells.append(_md_cell(_build_step_markdown_source(step_id, node, source=source)))

    if cache_aware:
        code_lines = _build_cache_aware_step_lines(
            step_id,
            node,
            var_name_map,
            callable_aliases,
            include_tracker=include_tracker,
        )
    else:
        code_lines = _build_step_execution_lines(
            step_id,
            node,
            var_name_map,
            callable_aliases,
            include_tracker=include_tracker,
        )

    cells.append(_code_cell("\n".join(code_lines)))
    return cells


def _build_save_recipe_cell(
    output_name: str = "pipeline_modified.yaml",
) -> nbformat.NotebookNode:
    return _code_cell(f"tracker.save_recipe({repr(output_name)})")


def _build_provenance_cell(dag: PipelineDAG) -> nbformat.NotebookNode:
    dep_names = []
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.implementation and node.implementation.dependency:
            name = node.implementation.dependency.name
            if name not in dep_names:
                dep_names.append(name)

    lines = [
        "# Capture the runtime environment at the time this notebook was run.",
        f"_provenance = ProvenanceRecorder.capture_environment({repr(dep_names)})",
        'print("Python:", _provenance["python_version"])',
        'print("Timestamp:", _provenance["timestamp"])',
        'if "installed_packages" in _provenance:',
        '    for pkg, ver in _provenance["installed_packages"].items():',
        '        print(f"  {pkg}: {ver}")',
    ]
    return _code_cell("\n".join(lines))


def _build_include_header_cell(
    source: str,
    step_ids: list[str],
) -> nbformat.NotebookNode:
    summary = ", ".join(step_ids) if step_ids else "no steps"
    return _md_cell(f"## Included: {source}\n\nSteps: {summary}")


def _build_include_footer_cell(source: str) -> nbformat.NotebookNode:
    return _md_cell(f"*End of included section: {source}*")


def _build_include_render_maps(
    dag: PipelineDAG,
) -> tuple[
    dict[str, list[tuple[str, list[str]]]],
    dict[str, list[str]],
    dict[str, str],
]:
    starts: dict[str, list[tuple[str, list[str]]]] = {}
    ends: dict[str, list[str]] = {}
    sources_by_step: dict[str, str] = {}
    topo_positions = {
        step_id: index for index, step_id in enumerate(dag.topological_order)
    }

    for block in dag.recipe.include_blocks:
        ordered_ids = [
            step_id for step_id in block.step_ids if step_id in topo_positions
        ]
        if not ordered_ids:
            continue
        ordered_ids.sort(key=lambda step_id: topo_positions[step_id])
        starts.setdefault(ordered_ids[0], []).append((block.source, ordered_ids))
        ends.setdefault(ordered_ids[-1], []).append(block.source)
        for step_id in ordered_ids:
            sources_by_step[step_id] = block.source

    return starts, ends, sources_by_step


# ---------------------------------------------------------------------------
# NotebookBackend
# ---------------------------------------------------------------------------

class NotebookBackend:
    """Generates a Jupyter notebook (.ipynb) from a validated PipelineDAG."""

    def generate(
        self,
        dag: PipelineDAG,
        resolved_deps: ResolvedDependencies,
        output_path: Path,
        options: dict[str, Any] | None = None,
    ) -> Path:
        """Write a .ipynb file to output_path and return the path."""
        nb = nbformat.v4.new_notebook()
        nb.cells = _build_notebook_cells(dag, resolved_deps, options)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            nbformat.write(nb, fh)
        return output_path


def _build_notebook_cells(
    dag: PipelineDAG,
    resolved_deps: ResolvedDependencies,
    options: dict[str, Any] | None = None,
) -> list[nbformat.NotebookNode]:
    opts = options or {}
    save_recipe_output: str = opts.get("save_recipe_output", "pipeline_modified.yaml")
    include_provenance: bool = opts.get("include_provenance", True)
    include_tracker: bool = opts.get("include_tracker", True)
    cache_aware: bool = opts.get("cache_aware", False)

    var_name_map = _build_variable_name_map_from_dag(dag)
    callable_aliases = _build_callable_aliases(dag)
    include_starts, include_ends, sources_by_step = _build_include_render_maps(dag)
    cells: list[nbformat.NotebookNode] = []

    cells.append(_build_title_cell(dag))

    deps_cell = _build_deps_cell(resolved_deps)
    if deps_cell is not None:
        cells.append(deps_cell)

    cells.append(
        _build_imports_cell(
            dag,
            callable_aliases,
            include_provenance=include_provenance,
            include_tracker=include_tracker,
        )
    )
    if include_tracker:
        cells.append(_build_tracker_init_cell(dag))
    cells.append(_build_inputs_cell(dag, cache_aware=cache_aware))

    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        for source, step_ids in include_starts.get(step_id, []):
            cells.append(_build_include_header_cell(source, step_ids))
        cells.extend(
            _build_step_cells(
                step_id,
                node,
                var_name_map,
                callable_aliases,
                cache_aware=cache_aware,
                include_tracker=include_tracker,
                source=sources_by_step.get(step_id),
            )
        )
        for source in reversed(include_ends.get(step_id, [])):
            cells.append(_build_include_footer_cell(source))

    if include_tracker:
        cells.append(_build_save_recipe_cell(save_recipe_output))
    if include_provenance:
        cells.append(_build_provenance_cell(dag))
    return cells


def _build_variable_name_map_from_dag(
    dag: PipelineDAG,
) -> dict[tuple[str, str], str]:
    """Thin shim — delegates to core module to avoid circular import."""
    from aa_recipe_manager.generator.core import build_variable_name_map

    return build_variable_name_map(dag)

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Render a flow plan as the text of an echodataflow flow module."""

from __future__ import annotations

import inspect
import keyword
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.generator.backends.notebook import (
    _INPUT_REF,
    _STEP_REF,
    _build_callable_aliases,
    _collect_imports,
    _indent_lines,
    _render_extraction,
)
from aa_recipe_manager.generator.echodataflow.flow_runtime import HELPERS
from aa_recipe_manager.generator.echodataflow.plan import (
    FlowPlan,
    TaskUnit,
    UnsupportedRecipeError,
    context_bound_params,
    iter_step_refs,
)

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG

_TYPE_HINTS = {
    "str": "str",
    "path": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list": "list",
    "dict": "dict",
}

#: Expressions for the params in plan.CONTEXT_BOUND_PARAMS and plan.FIGURE_PARAMS.
#: ``{step_id}`` is replaced with the step's id.
_CONTEXT_EXPRESSIONS = {
    ("initial_setup", "calibration_outputs"): 'str(Path(output_dir) / "calibration")',
    "save_image": "True",
    "show": "False",
    "save_dir": 'str(Path(output_dir) / "images" / "{step_id}")',
}


def _context_expression(op: str, param: str, step_id: str) -> str:
    template = _CONTEXT_EXPRESSIONS.get((op, param), _CONTEXT_EXPRESSIONS.get(param))
    return template.replace("{step_id}", step_id)

#: Points aa_si_utils at the flow's scratch folder, in whichever process runs.
_SCRATCH_LINE = (
    'os.environ["AA_SI_UTILS_TEMP_DIR"] = str(Path(output_dir) / "_scratch")'
)

_RUNNER_IMPORT = (
    "from echodataflow.deployment.task_runners import task_runner_from_environment"
)

#: Names the generated module uses for itself, which no recipe input or step
#: output may take.
_RESERVED_NAMES = frozenset({
    "output_dir", "logger", "map_item", "map_stem", "os", "Path", "flow",
    "task", "get_run_logger", "task_runner_from_environment", "datetime",
    "_item", "_items", "_index", "_futures", "_future", "_results", "_r", "_out",
    "_result", "_handoff", "_stem",
    *(helper.__name__ for helper in HELPERS),
})


def _docstring(text: str) -> str:
    """A triple quoted docstring literal holding ``text``."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"""{escaped}"""'


def _var(var_names: dict[tuple[str, str], str], step_id: str, out: str) -> str:
    return var_names.get((step_id, out), f"{step_id}_{out}")


def _render_string(
    raw: str, var_names: dict[tuple[str, str], str], optional_inputs: set[str]
) -> str:
    """Render a string value as the executor resolves it.

    A whole ``${_item}``, ``${step.output}`` or ``${inputs.x}`` becomes that
    value. Inside longer text only ``${inputs.x}`` is filled in, as an
    f-string; any other reference there stays literal text.

    Raises:
        UnsupportedRecipeError: If longer text holds an input that may be unset,
            which the executor would leave as literal ``${inputs.x}`` text.
    """
    if raw.strip() == "${_item}":
        return "_item"
    match = _INPUT_REF.fullmatch(raw)
    if match:
        return match.group(1)
    match = _STEP_REF.fullmatch(raw)
    if match:
        return _var(var_names, match.group(1), match.group(2))
    if "${inputs." not in raw:
        return repr(raw)

    unsettable = [n for n in _INPUT_REF.findall(raw) if n in optional_inputs]
    if unsettable:
        raise UnsupportedRecipeError(
            f"input {unsettable[0]!r} may be unset inside {raw!r}; the executor "
            "would keep the reference as text, so the flow would differ"
        )
    parts: list[str] = []
    last = 0
    for match in _INPUT_REF.finditer(raw):
        parts.append(raw[last:match.start()].replace("{", "{{").replace("}", "}}"))
        parts.append("{" + match.group(1) + "}")
        last = match.end()
    parts.append(raw[last:].replace("{", "{{").replace("}", "}}"))
    return "f" + repr("".join(parts))


def _render_value(
    raw: Any,
    var_names: dict[tuple[str, str], str],
    optional_inputs: set[str],
    in_dict: bool = False,
) -> str:
    """Render a raw input or param value.

    References are resolved inside lists, as the executor resolves them. A dict
    is passed to the callable as it is, so the executor only fills in recipe
    inputs that have a value there; a step or item reference, or an input that
    may be unset, inside a dict is refused rather than rendered differently.
    """
    if isinstance(raw, list):
        items = (_render_value(v, var_names, optional_inputs, in_dict) for v in raw)
        return "[" + ", ".join(items) + "]"
    if isinstance(raw, dict):
        items = ", ".join(
            f"{key!r}: {_render_value(value, var_names, optional_inputs, True)}"
            for key, value in raw.items()
        )
        return "{" + items + "}"
    if not isinstance(raw, str):
        return repr(raw)
    if in_dict:
        unsettable = [n for n in _INPUT_REF.findall(raw) if n in optional_inputs]
        if iter_step_refs(raw) or raw.strip() == "${_item}" or unsettable:
            raise UnsupportedRecipeError(
                f"reference {raw!r} inside a dict param: the executor passes it "
                "through unresolved, so the flow would differ"
            )
    return _render_string(raw, var_names, optional_inputs)


def _droppable(raw: Any, declaration: Any, optional_inputs: set[str]) -> bool:
    """Whether a kwarg is left out when its recipe input has no value.

    Mirrors the executor: an optional port or param fed by a whole
    ``${inputs.x}`` reference with no value is not passed, so the callable's
    own default applies.
    """
    if not isinstance(raw, str):
        return False
    match = _INPUT_REF.fullmatch(raw.strip())
    if match is None or match.group(1) not in optional_inputs:
        return False
    return declaration is not None and not declaration.required


def _call_lines(
    node: DAGNode,
    var_names: dict[tuple[str, str], str],
    aliases: dict[str, str],
    optional_inputs: set[str],
) -> list[str]:
    """Lines that call one step's implementation and bind its outputs."""
    impl = node.implementation
    step = node.step
    param_map = impl.param_map or {}
    fixed: list[tuple[str, str]] = []
    optional: list[tuple[str, str]] = []

    for values, declarations in (
        (step.inputs, node.spec.inputs),
        (step.params, node.spec.params),
    ):
        for name, raw in values.items():
            expr = _render_value(raw, var_names, optional_inputs)
            dropped = _droppable(raw, declarations.get(name), optional_inputs)
            (optional if dropped else fixed).append((param_map.get(name, name), expr))
    for name in context_bound_params(node):
        expr = _context_expression(step.op, name, step.id)
        fixed.append((param_map.get(name, name), expr))

    if step.collect is not None:
        match = _STEP_REF.fullmatch(step.collect)
        if match and match.group(1) != "inputs":
            source, out = match.group(1), match.group(2)
            if out in node.spec.inputs and out not in step.inputs:
                arg = param_map.get(out, out)
                if arg not in {name for name, _ in fixed + optional}:
                    fixed.append((arg, _var(var_names, source, out)))

    callable_name = aliases[impl.callable_path]
    if not fixed and not optional:
        call = f"{callable_name}()"
    elif len(fixed) <= 2 and not optional:
        call = f"{callable_name}({', '.join(f'{n}={e}' for n, e in fixed)})"
    else:
        arg_lines = [f"    {name}={expr}," for name, expr in fixed]
        if optional:
            inner = ", ".join(f"{name}={expr}" for name, expr in optional)
            arg_lines.append(f"    **_given({inner}),")
        call = "\n".join([f"{callable_name}(", *arg_lines, ")"])

    outputs = list(node.spec.outputs)
    output_map = impl.output_map or {}
    if node.spec.sink or not outputs:
        return [call]
    if len(outputs) == 1 and output_map.get(outputs[0], "__return__") == "__return__":
        return [f"{_var(var_names, step.id, outputs[0])} = {call}"]
    lines = [f"_result = {call}"]
    for out in outputs:
        default_rule = "__return__" if len(outputs) == 1 else out
        rule = output_map.get(out, default_rule)
        lines += _extraction_lines(
            _var(var_names, step.id, out), rule, node.spec.outputs[out]
        )
    return lines


def _extraction_lines(var: str, rule: str, port: Any) -> list[str]:
    """Bind one output.

    An optional port falls back to its default when extraction fails, as the
    executor's ``extract_outputs`` does.
    """
    expr = _render_extraction(rule)
    if port.required or rule == "__return__":
        return [f"{var} = {expr}"]
    return [
        "try:",
        f"    {var} = {expr}",
        "except (KeyError, IndexError, AttributeError, TypeError):",
        f"    {var} = {port.default!r}",
    ]


def _handoff_path(unit: TaskUnit, step_id: str, out: str) -> str:
    """Expression for the store path (without suffix) of one handed off output."""
    outputs_of_step = [key for key in unit.handoffs.values() if key[0] == step_id]
    parts = [f'"{step_id}"']
    if len(outputs_of_step) > 1:
        parts.append(f'"{out}"')
    if unit.is_chain:
        parts.append("_stem")
    return "str(Path(output_dir) / " + " / ".join(parts) + ")"


def _member_lines(
    dag: PipelineDAG,
    unit: TaskUnit,
    step_id: str,
    plan: FlowPlan,
    aliases: dict[str, str],
    optional_inputs: set[str],
) -> list[str]:
    node = dag.nodes[step_id]
    var_names = plan.var_names
    lines = [f"# {step_id} ({node.step.op})"]
    call = _call_lines(node, var_names, aliases, optional_inputs)

    guard: list[str] = []
    if unit.is_chain:
        earlier = set(unit.member_ids[: unit.member_ids.index(step_id)])
        for ref in iter_step_refs(list(node.step.inputs.values())):
            var = _var(var_names, *ref)
            if ref[0] in earlier and var not in guard:
                guard.append(var)
    if guard:
        lines.append("if " + " or ".join(f"{var} is None" for var in guard) + ":")
        out_vars = [_var(var_names, step_id, out) for out in node.spec.outputs]
        if node.spec.sink or not out_vars:
            lines.append("    pass")
        else:
            lines.extend(f"    {var} = None" for var in out_vars)
        lines.append("else:")
        lines.extend(_indent_lines(call))
    else:
        lines.extend(call)

    for var, (owner, out) in unit.handoffs.items():
        if owner != step_id:
            continue
        path = _handoff_path(unit, step_id, out)
        lines.append(f'_handoff["{var}"] = _write_handoff({var}, {path})')
        if var in unit.read_later:
            lines.append(f'{var} = _open_handoff(_handoff["{var}"])')
    return lines


def _task_text(
    dag: PipelineDAG,
    unit: TaskUnit,
    plan: FlowPlan,
    aliases: dict[str, str],
    optional_inputs: set[str],
) -> str:
    params: list[str] = []
    if unit.is_chain:
        params += ["map_item", "map_stem"]
    params += unit.args + unit.flow_params
    if unit.needs_output_dir:
        params.append("output_dir")

    members = ", ".join(unit.member_ids)
    if unit.is_chain:
        doc = f"Run {members} for one item of {unit.source_ref}."
    else:
        doc = f"Run {members}."

    body: list[str] = [_docstring(doc)]
    body += _collect_imports(dag, aliases, step_ids=set(unit.member_ids))
    if unit.is_chain:
        body.append(_SCRATCH_LINE)
        if _source_expr(unit.source_ref, plan.var_names) in plan.handoff_vars:
            body.append("_item = _open_handoff(map_item)")
        else:
            body.append("_item = map_item")
        if unit.handoffs:
            body.append("_stem = map_stem")
    for arg in unit.args:
        if arg in plan.handoff_vars:
            body.append(f"{arg} = _open_handoff({arg})")
    if unit.handoffs:
        body.append("_handoff = {}")
    for step_id in unit.member_ids:
        body.append("")
        body += _member_lines(dag, unit, step_id, plan, aliases, optional_inputs)
    if unit.exports:
        body.append("")
        body.append("return {")
        for var in unit.exports:
            value = f'_handoff["{var}"]' if var in unit.handoffs else var
            body.append(f'    "{var}": {value},')
        body.append("}")

    header = [
        f'@task(name="{unit.name}", log_prints=True)',
        f"def task_{unit.name}({', '.join(params)}):",
    ]
    return "\n".join(header + _indent_lines(body))


def _source_expr(source_ref: str, var_names: dict[tuple[str, str], str]) -> str:
    match = _STEP_REF.fullmatch(source_ref)
    if match and match.group(1) != "inputs":
        return _var(var_names, match.group(1), match.group(2))
    raise UnsupportedRecipeError(f"cannot render map_over source {source_ref!r}")


def _unit_call_lines(unit: TaskUnit, plan: FlowPlan) -> list[str]:
    kwargs = [f"{name}={name}" for name in unit.args + unit.flow_params]
    if unit.needs_output_dir:
        kwargs.append("output_dir=output_dir")
    lines = [f"# {', '.join(unit.member_ids)}"]

    if not unit.is_chain:
        call = f"task_{unit.name}({', '.join(kwargs)})"
        if unit.exports:
            lines.append(f"_out = {call}")
            lines += [f'{var} = _out["{var}"]' for var in unit.exports]
        else:
            lines.append(call)
        return lines

    submit_args = ["map_item=_item", "map_stem=_stem", *kwargs]
    lines += [
        f"_items = _as_items({_source_expr(unit.source_ref, plan.var_names)})",
        "_futures = [",
        f"    task_{unit.name}.with_options(",
        f'        task_run_name=f"{unit.name}[{{_stem}}]"',
        "    ).submit(",
        *[f"        {arg}," for arg in submit_args],
        "    )",
        "    for _item, _stem in zip(_items, _item_stems(_items))",
        "]",
    ]
    if unit.exports:
        lines.append("_results = [_future.result() for _future in _futures]")
        lines += [f'{var} = [_r["{var}"] for _r in _results]' for var in unit.exports]
    else:
        lines.append("for _future in _futures:")
        lines.append("    _future.result()")
    return lines


def _check_names(dag: PipelineDAG, plan: FlowPlan) -> None:
    """Refuse recipe input and step output names the generated code needs."""
    inputs = set(dag.recipe.inputs)
    variables = set(plan.var_names.values())
    for name in sorted(inputs):
        if not name.isidentifier() or keyword.iskeyword(name):
            raise UnsupportedRecipeError(f"recipe input {name!r} is not a valid name")
    reserved = sorted((inputs | variables) & _RESERVED_NAMES)
    if reserved:
        raise UnsupportedRecipeError(
            f"names {reserved} are used by the generated flow itself; rename "
            "those recipe inputs or step outputs"
        )
    clashes = sorted(variables & inputs)
    if clashes:
        raise UnsupportedRecipeError(
            f"recipe inputs {clashes} share names with step outputs"
        )


def _signature_lines(
    dag: PipelineDAG, required: list[str]
) -> tuple[list[str], set[str]]:
    """Flow parameters, required first, and the optional inputs with no default."""
    lines: list[str] = []
    optional_inputs: set[str] = set()
    inputs = dag.recipe.inputs
    ordered = [name for name in inputs if name in required] + [
        name for name in inputs if name not in required
    ]
    for name in ordered:
        decl = inputs[name]
        hint = _TYPE_HINTS.get(decl.type)
        if name in required:
            lines.append(f"{name}: {hint}," if hint else f"{name},")
            continue
        if decl.default is None:
            optional_inputs.add(name)
            annotated = f"{name}: {hint} | None" if hint else name
            lines.append(f"{annotated} = None,")
        else:
            annotated = f"{name}: {hint}" if hint else name
            lines.append(f"{annotated} = {decl.default!r},")
    lines.append('output_dir: str = "outputs",')
    return lines, optional_inputs


def _header_notes(dag: PipelineDAG) -> list[str]:
    """NOTE lines on what the flow leaves out or treats differently."""
    notes = []
    for step_id in dag.topological_order:
        step = dag.nodes[step_id].step
        dropped = []
        if step.checkpoint is not None:
            kept = " (written to output_dir)" if step.checkpoint == "always" else ""
            dropped.append(f"checkpoint: {step.checkpoint}{kept}")
        if step.regenerate is not None:
            dropped.append(f"regenerate: {step.regenerate}")
        if step.execution is not None:
            dropped.append("execution hints")
        if dropped:
            notes.append(f"#   {step_id}: {', '.join(dropped)}")
    if notes:
        notes.insert(0, "# NOTE: recipe manager directives with no flow equivalent:")

    defaulted = []
    for step_id in dag.topological_order:
        for port, raw in dag.nodes[step_id].step.inputs.items():
            match = _INPUT_REF.fullmatch(raw) if isinstance(raw, str) else None
            decl = dag.recipe.inputs.get(match.group(1)) if match else None
            if decl is not None and decl.default is not None:
                defaulted.append(f"#   {step_id}.{port} from {match.group(1)}")
    if defaulted:
        notes += [
            "# NOTE: these step inputs take a recipe input's default here; the",
            "# recipe manager executor applies defaults to params only:",
            *defaulted,
        ]
    return notes


def render_flow_module(
    dag: PipelineDAG,
    plan: FlowPlan,
    slug: str,
    required_inputs: list[str],
) -> str:
    """Return the source of the generated flow module.

    Args:
        dag: The recipe's DAG.
        plan: Its flow plan.
        slug: Flow key, used for the flow and module names.
        required_inputs: Recipe inputs a run must be given.

    Returns:
        Python source text.

    Raises:
        UnsupportedRecipeError: If a name or value cannot be rendered faithfully.
    """
    recipe = dag.recipe
    _check_names(dag, plan)
    aliases = _build_callable_aliases(dag)
    signature, optional_inputs = _signature_lines(dag, required_inputs)

    header = [
        _docstring(
            f"echodataflow flow generated from recipe {recipe.name!r} "
            f"(version {recipe.version}).\n\n"
            "Generated by aa-recipe generate --format echodataflow. It has no "
            "runtime\ndependency on the recipe manager and is yours to edit. It "
            "runs the recipe\nonce, end to end: each step is a Prefect task, each "
            "mapped chain a task\nsubmitted once per item, and outputs the recipe "
            "checkpoints are written\nas zarr stores under output_dir.\n"
        ),
    ]
    notes = _header_notes(dag)
    if notes:
        header += ["", *notes]

    imports = [
        "",
        "from __future__ import annotations",
        "",
        "import datetime",
        "import os",
        "from pathlib import Path",
        "",
        "from prefect import flow, get_run_logger, task",
        "",
        _RUNNER_IMPORT,
    ]
    helpers = [inspect.getsource(helper).rstrip("\n") for helper in HELPERS]
    tasks = [
        _task_text(dag, unit, plan, aliases, optional_inputs) for unit in plan.units
    ]

    description = (recipe.description or recipe.name).strip()
    flow_body: list[str] = [
        _docstring(description),
        "logger = get_run_logger()",
        'if "://" in str(output_dir):',
        '    raise ValueError(f"output_dir must be a local folder, got {output_dir}")',
        "output_dir = str(Path(output_dir).resolve())",
        "Path(output_dir).mkdir(parents=True, exist_ok=True)",
        'os.environ.setdefault("MPLBACKEND", "Agg")',
        "# Scratch folder for intermediate raw stores written by aa_si_utils.",
        _SCRATCH_LINE,
        f'logger.info("Running {slug}; outputs in %s", output_dir)',
    ]
    for unit in plan.units:
        flow_body.append("")
        flow_body += _unit_call_lines(unit, plan)
    flow_body += ["", f'logger.info("Finished {slug}")']

    flow_text = "\n".join([
        "@flow(",
        f'    name="{slug}",',
        "    log_prints=True,",
        "    task_runner=task_runner_from_environment(),",
        ")",
        f"def flow_{slug}(",
        *[f"    {line}" for line in signature],
        "):",
        *_indent_lines(flow_body),
    ])

    sections = ["\n".join(header + imports), *helpers, *tasks, flow_text]
    return "\n\n\n".join(sections) + "\n"

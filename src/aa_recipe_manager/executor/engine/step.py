# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Single-step invocation, independent of any executor backend.

:func:`execute_step` is the one place a recipe step's callable is actually
called. It is a free function (never a method) so distributed backends can
invoke it from inside a worker without shipping an executor instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor.invocation import (
    RuntimeContext,
    _ElementContext,
    build_kwargs,
    extract_outputs,
    import_callable,
)

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode


def execute_step(
    node: DAGNode,
    runtime: RuntimeContext | _ElementContext,
    pipeline_inputs: dict[str, Any],
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import, invoke, and output-map a single step's callable.

    ``param_overrides`` carries a swept instance's per-invocation param values.
    Every failure mode is wrapped as :class:`PipelineExecutionError` carrying
    the step id so a backend can attribute a worker-side failure without
    parsing tracebacks.
    """
    step_id = node.step.id
    if node.implementation is None:
        raise PipelineExecutionError(
            step_id,
            f"step {step_id!r} has no resolved implementation",
        )

    impl = node.implementation
    try:
        callable_obj = import_callable(impl.callable_path)
    except (ImportError, AttributeError, TypeError) as exc:
        raise PipelineExecutionError(
            step_id,
            f"failed to import callable {impl.callable_path!r} for step "
            f"{step_id!r}: {exc}",
            callable_path=impl.callable_path,
            original=exc,
        ) from exc

    if impl.setup:
        try:
            setup_fn = import_callable(impl.setup)
        except (ImportError, AttributeError, TypeError) as exc:
            raise PipelineExecutionError(
                step_id,
                f"failed to import setup callable {impl.setup!r} for "
                f"step {step_id!r}: {exc}",
                callable_path=impl.setup,
                original=exc,
            ) from exc
        setup_fn()

    try:
        kwargs = build_kwargs(
            node, runtime, pipeline_inputs, param_overrides=param_overrides
        )
    except (KeyError, ValueError) as exc:
        raise PipelineExecutionError(
            step_id,
            f"failed to build kwargs for step {step_id!r}: {exc}",
            callable_path=impl.callable_path,
            original=exc,
        ) from exc

    try:
        return_value = callable_obj(**kwargs)
    except Exception as exc:
        raise PipelineExecutionError(
            step_id,
            (
                f"callable {impl.callable_path!r} raised {type(exc).__name__} "
                f"for step {step_id!r}: {exc}"
            ),
            callable_path=impl.callable_path,
            original=exc,
        ) from exc

    try:
        outputs = extract_outputs(node, return_value)
    except Exception as exc:
        raise PipelineExecutionError(
            step_id,
            (
                f"output_map extraction failed for step {step_id!r}: {exc}"
            ),
            callable_path=impl.callable_path,
            original=exc,
        ) from exc

    if impl.teardown:
        try:
            teardown_fn = import_callable(impl.teardown)
        except (ImportError, AttributeError, TypeError) as exc:
            raise PipelineExecutionError(
                step_id,
                f"failed to import teardown callable {impl.teardown!r} for "
                f"step {step_id!r}: {exc}",
                callable_path=impl.teardown,
                original=exc,
            ) from exc
        teardown_fn()

    return outputs

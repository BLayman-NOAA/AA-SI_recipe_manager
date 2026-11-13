# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Compile a recipe into an echodataflow Prefect flow module.

The generated module has no runtime dependency on the recipe manager. It runs
the recipe once, end to end: one Prefect task per step, one per mapped chain
fanned out with ``.submit()``, and outputs handed between tasks as zarr stores
where the recipe checkpoints them or a chain hands them on. See
``Workflow_Recipe_Software_Documentation/_echodataflow_compiler_plan_v2.md``.

Modules: ``plan`` lowers the DAG into task units, ``render`` writes the module
text, ``flow_runtime`` holds the helpers copied into it, and ``sidecars``
writes the registry entry, YAML fragments and requirements beside it.
"""

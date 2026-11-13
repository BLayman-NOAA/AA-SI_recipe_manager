# echodataflow contrib specs

Op specs used to express echodataflow flows as recipes, for the recipe to
echodataflow flow compiler. They are not part of the built in registry. When a
recipe uses an op that is not built in, the recipe manager looks it up in any
folders named by `spec_dirs` in the run config or by `--spec-dir`, then in the
contrib sets, and loads only the specs the recipe uses. A contrib spec can add
an op but never replace a built in one.

A spec belongs here only when no built in op covers the capability. Prefer a
built in op with different params, and record any behaviour that still differs
from the original flow, over adding a near duplicate spec. Each spec here is
reviewed once the round trip comparison is done and is then promoted to the
built ins, kept, or deleted.

Implementations live in AA-SI_Utils and never import echodataflow, so a recipe
using these specs runs without echodataflow installed.

The plan is `Workflow_Recipe_Software_Documentation/_echodataflow_compiler_plan_v2.md`.

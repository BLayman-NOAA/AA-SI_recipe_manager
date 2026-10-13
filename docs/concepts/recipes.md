# Recipes

A recipe is a YAML file with four parts: a `recipe` block that names it, the
`inputs` a user can set, the `steps` of the pipeline, and optionally the
`outputs` it hands back.

```yaml
recipe:
  name: "quickstart"
  description: "Fetch HB1603 raw files from NCEI, compute Sv and plot an echogram."
  version: "1.0"
  author: "<Your Name>"
  schema_version: "1"

inputs:
  file_time_start:
    type: str
    default: "2016-07-25T20:58:00"
    description: "Start of the window, UTC."

steps:
  - id: query_ncei
    op: query_ncei_data
    params:
      file_time_start: ${inputs.file_time_start}
      file_time_end: "2016-07-25T21:45:00"
      filters:
        CRUISE_NAME: "HB1603"

  - id: read_raw
    op: read_raw_files
    inputs:
      raw_file_paths: ${query_ncei.raw_urls}
    params:
      sonar_model: "EK60"

outputs:
  stores:
    step_id: read_raw
    output_name: raw_stores
```

## Inputs

Each input has a `type` and usually a `default` and a `description`. An input
with no default is required. Set or override any input when running:

```bash
aa-recipe run quickstart.yaml --input file_time_start=2016-07-25T21:00:00
```

or in a [config file](../reference/config.md), which keeps machine-specific
values such as folders and buckets out of the shared recipe.

## Steps

Every step has an `id`, unique in the recipe, and an `op`, the operation it
runs. The op's spec decides what the step accepts:

`inputs`
:   Data the step consumes, normally another step's output. See
    [Wiring steps together](wiring.md).

`params`
:   Settings. Each op page in the [op reference](../ops/index.md) lists its
    parameters, their types, units and defaults.

Other step keys control how a step runs rather than what it computes:

| Key | Purpose |
| --- | --- |
| `description` | Free text, shown in generated notebooks. |
| `depends_on` | Run after the listed steps even though no data flows between them. |
| `map_over`, `collect`, `sweep` | Fan a step out per file or per parameter value, and back in. See [Per-file fan-out](fan-out.md). |
| `checkpoint` | `always`, `save` or `never`: whether this step's result is cached. See [Checkpoints and caching](caching.md). |
| `regenerate` | `if-missing` or `always`: re-create plots and files that are missing from the outputs folder. |
| `dispose_outputs` | Delete a step's scratch files as soon as its consumers have read them. |
| `execution` | Per-step executor settings, such as running one step in worker processes. |
| `implementation_override` | Choose between several implementations of the same op. |

## Custom steps

A step can call any Python function that is not a built-in op, by declaring
its spec inline with `op: custom`:

```yaml
- id: plan_dives
  op: custom
  params:
    json_dir: "./Auxiliary/JSON_Files"
  custom_spec:
    description: Turn the NEFSC dive configs into recipe windows.
    callable_path: aa_si_utils.dive_profiles.plan_dive_datasets
    params:
      json_dir: {type: path}
    outputs:
      windows: {type: list}
    output_map:
      windows: "['windows']"
```

`output_map` says how to pull each output out of the function's return value.

## Outputs

`outputs` names the step results the recipe exists to produce. A run always
produces them, even when they are cached and nothing else in the recipe reads
them. A recipe whose final steps write files and figures can leave it out:
those steps always run.

## Checking a recipe

```bash
aa-recipe dry-run my_recipe.yaml --visualize
```

checks that every op exists, every required parameter is set, every reference
points at a real step and output, and the steps form a graph with no cycles. It
runs nothing. `--visualize` adds a Mermaid diagram of the pipeline.

The full format is defined by a JSON Schema, which editors such as VS Code can
use for completion and checking:
[recipe.schema.json](../data/recipe.schema.json), or
`aa-recipe schema -o recipe.schema.json`.

# Wiring steps together

Steps are connected by naming another step's output: `${step_id.output_name}`.
The recipe manager reads these references to work out which steps depend on
which, and in what order they must run. The order steps are written in does
not matter to that.

```yaml
- id: compute_sv
  op: compute_sv
  inputs:
    echodata: ${combine_raw.echodata}

- id: remove_noise
  op: remove_background_noise
  inputs:
    ds_Sv: ${compute_sv.ds_Sv}
```

Each op's outputs are listed on its page in the [op reference](../ops/index.md),
for example [`compute_sv`](../ops/compute_sv.md) produces `ds_Sv`.

## The rules

- A step reference must be the **whole value**: `${compute_sv.ds_Sv}`, not
  `"${compute_sv.ds_Sv}_copy"`.
- **Lists** of references are allowed, and each element is wired separately:

    ```yaml
    - id: combine_masks
      op: combine_masks
      inputs:
        masks:
          - ${create_seafloor_mask.mask}
          - ${create_surface_mask.mask}
    ```

- A **param** can reference a step output too. The producing step then runs
  first:

    ```yaml
    params:
      depth_offset: ${compute_transducer_depth.transducer_depth}
    ```

- **Recipe inputs** are written `${inputs.name}` and, unlike step references,
  can sit inside a longer string: `"${inputs.output_root}/calibration"`.
- `depends_on: [step_a]` orders two steps that share no data.

## Seeing the wiring

`aa-recipe dry-run my_recipe.yaml --visualize` prints the graph as a Mermaid
diagram, with each edge labelled by the output that flows along it. On this
site, every recipe's **Flow diagram** tab draws the same graph with each
step's parameters, and the type of every input and output.

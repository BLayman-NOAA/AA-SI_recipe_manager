# Sub-recipes and includes

A long pipeline is easier to read and reuse split into pieces. The
[processing level](../levels/index.md) examples put each level in its own
**sub-recipe** file and assemble them in one parent recipe:

```yaml
steps:
  - include: sub_recipes/processing_lvl_1_per_file.yaml
    input_overrides:
      sonar_model: "EK80"

  - include: sub_recipes/processing_lvl_2_per_file.yaml
    input_overrides:
      echodata: ${combine_raw.echodata}
      calibration_dict: ${gen_cal_mapping.calibration_dict}
      surface_mask_depth: 5.0
```

## How an include works

- The sub-recipe's steps are copied into the parent in place of the `include`
  entry. Its path is relative to the file that includes it.
- `input_overrides` sets the sub-recipe's inputs. Inside the sub-recipe, every
  `${inputs.name}` is **replaced by the override's text** before anything
  else happens. So an override can be a literal, a parent input
  (`${inputs.seabed_line_folder}`), or another step's output
  (`${combine_raw.echodata}`), which wires the two files together.
- Sub-recipe inputs that are not overridden become inputs of the parent, with
  their defaults, so they can still be set with `--input`.
- Step ids share one namespace across the parent and every sub-recipe. A
  sub-recipe can refer to a step declared in another sub-recipe directly, as
  level 2 does with `${combine_raw.echodata}`. Two files declaring the same
  step id is an error.

## Where values live

A parent recipe can set every value its sub-recipes use on their includes.
Then the whole analysis reads from one file, and the sub-recipes stay generic.
A value more than one include needs is best made a parent input and passed
down; the rest can be literals in `input_overrides`. A literal is not
reachable with `--input`, so edit it in the parent.

## Sub-recipes on their own

A sub-recipe that reads another sub-recipe's steps cannot be run or checked by
itself: `${combine_raw.echodata}` points at a step it does not declare. That is
expected. Run and check the parent. On this site each sub-recipe is still
drawn on its own, with whatever flows in from other files shown as entry
points at the top, and its notebook tab is cut from the parent's generated
notebook.

## Caching is unaffected by files

A step's cache key depends on what it computes and what flows into it, not on
which file declares it. Moving steps between sub-recipes, as the
[HB2407 example](../levels/hb2407-herring-per-file.md) on this site does, keeps
every cached result valid.

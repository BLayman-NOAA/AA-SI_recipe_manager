# Checkpoints and caching

`aa-recipe run` can save a step's result to disk, a **checkpoint**, keyed by a
hash of everything that determines it: the op and its version, its parameters,
its inputs, and the hashes of the steps upstream of it. A later run that would
compute the same thing loads the checkpoint instead. So:

- an interrupted run resumes from the last checkpointed steps;
- changing one parameter re-runs only the steps it affects;
- per-file chains are cached per file.

Checkpoints go under `--user-cache-dir` (default `./recipe_cache`), which can
be a local folder or a `gs://` bucket.

## Which steps are checkpointed

The run's **checkpoint mode** picks a default, and a step's own `checkpoint`
key overrides it:

| Mode | Saves |
| --- | --- |
| `explicit` (default) | Only steps marked `checkpoint: always` or `checkpoint: save` |
| `eager` | Every step |
| `terminal` | Steps nothing else reads, plus `save` steps among them |
| `none` | Nothing except `checkpoint: always` steps |

| Step key | Effect |
| --- | --- |
| `checkpoint: always` | Saved under every mode |
| `checkpoint: save` | Saved under every mode except `none` |
| `checkpoint: never` | Never saved |

```bash
aa-recipe run my_recipe.yaml --checkpoint-mode eager
aa-recipe run my_recipe.yaml --checkpoint compute_sv      # one more step
aa-recipe run my_recipe.yaml --no-checkpoints             # read and write nothing
```

Checkpoint the expensive results worth reusing, such as Sv and gridded MVBS,
and leave cheap or very large intermediates unsaved. Data is stored as Zarr by
default (`--checkpoint-format netcdf` or `pickle` for objects Zarr cannot
hold).

## Plots and files

A step that writes a figure or a file is skipped like any other when its
result is cached. `regenerate: if-missing` on the step re-creates the file when
it is missing from the current outputs folder, and `--regenerate sinks` or
`--regenerate all` forces it for a whole run.

## Inspecting and cleaning the cache

```bash
aa-recipe explain-cache my_recipe.yaml          # which steps would load, and why the rest would not
aa-recipe clean my_recipe.yaml --stale --dry-run
aa-recipe clean my_recipe.yaml --all
```

## The shared survey cache

Computing Sv for a whole cruise once and sharing it saves everyone that time.
A run reads two cache tiers in order: the user's own (`--user-cache-dir`), then
a shared **survey** cache (`--survey-cache-dir`, usually set in the
[config file](../reference/config.md)). A curated run writes the survey tier
with `--cache-write-tier survey`, and bucket permissions decide who may.

For an analysis to load the survey's results, its steps up to that point must
hash exactly as the survey run's did: the same ops, parameters and wiring. The
[HB1603 dive analysis](../levels/hb1603-dive-analysis.md) is built this way.

Bumping `execution.cache_epoch` in a recipe deliberately invalidates every
cached result for it.

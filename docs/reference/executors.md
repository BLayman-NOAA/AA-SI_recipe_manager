# Running recipes and executors

`aa-recipe run` executes a recipe directly. The **executor** decides how the
steps are scheduled; the recipe itself does not change.

```bash
aa-recipe run my_recipe.yaml                                   # one step at a time
aa-recipe run my_recipe.yaml --executor dask                   # Dask, local threads
aa-recipe run my_recipe.yaml --executor dask --dask-scheduler processes --dask-workers 4
aa-recipe run my_recipe.yaml --executor dask --dask-scheduler tcp://scheduler:8786
aa-recipe run my_recipe.yaml --executor prefect
```

| Executor | Install | Runs |
| --- | --- | --- |
| `sequential` (default) | built in | One step at a time, in the current process. |
| `dask` | `pip install -e ".[dask]"` | Independent steps and per-file instances at the same time, on a local thread or process pool or an external Dask cluster. |
| `prefect` | `pip install -e ".[prefect]"` | Each step as a Prefect task, for Prefect's monitoring and retries. |

Precedence for choosing one: the `--executor` flag, then the config file's
`executor`, then the recipe's `execution.executor`, then `sequential`.

## Sizing a Dask run

`--dask-workers N` is how many steps or per-file instances run at once. Each
one holds a whole [per-file chain](../concepts/fan-out.md) instance in memory,
roughly one raw file's working set, so this is the setting to lower when a run
runs out of memory or disk.

`threads` (the default scheduler) shares one process and suits I/O-bound work
such as reading from a bucket. `processes` gives each worker its own process,
for CPU-bound steps.

A recipe can ask for processes for one step only, which is how the example
recipes run raw file conversion:

```yaml
- id: read_raw
  op: read_raw_files
  execution:
    dask_config:
      scheduler: processes
```

## Storage locations

A run uses three folders, each a local path or a `gs://` URL (with the `gcs`
extra):

| Flag | Default | Holds |
| --- | --- | --- |
| `--user-cache-dir` | `./recipe_cache` | [Checkpoints](../concepts/caching.md) |
| `--outputs-dir` | `outputs` next to the cache | Figures, files steps write, logs, `manifest.json`, provenance |
| `--temp-dir` | `exe_temp` next to the cache | Scratch for the run, deleted when it ends (`--keep-temp` keeps it) |

Put the cache, and if the disk is small the scratch too, on a bucket for a
survey that does not fit on a workstation:

```bash
aa-recipe run my_recipe.yaml \
    --user-cache-dir gs://my-bucket/surveys/HB1603/recipe_cache \
    --temp-dir gs://my-bucket/surveys/HB1603/exe_temp
```

A remote cache needs Zarr checkpoints, the default. Keep `--outputs-dir`
local for recipes that write calibration files or images. These locations are
usually set once in a [config file](config.md).

## One recipe over many files

`aa-recipe batch` runs a recipe once per file in a folder, binding each file to
one recipe input, with a shared cache and a separate outputs folder per file:

```bash
aa-recipe batch my_recipe.yaml \
  --input-dir gs://bucket/survey/raw --input-name raw_file --pattern "*.raw" \
  --user-cache-dir ./cache --outputs-dir ./outputs --executor dask
```

For most surveys a single run with [`map_over`](../concepts/fan-out.md) is
the better fit: it can gather the files back together, which `batch` cannot.

## Logs and timing

The run reports each step as it starts and finishes, and the total time.
`outputs/logs/standard_out.txt` has everything the steps printed, fenced per
step and per per-file instance, and names the executor and concurrency in
force. `outputs/manifest.json` records each step's time and whether it was
computed or loaded from the cache.

## From Python

```python
from aa_recipe_manager import api

api.execute("my_recipe.yaml", user_cache_dir="./recipe_cache")
api.execute(
    "my_recipe.yaml",
    executor="dask",
    executor_options={"scheduler": "processes", "n_workers": 4},
    inputs={"file_time_start": "2016-07-25T21:00:00"},
)
api.generate("my_recipe.yaml", output_format="notebook")
report = api.dry_run("my_recipe.yaml")
```

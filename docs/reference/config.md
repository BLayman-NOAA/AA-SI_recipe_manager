# Config files

A recipe should run anywhere, so it holds no machine-specific paths. Where the
cache goes, which bucket the raw files are in, and cloud credentials go in a
per-user **run config** file instead.

```yaml
# my_recipe.config.yaml
user_cache_dir: gs://my-bucket/surveys/HB2407/recipe_cache
temp_dir: ./exe_temp
outputs_dir: ./outputs
survey_cache_dir: gs://my-bucket/surveys/HB2407/survey_cache

executor: dask
dask_scheduler: threads
dask_workers: 8

inputs:
  raw_input_folder: gs://my-bucket/HB2407/raw
  cal_input_folder: ./HB2407_calibration_files

# Only for non-default credentials. Application Default Credentials need
# nothing here.
# storage_options:
#   token: /path/to/service-account.json
```

| Key | Meaning |
| --- | --- |
| `user_cache_dir`, `temp_dir`, `outputs_dir` | The run's [storage locations](executors.md#storage-locations) |
| `survey_cache_dir` | The shared [survey cache](../concepts/caching.md#the-shared-survey-cache) to read from |
| `executor`, `dask_scheduler`, `dask_workers` | Default [executor](executors.md) settings |
| `inputs` | Values for the recipe's inputs |
| `storage_options` | fsspec options for remote paths, such as a credentials token |

Any other key is an error, so a misspelling does not go unnoticed.

## Which file is used

The first one found wins, and only one file is read:

1. `--config path/to/file.yaml`
2. the path in the `AA_RECIPE_CONFIG` environment variable
3. `<recipe_stem>.config.yaml` next to the recipe, so recipes sharing a folder
   can each have their own
4. `./aa-recipe.config.yaml` in the current directory
5. `~/.config/aa-recipe/config.yaml`

## Which value wins

A command-line flag beats the config file, which beats the recipe's own
defaults:

```text
--input / --user-cache-dir / ...  >  config file  >  recipe default
```

`aa-recipe doctor my_recipe.yaml` shows which config file was found and
checks that its folders can be written.

Keep real config files, which name your buckets, out of version control.

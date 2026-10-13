# Provenance

Every `aa-recipe run` records what it did, under the outputs folder:

`provenance/provenance.yaml`
:   The recipe's name, version and hash, the time, the Python version and
    operating system, any input values given on the command line or in a
    config file, the resolved version of every package the steps called, and
    the raw files read.

`manifest.json`
:   How each step was satisfied (computed, loaded from the user or survey
    cache, or not needed), where its result is stored, its elapsed time, and
    the run's total time. The dependable way to find a run's products and to
    compare one run against another.

`logs/standard_out.txt`
:   Everything the steps printed, fenced per step and per fan-out instance,
    flushed as it is written so an interrupted run still shows where it
    stopped.

`--save-provenance PATH` writes the provenance file to `PATH` instead.

## Examples

The first two are from a real run of the [quickstart](../quickstart.md)
recipe: four HB1603 files read from NCEI, five steps, nothing cached
beforehand. The third is from the HB1603 calibration example.

=== "provenance.yaml"

    [Download](../examples/provenance.yaml)

    ```yaml
    --8<-- "examples/provenance.yaml"
    ```

    `recipe_hash` identifies the exact recipe that ran. `resolved_dependencies`
    lists every package the steps called, with the version installed when the
    run happened. `raw_inputs` names each raw file read, with a `digest` over
    the whole list, so two runs over the same files can be recognized as such.

=== "manifest.json"

    [Download](../examples/manifest.json)

    ```json
    --8<-- "examples/manifest.json"
    ```

    Each step has a `disposition`: `computed` here, since nothing was cached.
    Run the recipe again with `checkpoint: always` on a step and that step
    reads `user` instead, with its `step_hash` naming the cache entry it
    loaded. `elapsed_seconds` shows where the time went: reading from NCEI and
    drawing the echogram, which is where Sv is actually computed. The cache
    path under `tiers` is shortened here; a real manifest has the full path.

=== "calibration_provenance.yaml"

    [Download](../examples/calibration_provenance.yaml)

    ```yaml
    --8<-- "examples/calibration_provenance.yaml"
    ```

    Written by [`build_calibration_mapping`](../ops/build_calibration_mapping.md).
    `summary` counts the raw files and channels and how many matched a
    calibration. `tolerances` records how closely each setting had to match.
    Under `channels`, each channel is split into `segments`, one per stretch
    of time with the same settings, each saying which calibration was applied,
    or why none was, and the raw settings compared against the calibration's.

## Reproducing a run

The provenance file holds enough to rebuild the environment the run used:

```bash
aa-recipe env create outputs/provenance/provenance.yaml --path .venv-replay
```

See [Environments](../reference/environments.md).

## Calibration provenance

The calibration steps keep their own, more detailed record, the third example
above: for every channel, which calibration was applied over which stretch of
time and why. See [Calibration](../calibration/index.md).

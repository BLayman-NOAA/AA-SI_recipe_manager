# Quickstart

This walks through a complete five-step recipe: it fetches four raw files
from the NCEI water column sonar archive, converts them, computes volume
backscattering strength (Sv) and plots an echogram. You need the recipe
manager [installed](installation.md) with `all-builtin-specs`, and internet
access. Nothing needs to be downloaded first.

## The recipe

Save it as `quickstart.yaml`, or
[download it](recipes/quickstart/quickstart.yaml).

--8<-- "data/partials/quickstart.md"

Each step names an **op**, and the op's page in the
[op reference](ops/index.md) lists every parameter it accepts. Steps are
connected by naming another step's output: `${query_ncei.raw_urls}` is the list
of file URLs the first step found. Values under `inputs` at the top can be
changed without editing the file.

## 1. Check it

```bash
aa-recipe dry-run quickstart.yaml --visualize
```

This checks that every op exists, every parameter is valid and every
reference points at a real output, without running anything. It prints each
step's resolved parameters, then a Mermaid diagram of the pipeline, which you
can paste into [mermaid.live](https://mermaid.live) or a Markdown file:

```text
graph TD
    query_ncei["query_ncei\n(query_ncei_data)"]
    read_raw["read_raw\n(read_raw_files)"]
    combine_raw["combine_raw\n(combine_raw_files)"]
    compute_sv["compute_sv\n(compute_sv)"]
    plot_sv["plot_sv\n(plot_sv_echogram)"]
    query_ncei -->|"raw_urls"| read_raw
    read_raw -->|"raw_stores"| combine_raw
    combine_raw -->|"echodata"| compute_sv
    compute_sv -->|"ds_Sv"| plot_sv
```

## 2. Run it

```bash
aa-recipe run quickstart.yaml
```

Each step reports as it starts and finishes. On a typical connection the run
takes about eight minutes, nearly all of it spent reading the files from NCEI
and computing Sv for the plot. When it finishes it prints where things went:

- `outputs/images/plot_sv.png`, the echogram, one panel per frequency;
- `outputs/manifest.json`, what ran and how long each step took;
- `outputs/provenance/provenance.yaml`, the inputs and package versions used;
- `outputs/logs/standard_out.txt`, everything the steps printed.

The echogram is drawn at full resolution, one column per ping, so the image
is very large. Open it in a viewer that can zoom.

The scattering layers between 300 and 700 m show at every frequency. At 120
and 200 kHz the deeper water fills with background noise, which grows with
range because sound is absorbed faster at high frequencies. Removing it is part
of [level 2](levels/level-2.md).

## 3. Change it

Override any input on the command line:

```bash
aa-recipe run quickstart.yaml --input max_depth=500
aa-recipe run quickstart.yaml --input file_time_start=2016-07-25T21:30:00 --input file_time_end=2016-07-25T22:00:00
```

Or edit the recipe. Some things to try:

- Change `sonar_model` and the time window to another cruise's files, and
  `cruise_name` to that cruise.
- Add a step after `compute_sv` that removes background noise. The
  [`remove_background_noise`](ops/remove_background_noise.md) page lists its
  inputs and parameters:

    ```yaml
      - id: remove_noise
        op: remove_background_noise
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
        params:
          range_sample_num: 10
          ping_num: 5
    ```

    and point `plot_sv` at `${remove_noise.ds_Sv}`.

To make re-runs faster, add `checkpoint: always` to `compute_sv`. Its result is
then cached under `recipe_cache/`, and a later run with the same inputs loads it
instead of reading NCEI again. See [Checkpoints and caching](concepts/caching.md).

## 4. Turn it into a notebook

```bash
aa-recipe generate quickstart.yaml -o quickstart.ipynb
```

writes a Jupyter notebook with one cell per step, calling the same functions
with the same parameters, for stepping through and experimenting
interactively. The **Notebook** tab above shows what it contains.
`--format script` writes a Python script instead.

## Next

- [Concepts](concepts/recipes.md): the recipe format in full.
- [Processing levels](levels/index.md): the complete pipelines this recipe is
  a small slice of, level by level.

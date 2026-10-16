# HB2407 herring, per file

The complete recipe behind the HB2407 examples on the level pages. It
processes one day of a 2024 herring survey recorded by the Simrad EK80 on NOAA
ship Henry B. Bigelow, from raw files in a Google Cloud bucket through
HDBSCAN clustering.

```bash
aa-recipe dry-run Herring_10-15_per_file.yaml --visualize --input raw_input_folder=gs://...
aa-recipe run Herring_10-15_per_file.yaml --executor dask
```

## How it is assembled

| Include or step | Level | Steps |
| --- | --- | --- |
| `processing_lvl_0_calibration.yaml` | [0](level-0.md#hb2407) | List the raw files in the time window; build the calibration mapping in one step |
| `processing_lvl_1_per_file.yaml` | [1](level-1.md#hb2407) | Per file: convert and open |
| `processing_lvl_2_per_file.yaml` | [2](level-2.md#hb2407) | Per file: calibrated Sv, depth, seabed and surface masks; merge the survey; crop and remove noise |
| `plot_sv_clean` | | Echogram of the cleaned Sv |
| `processing_lvl_3.yaml` | [3](level-3.md#hb2407) | Sparse-cell mask, MVBS, per-cell statistics |
| `plot_mvbs` | | Echogram of the MVBS |
| `processing_lvl_4.yaml` | [4](level-4.md#hb2407) | HDBSCAN clustering and report |

**Levels 1 and 2 are one per-file chain**, so their includes must stay back
to back. The survey is merged at the end of level 2's per-file part, before
noise removal, which has to see neighbouring pings across file boundaries.

**The parent sets the values.** Inputs such as the time window and the plot
window are declared once here and passed to each include; settings only one
level uses are literals on its include.

## The whole recipe

**Flow diagram** draws the recipe one box per included file, plus the two
plots the parent declares itself, with the outputs each passes to the next.
Double click a box to list the values this file sets on it and the inputs
the level declares itself, and again to drill in and see its steps; the
inspector links each box to its level's page.
**Notebook** is the complete generated notebook, and can be downloaded as an
`.ipynb` file. See [Reading a level page](index.md#reading-a-level-page) for
the diagram's controls.

--8<-- "data/partials/hb2407-herring-per-file.md"

The original is
[`example_recipes/HB2407/Herring_10-15_per_file.yaml`](https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example/blob/main/example_recipes/HB2407/Herring_10-15_per_file.yaml)
in AA-SI_Full_Pipeline_Example, which keeps the calibration steps in its level
1 file. This copy moves file selection and calibration to level 0 and
`extract_cal_params` to level 2, to match the HB1603 levels; every step
computes exactly as in the original.

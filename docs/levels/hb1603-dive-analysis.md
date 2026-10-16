# HB1603 dive analysis

The complete recipe behind the HB1603 examples on the level pages. It
clusters the water column around 13 sperm whale dives recorded by the
Simrad EK60 on NOAA ship Henry B. Bigelow in 2016, and codes each dive by what
the whale passed through.

```bash
aa-recipe dry-run dive_analysis.yaml --visualize
aa-recipe run dive_analysis.yaml --executor dask --dask-workers 16
```

## How it is assembled

| Include | Level | Steps |
| --- | --- | --- |
| `dive_plan.yaml` | | Reads the NEFSC dive configs: one time window per dive. Not a processing level; it reads no acoustic data. |
| `processing_lvl_0_user.yaml` | [0](level-0.md#hb1603) | NCEI catalogue, raw channel scan, calibration standardize and mapping |
| `processing_lvl_1_per_file.yaml` | [1](level-1.md#hb1603) | Per file: convert and open |
| `processing_lvl_2_combined.yaml` | [2](level-2.md#hb1603) | Per file: calibrated Sv and depth; inside the dive windows, masks and noise removal |
| `processing_lvl_3_user.yaml` | [3](level-3.md#hb1603) | Per file: grid; then merge the files into one pooled dive grid |
| `processing_lvl_4.yaml` | [4](level-4.md#hb1603) | Pooled HDBSCAN clustering and report |
| `processing_lvl_5_sv_codes.yaml` | [5](level-5.md) | Per-dive SVCodes and depth-interval tables |

**Levels 1 to 3 are one per-file chain.** Every step from `read_raw` to the
per-file grid runs once per catalogue file, as one unit, and nothing
non-mapped may sit between those includes. That is why the dive plan comes
first. See [Per-file fan-out](../concepts/fan-out.md).

**The survey tier is shared.** Levels 0 to 2a are what a separate survey
recipe computes once for the whole cruise into a shared cache. This recipe's
copies of those steps are wired identically, so they hash the same and load
from that cache instead of recomputing; the analyst's own work starts at 2b.
Their parameters must therefore not be changed here. See
[the shared survey cache](../concepts/caching.md#the-shared-survey-cache).

**Every value is set in this file**, on the include that uses it. Values more
than one level needs, such as `ping_time_bin`, are recipe inputs passed down
to each include.

The dive plan:

--8<-- "data/partials/hb1603-dive-plan.md"

## The whole recipe

**Flow diagram** draws the recipe one box per included file, with the
outputs each passes to the next. Double click a box to list the values this
file sets on it and the inputs the level declares itself, and again to drill
in and see its steps; the inspector links each box to its level's page. **Notebook** is the complete generated notebook, and
can be downloaded as an `.ipynb` file. See
[Reading a level page](index.md#reading-a-level-page) for the diagram's
controls.

--8<-- "data/partials/hb1603-dive-analysis.md"

The original is
[`example_recipes/HB1603/UC1/dive_analysis.yaml`](https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example/blob/main/example_recipes/HB1603/UC1/dive_analysis.yaml)
in AA-SI_Full_Pipeline_Example, which keeps level 2 in two files. This copy
merges them; every step computes exactly as in the original.

# HB1603 survey echogram pyramid

This recipe turns the whole HB1603 cruise into one
[EchogramGL](https://github.com/BLayman-NOAA/AA-SI_EchogramGL) store: a
level-of-detail pyramid that the WebGPU echogram viewer can pan and zoom from
the full survey down to single cells. It keeps 18 and 38 kHz and masks the
seabed.

The store is the context the dive analysis sits in. It is gridded 2 m by
10 s on depth, the same grid the
[HB1603 dive analysis](hb1603-dive-analysis.md) clusters on, so the dive
results can be laid over it cell for cell.

```bash
aa-recipe dry-run survey_echogram_pyramid.yaml --visualize
aa-recipe run survey_echogram_pyramid.yaml --executor dask --dask-workers 16
```

## How it is assembled

| Include | Level | Steps |
| --- | --- | --- |
| [`processing_lvl_0_user.yaml`](#view=hb1603-survey-echogram-pyramid:query_ncei) | [0](level-0.md#hb1603) | NCEI catalogue, raw channel scan, calibration standardize and mapping |
| [`processing_lvl_1_per_file.yaml`](#view=hb1603-survey-echogram-pyramid:read_raw) | [1](level-1.md#hb1603) | Per file: convert and open |
| [`processing_lvl_2_survey.yaml`](#view=hb1603-survey-echogram-pyramid:extract_cal_params) | [2a](level-2.md#calibrated-sv) | Per file: calibrated Sv and depth, cropped at 1800 m |
| [`processing_lvl_2_viewer_seabed.yaml`](#view=hb1603-survey-echogram-pyramid:viewer_splitbeam_angle) | [2b](level-2.md#masks-and-noise-removal) | Per file: split-beam angles, a 3000 m crop, the seabed line, surface, frequency and seabed masks, noise removal |
| [`processing_lvl_3_viewer.yaml`](#view=hb1603-survey-echogram-pyramid:viewer_mask_sparse) | [3](level-3.md) | Per file: MVBS on a pinned depth grid; then merge the files into one survey store |
| [`viewer_pyramid.yaml`](#view=hb1603-survey-echogram-pyramid:plan_segments) | none | Plan segments, build a pyramid for each in parallel, merge them into the viewer store |

Each file name opens the diagram below at that level; each level links to its
section of the processing road map. Levels 0 and 1 are the same files the dive
analysis uses.

**It reuses the survey cache.** Levels 0 to 2a are the same files the dive
analysis includes, wired the same way, so their steps hash as the survey
recipe's do and load the per-file Sv from the
[shared survey cache](../concepts/caching.md#the-shared-survey-cache). The
viewer levels start from the survey's `ep_add_depth` output, because
split-beam angles have to be added before the crop. Keep the survey levels
in step with the survey recipe.

**The seabed line is picked, not drawn.** `phase_detect_seafloor` finds it in
each file from the 38 kHz phase and Sv, so no `.bot` file or Echoview line is
needed. Pings whose seabed lies below the 3000 m crop are kept whole.

**Levels 1 to 3 are one per-file chain**, as in the dive analysis. Nothing
that isn't per-file may sit between those includes. See
[Per-file fan-out](../concepts/fan-out.md).

## Running it

The pyramid ops come from `aa-si-echogram-gl`, which the `all-builtin-specs`
extra installs (see [Installation](../installation.md)).

To try a few files first, narrow the catalogue window. This one is nine files
on 25 July:

```bash
aa-recipe run survey_echogram_pyramid.yaml \
  --input file_time_start=2016-07-25T20:58 \
  --input file_time_end=2016-07-25T23:45
```

The catalogue query is a parent of every per-file step, so a narrowed window
is a separate cache lineage: it computes its own Sv and the full run reuses
none of it. Run the full survey with the default window.

The finished store is the checkpoint of `survey_echogram_store`. Find
`pyramid.zarr` under that step in the cache and open it with
`aa-echogram serve --store <path>`.

## The whole recipe

--8<-- "data/partials/hb1603-survey-echogram-pyramid.md"

The original is
[`example_recipes/HB1603/UC1/survey_echogram_pyramid.yaml`](https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example/blob/main/example_recipes/HB1603/UC1/survey_echogram_pyramid.yaml)
in AA-SI_Full_Pipeline_Example. This copy has shorter comments; every step
computes exactly as in the original.

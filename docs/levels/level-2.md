# Level 2: calibrated Sv and cleaning

Level 2 of the road map applies the calibration (2A), then removes noise and
excludes the regions that should not be analysed (2B). The examples keep the
whole level in one sub-recipe, so the road map's two halves are two parts of
one diagram.

## Calibrated Sv {#calibrated-sv}

| Road map | Recipe steps |
| --- | --- |
| Apply validated calibration data | [`extract_standardized_cal_params`](../ops/extract_standardized_cal_params.md) picks this file's channels out of the level 0 calibration mapping, and [`compute_sv`](../ops/compute_sv.md) computes Sv with them. |
| Supplemental data: transducer depth | [`compute_transducer_depth`](../ops/compute_transducer_depth.md) works out how deep the transducer was on each ping, from the file's recorded offsets and heave, and [`ep_add_depth`](../ops/ep_add_depth.md) adds depth below the surface to the Sv dataset. |

The calibration arrives from level 0 as two dictionaries, the standardized
calibration values and the mapping from each raw file's channels to them. A
channel no calibration file matched falls back to the values the raw file
recorded, with a warning. See [Calibration](../calibration/index.md) for where
these come from and the other ways calibration can reach this step.

Depth is computed explicitly rather than left to echopype's platform offsets,
which put an EK60 transducer above the sea surface.

## Masks and noise removal {#masks-and-noise-removal}

| Road map | Recipe steps |
| --- | --- |
| Exclusion regions: seabed | [`read_seafloor_line`](../ops/read_seafloor_line.md) reads a hand-checked Echoview seabed line onto the pings, and [`create_seafloor_mask`](../ops/create_seafloor_mask.md) masks everything below it, plus a buffer. [`detect_seafloor`](../ops/detect_seafloor.md), [`phase_detect_seafloor`](../ops/phase_detect_seafloor.md) and others detect the seabed instead when there is no line. |
| Exclusion regions: near surface | [`create_surface_mask`](../ops/create_surface_mask.md) masks the top few metres, where bubbles and the transducer's near field are. |
| Exclude channels | [`create_frequency_mask`](../ops/create_frequency_mask.md) drops frequencies the analysis does not use. |
| | [`combine_masks`](../ops/combine_masks.md) joins the masks and [`apply_sv_mask`](../ops/apply_sv_mask.md) applies them. |
| Background noise | [`remove_background_noise`](../ops/remove_background_noise.md) estimates the noise floor from the quietest samples and removes what does not clear it. |

**The seabed is masked before noise removal.** The noise estimate is a minimum
over range, so the very weak samples below the seabed would otherwise pull it
down.

Impulse and transient noise removal, and bubble and CTD exclusion regions,
have no recipe steps yet.

## HB1603 {#hb1603}

The cruise's raw files differ widely in sample interval, so their Sv cannot be
merged on a shared range axis. Everything here stays per file, and files are
merged in level 3 once they are on a common depth grid.

The level is in two parts that the working recipes keep in separate files:

- **2a**, the five calibration and depth steps, is the survey's shared
  product. It is computed once for the whole cruise and loaded from the survey
  cache by every analysis.
- **2b** keeps only the pings inside the 13 dive windows, masks the surface,
  three of the five frequencies and the seabed, and removes noise.

`crop_survey_range` cuts each file's Sv at 1800 m of range and checkpoints
it. That checkpoint is what the survey's other analyses start from: survey
MVBS, the echogram viewer and the Sv window example load it instead of
recomputing Sv. The dive analysis does not read it, because the dives reach
2343 m, below the crop, so 2b starts from the uncropped `ep_add_depth` output
beside it. It stays in this recipe so level 2a matches the survey recipe
step for step; its result is loaded from the cache, or saved there on a
first run, and changes nothing downstream.

--8<-- "data/partials/hb1603-level-2.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).

## HB2407 {#hb2407}

Each file is calibrated, given depth and masked on its own. Then
[`merge_datasets`](../ops/merge_datasets.md) collects the files into one survey
dataset, because noise removal works across neighbouring pings and has to see
them all. The merged survey is cropped to the deepest retained data and
rechunked before noise removal.

--8<-- "data/partials/hb2407-level-2.md"

Part of the [HB2407 herring pipeline](hb2407-herring-per-file.md).

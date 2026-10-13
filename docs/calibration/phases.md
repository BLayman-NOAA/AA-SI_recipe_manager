# Three-phase calibration recipes

The same calibration steps as the [complete pipeline](index.md#the-complete-pipeline-on-its-own),
split into three recipes run one after another, with a person reviewing the
result in between. They are also what the calibration service exposes as its
three calls.

1. **Standardize** reads the raw files' channel settings and turns the
   manufacturer calibration files into one standardized file per channel, then
   stops, so the channels can be reviewed, corrected or discarded before
   anything is matched against them.
2. **Map** matches every raw channel to a standardized channel and writes the
   mapping. Its first four steps are copied verbatim from phase 1, so they hash
   the same and load from phase 1's cache instead of re-scanning the survey.
3. **Archive** writes what a survey keeps, the mapping, the calibration
   provenance and the standardized channel files, to a directory or bucket of
   your choosing.

Phases 1 and 2 must share the same cache and outputs folders, so keep their
[config files](../reference/config.md) identical or pass `--config`.

## Phase 1: standardize

--8<-- "data/partials/calibration-standardize.md"

## Phase 2: map

The recipe repeats phase 1's four steps word for word, which is what lets it
load them from phase 1's cache instead of scanning the survey again. The
diagram shows only the step this phase adds, `build_cal_mapping`, with the
phase 1 steps feeding it drawn in pink.

A channel reviewed in phase 1 can be edited or dropped and passed back as
`override_channels`, which rewrites the standardized folder to match, so the
archived files are the ones the mapping was built from. With
`conflict_resolution: report`, conflicts are returned rather than raised;
re-run with `calibration_choices` to settle them.

--8<-- "data/partials/calibration-mapping.md"

## Phase 3: archive

```bash
aa-recipe run save_calibration.yaml --input archive_dir=./archive
aa-recipe run save_calibration.yaml --input archive_dir=gs://bucket/RL2307/calibration
```

An archive is written once and kept: a destination that is not empty stops the
run unless `overwrite` is set.

--8<-- "data/partials/calibration-save.md"

# Level 0: retrieval and calibration preparation

Level 0 of the road map gets the raw data and harvests its metadata: where the
files are, which echosounder and settings recorded them. In the recipes it
also prepares the calibration that level 2 applies, because matching a
calibration to a channel needs exactly that metadata.

| Road map | Recipe steps |
| --- | --- |
| Retrieve data from NCEI | [`query_ncei_data`](../ops/query_ncei_data.md) finds the files and their HTTPS URLs; later steps read them directly. [`download_ncei_data`](../ops/download_ncei_data.md) copies them to a folder instead. |
| Retrieve data from GCP or on-premise storage | [`initial_setup`](../ops/initial_setup.md) lists the raw files in a local folder or `gs://` bucket, within a time window. |
| Harvest file- and ping-level metadata | [`read_raw_file_config`](../ops/read_raw_file_config.md) reads each file's channel configuration (frequency, pulse length, transmit power, ...) from the start of the file, and [`record_raw_file_configs`](../ops/record_raw_file_configs.md) gathers them for the survey. |
| Calibration data and metadata (used at level 2) | [`standardize_calibration_files`](../ops/standardize_calibration_files.md) turns the manufacturer's `.cal` (EK60) or `.xml` (EK80) files into one standardized file per channel; [`build_calibration_mapping`](../ops/build_calibration_mapping.md) matches every raw channel to one. [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md) does all of it in one step. |

The output of level 0 is the list of raw files that every per-file step in
levels 1 to 3 runs over, and two dictionaries, the calibration data and the
mapping from raw channels to it, that level 2 reads.

The calibration steps are described in full, and as a standalone pipeline, in
[Calibration](../calibration/index.md).

## HB1603 {#hb1603}

The catalogue comes from NCEI: [`query_ncei_data`](../ops/query_ncei_data.md)
finds every HB1603 file, and the scan reads only the first megabyte of each
one straight from its NCEI URL. The calibration is built in four separately
cached steps, as in the [staged calibration pipeline](../calibration/index.md).

This is the analyst's copy of the survey's level 0: wired identically to the
survey recipe that builds the calibration, so it loads the survey's cached
mapping and never rebuilds it.

--8<-- "data/partials/hb1603-level-0.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).

## HB2407 {#hb2407}

The raw files are listed from a folder or bucket by
[`initial_setup`](../ops/initial_setup.md), and the whole calibration pipeline
runs as one step, [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md),
reading the EK80 `.xml` calibration files.

--8<-- "data/partials/hb2407-level-0.md"

Part of the [HB2407 herring pipeline](hb2407-herring-per-file.md).

### Downloading from NCEI instead

When the raw files are not yet in a bucket, this alternative level 0 queries
NCEI and downloads the matching files, with their `.bot` and `.idx`
companions, into the folder the rest of the pipeline reads.

--8<-- "data/partials/hb2407-level-0-ncei-download.md"

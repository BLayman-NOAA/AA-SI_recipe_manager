# Level 1: raw ingest

Level 1 of the road map turns the manufacturer's raw files into an open,
self-describing format. In the recipes that is two steps per raw file:

| Road map | Recipe steps |
| --- | --- |
| Reformat to the echopype format | [`read_raw_files`](../ops/read_raw_files.md) converts a raw file with echopype, into a Zarr or NetCDF store. |
| Supplemental data (GPS, motion, sound speed, transducer depth) | Carried in the converted file's Platform and Environment groups, and read from there by later steps such as [`compute_transducer_depth`](../ops/compute_transducer_depth.md). |
| | [`combine_raw_files`](../ops/combine_raw_files.md) opens the store as one EchoData object, the input to level 2. |

Both steps run once per raw file, as the start of a per-file chain that
continues through level 2 (and, for HB1603, level 3). See
[Per-file fan-out](../concepts/fan-out.md).

The road map's level 1 quality checks (GPS QA/QC, time-coordinate and motion
correction) have no recipe steps yet.

## HB1603 {#hb1603}

Each instance reads its file straight from its NCEI URL. The converted store
is scratch: `dispose_outputs` deletes it as soon as the file's chain has read
it, so the disk holds one store per worker rather than one per catalogue file.

--8<-- "data/partials/hb1603-level-1.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).

## HB2407 {#hb2407}

The same two steps for EK80 files read from a bucket, mapped over the file
list `initial_setup` made in level 0.

--8<-- "data/partials/hb2407-level-1.md"

Part of the [HB2407 herring pipeline](hb2407-herring-per-file.md).

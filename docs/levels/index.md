# Processing levels

The [AA-SI data road map](https://github.com/nmfs-ost/AA-SI_DataRoadMap)
describes active acoustics processing as a ladder of levels, based on
[echopype's proposed data processing levels](https://echolevels.readthedocs.io/en/latest/levels_proposed.html).
The recipe manager implements each level as a **sub-recipe**: a short recipe
file holding that level's steps. A full pipeline includes one sub-recipe per
level, in order.

This section shows those sub-recipes for two example surveys, so you can see
both what each level does and how a real pipeline is assembled from them.

| Level | Road map stage | In the recipes | HB1603 | HB2407 |
| --- | --- | --- | --- | --- |
| [0](level-0.md) | Retrieve data, harvest metadata | Find the raw files (NCEI, a bucket or a folder), read each file's channel settings, standardize the calibration files and match them to channels | [Level 0](level-0.md#hb1603) | [Level 0](level-0.md#hb2407) |
| [1](level-1.md) | Reformat to an open format | Convert each raw file to echopype's format | [Level 1](level-1.md#hb1603) | [Level 1](level-1.md#hb2407) |
| [2](level-2.md) | 2A apply calibration, 2B noise and exclusion regions | Calibrated Sv and depth; surface, frequency and seabed masks; background noise removal | [Level 2](level-2.md#hb1603) | [Level 2](level-2.md#hb2407) |
| [3](level-3.md) | 3A grid the data | Mask sparse bins, average onto a grid (MVBS), per-cell statistics | [Level 3](level-3.md#hb1603) | [Level 3](level-3.md#hb2407) |
| [4](level-4.md) | AI/ML models | HDBSCAN clustering of the gridded data | [Level 4](level-4.md#hb1603) | [Level 4](level-4.md#hb2407) |
| [5](level-5.md) | Beyond the road map | Analysis products, here per-dive SVCodes | [Level 5](level-5.md) | stops at level 4 |

Calibration data is a stream of its own: it starts from the manufacturer's
calibration files rather than the raw data, and joins the main pipeline at
level 2. Its preparation runs in level 0 of these examples. See
[Calibration](../calibration/index.md).

## The two example surveys

[HB1603 dive analysis](hb1603-dive-analysis.md)
:   NOAA ship Henry B. Bigelow, 2016, Simrad EK60. Clusters the water column
    around 13 sperm whale dives and codes each dive by what the whale passed
    through. Raw files are streamed straight from NCEI. Built on a shared
    survey cache: levels 0 to 2a are computed once per cruise and reused.

[HB2407 herring, per file](hb2407-herring-per-file.md)
:   Henry B. Bigelow, 2024, Simrad EK80. Clusters a herring survey day. Raw
    files come from a Google Cloud bucket, each is calibrated and masked on its
    own, and the survey is merged before noise removal.

Both are documentation copies of recipes in
[AA-SI_Full_Pipeline_Example](https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example),
regrouped so that each level is one file. Every step is computed exactly as in
the originals.

## Reading a level page

Each example on a level page has three tabs:

**Flow diagram**
:   The sub-recipe as a graph. Entry points at the top are what flows in from
    the recipe's inputs or from other levels; exit points at the bottom are
    results nothing in this level uses, which later levels or the user pick
    up. Click a step to expand it and see its parameters and the types of its
    inputs and outputs; click its op name for the op's reference page.
    Shaded groups run once per raw file. Hover over a step to highlight what
    it connects to.

    Drag to move around, and hold Ctrl (⌘ on a Mac) while scrolling, or
    pinch, to zoom. The toolbar zooms, fits the whole recipe in view, expands
    or collapses every step, and opens the diagram full screen, where plain
    scrolling zooms and Esc returns to the page. Drag the bottom-right corner
    of the diagram to make it taller.

    Each step's recipe inputs are listed in one box above it, with what the
    full recipe sets each one to, and its results used later in one box below
    it. The steps of other recipes that feed this one or read what it
    produces are drawn in pink, each linking to its own page; **Show other
    recipe steps** hides them on every diagram at once, and the choice is
    remembered.

**YAML**
:   The sub-recipe file itself, with the comments that explain its choices.

**Notebook**
:   The Python the recipe manager generates for these steps with
    `aa-recipe generate`, cut from the full recipe's notebook, which can be
    downloaded from the tab.

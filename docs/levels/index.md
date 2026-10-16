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

[HB1603 survey echogram pyramid](hb1603-survey-echogram-pyramid.md)
:   The whole HB1603 cruise as one EchogramGL viewer store, from the same
    shared survey cache.

[HB2407 herring, per file](hb2407-herring-per-file.md)
:   Henry B. Bigelow, 2024, Simrad EK80. Clusters a herring survey day. Raw
    files come from a Google Cloud bucket, each is calibrated and masked on its
    own, and the survey is merged before noise removal.

All three are documentation copies of recipes in
[AA-SI_Full_Pipeline_Example](https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example),
the first two regrouped so that each level is one file. Every step is computed
exactly as in the originals.

## Reading a level page

Each example on a level page has three tabs:

**Flow diagram**
:   An interactive, read only diagram of the recipe. A level page shows
    the full recipe drilled into that level. The breadcrumb above names the
    recipe and the level. The **Inputs** box has two sections: **Overrides**,
    the values the full recipe sets where it includes the level, and
    **Inputs**, the ones the level's own file declares, each with its value.
    An input with no default that nothing sets reads **unset**, in red,
    tagged **required**. The outputs box lists what the level hands on and
    which later steps use it, and steps of other levels that this one reads
    are drawn in dashed boxes; double click one to go to it. **Up one level**
    shows the whole recipe, one box per level. Its **Recipe inputs** box
    lists the inputs the full recipe's own file declares, then a count of
    those declared in the levels, and the labels on its lines name what it
    passes to each level. Each level's box counts its steps, overrides and
    inputs; double click it to list the overrides and inputs, and again to
    drill into it. Steps that run once per raw file carry a **per item**
    badge, and the step that gathers their results a **collects** badge. A
    level page draws its part of the per file run as a shaded group. When
    the run maps over one of the level's inputs, that input goes on one line
    into the group rather than on every step's line.

    Click a node to select it; double click a step to list its parameters, or
    use **Expand all** and **Collapse all**. The find box jumps to a step by
    id or op name, and **Highlight connected** fades everything the selected
    node does not connect to. **Diagram** and **YAML** switch the main view
    between the diagram and the recipe file, with the selected step
    highlighted. The toolbar also fits the diagram, toggles the layout
    direction and sets the text size. **Inspector** opens a side panel that
    describes the selected node (its op, with a link to the op's reference
    page, parameters, and inputs and outputs), in either view. **Link to
    this step** in the inspector gives an address, ending in
    `#view=<recipe>:<step>`, that opens the page with that step selected.
    The inspector starts open only when the diagram is wide (1100 px or
    more), and **Full screen** opens it too.

    Drag to move around, and hold Ctrl while scrolling, or pinch on a
    trackpad, to zoom; plain scrolling scrolls the page. **Full screen** opens
    the diagram with its inspector over the whole screen, where plain
    scrolling zooms and Esc returns to the page. Drag the bottom-right corner
    of the diagram to make it taller. Inside the diagram, F fits it and /
    jumps to the find box. Colours follow the site's light and dark switch,
    and the layout direction and text size apply to every diagram and are
    remembered.

**YAML**
:   The sub-recipe file itself, with the comments that explain its choices.

**Notebook**
:   The Python the recipe manager generates for these steps with
    `aa-recipe generate`, cut from the full recipe's notebook, which can be
    downloaded from the tab.

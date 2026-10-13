=== "Flow diagram"

    <div class="recipe-flow" data-recipe="hb1603-level-2" markdown>

    - `extract_cal_params`: [`extract_standardized_cal_params`](../ops/extract_standardized_cal_params.md)
    - `compute_sv`: [`compute_sv`](../ops/compute_sv.md)
    - `compute_transducer_depth`: [`compute_transducer_depth`](../ops/compute_transducer_depth.md)
    - `ep_add_depth`: [`ep_add_depth`](../ops/ep_add_depth.md)
    - `crop_survey_range`: [`crop_range`](../ops/crop_range.md)
    - `dive_sv`: [`select_ping_time_range`](../ops/select_ping_time_range.md)
    - `dive_surface_mask`: [`create_surface_mask`](../ops/create_surface_mask.md)
    - `dive_frequency_mask`: [`create_frequency_mask`](../ops/create_frequency_mask.md)
    - `dive_seabed_line`: [`read_seafloor_line`](../ops/read_seafloor_line.md)
    - `dive_seabed_mask`: [`create_seafloor_mask`](../ops/create_seafloor_mask.md)
    - `dive_combine_masks`: [`combine_masks`](../ops/combine_masks.md)
    - `dive_apply_mask`: [`apply_sv_mask`](../ops/apply_sv_mask.md)
    - `dive_remove_noise`: [`remove_background_noise`](../ops/remove_background_noise.md)

    </div>

=== "YAML"

    [Download `processing_lvl_2_combined.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_2_combined.yaml)

    ```yaml
    # Documentation copy combining AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/
    # processing_lvl_2_survey.yaml (level 2a) and processing_lvl_2_user.yaml (level 2b) into one file.
    # The original 2b re-added depth in a second ep_add_depth step (dive_depth) as range from the
    # transducer; this copy drops it and keeps 2a's surface-referenced depth throughout.

    # Processing level 2: calibrated Sv, then masks and noise removal, per file.
    #
    #   2a  extract_cal_params -> compute_sv -> compute_transducer_depth
    #                          -> ep_add_depth -> crop_survey_range
    #   2b  dive_sv -> surface + frequency + seabed masks -> apply -> remove noise
    #
    # The working recipes keep 2a and 2b in separate files because 2a is the
    # survey's shared artifact: computed once per cruise, checkpointed at
    # crop_survey_range, and loaded from the shared cache by every analysis. 2b is
    # the analyst's own and is varied freely. This copy puts them in one file so
    # the whole level reads as one recipe. The 2a steps are wired exactly as in
    # the original, so they hash the same and load from the survey cache.
    #
    # ONE VERTICAL REFERENCE
    #
    #   ep_add_depth adds depth below the sea surface: echo_range plus the file's
    #   transducer depth and per-ping heave. Everything after it uses that depth,
    #   so the surface mask, the Echoview seabed and dive lines, and the level 3
    #   grid all share the sea-surface reference.
    #
    # WHAT THE STEPS MAP OVER
    #
    #   per_file_source, an input the including recipe must set, normally
    #   ${query_ncei.raw_urls}. It must be the value levels 1 and 3 are given, or
    #   the per-file chain splits. The include substitutes it into map_over
    #   textually, so the fingerprinted map_over is the value passed.
    #
    # WHY THERE IS NO FAN-IN AT THIS LEVEL
    #
    #   range_sample is not a shared axis across HB1603. max_range_m is a ceiling,
    #   not a fixed length, and the cruise runs sample intervals differing by about
    #   11x, so the per-file Sv comes back with 17 distinct range_sample lengths.
    #   Concatenating on ping_time would NaN-pad all of them up to the longest.
    #   Files are merged in level 3, once they are on a shared depth grid.
    #
    # WHERE 2b STARTS
    #
    #   dive_sv reads ep_add_depth's uncropped Sv, so the dives see the whole water
    #   column down to the seabed line (up to 2343 m on these dives). The 1800 m
    #   crop_survey_range output is the shared survey artifact and is not consumed
    #   by the dive analysis; it stays so the survey cache is addressed.
    #
    # WHY THE SEABED IS MASKED BEFORE NOISE REMOVAL
    #
    #   remove_background_noise estimates the floor by taking a minimum over
    #   range, so what sits below the seabed feeds it. Six of the 13 dives have the
    #   seabed inside the 1800 m crop, so the seabed is masked first. It cannot go
    #   in the survey tier: the seabed lines cover only the six dive dates, and a
    #   ping with no line depth would be masked out entirely. seabed_min_coverage
    #   turns any gap into a failed step rather than a missing dive.

    recipe:
      name: "hb1603_processing_lvl_2"
      description: "Per file: calibration, Sv and depth (2a), then dive-window selection, surface, frequency and seabed masks, and noise removal (2b)."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      per_file_source:
        type: str
        description: >
          The ${step.output} list the per-file steps map over, one instance per
          entry. Must be overridden by the including recipe, with the same value
          for every level that shares the chain. Normally ${query_ncei.raw_urls}.
      calibration_dict:
        type: dict
        description: "Standardized calibration data, from build_cal_mapping."
      mapping_dict:
        type: dict
        description: "Raw channel to calibration key mapping, from build_cal_mapping."
      crop_max_range_m:
        type: float
        default: 1800.0
        description: "Fixed range ceiling of the shared survey Sv. A different ceiling is a full recompute."
      dive_windows:
        type: list
        description: >
          Window dicts, one per dive, each with start and end. Wire it to
          plan_dives.windows. Any list of windows works.
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Ping-time bin the level 3 grid is built on. dive_sv widens each window to
          whole bins of this width so the first and last bins of a dive are
          averaged over all of their pings.
      surface_depth_m:
        type: float
        default: 10.0
        description: "Range below the transducer above which samples are masked out, in metres."
      frequencies_to_mask:
        type: list
        default: [70, 120, 200]
        description: "Channel frequencies in kHz to drop before binning."
      seabed_lines_dir:
        type: path
        default: "./Auxiliary/seabed_lines"
        description: "Folder of half-day Echoview seabed exports."
      seabed_min_coverage:
        type: float
        default: 0.99
        description: >
          Fraction of a file's selected pings that must get a seabed depth, or the
          step fails.
      seafloor_buffer_m:
        type: float
        default: 10.0
        description: "Metres above the seabed line that are also masked out."
      noise_range_sample_num:
        type: int
        default: 10
        description: "Range samples per noise-estimation bin."
      noise_ping_num:
        type: int
        default: 5
        description: "Pings per noise-estimation bin."
      noise_snr_threshold:
        type: str
        default: "3.0dB"
        description: >
          Samples whose corrected Sv sits less than this above the noise estimate
          are masked. The NEFSC reference analysis config sets 6.0dB.
      noise_background_max:
        type: str
        default: null
        description: "Ceiling on the noise estimate. Null leaves it unbounded."

    steps:

      # --- Level 2a: calibrated Sv and depth ------------------------------------

      # Path(url).name gives the bare filename, which is the key the calibration
      # mapping is built under, so a URL resolves the same as a local path would.
      # unmapped_channels is left at its "warn" default, so a channel no
      # calibration file matched falls back to the values the raw file records for
      # it and says so on the terminal.
      - id: extract_cal_params
        op: extract_standardized_cal_params
        map_over: ${inputs.per_file_source}
        inputs:
          calibration_dict: ${inputs.calibration_dict}
          mapping_dict: ${inputs.mapping_dict}
          echodata: ${combine_raw.echodata}
        params:
          raw_file_path: ${_item}

      - id: compute_sv
        op: compute_sv
        map_over: ${inputs.per_file_source}
        checkpoint: always
        inputs:
          echodata: ${combine_raw.echodata}
          cal_params: ${extract_cal_params.cal_params}
          env_params: ${extract_cal_params.env_params}

      # The vertical reference, computed explicitly rather than left to echopype's
      # use_platform_vertical_offsets, which on an EK60 file puts the transducer
      # above the sea surface by twice its draft.
      - id: compute_transducer_depth
        op: compute_transducer_depth
        map_over: ${inputs.per_file_source}
        checkpoint: always
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
          echodata: ${combine_raw.echodata}
        params:
          use_heave: true
          heave_sign: 1.0

      - id: ep_add_depth
        op: ep_add_depth
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
          echodata: ${combine_raw.echodata}
        params:
          depth_offset: ${compute_transducer_depth.transducer_depth}

      # The shared survey artifact. checkpoint: always because read_raw's stores
      # are disposable, so the chain's surviving output must be durable.
      - id: crop_survey_range
        op: crop_range
        map_over: ${inputs.per_file_source}
        checkpoint: always
        inputs:
          ds_Sv: ${ep_add_depth.ds_Sv}
        params:
          max_range_m: ${inputs.crop_max_range_m}
          drop_trailing_all_nan: false
          outlier_sigma: null

      # --- Level 2b: window selection, masks and noise removal ------------------

      # Mapped over every file, it keeps the pings inside any dive window. For a
      # file no window touches it returns nothing, and every step below is skipped
      # for that file.
      - id: dive_sv
        op: select_ping_time_range
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${ep_add_depth.ds_Sv}
        params:
          windows: ${inputs.dive_windows}
          allow_empty: true
          snap_to_bin: ${inputs.ping_time_bin}

      # Masks the top surface_depth_m of range below the transducer: it reads
      # echo_range, not depth.
      - id: dive_surface_mask
        op: create_surface_mask
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_sv.ds_Sv}
        params:
          surface_depth_m: ${inputs.surface_depth_m}

      - id: dive_frequency_mask
        op: create_frequency_mask
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_sv.ds_Sv}
        params:
          frequencies_to_mask: ${inputs.frequencies_to_mask}

      # The folder, read whole, onto this file's selected pings. A ping outside
      # the line's span is NaN, and seabed_min_coverage makes any such ping a
      # failure.
      - id: dive_seabed_line
        op: read_seafloor_line
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_sv.ds_Sv}
        params:
          evl_path: ${inputs.seabed_lines_dir}
          min_coverage: ${inputs.seabed_min_coverage}

      - id: dive_seabed_mask
        op: create_seafloor_mask
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_sv.ds_Sv}
          seafloor_depth: ${dive_seabed_line.seafloor_depth}
        params:
          seafloor_buffer_m: ${inputs.seafloor_buffer_m}

      - id: dive_combine_masks
        op: combine_masks
        map_over: ${inputs.per_file_source}
        inputs:
          masks:
            - ${dive_surface_mask.mask}
            - ${dive_frequency_mask.mask}
            - ${dive_seabed_mask.mask}
        params:
          mode: "and"

      - id: dive_apply_mask
        op: apply_sv_mask
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_sv.ds_Sv}
          mask: ${dive_combine_masks.mask}

      # Noise removal on seabed-masked Sv.
      - id: dive_remove_noise
        op: remove_background_noise
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_apply_mask.ds_Sv}
        params:
          range_sample_num: ${inputs.noise_range_sample_num}
          ping_num: ${inputs.noise_ping_num}
          SNR_threshold: ${inputs.noise_snr_threshold}
          background_noise_max: ${inputs.noise_background_max}
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    !!! note "Shared loop"

        Steps here run inside a per-file loop shared with steps from another level (`read_raw`, `combine_raw`, `dive_mask_sparse`, `dive_file_mvbs`, `dive_cell_stats`), so the whole loop is shown.

    #### Step: `extract_cal_params`
    **Op:** `extract_standardized_cal_params`

    Extract calibration parameters (gain, sa-correction, EBA) and environmental parameters (sound speed, absorption) from standardized calibration files, cross-referenced against the EchoData object for channel alignment.

    **Parameters:**
    - `raw_file_path` path - Path to the specific raw file whose channel mapping should be selected from mapping_dict (matched by basename). Wire this to ${_item} in a per-file map_over chain so each instance picks its own file's calibration mapping instead of defaulting to the first file in the survey. Leave unset for whole-survey recipes.

    - `unmapped_channels` str - What to do about a channel the mapping does not cover, which happens when no calibration file matched it. "warn" (the default) reports the channel on the terminal and falls back to the values the raw file itself records, which is what echopype would calibrate that channel with given no calibration file at all. "error" stops the run instead. The fallback needs the echodata input; without it "warn" raises the way "error" would.

    - `output_logs_folder` path - Where the fallback writes its calibration flags. A temporary folder is used when omitted, which is what a per-file chain wants: several files falling back at once would otherwise race on one flags file.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `compute_sv`
    **Op:** `compute_sv`

    Compute volume backscattering strength (Sv) from raw EchoData. When cal_params and env_params are supplied they are applied to the computation. When omitted, echopype uses default environmental and calibration values embedded in the raw file.
    This step is echopype.calibrate.compute_Sv called directly, so the calibration model and its defaults are echopype's.

    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `compute_transducer_depth`
    **Op:** `compute_transducer_depth`

    Compute per-ping transducer depth in metres below the water surface from the EK Platform group, for feeding ``ep_add_depth`` as its ``depth_offset``.
    Reads the Platform group directly rather than going through ``ep_add_depth(use_platform_vertical_offsets=true)``, which gets the sign wrong on both EK families for different reasons.
    On an EK80 drop keel vessel the transducers ride on a retractable keel and ``drop_keel_offset`` records how far it was lowered, while the ``transducer_offset_z`` and ``water_level`` fields echopype's formula reads are left at zero. echopype carries ``drop_keel_offset`` for provenance and never reads it.
    An EK60 file has no ``drop_keel_offset`` at all, and echopype maps the ER60 per-ping transducer depth into ``water_level``, whose value its formula then subtracts (``transducer_offset_z - (water_level + vertical_offset)``, with ``transducer_offset_z`` zero because the ER60 does not populate it). The transducer comes out above the water surface and every depth is short by twice the draft. This step reads ``water_level`` directly instead.
    ``drop_keel_offset`` is preferred when both are present; the field actually used is recorded in the output's ``static_offset_field`` attr.
    The static position is per file and heave is per ping, so the result is ``static offset + datum_correction_m + heave_sign * vertical_offset``. Heave is interpolated onto ds_Sv's ping_time here, which also means ``ep_add_depth`` passes the array straight through instead of resampling it again with nearest neighbour.
    The output is small (one value per ping) and is the record of what vertical reference a run actually used, so checkpointing it is cheap and makes the reference auditable without recomputing Sv.

    **Parameters:**
    - `datum_correction_m` float (m) - Metres added to the static offset to reach depth below the water surface, for when the recorded offset is measured from a hull datum rather than the waterline. Positive pushes the transducer deeper. The default 0.0 takes the recorded offset as already waterline referenced.

    - `use_heave` bool - Add the per-ping ``vertical_offset`` (heave) term. False holds the static keel depth on every ping. When the Platform group carries no usable heave the step reports that and falls back to the static depth.

    - `heave_sign` float - Sign applied to ``vertical_offset``. The default 1.0 was determined empirically on HB2407 (EK80, Henry B. Bigelow) by correlating raw heave against the hand-verified EVL seabed line: the correct sign left a 0.14 m residual against 0.75 m for the wrong one. The convention is not recorded in the file attributes and is not the same sign echopype's own platform-vertical-offsets formula uses (that formula is referenced from a hull datum, not drop_keel_offset) - re-verify the same way on a new vessel or instrument setup rather than assuming.

    - `interp_method` str - Interpolation used to put heave on ping_time. Heave is typically sampled several times coarser than the ping rate, so the default linear is preferred over nearest.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `ep_add_depth`
    **Op:** `ep_add_depth`

    Add a surface-referenced ``depth`` data variable to an Sv Dataset. This step is echopype.consolidate.add_depth called directly, so the depth computation and its parameters are echopype's. The result preserves all existing variables (including ``echo_range``) and adds ``depth`` on ``(channel, ping_time, range_sample)`` in metres below surface.
    Whatever depth-computation technique is selected (constant ``depth_offset``, Platform vertical offsets, Platform/Beam angles, user-supplied ``tilt``) is fully captured in the values of the resulting ``depth`` array, so downstream consumers can treat the dataset as the single source of truth for vertical reference frame without knowing which technique was used.

    **Parameters:**
    - `depth_offset` (m) - Vertical offset for the transducer position, in metres. Either a constant, or a per-ping DataArray wired from a producer step such as ``compute_transducer_depth`` (echopype accepts both on this argument; a DataArray must have exactly one dimension and is aligned to ping_time). When set, takes precedence over Platform vertical offsets. Left untyped because the two accepted forms are a scalar and a DataArray.

    - `tilt` float (degree) - Constant transducer tilt; 0 corresponds to a vertically-pointing transducer. When set, takes precedence over Platform/Beam angles.

    - `downward` bool - True when transducers point downward (typical hull-mounted).
    - `use_platform_vertical_offsets` bool - Use Echodata Platform group vertical offsets to compute transducer depth. EK60/EK80 only. Ignored when ``depth_offset`` is set.

    - `use_platform_angles` bool - Use Echodata Platform group angles to compute echo_range scaling. EK60/EK80 only. Cannot be combined with ``use_beam_angles``.

    - `use_beam_angles` bool - Use Echodata Beam group angles to compute echo_range scaling. EK60/EK80 only. Cannot be combined with ``use_platform_angles``.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `crop_survey_range`
    **Op:** `crop_range`

    Trim trailing range samples that carry no data. A raw file records to the sonar's configured range, so after seafloor masking the deep end of ``range_sample`` is entirely NaN. Those samples still cost memory and time in every downstream step, and they widen the binned cell grid that ``compute_mvbs`` and ``compute_per_cell_statistics`` build - the flox reduction behind the latter allocates dense bookkeeping sized by channels x ping bins x range bins, so an over-wide range axis is what pushes it into a MemoryError on a long survey.
    A standalone step rather than a parameter on ``apply_sv_mask``, for the same reason ``rechunk_dataset`` is standalone: masking is ping-local and runs correctly per file, but the crop point is a property of the whole survey. Cropping per file would leave the segments with different ``range_sample`` lengths, and the ping_time concatenation resolves that by padding the short ones back out with NaN - which undoes the crop and leaves NaN in ``echo_range``, where downstream range binning reads it. Place this after the fan-in instead.
    Three criteria are available and compose - whichever are active each produce a candidate cut and the shallowest wins:
    * ``outlier_sigma`` rejects anomalous pings and then crops to the deepest
      **retained** ping. Prefer this survey-wide: a single glitch ping reaching
      far past the seafloor otherwise holds the whole range axis open, which is
      exactly what defeats the lossless mode. The threshold is deliberately
      generous, so uncommon-but-real depths survive and only wild outliers go.
    * ``max_range_m`` is a hard ceiling, applied whether or not samples above it
      hold data. Cheapest when the answer is already known (e.g. from the
      deepest point in the survey's seabed lines).
    * ``drop_trailing_all_nan`` is the lossless fallback used when no outlier
      test is requested.

    **Parameters:**
    - `max_range_m` float (m) - Explicit cap. Range samples deeper than this are dropped whether or not they hold data, so a cap shallower than the deepest echo of interest discards it. Leave unset to derive the crop from the data.

    - `outlier_sigma` float - Standard deviations above the mean, over the per-ping deepest finite sample, at which a ping becomes a candidate outlier. Combined with outlier_margin to form the reject threshold. Leave unset to disable outlier rejection and fall back to drop_trailing_all_nan.

    - `outlier_margin` float - Multiplier applied to (mean + outlier_sigma * sd) to get the reject threshold. Raise it to flag fewer pings, lower it to flag more. The default 1.5 is deliberately loose: on HB2407's 2024-10-15 window it puts the threshold at 424 m against a deepest retained ping of 214 m, so it catches only genuine anomalies. Only used when outlier_sigma is set.

    - `drop_trailing_all_nan` bool - Crop to one past the deepest range sample holding a finite Sv value anywhere in the dataset. Lossless. Ignored when outlier_sigma is set. Set false with the other two criteria unset to disable the step without removing it from the recipe.

    - `data_var` str - Variable inspected for finite values.
    - `range_var` str - Variable supplying each sample's range in metres.

    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_sv`
    **Op:** `select_ping_time_range`

    Narrow a Dataset to a ping_time window. The user-facing entry point into a survey-wide Sv store: a survey-level recipe computes and checkpoints Sv once for the whole cruise, and a user recipe reuses that checkpoint and calls this to work on its own segment.
    Cheap on a zarr-backed Dataset. ``sel`` narrows the dask graph to the chunks the window touches, so only those are read - measured on a 39570-ping store with 3305-ping chunks, a 4001-ping window read 4 of 24 partitions in 1.5 s and 1.4 GB against 16.5 s and 8.9 GB for the whole array. Chunk the survey store along ping_time (see ``rechunk_dataset``) at roughly the window size users ask for.
    PLACEMENT MATTERS. Step hashes are a Merkle chain: each step folds in its parents' hashes. Wiring a user's window into an upstream step's params - for example ``initial_setup``'s file_time_start - changes that step's hash and every hash below it, so no two windows ever share a cached Sv. Keep the window on this step, downstream of everything whose checkpoint you want shared, and give the survey-level steps a fixed survey-wide window instead.

    **Parameters:**
    - `start` str - Inclusive ISO datetime lower bound (e.g. "2024-10-15T13:38:52"). Leave unset to leave the start open.

    - `end` str - Inclusive ISO datetime upper bound. Leave unset to leave the end open. The step fails rather than returning an empty Dataset when the window selects no pings.

    - `window` dict - Mapping supplying start and end instead of passing them directly. Mutually exclusive with them: giving both is an error rather than a silent precedence rule.
    This is the form a mapped fan-out needs. ${_item} is an all-or-nothing token with no attribute access - there is no ${_item.start} - so a step mapped over a list of window dicts cannot pull the two bounds out of the item as separate params and has to take the dict itself. Wire it as ``window: ${_item}``.
    Extra keys are ignored, so one dict can carry everything an instance needs (a label, its line files, its raw file list) while this step reads only the bounds. An optional ``label`` key is printed to identify the instance in the run log.

    - `dim` str - Time coordinate to slice. Binned products carry the bin's left edge under a different name, so a compute_per_cell_statistics dataset is windowed with "cell_ping_time" while raw Sv uses the default. The coordinate has to be datetime-valued either way, which the cell grid's left edges are.

    - `windows` list - Several window dicts at once, each with ``start`` and ``end`` as the single ``window`` form takes. Keeps every ping inside at least one of them, in time order, each ping once even where windows overlap.
    This is how a step mapped over the survey's per-file Sv pulls a whole set of dive windows out of each file in one pass: the instance sees one file and cannot loop over the list itself. Wire it straight from the planner, ``windows: ${plan_dives.windows}``, and pair it with ``allow_empty: true`` so the files no window touches drop out at the fan-in. Mutually exclusive with ``window`` and ``start``/``end``.

    - `snap_to_bin` str - Bin width such as "10s". Widens every window outward to whole bins of that width before selecting: the start moves back to its bin's left edge and the end forward to the last instant of its bin.
    Set it to the downstream ping_time_bin when this selection feeds compute_mvbs. A window cut at its exact bounds leaves the first and last bins averaged over only part of their pings, and a window that ends just inside a bin can leave that bin with no ping at all, so the grid comes out one bin short. A later selection on the binned grid still trims to the true window.

    - `allow_empty` bool - Return nothing instead of failing when the window selects no pings.
    This is what lets the step be mapped over a whole survey's per-file Sv rather than applied to one merged store. Most files lie outside any one window, and ``merge_datasets`` drops the empty instances when it collects, so the fan-in keeps exactly the files the window touches. That is the route to a windowed analysis that reuses the per-file checkpoints without a survey-wide merge ever being built.
    Left false by default: against a single merged store, a window that selects nothing is a mistake and should fail loudly.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_surface_mask`
    **Op:** `create_surface_mask`

    Build a surface-interference mask from baseline Sv using a selectable method.

    **Parameters:**
    - `surface_depth_m` float (m) - Depth above which samples are masked out, removing the transmit pulse ringdown and near-surface bubble layer. The default of 0.0 masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_frequency_mask`
    **Op:** `create_frequency_mask`

    Build a mask that excludes selected frequency channels. Implementation can be swapped to use different exclusion criteria.

    **Parameters:**
    - `frequencies_to_mask` list (kHz) - Nominal channel frequencies to exclude, as numbers in kHz (e.g. [70, 120, 200]). Every sample on a listed channel is marked invalid and every other channel is left untouched. The default empty list masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_seabed_line`
    **Op:** `read_seafloor_line`

    Read a seafloor line from an Echoview ``.evl`` line file and align it to the Sv dataset's pings, returning a 1-D (ping_time,) DataArray in metres. Use it when a hand-verified Echoview bottom line is more trustworthy than running detection over the Sv data.
    Echoview exports a survey as a series of part-day line files, so ``evl_path`` may be a folder: the files whose names span ``file_time_start`` to ``file_time_end`` are selected and concatenated into one line before interpolation. Give it the same window that selected the raw files.
    Drop-in swap with the ``detect_seafloor`` and ``ep_detect_seafloor`` ops: the output port name (``seafloor_depth``) and shape (1-D, ping_time) match, so downstream steps such as ``create_seafloor_mask`` work unchanged. The recipe step's ``inputs`` drop the ``echodata`` line (no detection is run) and the ``params`` block takes ``evl_path`` plus the alignment settings below instead of ``channel`` / ``min_valid_depth_m`` or ``method`` / ``method_params``.
    Pings the line does not cover come back as NaN, and ``create_seafloor_mask`` rejects **every sample** of a ping whose seafloor is NaN (the metres-vs-metres comparison is False against NaN). The step therefore prints its ping coverage on every run; set ``min_coverage`` to turn a shortfall into a hard error, or widen ``edge_extend_s`` / ``max_gap_s`` if the gaps are benign.

    **Parameters:**
    - `evl_path` path - A single Echoview ``.evl`` line file, or a folder of them to select from with file_time_start / file_time_end. May be a local path or a remote fsspec URL (e.g. gs://bucket/survey/seabed_lines); remote files are read in place (no download) since line files are small. The must_exist check is skipped at validation time for remote URLs and enforced by the step at run time instead.
    Mutually exclusive with ``window``: exactly one of the two is required.

    - `window` dict - Mapping carrying the line path under ``window_key``, used instead of ``evl_path``.
    This is the form a mapped fan-out needs. ${_item} is an all-or-nothing token with no attribute access - there is no ${_item.seabed_evl} - so a step mapped over a list of window dicts cannot pull the path out of the item as its own param and has to take the dict. Wire it as ``window: ${_item}``.
    A window also supplies file_time_start / file_time_end when those are not given directly, so a folder of part-day seabed exports narrows to the window without repeating its bounds. Extra keys are ignored, and an optional ``label`` names the instance in errors.
    Note that a window's path is NOT content-fingerprinted the way evl_path is: it arrives as a runtime value rather than a declared param, so re-exporting that line does not by itself invalidate the step. The upstream step that produced the window is what carries its identity.

    - `window_key` str - Key the line path is read from when ``window`` is given. Defaults to "seabed_evl", which is what plan_dive_datasets emits.

    - `vertical_reference` str - What the EVL depths are measured from. "surface" (the default, and Echoview's usual seabed-line reference) means metres below the water surface; "transducer" means metres along the beam from the transducer face. The line is converted to whichever reference ds_Sv carries - depth when ep_add_depth ran upstream, otherwise echo_range.

    - `min_status` int - Drop line points whose Echoview status is below this value (0 = none, 1 = unverified, 2 = bad, 3 = good). When null (the default) every point is kept. Dropped points leave a gap in the line, subject to max_gap_s.

    - `max_gap_s` float (s) - Widest hole in the line, in seconds, to interpolate across; pings sitting inside a wider hole come back as NaN. A ping landing exactly on a line point always keeps that point's depth. When null (the default) holes of any width are interpolated across.

    - `edge_extend_s` float (s) - How many seconds past the line's first and last point to hold that point's depth. The default 0.0 does no extrapolation at all, so pings outside the line's time span are NaN - this is what keeps a line covering only part of a survey from silently asserting a constant seafloor over the rest. Set null to hold the end depths indefinitely.

    - `depth_offset_m` float (m) - Constant metres added to the line, for when the transducer draft configured in Echoview differs from the one baked into ds_Sv['depth']. Positive pushes the seafloor deeper.

    - `min_coverage` float - Fraction of pings (0-1) that must end up with a finite seafloor depth; below it the step fails instead of silently masking the uncovered pings away. Default 0.0 never fails.

    - `file_time_start` str - Optional inclusive ISO datetime (e.g. "2024-10-12T00:15") selecting which line files in an evl_path folder to read, by the d{YYYYMMDD}_t{HHMMSS}-t{HHMMSS} span in their names. Pass the same window that selected the raw files so the line and the pings stay in step.
    A file's span runs from its own name stamp to the *next* file's, not to the end stamp in its own name - Echoview's end stamp under-reports the line's real last point by about one raw file's duration, so trusting it would drop the export straddling the window start and leave the leading pings with no seafloor. Selection is name-based, so no line data is read (or downloaded) to filter. Ignored when evl_path names a single file.

    - `file_time_end` str - Optional inclusive ISO datetime upper bound for line file selection. See file_time_start.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_seabed_mask`
    **Op:** `create_seafloor_mask`

    Build a seafloor mask from baseline Sv and detected seafloor depth. The seafloor depth is passed as an explicit input, enabling swappable detection methods without modifying masking logic.

    **Parameters:**
    - `seafloor_buffer_m` float (m) - Height above the detected seafloor line to also mask out. Widens the exclusion zone to cover detection error and the near-bottom dead zone, at the cost of discarding real backscatter just above the bottom.

    - `range_var` str - Name of the meter-valued variable on ds_Sv to compare against the seafloor line. Must use the same vertical reference as the seafloor line. When null (the default), auto-selects "depth" if present on ds_Sv (i.e., add_depth was run upstream), otherwise falls back to "echo_range".


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_combine_masks`
    **Op:** `combine_masks`

    Combine two or more mask datasets into one final mask. Accepts a list of mask inputs to support inserting additional custom mask-producing steps into the chain without changing core framework code.

    **Parameters:**
    - `mode` str - Combination mode: 'and' (all must be valid) or 'or' (any must be valid)

    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_apply_mask`
    **Op:** `apply_sv_mask`

    Apply a boolean mask to an Sv Dataset, setting masked samples to NaN.
    Thin wrapper over echopype.mask.apply_mask, calling it against the Sv variable with NaN as the fill value. It adds nothing of its own, so the broadcasting and alignment rules are echopype's.

    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_remove_noise`
    **Op:** `remove_background_noise`

    Estimate and remove background noise from Sv using the De Robertis and Higginbottom method. Returns a cleaned Sv Dataset.
    Thin wrapper over echopype.clean.remove_background_noise. Every parameter below is forwarded to it unchanged, so its behavior is echopype's. The one thing the wrapper adds is that the corrected values are copied over the Sv variable, so downstream steps reading Sv get the cleaned data without having to know about Sv_corrected. Both variables are present on the result.

    **Parameters:**
    - `ping_num` int - Number of pings used to estimate the noise level
    - `range_sample_num` int - Number of range samples used to estimate the noise level
    - `background_noise_max` str - Upper limit on the estimated noise level (e.g. '-125dB'); omit to leave it unbounded
    - `SNR_threshold` str - Signal-to-noise ratio below which corrected samples are masked out (e.g. '3.0dB')

    *Runs once per `map_over` instance (fan-out loop).*

    ```python
    raw_stores = []
    echodata = []
    cal_params = []
    env_params = []
    other_params = []
    compute_sv_ds_Sv = []
    transducer_depth = []
    ep_add_depth_ds_Sv = []
    crop_survey_range_ds_Sv = []
    dive_sv_ds_Sv = []
    dive_surface_mask_mask = []
    dive_frequency_mask_mask = []
    seafloor_depth = []
    dive_seabed_mask_mask = []
    dive_combine_masks_mask = []
    dive_apply_mask_ds_Sv = []
    dive_remove_noise_ds_Sv = []
    dive_mask_sparse_ds_Sv = []
    ds_MVBS = []
    dive_cell_stats_ds_Sv = []
    for _item in raw_urls:
        _iter_raw_stores = read_raw_files_to_stores(
        raw_file_paths=[_item],
        sonar_model='EK60',
        include_bot=False,
        intermediate_format='zarr',
    )
        raw_stores.append(_iter_raw_stores)
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores)
        echodata.append(_iter_echodata)
        _result = extract_standardized_calibration_parameters(
        calibration_dict=calibration_dict,
        mapping_dict=mapping_dict,
        echodata=_iter_echodata,
        raw_file_path=_item,
    )
        _iter_cal_params = _result['cal_params']
        _iter_env_params = _result['env_params']
        _iter_other_params = _result['other_params']
        cal_params.append(_iter_cal_params)
        env_params.append(_iter_env_params)
        other_params.append(_iter_other_params)
        _iter_compute_sv_ds_Sv = compute_Sv(
        echodata=_iter_echodata,
        cal_params=_iter_cal_params,
        env_params=_iter_env_params,
    )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)
        _iter_transducer_depth = compute_transducer_depth(
        ds_Sv=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        use_heave=True,
        heave_sign=1.0,
    )
        transducer_depth.append(_iter_transducer_depth)
        _iter_ep_add_depth_ds_Sv = add_depth(
        ds=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        depth_offset=_iter_transducer_depth,
    )
        ep_add_depth_ds_Sv.append(_iter_ep_add_depth_ds_Sv)
        _iter_crop_survey_range_ds_Sv = crop_range_samples(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        max_range_m=1800.0,
        drop_trailing_all_nan=False,
        outlier_sigma=None,
    )
        crop_survey_range_ds_Sv.append(_iter_crop_survey_range_ds_Sv)
        _iter_dive_sv_ds_Sv = select_ping_time_range(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        windows=windows,
        allow_empty=True,
        snap_to_bin='10s',
    )
        dive_sv_ds_Sv.append(_iter_dive_sv_ds_Sv)
        _iter_dive_surface_mask_mask = create_surface_mask(ds_Sv=_iter_dive_sv_ds_Sv, surface_depth_m=10.0)
        dive_surface_mask_mask.append(_iter_dive_surface_mask_mask)
        _iter_dive_frequency_mask_mask = create_frequency_mask(ds_Sv=_iter_dive_sv_ds_Sv, frequencies_to_mask=[70, 120, 200])
        dive_frequency_mask_mask.append(_iter_dive_frequency_mask_mask)
        _iter_seafloor_depth = read_seafloor_line_evl(
        ds_Sv=_iter_dive_sv_ds_Sv,
        evl_path='./Auxiliary/seabed_lines',
        min_coverage=0.99,
    )
        seafloor_depth.append(_iter_seafloor_depth)
        _iter_dive_seabed_mask_mask = create_seafloor_mask(
        ds_Sv=_iter_dive_sv_ds_Sv,
        seafloor_depth=_iter_seafloor_depth,
        seafloor_buffer_m=10.0,
    )
        dive_seabed_mask_mask.append(_iter_dive_seabed_mask_mask)
        _iter_dive_combine_masks_mask = combine_masks(masks=[_iter_dive_surface_mask_mask, _iter_dive_frequency_mask_mask, _iter_dive_seabed_mask_mask], mode='and')
        dive_combine_masks_mask.append(_iter_dive_combine_masks_mask)
        _iter_dive_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_dive_sv_ds_Sv, mask=_iter_dive_combine_masks_mask)
        dive_apply_mask_ds_Sv.append(_iter_dive_apply_mask_ds_Sv)
        _iter_dive_remove_noise_ds_Sv = remove_noise(
        ds_Sv=_iter_dive_apply_mask_ds_Sv,
        range_sample_num=10,
        ping_num=5,
        SNR_threshold='6.0dB',
        background_noise_max='-66dB',
    )
        dive_remove_noise_ds_Sv.append(_iter_dive_remove_noise_ds_Sv)
        _iter_dive_mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=_iter_dive_remove_noise_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        nan_threshold=0.99,
    )
        dive_mask_sparse_ds_Sv.append(_iter_dive_mask_sparse_ds_Sv)
        _iter_ds_MVBS = compute_MVBS(
        ds_Sv=_iter_dive_mask_sparse_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        range_var_max='3020m',
    )
        ds_MVBS.append(_iter_ds_MVBS)
        _iter_dive_cell_stats_ds_Sv = compute_per_cell_statistics(
        ds_Sv=_iter_dive_remove_noise_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        range_var_max='3020m',
        statistics=['cv'],
        cells_only=True,
    )
        dive_cell_stats_ds_Sv.append(_iter_dive_cell_stats_ds_Sv)
    ```

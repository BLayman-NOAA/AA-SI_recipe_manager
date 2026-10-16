=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-2" data-drill="inc:sub_recipes/processing_lvl_2_per_file.yaml" markdown>

    - `extract_cal_params`: [`extract_standardized_cal_params`](../ops/extract_standardized_cal_params.md)
    - `compute_sv`: [`compute_sv`](../ops/compute_sv.md)
    - `compute_transducer_depth`: [`compute_transducer_depth`](../ops/compute_transducer_depth.md)
    - `ep_add_depth`: [`ep_add_depth`](../ops/ep_add_depth.md)
    - `detect_seafloor`: [`read_seafloor_line`](../ops/read_seafloor_line.md)
    - `create_seafloor_mask`: [`create_seafloor_mask`](../ops/create_seafloor_mask.md)
    - `create_surface_mask`: [`create_surface_mask`](../ops/create_surface_mask.md)
    - `combine_masks`: [`combine_masks`](../ops/combine_masks.md)
    - `apply_mask`: [`apply_sv_mask`](../ops/apply_sv_mask.md)
    - `merge_sv`: [`merge_datasets`](../ops/merge_datasets.md)
    - `crop_range`: [`crop_range`](../ops/crop_range.md)
    - `rechunk_before_noise`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `remove_noise`: [`remove_background_noise`](../ops/remove_background_noise.md)

    </div>

=== "YAML"

    [Download `processing_lvl_2_per_file.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_2_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # sub_recipes/processing_lvl_2_per_file.yaml, with extract_cal_params moved here
    # from level 1 to match the HB1603 levels.

    # Processing level 2 (per file): Sv and masks per file, then the survey fan-in.
    #
    #   [per file] extract_cal_params -> compute_sv -> compute_transducer_depth
    #              -> ep_add_depth -> detect_seafloor -> masks -> apply_mask
    #   [fan-in]   merge_sv, concat along ping_time
    #   [survey]   crop_range -> rechunk_before_noise -> remove_noise
    #
    # Include this level immediately after processing_lvl_1_per_file.yaml, since it
    # continues the same mapped chain. The per-file steps are all ping-local, so the
    # merge can wait until remove_noise, the first step that needs the whole survey.
    #
    # ep_add_splitbeam_angle is commented out: HB2407's calibration has no
    # angle_sensitivity, so echopype raises TypeError. Nothing downstream needs it.

    recipe:
      name: "processing_lvl_2_per_file"
      description: "Per-file Sv and masking, fan-in to survey Sv, then noise removal."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      echodata:
        type: echodata
        description: "Per-file EchoData from level 1."
      calibration_dict:
        type: dict
        description: "Standardized calibration data, from gen_cal_mapping."
      mapping_dict:
        type: dict
        description: "Raw channel to calibration key mapping, from gen_cal_mapping."
      datum_correction_m:
        type: float
        default: 0.0
        description: "Metres added to each file's drop keel offset to give depth below the waterline."
      use_heave:
        type: bool
        default: true
        description: "Add the per-ping platform heave term to transducer depth."
      heave_sign:
        type: float
        default: 1.0
        description: "Sign applied to platform heave. +1.0 fits the EVL seabed line best on HB2407."
      seabed_line_folder:
        type: path
        description: >
          Folder of Echoview .evl seabed line exports. The files covering the time
          window are combined into the seafloor; a single .evl file is read whole.
      file_time_start:
        type: str
        required: false
        description: >
          Optional inclusive ISO start time selecting seabed line files by the time
          span in their names. Pass the window that selected the raw files.
      file_time_end:
        type: str
        required: false
        description: "Optional inclusive ISO datetime upper bound; see file_time_start."
      seabed_line_max_gap_s:
        type: float
        default: 600
        description: >
          Widest gap in the combined seabed line to interpolate across, in seconds.
          Only a break between survey legs is this wide.
      seabed_line_min_coverage:
        type: float
        default: 0.99
        description: >
          Fraction of a file's pings the line must cover, or the step fails. Per
          file this is stricter than it looks. Lower it or widen the window rather
          than let uncovered pings through, since all their samples get masked.
      surface_mask_depth:
        type: float
        default: 10.0
        description: "Depth in meters above which the surface mask excludes samples."
      seafloor_buffer_m:
        type: float
        default: 1.0
        description: "Height of buffer above seabed line to mask."
      crop_outlier_sigma:
        type: float
        required: false
        description: >
          Standard deviations above the mean deepest sample at which a ping counts
          as an outlier; the crop lands on the deepest kept ping. Unset falls back
          to the lossless trim below, which one glitch ping defeats.
      crop_outlier_margin:
        type: float
        default: 1.5
        description: >
          Multiplier on (mean + crop_outlier_sigma * sd) giving the reject
          threshold. Raise it to flag fewer pings.
      crop_trailing_all_nan:
        type: bool
        default: true
        description: >
          Drop trailing range samples with no finite Sv anywhere. Lossless, and
          ignored when crop_outlier_sigma is set. False with the other two unset
          disables the crop.
      crop_max_range_m:
        type: float
        required: false
        description: >
          Hard depth ceiling for the crop, in metres; the shallowest cut wins. It
          drops data too, so check the crop step's "deepest kept" against the
          seabed line before choosing one.
      noise_range_sample_num:
        type: int
        default: 10
        description: "Range samples per bin for background noise estimation."
      noise_ping_num:
        type: int
        default: 5
        description: "Pings per bin for background noise estimation."
      noise_chunk_ping_time:
        type: int
        default: 1000
        description: >
          Ping-time chunk size of the merged Sv before remove_noise, so the noise
          estimate runs in dask chunks. Keep it a multiple of noise_ping_num.

    steps:

      # Per-file chain, continuing the map_over from level 1.

      # Picks this file's calibration out of the survey mapping.
      - id: extract_cal_params
        op: extract_standardized_cal_params
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          calibration_dict: ${inputs.calibration_dict}
          mapping_dict: ${inputs.mapping_dict}
          echodata: ${inputs.echodata}
        params:
          raw_file_path: ${_item}

      - id: compute_sv
        op: compute_sv
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: always
        inputs:
          echodata: ${inputs.echodata}
          cal_params: ${extract_cal_params.cal_params}
          env_params: ${extract_cal_params.env_params}
          waveform_mode: "CW"
          encode_mode: "power"

      # Drop keel offset plus per-ping heave. Checkpointed so the vertical reference
      # the run used can be inspected later.
      - id: compute_transducer_depth
        op: compute_transducer_depth
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: always
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
          echodata: ${inputs.echodata}
        params:
          datum_correction_m: ${inputs.datum_correction_m}
          use_heave: ${inputs.use_heave}
          heave_sign: ${inputs.heave_sign}

      # Adds the surface-referenced 'depth' the seabed steps compare against. Without
      # it, create_seafloor_mask silently offsets the mask by the transducer depth.
      - id: ep_add_depth
        op: ep_add_depth
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
          echodata: ${inputs.echodata}
        params:
          depth_offset: ${compute_transducer_depth.transducer_depth}

      # Disabled, see the file header.
      # - id: ep_add_splitbeam_angle
      #   op: ep_add_splitbeam_angle
      #   map_over: ${initial_setup.raw_file_paths}
      #   inputs:
      #     ds_Sv: ${ep_add_depth.ds_Sv}
      #     echodata: ${inputs.echodata}
      #   params:
      #     waveform_mode: "CW"
      #     encode_mode: "power"
      #     to_disk: false

      - id: detect_seafloor
        op: read_seafloor_line
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: save
        inputs:
          ds_Sv: ${ep_add_depth.ds_Sv}
        params:
          evl_path: ${inputs.seabed_line_folder}
          # Same window that selects the raw files, so the line spans the pings.
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          # No extrapolation past the line's ends. Uncovered pings fail min_coverage
          # rather than being masked away.
          edge_extend_s: 0.0
          max_gap_s: ${inputs.seabed_line_max_gap_s}
          min_coverage: ${inputs.seabed_line_min_coverage}

      - id: create_seafloor_mask
        op: create_seafloor_mask
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          # Must carry 'depth' to match the surface-referenced line.
          ds_Sv: ${ep_add_depth.ds_Sv}
          seafloor_depth: ${detect_seafloor.seafloor_depth}
        params:
          seafloor_buffer_m: ${inputs.seafloor_buffer_m}

      # Compares against echo_range, so it takes compute_sv's dataset.
      - id: create_surface_mask
        op: create_surface_mask
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
        params:
          surface_depth_m: ${inputs.surface_mask_depth}

      - id: combine_masks
        op: combine_masks
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          masks:
            - ${create_seafloor_mask.mask}
            - ${create_surface_mask.mask}
        params:
          mode: "and"

      - id: apply_mask
        op: apply_sv_mask
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          ds_Sv: ${ep_add_depth.ds_Sv}
          mask: ${combine_masks.mask}

      # Fan-in: reassemble the survey from the per-file masked Sv.

      - id: merge_sv
        op: merge_datasets
        collect: ${apply_mask.ds_Sv}
        checkpoint: always
        inputs:
          datasets: ${apply_mask.ds_Sv}
        params:
          dim: "ping_time"

      # Survey-wide from here on.

      # Drops the all-NaN range samples below the deepest seabed, shrinking every
      # later step. It must follow the merge, or merge_sv pads them back in.
      - id: crop_range
        op: crop_range
        checkpoint: always
        inputs:
          ds_Sv: ${merge_sv.merged}
        params:
          outlier_sigma: ${inputs.crop_outlier_sigma}
          outlier_margin: ${inputs.crop_outlier_margin}
          drop_trailing_all_nan: ${inputs.crop_trailing_all_nan}
          max_range_m: ${inputs.crop_max_range_m}

      - id: rechunk_before_noise
        op: rechunk_dataset
        inputs:
          ds_Sv: ${crop_range.ds_Sv}
        params:
          ping_time_chunk: ${inputs.noise_chunk_ping_time}

      - id: remove_noise
        op: remove_background_noise
        checkpoint: always
        inputs:
          ds_Sv: ${rechunk_before_noise.ds_Sv}
        params:
          range_sample_num: ${inputs.noise_range_sample_num}
          ping_num: ${inputs.noise_ping_num}
          # Must be a dB string, not a bare number. Lower it to keep more weak data.
          # SNR_threshold: "0dB"
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb). They use variables earlier levels define, so run them from the full notebook.

    !!! note "Shared loop"

        Steps here run inside a per-file loop shared with steps from another level (`read_raw`, `combine_raw`), so the whole loop is shown.

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

    #### Step: `detect_seafloor`
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

    #### Step: `create_seafloor_mask`
    **Op:** `create_seafloor_mask`

    Build a seafloor mask from baseline Sv and detected seafloor depth. The seafloor depth is passed as an explicit input, enabling swappable detection methods without modifying masking logic.

    **Parameters:**
    - `seafloor_buffer_m` float (m) - Height above the detected seafloor line to also mask out. Widens the exclusion zone to cover detection error and the near-bottom dead zone, at the cost of discarding real backscatter just above the bottom.

    - `range_var` str - Name of the meter-valued variable on ds_Sv to compare against the seafloor line. Must use the same vertical reference as the seafloor line. When null (the default), auto-selects "depth" if present on ds_Sv (i.e., add_depth was run upstream), otherwise falls back to "echo_range".


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `create_surface_mask`
    **Op:** `create_surface_mask`

    Build a surface-interference mask from baseline Sv using a selectable method.

    **Parameters:**
    - `surface_depth_m` float (m) - Depth above which samples are masked out, removing the transmit pulse ringdown and near-surface bubble layer. The default of 0.0 masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `combine_masks`
    **Op:** `combine_masks`

    Combine two or more mask datasets into one final mask. Accepts a list of mask inputs to support inserting additional custom mask-producing steps into the chain without changing core framework code.

    **Parameters:**
    - `mode` str - Combination mode: 'and' (all must be valid) or 'or' (any must be valid)

    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `apply_mask`
    **Op:** `apply_sv_mask`

    Apply a boolean mask to an Sv Dataset, setting masked samples to NaN.
    Thin wrapper over echopype.mask.apply_mask, calling it against the Sv variable with NaN as the fill value. It adds nothing of its own, so the broadcasting and alignment rules are echopype's.

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
    seafloor_depth = []
    create_seafloor_mask_mask = []
    create_surface_mask_mask = []
    combine_masks_mask = []
    apply_mask_ds_Sv = []
    for _item in raw_file_paths:
        _iter_raw_stores = read_raw_files_to_stores(
        raw_file_paths=[_item],
        sonar_model='EK80',
        include_bot=False,
        intermediate_format='zarr',
    )
        raw_stores.append(_iter_raw_stores)
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores, ping_time_chunk=10000)
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
        waveform_mode='CW',
        encode_mode='power',
    )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)
        _iter_transducer_depth = compute_transducer_depth(
        ds_Sv=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        datum_correction_m=-1.0,
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
        _iter_seafloor_depth = read_seafloor_line_evl(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        evl_path='gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines',
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
        edge_extend_s=0.0,
        max_gap_s=600,
        min_coverage=0.99,
    )
        seafloor_depth.append(_iter_seafloor_depth)
        _iter_create_seafloor_mask_mask = create_seafloor_mask(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        seafloor_depth=_iter_seafloor_depth,
        seafloor_buffer_m=1,
    )
        create_seafloor_mask_mask.append(_iter_create_seafloor_mask_mask)
        _iter_create_surface_mask_mask = create_surface_mask(ds_Sv=_iter_compute_sv_ds_Sv, surface_depth_m=5.0)
        create_surface_mask_mask.append(_iter_create_surface_mask_mask)
        _iter_combine_masks_mask = combine_masks(masks=[_iter_create_seafloor_mask_mask, _iter_create_surface_mask_mask], mode='and')
        combine_masks_mask.append(_iter_combine_masks_mask)
        _iter_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_ep_add_depth_ds_Sv, mask=_iter_combine_masks_mask)
        apply_mask_ds_Sv.append(_iter_apply_mask_ds_Sv)
    ```

    #### Step: `merge_sv`
    **Op:** `merge_datasets`

    Reconsolidate a collected list of xarray Datasets into a single Dataset by concatenating along a dimension. This is the recipe system's fan-in (collect) target for segment-parallel work: a mapped step that produces one Dataset per data segment (e.g. per-file MVBS) is gathered via ``collect`` and merged back into one Dataset along ``dim`` (default ``ping_time``). A single Dataset passes through unchanged (single-item transparency).

    **Parameters:**
    - `dim` str - Dimension to concatenate the collected Datasets along.
    - `on_duplicate` str - What to do when two collected segments carry the same label on ``dim``.
    Unset (the default) leaves both, which is right when segments cannot overlap - per-file Sv concatenated on raw ping_time, for instance.
    "mean" averages them into one entry. That is what a fan-in over per segment BINNED products needs: compute_mvbs and compute_per_cell_statistics anchor their time bins to a global origin, so a segment boundary falling inside a bin makes the segment on each side emit that bin, each holding part of its pings. Left alone the merged store carries a duplicate index, which every downstream reindex rejects - add_auxiliary_features raises InvalidIndexError on it.
    "first" keeps the first entry for a label and sorts the result. That is what a fan-in over overlapping SLICES OF ONE STORE needs: dive windows cut from the survey grid, where two dives sharing a minute are the same cells twice, so nothing should be averaged. A per-segment variable that differs on the overlap is kept from the first segment only, which is the sign it does not belong on the shared axis; attach it per segment after the fan-in.
    Variables carrying "units: dB" are averaged in the linear domain and converted back, since a mean of decibels is not the mean of what they measure. The average is unweighted: the partial bins do not record how many pings each held.

    ```python
    # --- Parameters ---
    _merge_sv__dim = 'ping_time'

    merged = concat_datasets(datasets=apply_mask_ds_Sv, dim=_merge_sv__dim)
    ```

    #### Step: `crop_range`
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

    ```python
    # --- Parameters ---
    _crop_range__outlier_sigma = 3.0
    _crop_range__outlier_margin = 1.5
    _crop_range__drop_trailing_all_nan = True
    _crop_range__max_range_m = crop_max_range_m

    crop_range_ds_Sv = crop_range_samples(
        ds_Sv=merged,
        outlier_sigma=_crop_range__outlier_sigma,
        outlier_margin=_crop_range__outlier_margin,
        drop_trailing_all_nan=_crop_range__drop_trailing_all_nan,
        max_range_m=_crop_range__max_range_m,
    )
    ```

    #### Step: `rechunk_before_noise`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_before_noise__ping_time_chunk = 1000

    rechunk_before_noise_ds_Sv = rechunk_dataset(ds_Sv=crop_range_ds_Sv, ping_time_chunk=_rechunk_before_noise__ping_time_chunk)
    ```

    #### Step: `remove_noise`
    **Op:** `remove_background_noise`

    Estimate and remove background noise from Sv using the De Robertis and Higginbottom method. Returns a cleaned Sv Dataset.
    Thin wrapper over echopype.clean.remove_background_noise. Every parameter below is forwarded to it unchanged, so its behavior is echopype's. The one thing the wrapper adds is that the corrected values are copied over the Sv variable, so downstream steps reading Sv get the cleaned data without having to know about Sv_corrected. Both variables are present on the result.

    **Parameters:**
    - `ping_num` int - Number of pings used to estimate the noise level
    - `range_sample_num` int - Number of range samples used to estimate the noise level
    - `background_noise_max` str - Upper limit on the estimated noise level (e.g. '-125dB'); omit to leave it unbounded
    - `SNR_threshold` str - Signal-to-noise ratio below which corrected samples are masked out (e.g. '3.0dB')

    ```python
    # --- Parameters ---
    _remove_noise__range_sample_num = 10
    _remove_noise__ping_num = 5

    remove_noise_ds_Sv = remove_noise(
        ds_Sv=rechunk_before_noise_ds_Sv,
        range_sample_num=_remove_noise__range_sample_num,
        ping_num=_remove_noise__ping_num,
    )
    ```

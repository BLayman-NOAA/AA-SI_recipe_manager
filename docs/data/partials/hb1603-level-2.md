=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-2" data-drill="inc:sub_recipes/processing_lvl_2_combined.yaml" markdown>

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
    # processing_lvl_2_survey.yaml (2a) and processing_lvl_2_user.yaml (2b). This copy
    # drops the original 2b's second depth step and keeps 2a's surface-referenced depth.

    # Processing level 2: calibrated Sv, then masks and noise removal, per file.
    #
    #   2a  extract_cal_params -> compute_sv -> compute_transducer_depth
    #                          -> ep_add_depth -> crop_survey_range
    #   2b  dive_sv -> surface + frequency + seabed masks -> apply -> remove noise
    #
    # 2a is the survey's shared Sv, computed once per cruise and loaded from the
    # survey cache, so keep its steps as the survey recipe has them. 2b is the
    # analyst's own. Depth throughout is below the sea surface.
    #
    # Pass the same per_file_source as levels 1 and 3, or the per-file chain
    # splits. Files are merged in level 3, once they share a depth grid.

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
          The step output list the per-file steps map over, one instance per
          entry, normally ${query_ncei.raw_urls}. Required; give every level in
          the chain the same value.
      # Wire both to build_cal_mapping.
      calibration_dict:
        type: dict
        description: "Standardized calibration values keyed by calibration key, from the calibration mapping step."
      mapping_dict:
        type: dict
        description: "Mapping from each raw file's channels to their calibration key, from the calibration mapping step."
      crop_max_range_m:
        type: float
        default: 1800.0
        description: "Range ceiling in metres; samples beyond it are dropped even if they hold data. Changing it recomputes the whole survey Sv."
      # Wire it to plan_dives.windows. Any window list works, which lets the level
      # serve analyses other than the dives.
      dive_windows:
        type: list
        description: "Dive time windows, a list of dicts each with start and end, one per dive."
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Ping-time bin of the level 3 grid, as a duration string such as "10s".
          Each dive window is widened to whole bins of it, so it must equal level
          3's value.
      surface_depth_m:
        type: float
        default: 10.0
        description: "Range from the transducer, in metres, above which samples are masked, to remove the transmit pulse and ringdown."
      # The HB1603 default drops the high frequencies, which at depth carry only
      # range-amplified noise. Blanked channels are all NaN, the source of noise
      # removal's harmless "All-NaN slice" warning.
      frequencies_to_mask:
        type: list
        default: [70, 120, 200]
        description: >
          Nominal channel frequencies in kHz to blank, such as [70, 120, 200];
          every sample on those channels is masked. An empty list keeps every
          channel.
      seabed_lines_dir:
        type: path
        default: "./Auxiliary/seabed_lines"
        description: >
          Folder of half-day Echoview .evl seabed exports; each per-file instance
          reads the folder and interpolates the line onto its own pings.
      seabed_min_coverage:
        type: float
        default: 0.99
        description: >
          Fraction of a file's selected pings (0 to 1) that must get a seabed
          depth, or the step fails. A ping with no seabed depth is masked out
          entirely.
      seafloor_buffer_m:
        type: float
        default: 10.0
        description: >
          Height above the seabed line, in metres, that is also masked, to drop the
          bottom echo and detection error.
      noise_range_sample_num:
        type: int
        default: 10
        description: "Range samples per bin for the background noise estimate."
      noise_ping_num:
        type: int
        default: 5
        description: "Pings per bin for the background noise estimate."
      # The NEFSC reference analysis uses 6.0dB; 3.0dB is the survey tier's value.
      noise_snr_threshold:
        type: str
        default: "3.0dB"
        description: >
          Samples whose noise-corrected Sv is less than this above the noise
          estimate are masked, as a dB string such as "3.0dB". Raise it for a
          cleaner, shallower picture.
      # Null matches the survey tier.
      noise_background_max:
        type: str
        default: null
        description: >
          Ceiling on the background noise estimate, as a dB string such as
          "-125dB". Null leaves it unbounded.

    steps:

      # Level 2a: calibrated Sv and depth

      # Path(url).name is the key the calibration mapping is built under, so a URL
      # resolves like a local path.
      #
      # unmapped_channels stays at "warn": a channel no calibration matched uses
      # the raw file's own values, with a warning. HB1603 needs this for pulse
      # length, not power: on the first 151 files (27 and 28 June) 18 kHz ran at
      # 2.048 ms and 120 and 200 kHz at 0.256 ms, against calibrations at 1.024 ms,
      # a different measurement rather than a tolerance question. No dive is
      # affected (the first is 7 July). mapping_files/calibration_provenance.yaml
      # records it per channel.
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

      # Computed explicitly rather than with echopype's
      # use_platform_vertical_offsets, which puts an EK60 transducer 8.92 m above
      # the surface instead of below it. The dive profiles, their 99% bounds and
      # the seabed lines are all surface referenced, so this has to be right.
      #
      # The heave sign is HB2407's, verified there against an EVL seabed line but
      # not on HB1603 (it could be, on 25 July and 14 August, where the seabed is
      # inside the crop). It is worth at most +/- 0.45 m against a 2 m bin.
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

      # Surface-referenced depth, added here while this file's EchoData is at hand.
      - id: ep_add_depth
        op: ep_add_depth
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
          echodata: ${combine_raw.echodata}
        params:
          depth_offset: ${compute_transducer_depth.transducer_depth}

      # The shared survey Sv. The dive steps below read the uncropped Sv, but this
      # step stays so the survey cache is addressed.
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

      # Level 2b: window selection, masks and noise removal

      # Keeps the pings inside any dive window, from the uncropped Sv. Files no
      # window touches skip every step below.
      - id: dive_sv
        op: select_ping_time_range
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${ep_add_depth.ds_Sv}
        params:
          windows: ${inputs.dive_windows}
          allow_empty: true
          snap_to_bin: ${inputs.ping_time_bin}

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

      # The folder, read whole, onto this file's selected pings. edge_extend_s is
      # left at 0, so a ping outside the line's span is NaN rather than held at
      # the end depth, and seabed_min_coverage makes any such ping a failure.
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

      # Now on seabed-masked Sv: the point of this recipe.
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

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_calibration.calibration import extract_standardized_calibration_parameters
    from echopype.calibrate import compute_Sv
    from aa_si_utils.utils import compute_transducer_depth
    from echopype.consolidate import add_depth
    from aa_si_utils.utils import crop_range_samples
    from aa_si_utils.utils import select_ping_time_range
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import create_frequency_mask
    from aa_si_utils.utils import read_seafloor_line_evl
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_ml.ml import remove_noise

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
    for _item in raw_urls:
        ...  # HB1603 level 1, raw ingest

        # extract_cal_params: Extract calibration parameters (gain, sa-correction,
        # EBA) and environmental parameters (sound speed, absorption) from
        # standardized calibration files, cross-referenced against the EchoData object
        # for channel alignment.
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

        # compute_sv: Compute volume backscattering strength (Sv) from raw EchoData,
        # applying cal_params and env_params when given and the raw file's own values
        # otherwise.
        _iter_compute_sv_ds_Sv = compute_Sv(
            echodata=_iter_echodata,
            cal_params=_iter_cal_params,
            env_params=_iter_env_params,
        )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)

        # compute_transducer_depth: Compute per-ping transducer depth in metres below
        # the water surface from the EK Platform group, for feeding ep_add_depth as
        # its depth_offset.
        _iter_transducer_depth = compute_transducer_depth(
            ds_Sv=_iter_compute_sv_ds_Sv,
            echodata=_iter_echodata,
            use_heave=True,
            heave_sign=1.0,
        )
        transducer_depth.append(_iter_transducer_depth)

        # ep_add_depth: Add a depth variable, in metres below the surface, to an Sv
        # Dataset using echopype.
        _iter_ep_add_depth_ds_Sv = add_depth(
            ds=_iter_compute_sv_ds_Sv,
            echodata=_iter_echodata,
            depth_offset=_iter_transducer_depth,
        )
        ep_add_depth_ds_Sv.append(_iter_ep_add_depth_ds_Sv)

        # crop_survey_range: Trim the deep range samples that hold no data after
        # seafloor masking.
        _iter_crop_survey_range_ds_Sv = crop_range_samples(
            ds_Sv=_iter_ep_add_depth_ds_Sv,
            max_range_m=1800.0,
            drop_trailing_all_nan=False,
            outlier_sigma=None,
        )
        crop_survey_range_ds_Sv.append(_iter_crop_survey_range_ds_Sv)

        # dive_sv: Narrow a Dataset to one or more ping_time windows, for example to
        # work on a segment of a shared survey-wide Sv store.
        _iter_dive_sv_ds_Sv = select_ping_time_range(
            ds_Sv=_iter_ep_add_depth_ds_Sv,
            windows=windows,
            allow_empty=True,
            snap_to_bin='10s',
        )
        dive_sv_ds_Sv.append(_iter_dive_sv_ds_Sv)

        # dive_surface_mask: Build a surface-interference mask from baseline Sv using
        # a selectable method.
        _iter_dive_surface_mask_mask = create_surface_mask(ds_Sv=_iter_dive_sv_ds_Sv, surface_depth_m=10.0)
        dive_surface_mask_mask.append(_iter_dive_surface_mask_mask)

        # dive_frequency_mask: Build a mask that excludes selected frequency channels.
        _iter_dive_frequency_mask_mask = create_frequency_mask(ds_Sv=_iter_dive_sv_ds_Sv, frequencies_to_mask=[70, 120, 200])
        dive_frequency_mask_mask.append(_iter_dive_frequency_mask_mask)

        # dive_seabed_line: Read a seafloor line from Echoview .evl files and align it
        # to the Sv pings, in metres.
        _iter_seafloor_depth = read_seafloor_line_evl(
            ds_Sv=_iter_dive_sv_ds_Sv,
            evl_path='./Auxiliary/seabed_lines',
            min_coverage=0.99,
        )
        seafloor_depth.append(_iter_seafloor_depth)

        # dive_seabed_mask: Build a seafloor mask from baseline Sv and detected
        # seafloor depth.
        _iter_dive_seabed_mask_mask = create_seafloor_mask(
            ds_Sv=_iter_dive_sv_ds_Sv,
            seafloor_depth=_iter_seafloor_depth,
            seafloor_buffer_m=10.0,
        )
        dive_seabed_mask_mask.append(_iter_dive_seabed_mask_mask)

        # dive_combine_masks: Combine two or more mask datasets into one final mask.
        _iter_dive_combine_masks_mask = combine_masks(masks=[_iter_dive_surface_mask_mask, _iter_dive_frequency_mask_mask, _iter_dive_seabed_mask_mask], mode='and')
        dive_combine_masks_mask.append(_iter_dive_combine_masks_mask)

        # dive_apply_mask: Apply a boolean mask to an Sv Dataset, setting masked
        # samples to NaN.
        _iter_dive_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_dive_sv_ds_Sv, mask=_iter_dive_combine_masks_mask)
        dive_apply_mask_ds_Sv.append(_iter_dive_apply_mask_ds_Sv)

        # dive_remove_noise: Estimate and remove background noise from Sv using the De
        # Robertis and Higginbottom method.
        _iter_dive_remove_noise_ds_Sv = remove_noise(
            ds_Sv=_iter_dive_apply_mask_ds_Sv,
            range_sample_num=10,
            ping_num=5,
            SNR_threshold='6.0dB',
            background_noise_max='-66dB',
        )
        dive_remove_noise_ds_Sv.append(_iter_dive_remove_noise_ds_Sv)

        ...  # HB1603 level 3, gridding and per-cell statistics
    ```

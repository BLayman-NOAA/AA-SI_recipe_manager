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

    # Processing level 2, per file: Sv and masking per file, then the survey fan-in.
    # Continues the mapped chain from processing_lvl_1_per_file.yaml, which must be
    # included immediately before this level.
    #
    #   [per file] extract_cal_params -> compute_sv -> compute_transducer_depth
    #              -> ep_add_depth -> detect_seafloor -> masks -> apply_mask
    #   [fan-in]   merge_sv, concat along ping_time
    #   [survey]   crop_range -> rechunk_before_noise -> remove_noise
    #
    # The fan-in sits at apply_mask: every step above it is ping-local, and
    # remove_background_noise is the first with a cross-ping window.
    #
    # ep_add_splitbeam_angle is disabled: HB2407's calibration mapping has no
    # angle_sensitivity, so echopype raises. Nothing downstream uses the angles.

    recipe:
      name: "processing_lvl_2_per_file"
      description: "Per-file Sv and masking, fan-in to survey Sv, then noise removal."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      echodata:
        type: echodata
        description: "One file's raw EchoData from level 1, calibrated to Sv here and used for transducer depth."
      calibration_dict:
        type: dict
        description: "Standardized calibration values keyed by calibration key, from the calibration mapping step."
      mapping_dict:
        type: dict
        description: "Mapping from each raw file's channels to their calibration key, from the calibration mapping step."
      datum_correction_m:
        type: float
        default: 0.0
        description: >
          Metres added to each file's recorded drop keel offset to give depth below
          the waterline; positive pushes the transducer deeper. 0 takes the
          recorded offset as waterline referenced.
      use_heave:
        type: bool
        default: true
        description: "Add the per-ping platform heave to transducer depth. False holds the static keel depth on every ping."
      # heave_sign was found by correlating raw heave against the EVL line: 0.14 m
      # residual for +1.0 against 0.75 m for -1.0.
      heave_sign:
        type: float
        default: 1.0
        description: >
          Sign applied to platform heave when computing transducer depth, +1.0 or
          -1.0. +1.0 fits the hand-verified EVL seabed line best on HB2407.
      seabed_line_folder:
        type: path
        description: >
          Folder of Echoview .evl seabed line exports, a local path or a gs:// URL.
          The files covering the time window are combined into the seafloor; a
          single .evl file is read whole.
      # The seabed line window is survey-wide: every mapped instance reads the same
      # line files and interpolates them onto its own pings.
      file_time_start:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime selecting the seabed line files to read,
          by the time span in their names. Pass the window that selected the raw
          files; unset reads every line file.
      file_time_end:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime selecting the seabed line files to read,
          by the time span in their names. Pass the window that selected the raw
          files; unset reads every line file.
      seabed_line_max_gap_s:
        type: float
        default: 600
        description: >
          Widest gap in the combined seabed line, in seconds, to interpolate
          across; pings in a wider gap get no seafloor. Only a break between
          survey legs is this wide.
      # Per file the coverage test is stricter than it looks: a short uncovered
      # stretch can be a large fraction of the one file it falls in.
      seabed_line_min_coverage:
        type: float
        default: 0.99
        description: >
          Fraction of a file's pings (0 to 1) the seabed line must cover, or the
          step fails. Lower it or widen the window rather than let uncovered pings
          through, since all their samples are masked.
      surface_mask_depth:
        type: float
        default: 10.0
        description: >
          Range from the transducer, in metres, above which samples are masked, to
          remove the transmit pulse and ringdown.
      seafloor_buffer_m:
        type: float
        default: 1.0
        description: >
          Height above the seabed line, in metres, that is also masked, to drop the
          bottom echo and detection error.
      crop_outlier_sigma:
        type: float
        required: false
        description: >
          Standard deviations above the mean per-ping deepest sample at which a
          ping is an outlier; the crop lands on the deepest kept ping. Unset falls
          back to the trailing NaN trim.
      crop_outlier_margin:
        type: float
        default: 1.5
        description: >
          Multiplier on (mean + crop_outlier_sigma * sd) of the per-ping deepest
          sample that gives the reject threshold. Raise it to flag fewer pings.
          Used only when crop_outlier_sigma is set.
      crop_trailing_all_nan:
        type: bool
        default: true
        description: >
          Drop trailing range samples with no finite Sv anywhere. Lossless; ignored
          when crop_outlier_sigma is set. False, with the other two crop inputs
          unset, turns the crop off.
      crop_max_range_m:
        type: float
        required: false
        description: >
          Range ceiling in metres; samples beyond it are dropped even if they hold
          data. With other crop criteria set, the shallowest cut wins. Unset
          derives the crop from the data.
      noise_range_sample_num:
        type: int
        default: 10
        description: "Range samples per bin for the background noise estimate."
      noise_ping_num:
        type: int
        default: 5
        description: "Pings per bin for the background noise estimate."
      noise_chunk_ping_time:
        type: int
        default: 1000
        description: >
          Ping-time chunk size set before noise removal, so the noise estimate runs
          in dask chunks rather than as one in-memory block. Keep it a multiple of
          noise_ping_num.

    steps:

      # Per-file chain, continuing the map_over from level 1.

      # Picks this file's calibration out of the shared dicts, so each file is
      # calibrated for its own channel configuration.
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

      # Checkpointed because it is tiny and records the vertical reference the run
      # used, so it can be inspected without recomputing Sv.
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

      # Everything compared against the seabed line reads this dataset, not
      # compute_sv's: without 'depth', create_seafloor_mask silently falls back to
      # echo_range and offsets the mask by the transducer depth.
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
          # No extrapolation past the line's ends. Uncovered pings would be masked
          # away entirely, so min_coverage fails the run instead.
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

      # create_surface_mask compares against echo_range, so compute_sv's dataset
      # is enough.
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

      # Drops the all-NaN range samples below the deepest seabed once, which
      # shrinks every later step. It must follow the fan-in: cropping per file
      # leaves different range_sample lengths that merge_sv pads back with NaN.
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

=== "Python"

    The function calls this part of the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_calibration.calibration import extract_standardized_calibration_parameters
    from echopype.calibrate import compute_Sv
    from aa_si_utils.utils import compute_transducer_depth
    from echopype.consolidate import add_depth
    from aa_si_utils.utils import read_seafloor_line_evl
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import crop_range_samples
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_ml.ml import remove_noise

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
        ...  # HB2407 level 1, raw ingest

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
            waveform_mode='CW',
            encode_mode='power',
        )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)

        # compute_transducer_depth: Compute per-ping transducer depth in metres below
        # the water surface from the EK Platform group, for feeding ep_add_depth as
        # its depth_offset.
        _iter_transducer_depth = compute_transducer_depth(
            ds_Sv=_iter_compute_sv_ds_Sv,
            echodata=_iter_echodata,
            datum_correction_m=-1.0,
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

        # detect_seafloor: Read a seafloor line from Echoview .evl files and align it
        # to the Sv pings, in metres.
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

        # create_seafloor_mask: Build a seafloor mask from baseline Sv and detected
        # seafloor depth.
        _iter_create_seafloor_mask_mask = create_seafloor_mask(
            ds_Sv=_iter_ep_add_depth_ds_Sv,
            seafloor_depth=_iter_seafloor_depth,
            seafloor_buffer_m=1,
        )
        create_seafloor_mask_mask.append(_iter_create_seafloor_mask_mask)

        # create_surface_mask: Build a surface-interference mask from baseline Sv
        # using a selectable method.
        _iter_create_surface_mask_mask = create_surface_mask(ds_Sv=_iter_compute_sv_ds_Sv, surface_depth_m=5.0)
        create_surface_mask_mask.append(_iter_create_surface_mask_mask)

        # combine_masks: Combine two or more mask datasets into one final mask.
        _iter_combine_masks_mask = combine_masks(masks=[_iter_create_seafloor_mask_mask, _iter_create_surface_mask_mask], mode='and')
        combine_masks_mask.append(_iter_combine_masks_mask)

        # apply_mask: Apply a boolean mask to an Sv Dataset, setting masked samples to
        # NaN.
        _iter_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_ep_add_depth_ds_Sv, mask=_iter_combine_masks_mask)
        apply_mask_ds_Sv.append(_iter_apply_mask_ds_Sv)

    # merge_sv: Concatenate a collected list of Datasets into one along dim, for
    # example per-file MVBS gathered with collect.
    merged = concat_datasets(datasets=apply_mask_ds_Sv, dim='ping_time')

    # crop_range: Trim the deep range samples that hold no data after seafloor
    # masking.
    crop_range_ds_Sv = crop_range_samples(
        ds_Sv=merged,
        outlier_sigma=3.0,
        outlier_margin=1.5,
        drop_trailing_all_nan=True,
        max_range_m=crop_max_range_m,
    )

    # rechunk_before_noise: Rechunk a Dataset along ping_time so the steps after
    # it can run dask-parallel.
    rechunk_before_noise_ds_Sv = rechunk_dataset(ds_Sv=crop_range_ds_Sv, ping_time_chunk=1000)

    # remove_noise: Estimate and remove background noise from Sv using the De
    # Robertis and Higginbottom method.
    remove_noise_ds_Sv = remove_noise(
        ds_Sv=rechunk_before_noise_ds_Sv,
        range_sample_num=10,
        ping_num=5,
    )
    ```

=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-survey-echogram-pyramid" data-slug="hb1603-survey-echogram-pyramid" markdown>

    - `query_ncei`: [`query_ncei_data`](../ops/query_ncei_data.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)
    - `build_cal_mapping`: [`build_calibration_mapping`](../ops/build_calibration_mapping.md)
    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)
    - `extract_cal_params`: [`extract_standardized_cal_params`](../ops/extract_standardized_cal_params.md)
    - `compute_sv`: [`compute_sv`](../ops/compute_sv.md)
    - `compute_transducer_depth`: [`compute_transducer_depth`](../ops/compute_transducer_depth.md)
    - `ep_add_depth`: [`ep_add_depth`](../ops/ep_add_depth.md)
    - `crop_survey_range`: [`crop_range`](../ops/crop_range.md)
    - `viewer_splitbeam_angle`: [`ep_add_splitbeam_angle`](../ops/ep_add_splitbeam_angle.md)
    - `viewer_crop`: [`crop_range`](../ops/crop_range.md)
    - `viewer_seabed_line`: [`phase_detect_seafloor`](../ops/phase_detect_seafloor.md)
    - `viewer_seabed_mask`: [`create_seafloor_mask`](../ops/create_seafloor_mask.md)
    - `viewer_surface_mask`: [`create_surface_mask`](../ops/create_surface_mask.md)
    - `viewer_frequency_mask`: [`create_frequency_mask`](../ops/create_frequency_mask.md)
    - `viewer_combine_masks`: [`combine_masks`](../ops/combine_masks.md)
    - `viewer_apply_mask`: [`apply_sv_mask`](../ops/apply_sv_mask.md)
    - `viewer_remove_noise`: [`remove_background_noise`](../ops/remove_background_noise.md)
    - `viewer_mask_sparse`: [`mask_sparse_bins`](../ops/mask_sparse_bins.md)
    - `viewer_file_mvbs`: [`compute_mvbs`](../ops/compute_mvbs.md)
    - `merge_viewer_mvbs`: [`merge_datasets`](../ops/merge_datasets.md)
    - `rechunk_viewer_mvbs`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `plan_segments`: [`plan_pyramid_segments`](../ops/plan_pyramid_segments.md)
    - `build_pyramid`: [`build_echogram_pyramid`](../ops/build_echogram_pyramid.md)
    - `survey_echogram_store`: [`merge_echogram_pyramids`](../ops/merge_echogram_pyramids.md)

    </div>

=== "YAML"

    [Download `survey_echogram_pyramid.yaml`](../recipes/HB1603/survey_echogram_pyramid.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/survey_echogram_pyramid.yaml

    # HB1603 survey echogram pyramid: the whole cruise, built for EchogramGL.
    #
    # Every catalogue file, 18 and 38 kHz, seabed removed, as one level-of-detail
    # store the WebGPU viewer can open. Levels 0 to 2a are the survey's, included
    # so their hashes match and the per-file Sv loads from the cache; everything
    # from the split-beam angles down is this recipe's own.
    #
    #   aa-recipe run survey_echogram_pyramid.yaml --executor dask --dask-workers 16
    #
    #   level 0 (user)     the survey's catalogue and calibration mapping, loaded
    #   level 1, 2a        the survey's per-file Sv, cache hits at compute_sv and
    #                      compute_transducer_depth
    #   level 2b (viewer)  per file: split-beam angles, crop, phase-aware seabed
    #                      line, surface + frequency + seabed masks, noise removal
    #   level 3 (viewer)   per file: sparse mask, MVBS on the pinned depth grid;
    #                      fan in to one survey-wide MVBS store
    #   pyramid            plan segments, build each, merge: the viewer store
    #
    # The survey context store of the dive-profile workflow (Phase 5.4): on depth,
    # from per-file MVBS, so the per-dive analysis store can be layered over it.
    # processing_lvl_2_viewer_seabed.yaml explains the masking choices.
    #
    # THE GRID MATCHES THE ANALYSIS GRID
    #
    #   Level 3 bins 2 m by 10 s on depth, as dive_analysis.yaml does, with time
    #   bins on a global origin, so the cells coincide in time and size. Not
    #   vertically: the analysis uses dive_depth_offset 0.0 (range from the
    #   transducer) while this store is surface referenced, about 8.9 m deeper.
    #   Wire the transducer depth into the analysis's dive_depth_offset, or use 0.0
    #   here, to make them coincide.
    #
    # WHAT THIS RUN COSTS
    #
    #   Hours: 3322 files each given angles, cropped, bottom-picked, masked,
    #   denoised and binned; crop_survey_range cannot be reused because the angles
    #   go on before the crop. The seabed line and per-file MVBS are checkpointed,
    #   so an interrupted run resumes; the pyramid itself takes minutes, and
    #   changing only its settings reruns only the pyramid. Every artifact is zarr,
    #   so --cache-write-tier survey can share the store.
    #
    # The pyramid ops live in AA-SI_EchogramGL: pip install -e it into the
    # environment aa-recipe runs from. sub_recipes/viewer_pyramid.yaml says where
    # the store lands and how to serve it.
    #
    # THE SURVEY LEVELS MUST STAY IN STEP WITH survey_sv.yaml
    #
    #   Same include order, overrides and input values. Levels 1 to 3 are one
    #   per-file chain: nothing non-mapped may be declared between those includes.
    #
    # TESTING ON A SUBSET FIRST
    #
    #   Every per-file step maps over the catalogue query, so a narrower window is
    #   all a subset needs:
    #
    #     aa-recipe run survey_echogram_pyramid.yaml \
    #       --input file_time_start=2016-07-25T20:58 \
    #       --input file_time_end=2016-07-25T23:45
    #
    #   That is the nine-file leg the picker was validated on (1500 to 2000 m,
    #   canyon walls and soft sediment), about 15 minutes. A narrowed query is a
    #   separate cache lineage: the subset computes its own Sv and the full run
    #   reuses nothing from it, so run the full survey with the default window.
    #   --user-cache-dir or --outputs-dir keep a test run's artifacts apart.
    #
    # Check the shape before a long run:
    #   aa-recipe dry-run survey_echogram_pyramid.yaml --visualize

    recipe:
      name: "hb1603_survey_echogram_pyramid"
      description: "The whole HB1603 cruise as an EchogramGL pyramid: per-file MVBS on depth, 18 and 38 kHz, seabed masked by the phase-aware picker."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:

      # Survey level: must equal survey_sv.yaml's, or the cached survey chain is
      # recomputed. Narrow the query window only for a test run; see TESTING ON A
      # SUBSET FIRST in the header.
      file_time_start:
        type: str
        default: "2016-06-01T00:00:00"
        description: >
          Start of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T20:58". Every file overlapping the window is fetched.
      file_time_end:
        type: str
        default: "2016-09-01T00:00:00"
        description: >
          End of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T21:45". Every file overlapping the window is fetched.
      cal_input_folder:
        type: path
        default: "../calibration_files/HB201607_cal"
        description: "Folder of manufacturer calibration files (.cal for EK60, .xml for EK80), a local path or a gs:// URL."
      calibration_output_dir:
        type: path
        default: "./outputs/calibration"
        description: "Root folder the calibration artifacts are written under."
      crop_max_range_m:
        type: float
        default: 1800.0
        description: "Range ceiling in metres; samples beyond it are dropped even if they hold data. Changing it recomputes the whole survey Sv."

      # Viewer level.
      # HB1603 recorded to 2960-3003 m, and seabeds below 1800 m sat at 2400 to
      # 2950 m, so a 2100 m ceiling would recover almost none of them.
      viewer_crop_max_range_m:
        type: float
        default: 3000.0
        description: "Range ceiling of the viewer picture, in metres. Separate from crop_max_range_m, so the shared survey Sv keeps its own crop."
      viewer_grid_max_depth:
        type: str
        default: "3010m"
        description: >
          Deepest depth the viewer bins cover, as a length string such as "3010m".
          It must clear viewer_crop_max_range_m plus the transducer depth, or the
          deepest samples are dropped.

      # Every other input lives in the level file that uses it, and is still
      # settable from here or with --input: includes promote them.

    steps:

      # Survey levels: keep in step with survey_sv.yaml

      - include: sub_recipes/processing_lvl_0_user.yaml
        input_overrides:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          cal_input_folder: ${inputs.cal_input_folder}
          calibration_output_dir: ${inputs.calibration_output_dir}

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}

      - include: sub_recipes/processing_lvl_2_survey.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          calibration_dict: ${build_cal_mapping.calibration_dict}
          mapping_dict: ${build_cal_mapping.mapping_dict}
          crop_max_range_m: ${inputs.crop_max_range_m}

      # Viewer levels

      # Its own, deeper crop: the angles go on before any crop, so the viewer
      # can see past the survey's 1800 m without touching the shared Sv.
      - include: sub_recipes/processing_lvl_2_viewer_seabed.yaml
        input_overrides:
          crop_max_range_m: ${inputs.viewer_crop_max_range_m}

      - include: sub_recipes/processing_lvl_3_viewer.yaml
        input_overrides:
          grid_max_depth: ${inputs.viewer_grid_max_depth}

      - include: sub_recipes/viewer_pyramid.yaml
        input_overrides:
          gridded_sv: ${rechunk_viewer_mvbs.ds_Sv}
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/hb1603-survey-echogram-pyramid.ipynb).

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
    from aa_si_calibration.calibration import extract_standardized_calibration_parameters
    from echopype.calibrate import compute_Sv
    from aa_si_utils.utils import compute_transducer_depth
    from echopype.consolidate import add_depth
    from aa_si_utils.utils import crop_range_samples
    from echopype.consolidate import add_splitbeam_angle
    from aa_si_utils.seabed import detect_seafloor_phase
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import create_frequency_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_ml.ml import remove_noise
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_echogram_gl.ops import plan_pyramid_segments
    from aa_si_echogram_gl.ops import build_echogram_pyramid
    from aa_si_echogram_gl.ops import merge_echogram_pyramids

    # Inputs
    noise_background_max = None  # TODO: set this value  # str

    # processing_lvl_0_user.yaml

    # query_ncei: Query an NCEI acoustic data catalog for file listings within a
    # time window.
    _result = query_ncei_data(
        file_time_start='2016-06-01T00:00:00',
        file_time_end='2016-09-01T00:00:00',
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']

    file_config = []
    for _item in raw_urls:
        # scan_raw_config: Read one raw file's channel configuration from its datagram
        # headers.
        _iter_file_config = read_raw_file_config(
            raw_file_path=_item,
            verbose=False,
            max_scan_bytes=1048576,
        )
        file_config.append(_iter_file_config)

    # record_raw_configs: Collect per-file channel configurations into
    # raw_file_configs/raw_file_configs.yaml under the calibration output
    # directory.
    _result = record_raw_file_configs(file_configs=file_config, output_base='./outputs/calibration')
    raw_file_configs = _result['raw_file_configs']
    frequencies = _result['frequencies']
    raw_configs_path = _result['raw_configs_path']

    # standardize_cal: Convert manufacturer calibration files (EK60 .cal or EK80
    # .xml) to standardized single-channel .yaml files under
    # single_channel_calibration_files/.
    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder='../calibration_files/HB201607_cal',
        output_base='./outputs/calibration',
        cruise_id='HB1603',
        record_author='<Your Name>',
        short_filenames=True,
    )
    single_channel_dir = _result['single_channel_dir']
    channel_count = _result['channel_count']
    skipped = _result['skipped']
    standardize_cal_single_channel_data = _result['single_channel_data']

    # build_cal_mapping: Match each raw channel to its calibration data and save
    # the mapping files under mapping_files/.
    _result = build_calibration_mapping(
        raw_file_configs=raw_file_configs,
        output_base='./outputs/calibration',
        single_channel_dir=single_channel_dir,
        raw_configs_path=raw_configs_path,
        short_filenames=True,
        tolerances={'transmit_power': 1001.0},
        keep_unused=True,
        conflict_resolution='error',
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']
    missing_params = _result['missing_params']
    unused_files = _result['unused_file_names']
    provenance = _result['provenance']
    provenance_path = _result['provenance_path']
    conflicts = _result['conflicts']
    build_cal_mapping_single_channel_data = _result['single_channel_data']

    # processing_lvl_1_per_file.yaml

    raw_stores = []
    echodata = []
    cal_params = []
    env_params = []
    other_params = []
    compute_sv_ds_Sv = []
    transducer_depth = []
    ep_add_depth_ds_Sv = []
    crop_survey_range_ds_Sv = []
    viewer_splitbeam_angle_ds_Sv = []
    viewer_crop_ds_Sv = []
    seafloor_depth = []
    viewer_seabed_mask_mask = []
    viewer_surface_mask_mask = []
    viewer_frequency_mask_mask = []
    viewer_combine_masks_mask = []
    viewer_apply_mask_ds_Sv = []
    viewer_remove_noise_ds_Sv = []
    viewer_mask_sparse_ds_Sv = []
    ds_MVBS = []
    for _item in raw_urls:
        # read_raw: Open raw echosounder files with echopype and write each one to an
        # intermediate store.
        _iter_raw_stores = read_raw_files_to_stores(
            raw_file_paths=[_item],
            sonar_model='EK60',
            include_bot=False,
            intermediate_format='zarr',
        )
        raw_stores.append(_iter_raw_stores)

        # combine_raw: Combine the per-file stores or EchoData objects from
        # read_raw_files into one EchoData object.
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores)
        echodata.append(_iter_echodata)

        # processing_lvl_2_survey.yaml

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

        # processing_lvl_2_viewer_seabed.yaml

        # viewer_splitbeam_angle: Add split-beam angle_alongship and angle_athwartship
        # variables to an Sv Dataset using echopype.
        _iter_viewer_splitbeam_angle_ds_Sv = add_splitbeam_angle(
            source_Sv=_iter_ep_add_depth_ds_Sv,
            echodata=_iter_echodata,
            waveform_mode='CW',
            encode_mode='power',
            to_disk=False,
        )
        viewer_splitbeam_angle_ds_Sv.append(_iter_viewer_splitbeam_angle_ds_Sv)

        # viewer_crop: Trim the deep range samples that hold no data after seafloor
        # masking.
        _iter_viewer_crop_ds_Sv = crop_range_samples(
            ds_Sv=_iter_viewer_splitbeam_angle_ds_Sv,
            max_range_m=3000.0,
            drop_trailing_all_nan=False,
            outlier_sigma=None,
        )
        viewer_crop_ds_Sv.append(_iter_viewer_crop_ds_Sv)

        # viewer_seabed_line: Detect the seafloor from Sv and split-beam phase,
        # returning the seabed echo's leading edge in metres per ping.
        _iter_seafloor_depth = detect_seafloor_phase(
            ds_Sv=_iter_viewer_crop_ds_Sv,
            channel='38',
            max_slope_deg=30.0,
            max_gap_s=600.0,
            line='integration',
            missing='max_range',
        )
        seafloor_depth.append(_iter_seafloor_depth)

        # viewer_seabed_mask: Build a seafloor mask from baseline Sv and detected
        # seafloor depth.
        _iter_viewer_seabed_mask_mask = create_seafloor_mask(
            ds_Sv=_iter_viewer_crop_ds_Sv,
            seafloor_depth=_iter_seafloor_depth,
            seafloor_buffer_m=10.0,
        )
        viewer_seabed_mask_mask.append(_iter_viewer_seabed_mask_mask)

        # viewer_surface_mask: Build a surface-interference mask from baseline Sv
        # using a selectable method.
        _iter_viewer_surface_mask_mask = create_surface_mask(ds_Sv=_iter_viewer_crop_ds_Sv, surface_depth_m=10.0)
        viewer_surface_mask_mask.append(_iter_viewer_surface_mask_mask)

        # viewer_frequency_mask: Build a mask that excludes selected frequency
        # channels.
        _iter_viewer_frequency_mask_mask = create_frequency_mask(ds_Sv=_iter_viewer_crop_ds_Sv, frequencies_to_mask=[70, 120, 200])
        viewer_frequency_mask_mask.append(_iter_viewer_frequency_mask_mask)

        # viewer_combine_masks: Combine two or more mask datasets into one final mask.
        _iter_viewer_combine_masks_mask = combine_masks(masks=[_iter_viewer_surface_mask_mask, _iter_viewer_frequency_mask_mask, _iter_viewer_seabed_mask_mask], mode='and')
        viewer_combine_masks_mask.append(_iter_viewer_combine_masks_mask)

        # viewer_apply_mask: Apply a boolean mask to an Sv Dataset, setting masked
        # samples to NaN.
        _iter_viewer_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_viewer_crop_ds_Sv, mask=_iter_viewer_combine_masks_mask)
        viewer_apply_mask_ds_Sv.append(_iter_viewer_apply_mask_ds_Sv)

        # viewer_remove_noise: Estimate and remove background noise from Sv using the
        # De Robertis and Higginbottom method.
        _iter_viewer_remove_noise_ds_Sv = remove_noise(
            ds_Sv=_iter_viewer_apply_mask_ds_Sv,
            range_sample_num=10,
            ping_num=5,
            SNR_threshold='3.0dB',
            background_noise_max=noise_background_max,
        )
        viewer_remove_noise_ds_Sv.append(_iter_viewer_remove_noise_ds_Sv)

        # processing_lvl_3_viewer.yaml

        # viewer_mask_sparse: Remove spatiotemporal bins with fewer valid samples than
        # a threshold, reducing noise in MVBS computation.
        _iter_viewer_mask_sparse_ds_Sv = mask_sparse_bins(
            ds_Sv=_iter_viewer_remove_noise_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            nan_threshold=0.99,
        )
        viewer_mask_sparse_ds_Sv.append(_iter_viewer_mask_sparse_ds_Sv)

        # viewer_file_mvbs: Compute Mean Volume Backscattering Strength by averaging
        # Sv over range and time bins.
        _iter_ds_MVBS = compute_MVBS(
            ds_Sv=_iter_viewer_mask_sparse_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            range_var_max='3010m',
        )
        ds_MVBS.append(_iter_ds_MVBS)

    # merge_viewer_mvbs: Concatenate a collected list of Datasets into one along
    # dim, for example per-file MVBS gathered with collect.
    merged = concat_datasets(
        datasets=ds_MVBS,
        dim='ping_time',
        on_duplicate='mean',
    )

    # rechunk_viewer_mvbs: Rechunk a Dataset along ping_time so the steps after it
    # can run dask-parallel.
    rechunk_viewer_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merged, ping_time_chunk=2048)

    # viewer_pyramid.yaml

    # plan_segments: Choose ping ranges that a pyramid can be built over
    # independently and then concatenated exactly.
    _result = plan_pyramid_segments(
        ds_Sv=rechunk_viewer_mvbs_ds_Sv,
        levels=8,
        target_pings=16384,
    )
    ranges = _result['ranges']
    grid = _result['grid']

    build_pyramid_pyramid = []
    for _item in ranges:
        # build_pyramid: Build a level-of-detail pyramid from an Sv Dataset for the
        # WebGPU echogram viewer, where level k merges 2**k pings.
        _iter_build_pyramid_pyramid = build_echogram_pyramid(
            ds_Sv=rechunk_viewer_mvbs_ds_Sv,
            ping_range=_item,
            levels=8,
            range_var='depth',
            aggregation='linear_mean',
        )
        build_pyramid_pyramid.append(_iter_build_pyramid_pyramid)

    # survey_echogram_store: Collect per segment pyramids into one, level by
    # level.
    survey_echogram_store_pyramid = merge_echogram_pyramids(parts=build_pyramid_pyramid, dim='ping_time')
    ```

=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-dive-analysis" markdown>

    - HB1603 dive plan
    - [HB1603 level 0, NCEI catalogue and calibration mapping](level-0.md)
    - [HB1603 level 1, raw ingest](level-1.md)
    - [HB1603 level 2, calibrated Sv, masks and noise removal](level-2.md)
    - [HB1603 level 3, gridding and per-cell statistics](level-3.md)
    - [HB1603 level 4, clustering](level-4.md)
    - [HB1603 level 5, dive SVCodes](level-5.md)

    </div>

=== "YAML"

    [Download `dive_analysis.yaml`](../recipes/HB1603/dive_analysis.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/dive_analysis.yaml,
    # with levels 2a and 2b merged into processing_lvl_2_combined.yaml. Level 2b
    # keeps the survey's surface-referenced depth, so the level 3 depth axis is
    # pinned 10 m deeper than in the original.

    # HB1603 dive analysis: 13 dives, one clustering, per-dive SVCodes.
    #
    # The user-level recipe of the survey/user split. It computes no Sv and builds
    # no calibration: levels 0 to 2a are the survey's, included so their hashes
    # match and their checkpoints load. Everything from the dive windows down is
    # this analyst's own.
    #
    #   aa-recipe run dive_analysis.yaml --executor dask --dask-workers 16
    #
    #   plan              read the dive configs, one window per dive
    #   level 0 (user)    the survey's catalogue and calibration mapping, loaded
    #   level 1, 2a       the survey's per-file Sv, a cache hit at crop_survey_range
    #   level 2b (user)   per file, inside the dive windows: surface, frequency and
    #                     seabed masks, then noise removal
    #   level 3 (user)    per file: sparse mask, grid, per-cell CV; fan in to the
    #                     pooled dive grid
    #   level 4           pooled HDBSCAN clustering and the clustering report
    #   level 5           per-dive SVCodes and the depth-interval tables
    #
    # WHERE THE VALUES LIVE
    #
    #   Every user-level value is set here, on the include that uses it: a recipe
    #   input when several includes share it, otherwise a literal, which --input
    #   cannot reach. The survey-level overrides stay exactly as survey_sv.yaml has
    #   them, because their hashes address the shared cache.
    #
    # MATCHED TO THE NEFSC REFERENCE ANALYSIS
    #
    #   Noise removal uses the reference config's 6.0dB SNR threshold. Sound speed
    #   and absorption are not matched; they are set in the survey's Sv.
    #
    # WHAT THIS RUN COSTS
    #
    #   Levels 1 and 2a are declared over all 3322 catalogue files and resolve per
    #   file against the cache; level 2b drops the roughly 3280 files no dive
    #   window touches, so real work happens on about 40. If level 0 fails with
    #   "Calibration files directory not found", run survey_sv.yaml first.
    #
    # KEEP THE SURVEY LEVELS IN STEP WITH survey_sv.yaml
    #
    #   Same include order, overrides and input values. Do not narrow the
    #   catalogue query to get a subset: it is a parent of every per-file step, so
    #   the cruise would be recomputed from raw. The window belongs in level 2b.
    #   Levels 1 to 3 are one per-file chain, so nothing non-mapped may be declared
    #   between those includes; that is why the dive plan comes first.
    #
    # Derived from full_analysis_seabed_sv_mask.yaml; the survey levels still hash
    # identically to it. Check the shape before a long run:
    #   aa-recipe dry-run dive_analysis.yaml --visualize

    recipe:
      name: "hb1603_dive_analysis"
      description: "Pooled HDBSCAN clustering of 13 HB1603 sperm whale dives with per-dive SVCodes, seabed masked on Sv."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:

      # Survey level: must equal survey_sv.yaml's, or the cached survey chain is
      # recomputed.
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

      # Shared by levels 2b, 3 and 5; wire all three from this one input. Level 2b
      # widens each dive window to whole bins, level 3 grids on it and level 5
      # counts one bin per ping.
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Ping-time bin width of the MVBS grid, as a duration string such as
          "10s". The sparse-bin mask, the cell statistics and the SVCode duration
          tables use the same bin.

    steps:

      # Every localized HB1603 dive except SWD_20160703-OE20, whose dive data is
      # incorrect. SWD_20160707-OE20-OE29-OE32 is a combined view of three listed
      # dives, not a dive of its own.
      - include: sub_recipes/dive_plan.yaml
        input_overrides:
          json_dir: "./Auxiliary/JSON_Files"
          evl_root: "./Auxiliary"
          pad_minutes: 0.0
          dive_labels:
            - "SWD_20160707-OE20"
            - "SWD_20160707-OE29"
            - "SWD_20160707-OE32"
            - "SWD_20160719-OE07"
            - "SWD_20160725-OE50"
            - "SWD_20160725-OE54"
            - "SWD_20160725-OE61"
            - "SWD_20160725-OE62"
            - "SWD_20160725-OE64"
            - "SWD_20160802-OE16"
            - "SWD_20160814-OE03"
            - "SWD_20160814-OE09"
            - "SWD_20160814-OE44"

      # Survey levels: keep in step with survey_sv.yaml

      - include: sub_recipes/processing_lvl_0_user.yaml
        input_overrides:
          cal_input_folder: ${inputs.cal_input_folder}
          calibration_output_dir: ${inputs.calibration_output_dir}

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}

      - include: sub_recipes/processing_lvl_2_combined.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          calibration_dict: ${build_cal_mapping.calibration_dict}
          mapping_dict: ${build_cal_mapping.mapping_dict}
          crop_max_range_m: ${inputs.crop_max_range_m}
          dive_windows: ${plan_dives.windows}
          ping_time_bin: ${inputs.ping_time_bin}
          surface_depth_m: 10.0
          frequencies_to_mask: [70, 120, 200]
          seabed_lines_dir: "./Auxiliary/seabed_lines"
          seabed_min_coverage: 0.99
          seafloor_buffer_m: 10.0
          noise_range_sample_num: 10
          noise_ping_num: 5
          noise_snr_threshold: "6.0dB"
          noise_background_max: "-66dB"

      - include: sub_recipes/processing_lvl_3_user.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          ping_time_bin: ${inputs.ping_time_bin}
          range_bin: "2m"
          range_var: "depth"
          nan_threshold: 0.99
          # Pinned so every file shares one depth axis. It must clear the deepest
          # range recorded (about 3003 m) plus the transducer depth (about 9 m).
          mvbs_range_var_max: "3020m"
          dive_chunk_ping_time: 1000

      - include: sub_recipes/processing_lvl_4.yaml
        input_overrides:
          mvbs: ${rechunk_dive_mvbs.ds_Sv}
          cell_stats_sv: ${merge_dive_files_cell_stats.merged}
          dive_windows: ${plan_dives.windows}
          sample_size: 1200000
          min_cluster_size_fraction: 0.01
          min_samples: 4

      - include: sub_recipes/processing_lvl_5_sv_codes.yaml
        input_overrides:
          clustered: ${embed_results.ds_normalized}
          dive_windows: ${plan_dives.windows}
          ping_time_bin: ${inputs.ping_time_bin}
          code_output_dir: "./outputs/sv_codes"
          depth_interval_m: 200.0
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/hb1603-dive-analysis.ipynb).

    ```python
    from aa_si_utils.dive_profiles import plan_dive_datasets
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
    from aa_si_utils.utils import select_ping_time_range
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import create_frequency_mask
    from aa_si_utils.utils import read_seafloor_line_evl
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_ml.ml import remove_noise
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import assign_clusters_by_prediction
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report
    from aa_si_utils.dive_profiles import generate_sv_codes
    from aa_si_utils.dive_profiles import sv_code_depth_table

    # HB1603 dive plan

    # plan_dives: Turn the NEFSC dive configs into recipe windows.
    _result = plan_dive_datasets(
        json_dir='./Auxiliary/JSON_Files',
        evl_root='./Auxiliary',
        pad_minutes=0.0,
        include_labels=['SWD_20160707-OE20', 'SWD_20160707-OE29', 'SWD_20160707-OE32', 'SWD_20160719-OE07', 'SWD_20160725-OE50', 'SWD_20160725-OE54', 'SWD_20160725-OE61', 'SWD_20160725-OE62', 'SWD_20160725-OE64', 'SWD_20160802-OE16', 'SWD_20160814-OE03', 'SWD_20160814-OE09', 'SWD_20160814-OE44'],
    )
    windows = _result['windows']
    raw_files = _result['raw_files']

    # HB1603 level 0, NCEI catalogue and calibration mapping

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

    # HB1603 level 1, raw ingest

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

        # HB1603 level 2, calibrated Sv, masks and noise removal

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

        # HB1603 level 3, gridding and per-cell statistics

        # dive_mask_sparse: Remove spatiotemporal bins with fewer valid samples than a
        # threshold, reducing noise in MVBS computation.
        _iter_dive_mask_sparse_ds_Sv = mask_sparse_bins(
            ds_Sv=_iter_dive_remove_noise_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            nan_threshold=0.99,
        )
        dive_mask_sparse_ds_Sv.append(_iter_dive_mask_sparse_ds_Sv)

        # dive_file_mvbs: Compute Mean Volume Backscattering Strength by averaging Sv
        # over range and time bins.
        _iter_ds_MVBS = compute_MVBS(
            ds_Sv=_iter_dive_mask_sparse_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            range_var_max='3020m',
        )
        ds_MVBS.append(_iter_ds_MVBS)

        # dive_cell_stats: Compute statistics within each MVBS cell (such as the
        # coefficient of variation) from fine-resolution Sv, at MVBS bin resolution.
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

    # merge_dive_files_mvbs: Concatenate a collected list of Datasets into one
    # along dim, for example per-file MVBS gathered with collect.
    merge_dive_files_mvbs_merged = concat_datasets(
        datasets=ds_MVBS,
        dim='ping_time',
        on_duplicate='mean',
    )

    # rechunk_dive_mvbs: Rechunk a Dataset along ping_time so the steps after it
    # can run dask-parallel.
    rechunk_dive_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merge_dive_files_mvbs_merged, ping_time_chunk=1000)

    # merge_dive_files_cell_stats: Concatenate a collected list of Datasets into
    # one along dim, for example per-file MVBS gathered with collect.
    merge_dive_files_cell_stats_merged = concat_datasets(
        datasets=dive_cell_stats_ds_Sv,
        dim='cell_ping_time',
        on_duplicate='mean',
    )

    # HB1603 level 4, clustering

    # reshape_ml: Reshape an MVBS Dataset into a flat, ML-ready xarray Dataset
    # where each observation corresponds to one spatial or temporal bin.
    reshape_ml_ds_ml_ready = reshape_data_for_ml(
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
        data_var='Sv',
        dataset_name='ml_dataset',
        feature_strategy='mean_centered',
        baseline_channel=0,
    )

    # add_aux_features: Append auxiliary coordinate-derived features (e.g. depth,
    # ping_time_seconds) to the flattened ML-ready Dataset.
    add_aux_features_ds_ml_ready = add_auxiliary_features(
        ds_ml_ready=reshape_ml_ds_ml_ready,
        ds_sv=merge_dive_files_cell_stats_merged,
        dataset_name='ml_dataset',
        features=['cell_cv'],
    )

    # normalize_ml: Normalize ML features in the flattened Dataset.
    normalize_ml_ds_normalized = normalize_data(
        ds_ml_ready=add_aux_features_ds_ml_ready,
        method='flatten',
        dataset_name='ml_dataset',
        normalization_name='normalized_flatten_mean_centered',
        shift_positive=False,
    )

    # run_hdbscan: Run HDBSCAN unsupervised clustering on normalized feature data.
    _result = run_hdbscan(
        ds_normalized=normalize_ml_ds_normalized,
        dataset_name='ml_dataset',
        normalization_name='normalized_flatten_mean_centered',
        ml_result_name='hdbscan_results',
        min_cluster_size_fraction=0.01,
        min_samples=4,
        sample_size=1200000,
        cluster_selection_method='leaf',
        use_hdbscan=True,
        soft_membership_threshold=0.1,
    )
    run_hdbscan_clustering_results = _result['clustering_results']
    run_hdbscan_clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    run_hdbscan_cluster_labels = _result['cluster_labels']

    # label_all_points: Label every point of a subsampled clustering, so no cell
    # is left unlabelled.
    _result = assign_clusters_by_prediction(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        dataset_name='ml_dataset',
        normalization_name='normalized_flatten_mean_centered',
    )
    label_all_points_clustering_results = _result['clustering_results']
    label_all_points_clustering_model = _result['clustering_model']
    label_all_points_cluster_labels = _result['cluster_labels']

    # embed_results: Embed clustering results into the normalized ML Dataset as
    # cluster-label variables.
    _result = embed_clustering_results(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=label_all_points_clustering_results,
        dataset_name='ml_dataset',
        ml_result_name='hdbscan_results',
    )
    embed_results_ds_normalized = _result['ds_normalized']
    gridded_results = _result['gridded_results']

    # plot_clustering_report: Render a clustering report: the clustering echogram,
    # per-cluster statistics, and the HDBSCAN hierarchy plot when a fitted model
    # is available.
    plot_clustering_report(
        ds_normalized=embed_results_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
        dataset_name='ml_dataset',
        ml_result_name='hdbscan_results',
        x_axis_units='bins',
        y_to_x_aspect_ratio_override=1.78,
        plot_window=[0, 2400],
        cluster_stats_sv_data_var='Sv',
        overlay_windows=windows,
        overlay_fit_style={'color': '#23FFFF', 'linewidth': 3.0},
        overlay_bound_style={'color': '#D08BFF', 'linewidth': 1.8, 'linestyle': ':'},
    )

    # HB1603 level 5, dive SVCodes

    # generate_sv_codes: Write per-dive SVCode CSVs from the embedded clustering.
    _result = generate_sv_codes(
        ds=embed_results_ds_normalized,
        windows=windows,
        output_dir='./outputs/sv_codes',
        dataset_name='ml_dataset',
        ml_result_name='hdbscan_results',
        range_var='depth',
        ping_time_bin='10s',
    )
    code_csv_paths = _result['code_csv_paths']
    summary_csv_path = _result['summary_csv_path']
    dive_labels = _result['dive_labels']

    # sv_code_depth_table: Percent of dive duration per depth interval per code.
    _result = sv_code_depth_table(
        summary_csv_path=summary_csv_path,
        output_dir='./outputs/sv_codes',
        depth_interval_m=200.0,
    )
    per_dive_csv_path = _result['per_dive_csv_path']
    combined_csv_path = _result['combined_csv_path']
    totals_csv_path = _result['totals_csv_path']
    ```

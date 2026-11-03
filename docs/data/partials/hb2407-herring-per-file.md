=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-herring-per-file" markdown>

    - [HB2407 level 0, file selection and calibration mapping](level-0.md)
    - [HB2407 level 1, raw ingest](level-1.md)
    - [HB2407 level 2, calibrated Sv, masks and noise removal](level-2.md)
    - `plot_sv_clean`: [`plot_sv_echogram`](../ops/plot_sv_echogram.md)
    - [HB2407 level 3, gridding and per-cell statistics](level-3.md)
    - `plot_mvbs`: [`plot_sv_echogram`](../ops/plot_sv_echogram.md)
    - [HB2407 level 4, clustering](level-4.md)

    </div>

=== "YAML"

    [Download `Herring_10-15_per_file.yaml`](../recipes/HB2407/Herring_10-15_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # Herring_10-15_per_file.yaml, regrouped into the HB1603 levels. Every step is
    # wired as in the original, in the same order.

    # HB2407 Herring 10-15, per-raw-file pipeline.
    #
    # Each raw file is calibrated and masked on its own; merge_sv reassembles the
    # survey before noise removal.
    #
    #   level 0   select the raw files and build the calibration mapping
    #   level 1   per file: raw ingest
    #   level 2   per file: Sv, depth and masks; then merge, crop and noise removal
    #   level 3   sparse mask, MVBS and per-cell statistics
    #   level 4   HDBSCAN clustering and its report
    #
    # Usage:
    #   aa-recipe run Herring_10-15_per_file.yaml
    #   aa-recipe run Herring_10-15_per_file.yaml --executor dask

    recipe:
      name: "herring_10_15_per_file"
      description: "HB2407 Herring 10-15 pipeline with per-raw-file processing and a pre-MVBS fan-in."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        fingerprint_mode: auto
        description: "Folder of raw .raw files, a local path or a gs:// URL."
      cal_input_folder:
        type: path
        default: "./HB2407_calibration_files"
        description: >
          Folder of manufacturer calibration files (.cal for EK60, .xml for EK80),
          a local path or a gs:// URL.
      seabed_line_folder:
        type: path
        default: "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines"
        description: >
          Folder of Echoview .evl seabed line exports, a local path or a gs://
          URL. The files covering the time window are combined into the seafloor;
          a single .evl file is read whole.

      # Data time range.
      file_time_start:
        type: str
        default: "2024-10-15T13:38:52"
        description: >
          Optional inclusive ISO datetime matched to the D{YYYYMMDD}-T{HHMMSS}
          time in each raw file name; files at or after it are processed and
          scanned for calibration. Unset takes every file. It also selects the
          seabed line files to read.
      file_time_end:
        type: str
        default: "2024-10-15T23:43:36"
        description: >
          Optional inclusive ISO datetime matched to the D{YYYYMMDD}-T{HHMMSS}
          time in each raw file name; files at or before it are processed and
          scanned for calibration. Unset takes every file. It also selects the
          seabed line files to read.

      # Display window shared by every plotting step; hardcode a value in a step
      # or sub-recipe to override it there.
      min_depth:
        type: float
        default: 0
        description: "Shallowest depth shown on the echograms, in metres."
      max_depth:
        type: float
        # default: 50
        description: >
          Deepest depth shown on the echograms, in metres. Unset uses the deepest
          depth in the data.
      time_min:
        type: str
        # default: "2024-10-15T00:00"
        description: >
          Start of the echogram window, as a UTC timestamp such as
          "2024-10-15T12:30". Unset starts at the first ping.
      time_max:
        type: str
        # default: "2024-10-15T01:00"
        description: >
          End of the echogram window, as a UTC timestamp such as
          "2024-10-15T12:30". Unset runs to the last ping.
      x_axis_units:
        type: str
        default: "datetime"
        description: >
          Echogram x axis: "datetime" (UTC clock time), "seconds" from the first
          ping shown, "pings", "meters" travelled, or "bins" (MVBS only).
      y_axis_units:
        type: str
        default: "meters"
        description: >
          Echogram y axis: "meters", "range_sample", or "bins" (MVBS only).


    steps:

      - include: sub_recipes/processing_lvl_0_calibration.yaml
        input_overrides:
          raw_input_folder: ${inputs.raw_input_folder}
          cal_input_folder: ${inputs.cal_input_folder}
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          cruise_id: "HB2407"
          record_author: "<Your Name>"

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          sonar_model: "EK80"

      # Must follow level 1 directly: it continues the same mapped chain.
      - include: sub_recipes/processing_lvl_2_per_file.yaml
        input_overrides:
          echodata: ${combine_raw.echodata}
          calibration_dict: ${gen_cal_mapping.calibration_dict}
          mapping_dict: ${gen_cal_mapping.mapping_dict}
          datum_correction_m: -1.0
          use_heave: true
          heave_sign: 1.0
          # Masks
          surface_mask_depth: 5.0
          seafloor_buffer_m: 1
          # Range crop by outlier rejection, so one anomalously deep ping does not
          # hold the range axis open. Add crop_max_range_m for a hard ceiling.
          crop_outlier_sigma: 3.0
          crop_outlier_margin: 1.5
          # Background noise removal
          noise_ping_num: 5
          noise_range_sample_num: 10
          seabed_line_folder: ${inputs.seabed_line_folder}
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      - id: plot_sv_clean
        op: plot_sv_echogram
        regenerate: if-missing
        inputs:
          ds_Sv: ${remove_noise.ds_Sv}
        params:
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 100

      - include: sub_recipes/processing_lvl_3.yaml
        input_overrides:
          sv_clean_unmasked: ${remove_noise.ds_Sv}
          # MVBS bin size:
          range_bin: "4m"
          ping_time_bin: "4s"
          nan_threshold: 0.95

      - id: plot_mvbs
        op: plot_sv_echogram
        regenerate: if-missing
        inputs:
          ds_Sv: ${compute_mvbs.ds_MVBS}
          ds_Sv_source: ${mask_sparse.ds_Sv}
        params:
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 50

      # echodata and seafloor_depth stay unwired: features: [cell_cv] needs neither.
      # To use them, collect the per-file lines with a merge_datasets step.
      - include: sub_recipes/processing_lvl_4.yaml
        input_overrides:
          mvbs: ${compute_mvbs.ds_MVBS}
          overlay_line_var: "sw_dive_profile"
          sv_clean_unmasked: ${remove_noise.ds_Sv}
          cell_stats_sv: ${compute_cell_stats.ds_Sv}
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          # HDBSCAN tuning
          hdbscan_min_cluster_size_fraction: 0.001
          hdbscan_min_samples: 7
          hdbscan_cluster_selection_method: "eom"
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/hb2407-herring-per-file.ipynb).

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import generate_standardized_cal_mapping
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
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
    from aa_si_visualization.echogram import plot_sv_echogram
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_visualization.echogram import plot_flattened_data_echogram
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report

    # Inputs
    max_depth = None  # TODO: set this value  # float
    time_min = None  # TODO: set this value  # str
    time_max = None  # TODO: set this value  # str
    crop_max_range_m = None  # TODO: set this value  # float
    echodata = None  # TODO: set this value  # echodata
    seafloor_depth = None  # TODO: set this value  # dataarray

    # HB2407 level 0, file selection and calibration mapping

    # initial_setup: Validate the raw files in the input folder, create the
    # calibration output directory if needed, and return the raw file paths and
    # that directory.
    _result = initial_setup_and_validation(
        raw_input_folder='${inputs.raw_input_folder}',
        calibration_outputs_string='calibration',
        raw_file_names=[],
        clear_previous_json_logs=True,
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
    )
    raw_file_paths = _result['raw_file_paths']
    calibration_output_dir = _result['calibration_output_dir']

    # gen_cal_mapping: Read manufacturer calibration files, convert them to per-
    # channel standardized files, match each raw channel to its calibration data,
    # and return a mapping dict and a calibration dict.
    _result = generate_standardized_cal_mapping(
        raw_input_folder='${inputs.raw_input_folder}',
        cal_input_folder='./HB2407_calibration_files',
        output_base=calibration_output_dir,
        cruise_id='HB2407',
        record_author='<Your Name>',
        short_filenames=True,
        keep_unused=True,
        conflict_resolution='error',
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']

    # HB2407 level 1, raw ingest

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
        # read_raw: Open raw echosounder files with echopype and write each one to an
        # intermediate store.
        _iter_raw_stores = read_raw_files_to_stores(
            raw_file_paths=[_item],
            sonar_model='EK80',
            include_bot=False,
            intermediate_format='zarr',
        )
        raw_stores.append(_iter_raw_stores)

        # combine_raw: Combine the per-file stores or EchoData objects from
        # read_raw_files into one EchoData object.
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores, ping_time_chunk=10000)
        echodata.append(_iter_echodata)

        # HB2407 level 2, calibrated Sv, masks and noise removal

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

    # Herring_10-15_per_file.yaml

    # plot_sv_clean: Render an Sv echogram of ds_Sv, optionally on the coordinate
    # axes of ds_Sv_source (for example MVBS on the original Sv grid).
    plot_sv_echogram(
        ds_Sv=remove_noise_ds_Sv,
        min_depth=0,
        max_depth=max_depth,
        time_min=time_min,
        time_max=time_max,
        x_axis_units='datetime',
        y_axis_units='meters',
        dpi=100,
    )

    # HB2407 level 3, gridding and per-cell statistics

    # mask_sparse: Remove spatiotemporal bins with fewer valid samples than a
    # threshold, reducing noise in MVBS computation.
    mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=remove_noise_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
        nan_threshold=0.95,
    )

    # rechunk_before_mvbs: Rechunk a Dataset along ping_time so the steps after it
    # can run dask-parallel.
    rechunk_before_mvbs_ds_Sv = rechunk_dataset(ds_Sv=mask_sparse_ds_Sv, ping_time_chunk=1000)

    # compute_mvbs: Compute Mean Volume Backscattering Strength by averaging Sv
    # over range and time bins.
    ds_MVBS = compute_MVBS(
        ds_Sv=rechunk_before_mvbs_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
    )

    # compute_cell_stats: Compute statistics within each MVBS cell (such as the
    # coefficient of variation) from fine-resolution Sv, at MVBS bin resolution.
    compute_cell_stats_ds_Sv = compute_per_cell_statistics(
        ds_Sv=remove_noise_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
        statistics=['cv'],
    )

    # Herring_10-15_per_file.yaml

    # plot_mvbs: Render an Sv echogram of ds_Sv, optionally on the coordinate axes
    # of ds_Sv_source (for example MVBS on the original Sv grid).
    plot_sv_echogram(
        ds_Sv=ds_MVBS,
        ds_Sv_original=mask_sparse_ds_Sv,
        min_depth=0,
        max_depth=max_depth,
        time_min=time_min,
        time_max=time_max,
        x_axis_units='datetime',
        y_axis_units='meters',
        dpi=50,
    )

    # HB2407 level 4, clustering

    # reshape_ml: Reshape an MVBS Dataset into a flat, ML-ready xarray Dataset
    # where each observation corresponds to one spatial or temporal bin.
    reshape_ml_ds_ml_ready = reshape_data_for_ml(
        ds_Sv=ds_MVBS,
        data_var='Sv',
        dataset_name='ml_dataset',
        feature_strategy='mean_centered',
        baseline_channel=0,
    )

    # add_aux_features: Append auxiliary coordinate-derived features (e.g. depth,
    # ping_time_seconds) to the flattened ML-ready Dataset.
    add_aux_features_ds_ml_ready = add_auxiliary_features(
        ds_ml_ready=reshape_ml_ds_ml_ready,
        echodata=echodata,
        ds_sv=compute_cell_stats_ds_Sv,
        seafloor_depth=seafloor_depth,
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

    # plot_normalized_ml: Render a normalized ML feature echogram for inspection.
    plot_flattened_data_echogram(
        ds_ml=normalize_ml_ds_normalized,
        ds_Sv_original=remove_noise_ds_Sv,
        ml_dataset_name='ml_dataset',
        ml_specific_data_name='normalized_flatten_mean_centered',
        min_depth=0,
        max_depth=max_depth,
        time_min=time_min,
        time_max=time_max,
        x_axis_units='datetime',
        y_axis_units='meters',
        dpi=30,
    )

    # run_hdbscan: Run HDBSCAN unsupervised clustering on normalized feature data.
    _result = run_hdbscan(
        ds_normalized=normalize_ml_ds_normalized,
        dataset_name='ml_dataset',
        normalization_name='normalized_flatten_mean_centered',
        ml_result_name='hdbscan_results',
        min_cluster_size_fraction=0.001,
        min_samples=7,
        sample_size=1000000,
        cluster_selection_method='eom',
        use_hdbscan=True,
        soft_membership_threshold=0.6,
    )
    clustering_results = _result['clustering_results']
    clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    cluster_labels = _result['cluster_labels']

    # embed_results: Embed clustering results into the normalized ML Dataset as
    # cluster-label variables.
    _result = embed_clustering_results(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=clustering_results,
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
        clustering_results=clustering_results,
        clustering_model=clustering_model,
        ds_Sv=remove_noise_ds_Sv,
        dataset_name='ml_dataset',
        ml_result_name='hdbscan_results',
        plot_window=[0, '${inputs.max_depth}'],
        time_min=time_min,
        time_max=time_max,
        x_axis_units='datetime',
        y_axis_units='meters',
        dpi=50,
        cluster_stats_dpi=200,
        overlay_line_var='sw_dive_profile',
        cluster_colors=['#9D00FF', '#35E200', '#FF0000', '#2F00FF', '#FF8800', '#FFFB1C', '#FF44E9', '#00BE8BCA', '#00FFE5', '#3D8D00FF', '#9C67FFFF'],
        cluster_stats_sv_data_var='Sv',
    )
    ```

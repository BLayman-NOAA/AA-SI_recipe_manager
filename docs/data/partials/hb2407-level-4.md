=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-4" data-drill="inc:sub_recipes/processing_lvl_4.yaml" markdown>

    - `reshape_ml`: [`reshape_for_ml`](../ops/reshape_for_ml.md)
    - `add_aux_features`: [`add_auxiliary_features`](../ops/add_auxiliary_features.md)
    - `normalize_ml`: [`normalize_ml_data`](../ops/normalize_ml_data.md)
    - `plot_normalized_ml`: [`plot_ml_echogram`](../ops/plot_ml_echogram.md)
    - `run_hdbscan`: [`run_hdbscan`](../ops/run_hdbscan.md)
    - `embed_results`: [`embed_clustering_results`](../ops/embed_clustering_results.md)
    - `plot_clustering_report`: [`plot_clustering_report`](../ops/plot_clustering_report.md)

    </div>

=== "YAML"

    [Download `processing_lvl_4.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_4.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/sub_recipes/processing_lvl_4.yaml

    # Processing level 4: machine learning.
    #
    # Reshapes MVBS for ML, adds auxiliary features, normalizes the ML dataset,
    # runs HDBSCAN clustering, and writes the clustering report.

    recipe:
      name: "processing_lvl_4"
      description: "ML reshape, normalization, clustering, and reporting."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      mvbs:
        type: dataset
        description: "MVBS dataset with line overlay variables added."
      echodata:
        type: echodata
        required: false
        description: >
          EchoData for the 'seafloor_depth' and 'altitude' features, used only when
          seafloor_depth below is not wired. This recipe's features do not need it.
      seafloor_depth:
        type: dataarray
        required: false
        description: >
          Seafloor line over ping_time from a detect_seafloor step, preferred over
          echodata for the 'seafloor_depth' and 'altitude' features. In a per-file
          pipeline, wire a merge_datasets step that collects the per-file lines.
      overlay_line_var:
        type: str
        default: "overlay_line"
        description: "Line variable to overlay on the clustering report plot."
      sv_clean_unmasked:
        type: dataset
        description: "Pre-sparse-mask Sv, the backdrop and ping reference for the ML and cluster plots."
      cell_stats_sv:
        type: dataset
        description: "Per-cell statistics dataset produced from the clean unmasked Sv dataset."
      min_depth:
        type: float
        default: 0
        description: "Shallowest depth in meters shown by the ML and clustering echograms."
      max_depth:
        type: float
        default: 100
        description: "Deepest depth in meters shown by the ML and clustering echograms."
      time_min:
        type: str
        description: "Start of the ML and clustering echograms, UTC. Omit to start at the first ping."
      time_max:
        type: str
        description: "End of the ML and clustering echograms, UTC. Omit to run to the last ping."
      x_axis_units:
        type: str
        default: "datetime"
        description: "Units for the x-axis of the ML and clustering echograms."
      y_axis_units:
        type: str
        default: "meters"
        description: "Units for the y-axis of the ML and clustering echograms."

      # HDBSCAN tuning, exposed so a parent recipe can sweep it.
      hdbscan_min_cluster_size_fraction:
        type: float
        default: 0.001
        description: "Smallest cluster HDBSCAN keeps, as a fraction of the sampled points."
      hdbscan_min_samples:
        type: int
        default: 7
        description: "Neighbourhood size for judging density. Higher leaves more points as noise."
      hdbscan_cluster_selection_method:
        type: str
        default: "eom"
        description: "How clusters are chosen: 'eom' for excess of mass, 'leaf' for the finest leaves."

    steps:

      - id: reshape_ml
        op: reshape_for_ml
        inputs:
          ds_MVBS: ${inputs.mvbs}
        params:
          data_var: "Sv"
          dataset_name: "ml_dataset"
          feature_strategy: "mean_centered"
          baseline_channel: 0

      - id: add_aux_features
        op: add_auxiliary_features
        inputs:
          ds_ml: ${reshape_ml.ds_ml_ready}
          echodata: ${inputs.echodata}
          ds_sv: ${inputs.cell_stats_sv}
          seafloor_depth: ${inputs.seafloor_depth}
        params:
          dataset_name: "ml_dataset"
          features:
            - cell_cv

      - id: normalize_ml
        op: normalize_ml_data
        checkpoint: always
        inputs:
          ds_ml: ${add_aux_features.ds_ml_ready}
        params:
          method: "flatten"
          dataset_name: "ml_dataset"
          normalization_name: "normalized_flatten_mean_centered"
          shift_positive: false

      - id: plot_normalized_ml
        op: plot_ml_echogram
        regenerate: if-missing
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
          ds_Sv: ${inputs.sv_clean_unmasked}
        params:
          dataset_name: "ml_dataset"
          normalization_name: "normalized_flatten_mean_centered"
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 30

      - id: run_hdbscan
        op: run_hdbscan
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
        params:
          dataset_name: "ml_dataset"
          normalization_name: "normalized_flatten_mean_centered"
          ml_result_name: "hdbscan_results"
          min_cluster_size_fraction: ${inputs.hdbscan_min_cluster_size_fraction}
          min_samples: ${inputs.hdbscan_min_samples}
          sample_size: 1000000
          cluster_selection_method: ${inputs.hdbscan_cluster_selection_method}
          use_hdbscan: true
          soft_membership_threshold: 0.6

      - id: embed_results
        op: embed_clustering_results
        checkpoint: always
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
          clustering_results: ${run_hdbscan.clustering_results}
        params:
          dataset_name: "ml_dataset"
          ml_result_name: "hdbscan_results"

      - id: plot_clustering_report
        op: plot_clustering_report
        regenerate: if-missing
        inputs:
          ds_normalized: ${embed_results.ds_normalized}
          clustering_results: ${run_hdbscan.clustering_results}
          clustering_model: ${run_hdbscan.clustering_model}
          ds_Sv: ${inputs.sv_clean_unmasked}
        params:
          dataset_name: "ml_dataset"
          ml_result_name: "hdbscan_results"
          # [min_depth, max_depth]; time_min and time_max set the horizontal window.
          plot_window:
            - ${inputs.min_depth}
            - ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 50
          cluster_stats_dpi: 200
          overlay_line_var: ${inputs.overlay_line_var}
          cluster_colors:
            - "#9D00FF"
            - "#35E200"
            - "#FF0000"
            - "#2F00FF"
            - "#FF8800"
            - "#FFFB1C"
            - "#FF44E9"
            - "#00BE8BCA"
            - "#00FFE5"
            - "#3D8D00FF"
            - "#9C67FFFF"
          cluster_stats_sv_data_var: "Sv"
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `reshape_ml`
    **Op:** `reshape_for_ml`

    Reshape an MVBS Dataset into a flat, ML-ready xarray Dataset where each observation corresponds to one spatial or temporal bin.

    **Parameters:**
    - `data_var` str - Name of the data variable to use as features
    - `dataset_name` str - Name this ML dataset is stored under inside the returned Dataset. Every later ML step (add_auxiliary_features, normalize_ml_data, run_hdbscan, the ML plots) takes the same dataset_name to find the feature matrix again, so a recipe can carry several independent ML datasets side by side.

    - `feature_strategy` str - Feature construction strategy (e.g. 'mean_centered')
    - `baseline_channel` int - Channel index used as the baseline when computing relative features

    ```python
    # --- Parameters ---
    _reshape_ml__data_var = 'Sv'
    _reshape_ml__dataset_name = 'ml_dataset'
    _reshape_ml__feature_strategy = 'mean_centered'
    _reshape_ml__baseline_channel = 0

    reshape_ml_ds_ml_ready = reshape_data_for_ml(
        ds_Sv=ds_MVBS,
        data_var=_reshape_ml__data_var,
        dataset_name=_reshape_ml__dataset_name,
        feature_strategy=_reshape_ml__feature_strategy,
        baseline_channel=_reshape_ml__baseline_channel,
    )
    ```

    #### Step: `add_aux_features`
    **Op:** `add_auxiliary_features`

    Append auxiliary coordinate-derived features (e.g. depth, ping_time_seconds) to the flattened ML-ready Dataset. Optional step; omit when no auxiliary features are needed.

    **Parameters:**
    - `dataset_name` str - ML dataset to append the features to. Must match the dataset_name used by reshape_for_ml.

    - `features` list - List of built-in feature name strings to append. Supported built-ins: 'depth', 'ping_time_seconds', 'seafloor_depth', 'altitude', 'cell_cv'. 'seafloor_depth' and 'altitude' require the echodata input. 'cell_cv' requires the ds_sv input (from compute_per_cell_statistics).

    ```python
    # --- Parameters ---
    _add_aux_features__dataset_name = 'ml_dataset'
    _add_aux_features__features = ['cell_cv']

    add_aux_features_ds_ml_ready = add_auxiliary_features(
        ds_ml_ready=reshape_ml_ds_ml_ready,
        echodata=echodata,
        ds_sv=compute_cell_stats_ds_Sv,
        seafloor_depth=seafloor_depth,
        dataset_name=_add_aux_features__dataset_name,
        features=_add_aux_features__features,
    )
    ```

    #### Step: `normalize_ml`
    **Op:** `normalize_ml_data`

    Normalize ML features in the flattened Dataset. Supports per-feature normalization methods via per_group_methods.

    **Parameters:**
    - `method` str - Global normalization method (e.g. 'flatten', 'minmax', 'zscore')
    - `dataset_name` str - ML dataset to normalize. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Name this normalized result is stored under. run_hdbscan, the ML plots, and filter_normalized_by_cluster take the same name to find it, so several normalizations of one dataset can coexist and be compared.

    - `shift_positive` bool - Shift all values to be non-negative after normalization
    - `feature_weights` list - Per-feature weight multipliers applied before normalization
    - `per_group_methods` dict - Map of feature name to individual normalization method override

    ```python
    # --- Parameters ---
    _normalize_ml__method = 'flatten'
    _normalize_ml__dataset_name = 'ml_dataset'
    _normalize_ml__normalization_name = 'normalized_flatten_mean_centered'
    _normalize_ml__shift_positive = False

    normalize_ml_ds_normalized = normalize_data(
        ds_ml_ready=add_aux_features_ds_ml_ready,
        method=_normalize_ml__method,
        dataset_name=_normalize_ml__dataset_name,
        normalization_name=_normalize_ml__normalization_name,
        shift_positive=_normalize_ml__shift_positive,
    )
    ```

    #### Step: `plot_normalized_ml`
    **Op:** `plot_ml_echogram`

    Render a normalized ML feature echogram for inspection. This is a sink step: it produces a side-effect (figure) with no data output.

    **Parameters:**
    - `dataset_name` str - ML dataset to plot. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Normalized feature set to plot. Must match the normalization_name used by normalize_ml_data.

    - `min_depth` float - Minimum depth (y-axis) in meters
    - `max_depth` float - Maximum depth (y-axis) in meters. Omit to use the deepest depth present in the data.

    - `ping_min` int - First ping index to display
    - `ping_max` int - Last ping index to display. Omit to display through the last ping.
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of ping_min. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of ping_max. Omit to window by ping index.

    - `x_axis_units` str - Units for the x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'meters' (distance travelled, derived from the GPS track), or 'bins' (MVBS-derived data only).

    - `y_axis_units` str - Units for the y-axis: 'meters' (default), 'range_sample', or 'bins' (MVBS-derived data only).

    - `save_image` bool - Save the rendered figure to an image file; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display the figure interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_normalized_ml__dataset_name = 'ml_dataset'
    _plot_normalized_ml__normalization_name = 'normalized_flatten_mean_centered'
    _plot_normalized_ml__min_depth = 0
    _plot_normalized_ml__max_depth = max_depth
    _plot_normalized_ml__time_min = time_min
    _plot_normalized_ml__time_max = time_max
    _plot_normalized_ml__x_axis_units = 'datetime'
    _plot_normalized_ml__y_axis_units = 'meters'
    _plot_normalized_ml__dpi = 30

    plot_flattened_data_echogram(
        ds_ml=normalize_ml_ds_normalized,
        ds_Sv_original=remove_noise_ds_Sv,
        ml_dataset_name=_plot_normalized_ml__dataset_name,
        ml_specific_data_name=_plot_normalized_ml__normalization_name,
        min_depth=_plot_normalized_ml__min_depth,
        max_depth=_plot_normalized_ml__max_depth,
        time_min=_plot_normalized_ml__time_min,
        time_max=_plot_normalized_ml__time_max,
        x_axis_units=_plot_normalized_ml__x_axis_units,
        y_axis_units=_plot_normalized_ml__y_axis_units,
        dpi=_plot_normalized_ml__dpi,
    )
    ```

    #### Step: `run_hdbscan`
    **Op:** `run_hdbscan`

    Run HDBSCAN unsupervised clustering on normalized feature data. Pure-compute step: accepts normalized features and returns a clustering result dict without modifying any Dataset. Designed to be a sweep target for hyperparameter comparison.

    **Parameters:**
    - `dataset_name` str - ML dataset to cluster. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Normalized feature set to cluster. Must match the normalization_name used by normalize_ml_data.

    - `ml_result_name` str - Name this clustering result is stored under. embed_clustering_results and plot_clustering_report take the same name to find it, so a recipe can keep several clusterings of one dataset side by side.

    - `min_cluster_size` int - Absolute minimum cluster size. Mutually exclusive with min_cluster_size_fraction.
    - `min_cluster_size_fraction` float - Fraction of the points actually fitted used to derive min_cluster_size when min_cluster_size is omitted. Mutually exclusive with min_cluster_size. When omitted, the ML function defaults to 0.03.
    Taken of the subsample when sample_size binds, so the number means the same thing at any scale: a cluster must hold this share of what the model saw. Raising sample_size therefore raises the absolute floor proportionally, and lowering it lowers it.

    - `min_samples` int - Neighbourhood size HDBSCAN uses to judge how dense a point is. Higher values make the run more conservative, pushing sparse points into noise instead of into a cluster.

    - `sample_size` int - Cap on how many samples are clustered. A survey flattens to more points than HDBSCAN can handle in memory, so when the dataset is larger than this a random subset of this size is clustered and the remaining points receive no label at all. Set it above the sample count to cluster everything.

    - `cluster_selection_method` str - HDBSCAN cluster selection method ('eom' or 'leaf')
    - `use_hdbscan` bool - Use HDBSCAN; set false to fall back to a simpler clustering method
    - `epsilon` float - DBSCAN epsilon distance parameter
    - `find_background_cluster` bool - Run background-cluster detection path instead of standard HDBSCAN. When true, background_label output is populated.

    - `soft_membership_threshold` float - Reassign noise points via soft-membership probabilities when set. null disables soft-membership reassignment.

    ```python
    # --- Parameters ---
    _run_hdbscan__dataset_name = 'ml_dataset'
    _run_hdbscan__normalization_name = 'normalized_flatten_mean_centered'
    _run_hdbscan__ml_result_name = 'hdbscan_results'
    _run_hdbscan__min_cluster_size_fraction = 0.001
    _run_hdbscan__min_samples = 7
    _run_hdbscan__sample_size = 1000000
    _run_hdbscan__cluster_selection_method = 'eom'
    _run_hdbscan__use_hdbscan = True
    _run_hdbscan__soft_membership_threshold = 0.6

    _result = run_hdbscan(
        ds_normalized=normalize_ml_ds_normalized,
        dataset_name=_run_hdbscan__dataset_name,
        normalization_name=_run_hdbscan__normalization_name,
        ml_result_name=_run_hdbscan__ml_result_name,
        min_cluster_size_fraction=_run_hdbscan__min_cluster_size_fraction,
        min_samples=_run_hdbscan__min_samples,
        sample_size=_run_hdbscan__sample_size,
        cluster_selection_method=_run_hdbscan__cluster_selection_method,
        use_hdbscan=_run_hdbscan__use_hdbscan,
        soft_membership_threshold=_run_hdbscan__soft_membership_threshold,
    )
    clustering_results = _result['clustering_results']
    clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    cluster_labels = _result['cluster_labels']
    ```

    #### Step: `embed_results`
    **Op:** `embed_clustering_results`

    Embed HDBSCAN clustering results into the normalized ML Dataset. Accepts a single result dict from run_hdbscan, or a collected list of result dicts when used with the collect pattern for ensemble runs. For a list, one cluster-label variable is embedded per collected result using deterministic names derived from ml_result_name.

    **Parameters:**
    - `dataset_name` str - ML dataset the labels are embedded into. Must match the dataset_name used by reshape_for_ml.

    - `ml_result_name` str - Name the embedded cluster labels are stored under. Normally the ml_result_name from run_hdbscan, so plot_clustering_report finds the same result. For a collected list of results this is the stem the per-result variable names are derived from.

    ```python
    # --- Parameters ---
    _embed_results__dataset_name = 'ml_dataset'
    _embed_results__ml_result_name = 'hdbscan_results'

    _result = embed_clustering_results(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=clustering_results,
        dataset_name=_embed_results__dataset_name,
        ml_result_name=_embed_results__ml_result_name,
    )
    embed_results_ds_normalized = _result['ds_normalized']
    gridded_results = _result['gridded_results']
    ```

    #### Step: `plot_clustering_report`
    **Op:** `plot_clustering_report`

    Render a full clustering report for one or more clustering results. Produces the clustering echogram, per-cluster statistics report, and the HDBSCAN hierarchy plot when the clustering result includes a model and hierarchy plotting is applicable. This is a sink step: it produces figures with no data output.

    **Parameters:**
    - `dataset_name` str - ML dataset to report on. Must match the dataset_name used by reshape_for_ml.

    - `ml_result_name` str - Clustering result to report on. Must match the ml_result_name used by embed_clustering_results, which is the name the labels were stored under in ds_normalized.

    - `plot_window` list - [min_depth, max_depth, ping_min, ping_max] for echogram display
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of the ping_min entry in plot_window. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of the ping_max entry in plot_window. Omit to window by ping index.

    - `x_axis_units` str - Units for the clustering echogram x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'bins', or 'meters'.

    - `y_axis_units` str - Units for the clustering echogram y-axis ('meters', 'range_sample', or 'bins')
    - `overlay_line_var` str - Name of a variable in ds_normalized to draw as a line overlay
    - `overlay_windows` list - Window dicts (as plan_dive_datasets emits) whose line files are drawn onto the echogram. Each window's lines are read onto its own pings, so every window is a separate segment and windows that overlap in time each keep their own line. Empty draws nothing.

    - `overlay_window_keys` list - Window keys naming the line files to draw. Defaults to the three dive profile keys dive_fit_evl, dive_u99_evl and dive_l99_evl. A window missing a key is skipped for that key.

    - `overlay_fit_style` dict - Matplotlib line keywords for the fitted dive profile drawn by overlay_windows, for example {color: "#23FFFF", linewidth: 3.0}. Merged over the built-in style, so naming only one key leaves the rest alone. Empty uses the built-in style: solid cyan.

    - `overlay_bound_style` dict - The same for both 99% confidence bound lines, for example {color: "#D08BFF", linestyle: ":"}. Empty uses the built-in style: dotted violet, thinner than the fitted profile. Both bounds always share one style, since they are two edges of one interval.

    - `normalization_name` str - Normalized feature set to plot statistics for when cluster_stats_sv_data_var is "ml_features" and no clustering_results input is wired. Ignored otherwise; with a result wired, its own normalization_name is used.

    - `cluster_colors` list - Hex color strings for each cluster label
    - `y_to_x_aspect_ratio_override` float - Override the echogram aspect ratio; null uses the default
    - `cluster_stats_sv_data_var` str - Sv variable name used when computing per-cluster statistics, or "ml_features" to plot the flattened ML features used for clustering
    - `cluster_stats_compute_pairwise_differences` bool - Compute inter-channel diffs in cluster statistics, taken against the second-lowest-frequency channel

    - `cluster_stats_dpi` int - DPI for the cluster statistics figure, which holds much smaller text than the echogram. Null falls back to dpi.

    - `save_image` bool - Save the rendered figures to image files; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display figures interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_clustering_report__dataset_name = 'ml_dataset'
    _plot_clustering_report__ml_result_name = 'hdbscan_results'
    _plot_clustering_report__plot_window = [0, '${inputs.max_depth}']
    _plot_clustering_report__time_min = time_min
    _plot_clustering_report__time_max = time_max
    _plot_clustering_report__x_axis_units = 'datetime'
    _plot_clustering_report__y_axis_units = 'meters'
    _plot_clustering_report__dpi = 50
    _plot_clustering_report__cluster_stats_dpi = 200
    _plot_clustering_report__overlay_line_var = 'sw_dive_profile'
    _plot_clustering_report__cluster_colors = ['#9D00FF', '#35E200', '#FF0000', '#2F00FF', '#FF8800', '#FFFB1C', '#FF44E9', '#00BE8BCA', '#00FFE5', '#3D8D00FF', '#9C67FFFF']
    _plot_clustering_report__cluster_stats_sv_data_var = 'Sv'

    plot_clustering_report(
        ds_normalized=embed_results_ds_normalized,
        clustering_results=clustering_results,
        clustering_model=clustering_model,
        ds_Sv=remove_noise_ds_Sv,
        dataset_name=_plot_clustering_report__dataset_name,
        ml_result_name=_plot_clustering_report__ml_result_name,
        plot_window=_plot_clustering_report__plot_window,
        time_min=_plot_clustering_report__time_min,
        time_max=_plot_clustering_report__time_max,
        x_axis_units=_plot_clustering_report__x_axis_units,
        y_axis_units=_plot_clustering_report__y_axis_units,
        dpi=_plot_clustering_report__dpi,
        cluster_stats_dpi=_plot_clustering_report__cluster_stats_dpi,
        overlay_line_var=_plot_clustering_report__overlay_line_var,
        cluster_colors=_plot_clustering_report__cluster_colors,
        cluster_stats_sv_data_var=_plot_clustering_report__cluster_stats_sv_data_var,
    )
    ```

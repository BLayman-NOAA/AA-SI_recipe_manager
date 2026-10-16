=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-4" data-drill="inc:sub_recipes/processing_lvl_4.yaml" markdown>

    - `reshape_ml`: [`reshape_for_ml`](../ops/reshape_for_ml.md)
    - `add_aux_features`: [`add_auxiliary_features`](../ops/add_auxiliary_features.md)
    - `normalize_ml`: [`normalize_ml_data`](../ops/normalize_ml_data.md)
    - `run_hdbscan`: [`run_hdbscan`](../ops/run_hdbscan.md)
    - `label_all_points`: [`assign_clusters_to_all_points`](../ops/assign_clusters_to_all_points.md)
    - `embed_results`: [`embed_clustering_results`](../ops/embed_clustering_results.md)
    - `plot_clustering_report`: [`plot_clustering_report`](../ops/plot_clustering_report.md)

    </div>

=== "YAML"

    [Download `processing_lvl_4.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_4.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_4.yaml

    # Processing level 4: machine learning.
    #
    #   reshape -> add cell_cv -> normalize -> HDBSCAN -> label every point
    #           -> embed -> clustering report
    #
    # Clusters any MVBS on a shared grid with its per-cell statistics. The dive
    # windows only feed the report's line overlays.
    #
    # The labels are read from the embed_results checkpoint, and run_hdbscan is
    # checkpointed too, so redrawing the report or the tables never refits.
    # Plots use x_axis_units "bins": the dives span 38 days, so a clock-time axis
    # draws each dive as a hairline.
    # Cluster ids change between runs, so read the codes against their per-code
    # Sv in the level 5 CSVs, not against an earlier analysis's numbering.

    recipe:
      name: "hb1603_processing_lvl_4"
      description: "Pooled HDBSCAN clustering of an MVBS grid, embedded back onto the grid, with the clustering report."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      mvbs:
        type: Dataset
        description: "MVBS on one shared grid. Wire it to rechunk_dive_mvbs.ds_Sv."
      cell_stats_sv:
        type: Dataset
        description: "Per-cell statistics on the same grid. Wire it to merge_dive_files_cell_stats.merged."
      dive_windows:
        type: list
        description: >
          Windows whose dive lines the report overlays. Wire it to
          plan_dives.windows, or pass an empty list for no overlay.
      sample_size:
        type: int
        default: 1200000
        description: >
          Cap on the points HDBSCAN is fitted on, set above the pooled grid's
          roughly 1.16 M cells so every point is fitted.
      min_cluster_size_fraction:
        type: float
        default: 0.01
        description: "Minimum cluster size as a fraction of the points HDBSCAN is fitted on."
      min_samples:
        type: int
        default: 4
        description: "HDBSCAN min_samples: how conservative the density estimate is. Larger means more points called noise."

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
          ds_sv: ${inputs.cell_stats_sv}
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

      # The fitted model is cached as a pickle, tied to this environment's hdbscan
      # and scikit-learn. If it fails to load, delete this step's cache entry.
      - id: run_hdbscan
        op: run_hdbscan
        checkpoint: always
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
        params:
          dataset_name: "ml_dataset"
          normalization_name: "normalized_flatten_mean_centered"
          ml_result_name: "hdbscan_results"
          min_cluster_size_fraction: ${inputs.min_cluster_size_fraction}
          min_samples: ${inputs.min_samples}
          sample_size: ${inputs.sample_size}
          cluster_selection_method: "leaf"
          use_hdbscan: true
          soft_membership_threshold: .1

      # Labels any points past sample_size, which would otherwise come back NaN.
      # Not checkpointed, since embed_results holds its labels.
      - id: label_all_points
        op: assign_clusters_to_all_points
        checkpoint: never
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
          clustering_results: ${run_hdbscan.clustering_results}
          clustering_model: ${run_hdbscan.clustering_model}
        params:
          dataset_name: "ml_dataset"
          normalization_name: "normalized_flatten_mean_centered"

      - id: embed_results
        op: embed_clustering_results
        checkpoint: always
        inputs:
          ds_normalized: ${normalize_ml.ds_normalized}
          clustering_results: ${label_all_points.clustering_results}
        params:
          dataset_name: "ml_dataset"
          ml_result_name: "hdbscan_results"

      # Labels come from embed_results. The run_hdbscan outputs feed the hierarchy
      # panel only.
      - id: plot_clustering_report
        op: plot_clustering_report
        regenerate: if-missing
        inputs:
          ds_normalized: ${embed_results.ds_normalized}
          clustering_results: ${run_hdbscan.clustering_results}
          clustering_model: ${run_hdbscan.clustering_model}
          ds_Sv: ${inputs.mvbs}
        params:
          dataset_name: "ml_dataset"
          ml_result_name: "hdbscan_results"
          # Plots against bin index so the dives sit side by side; see the header.
          x_axis_units: "bins"
          # Pins a 16:9 panel so the figure fits a screen.
          y_to_x_aspect_ratio_override: 1.78
          # Down to 2400 m, below the deepest seabed on these dives.
          plot_window: [0, 2400]
          cluster_stats_sv_data_var: "Sv"
          # Each dive's profile and 99% bounds, as its own segment.
          overlay_windows: ${inputs.dive_windows}
          # Each style merges over the op's default. linestyle takes matplotlib's
          # spellings: ":" dotted, "--" dashed, "-." dash-dot, "-" solid.
          overlay_fit_style: {color: "#23FFFF", linewidth: 3.0}
          overlay_bound_style: {color: "#D08BFF", linewidth: 1.8, linestyle: ":"}
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

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
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
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
        ds_sv=merge_dive_files_cell_stats_merged,
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
    _run_hdbscan__min_cluster_size_fraction = 0.01
    _run_hdbscan__min_samples = 4
    _run_hdbscan__sample_size = 1200000
    _run_hdbscan__cluster_selection_method = 'leaf'
    _run_hdbscan__use_hdbscan = True
    _run_hdbscan__soft_membership_threshold = 0.1

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
    run_hdbscan_clustering_results = _result['clustering_results']
    run_hdbscan_clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    run_hdbscan_cluster_labels = _result['cluster_labels']
    ```

    #### Step: `label_all_points`
    **Op:** `assign_clusters_to_all_points`

    Extend a subsampled clustering to every point in the dataset, so no cell is left unlabelled.
    ``run_hdbscan`` clusters at most ``sample_size`` points, and above that the remaining points receive no label at all - they come back NaN once ``embed_clustering_results`` regrids the result. On a survey-scale ML dataset that is most of the data. This step projects the fitted structure onto the unlabelled points with hdbscan's ``approximate_predict``, which walks each point down the condensed tree the fit produced and returns the cluster it falls into, or -1 when it falls outside every cluster.
    Points the fit did see keep their fitted label rather than a predicted one: the fit is the more authoritative answer where it exists, and only the remainder is being filled in.
    Place it between ``run_hdbscan`` and ``embed_clustering_results``. The output is the same shape ``run_hdbscan`` produces, so everything downstream (``embed_clustering_results``, ``plot_clustering_report``, ``merge_clustering_passes``) consumes it unchanged.
    CHECKPOINTING. This step needs the fitted model, and a checkpointed clustering_results Dataset does not carry one: converting a result for Zarr drops the model and records only model_type and model_available. The clustering_model port is a plain object, so it falls back to pickle format, which the tiered store refuses to write to the shared survey tier. To checkpoint this step without pickle, set keep_model: false: the model port is then null, every output is Zarr or JSON, and a downstream plot_clustering_report simply skips the hierarchy panel. Otherwise keep this step adjacent to the run_hdbscan that feeds it, in the user tier, or set checkpoint: never and let it recompute.

    **Parameters:**
    - `dataset_name` str - ML dataset the clustering belongs to. Must match the dataset_name used by reshape_for_ml and run_hdbscan.

    - `normalization_name` str - Normalized feature set that was clustered. Must match the normalization_name used by normalize_ml_data and run_hdbscan.

    - `ml_result_name` str - Name the extended result is stored under. Leave unset to keep the name already on the incoming result, which is what keeps a downstream embed_clustering_results / plot_clustering_report pair pointing at it without changes.

    - `batch_size` int - Rows predicted per approximate_predict call. The prediction materialises intermediate arrays proportional to the batch, so this is the memory knob; lower it if a survey-scale run runs out of memory. Batching does not change the labels.

    - `keep_model` bool - Pass the fitted model through on the clustering_model output. Set false when this step is checkpointed and pickle is unwanted; the model is only used by the hierarchy panel of plot_clustering_report, which is skipped when no model reaches it.

    ```python
    # --- Parameters ---
    _label_all_points__dataset_name = 'ml_dataset'
    _label_all_points__normalization_name = 'normalized_flatten_mean_centered'

    _result = assign_clusters_by_prediction(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        dataset_name=_label_all_points__dataset_name,
        normalization_name=_label_all_points__normalization_name,
    )
    label_all_points_clustering_results = _result['clustering_results']
    label_all_points_clustering_model = _result['clustering_model']
    label_all_points_cluster_labels = _result['cluster_labels']
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
        clustering_results=label_all_points_clustering_results,
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
    _plot_clustering_report__x_axis_units = 'bins'
    _plot_clustering_report__y_to_x_aspect_ratio_override = 1.78
    _plot_clustering_report__plot_window = [0, 2400]
    _plot_clustering_report__cluster_stats_sv_data_var = 'Sv'
    _plot_clustering_report__overlay_windows = windows

    plot_clustering_report(
        ds_normalized=embed_results_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
        dataset_name=_plot_clustering_report__dataset_name,
        ml_result_name=_plot_clustering_report__ml_result_name,
        x_axis_units=_plot_clustering_report__x_axis_units,
        y_to_x_aspect_ratio_override=_plot_clustering_report__y_to_x_aspect_ratio_override,
        plot_window=_plot_clustering_report__plot_window,
        cluster_stats_sv_data_var=_plot_clustering_report__cluster_stats_sv_data_var,
        overlay_windows=_plot_clustering_report__overlay_windows,
        overlay_fit_style={'color': '#23FFFF', 'linewidth': 3.0},
        overlay_bound_style={'color': '#D08BFF', 'linewidth': 1.8, 'linestyle': ':'},
    )
    ```

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
    # Takes any MVBS on a shared grid plus matching per-cell statistics. It knows
    # nothing about dives except the optional window list the report draws line
    # overlays from. embed_results holds the labels the report and the SVCode
    # tables read; run_hdbscan is checkpointed, as a pickle, so the report gets the
    # fitted model without refitting.
    #
    # THE PLOTS MUST USE x_axis_units: bins
    #
    #   The 13 dives hold 214 minutes of data across 38 days, so a clock-time axis
    #   ("datetime" or "seconds") draws each dive as a hairline. "bins" plots
    #   against MVBS bin index, so the dives sit side by side.
    #
    # SIZING HDBSCAN FOR THE POOLED SET
    #
    #   sample_size 1200000 is not expected to bind: the pooled grid is about
    #   1.16 M cells, so HDBSCAN fits every point. min_cluster_size_fraction is a
    #   fraction of the points fitted; 0.01 of the pooled set is a large enough
    #   floor that one dive's local structure does not become a survey-wide code.
    #
    # CLUSTER IDS ARE RUN SCOPED
    #
    #   HDBSCAN ids are not stable across runs, so an SVCode integer does not carry
    #   the fixed meaning codes 1, 2 and 3 had in the previous analysis. Read the
    #   tables against the per-code Sv statistics in the SVCode CSVs.

    recipe:
      name: "hb1603_processing_lvl_4"
      description: "Pooled HDBSCAN clustering of an MVBS grid, embedded back onto the grid, with the clustering report."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      # Wire it to rechunk_dive_mvbs.ds_Sv.
      mvbs:
        type: Dataset
        description: "Gridded MVBS to cluster, one ML sample per cell, and the report backdrop."
      # Wire it to merge_dive_files_cell_stats.merged.
      cell_stats_sv:
        type: Dataset
        description: "Per-cell statistics, such as the coefficient of variation, on the MVBS grid. Needed when the cell_cv feature is listed."
      # Wire it to plan_dives.windows.
      dive_windows:
        type: list
        description: "Windows whose dive lines the cluster report draws on the echogram, a list of dicts with start and end. An empty list draws none."
      # Read SIZING HDBSCAN in the header before lowering it.
      sample_size:
        type: int
        default: 1200000
        description: "Cap on the points HDBSCAN is fitted on; points over the cap are dropped at random and get no label. Set it above the valid cell count."
      min_cluster_size_fraction:
        type: float
        default: 0.01
        description: "Smallest cluster HDBSCAN keeps, as a fraction of the points it is fitted on."
      min_samples:
        type: int
        default: 4
        description: "Neighbourhood size HDBSCAN uses to judge density; higher values leave more points unclustered as noise."

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

      # Checkpointed so the report gets the fitted model without refitting. The
      # model saves as pickle, tied to this environment's hdbscan and scikit-learn
      # versions and refused by the shared survey tier. If it fails to load,
      # delete this step's cache entry and the fit reruns once.
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

      # The guard for sample_size binding: cells past the cap would otherwise come
      # back unlabelled. checkpoint: never because embed_results holds the labels,
      # and a rerun gets the model from run_hdbscan's checkpoint.
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

      # Labels come from the embed_results checkpoint. clustering_results and
      # clustering_model feed the HDBSCAN hierarchy panel, loaded from
      # run_hdbscan's checkpoint, so redrawing the report does not refit.
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
          # The op defaults to "datetime"; see THE PLOTS MUST USE x_axis_units: bins.
          x_axis_units: "bins"
          # Pin a 16:9 panel; the data aspect, about 1300 bins by 2400 m, would
          # otherwise give a panel taller than it is wide.
          y_to_x_aspect_ratio_override: 1.78
          # The dives use the water column down to the seabed, 2343 m at deepest.
          plot_window: [0, 2400]
          cluster_stats_sv_data_var: "Sv"
          # Per-dive profile and 99% bounds, each dive its own segment; see THE
          # POOLED GRID IS THE ANALYSIS SET in processing_lvl_3_user.yaml.
          overlay_windows: ${inputs.dive_windows}
          # Each style dict is merged over the op's default, so naming just one
          # key restyles that and leaves the others. linestyle takes matplotlib's
          # spellings: ":" dotted, "--" dashed, "-." dash-dot, "-" solid.
          overlay_fit_style: {color: "#23FFFF", linewidth: 3.0}
          overlay_bound_style: {color: "#D08BFF", linewidth: 1.8, linestyle: ":"}
    ```

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import assign_clusters_by_prediction
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report

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
    ```

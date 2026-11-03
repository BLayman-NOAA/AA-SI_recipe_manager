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

    recipe:
      name: "processing_lvl_4"
      description: "ML reshape, normalization, clustering, and reporting."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      mvbs:
        type: dataset
        description: >
          Gridded MVBS to cluster, one ML sample per cell, with any line overlay
          variables already added.
      # The feature list these recipes use does not need echodata.
      echodata:
        type: echodata
        required: false
        description: >
          Raw EchoData supplying the echosounder's own bottom pick for the
          'seafloor_depth' and 'altitude' features. Used only when seafloor_depth
          is not wired.
      # In a per-file pipeline wire a merge_datasets step that collects the
      # per-file lines; referencing the mapped step directly fails on a resumed
      # run, since the runner prunes a mapped chain once its fan-in is a cache hit.
      seafloor_depth:
        type: dataarray
        required: false
        description: >
          Seafloor line over ping_time, preferred over echodata for the
          'seafloor_depth' and 'altitude' features so they use the line the
          masking used.
      overlay_line_var:
        type: str
        default: "overlay_line"
        description: "Name of the line variable drawn over the clustering report plots."
      sv_clean_unmasked:
        type: dataset
        description: >
          Noise-removed Sv before sparse-bin masking, used as the backdrop and ping
          reference for the ML and cluster echograms.
      cell_stats_sv:
        type: dataset
        description: >
          Per-cell statistics, such as the coefficient of variation, on the MVBS
          grid. Needed when the cell_cv feature is listed.
      min_depth:
        type: float
        default: 0
        description: "Shallowest depth shown on the ML and clustering echograms, in metres."
      max_depth:
        type: float
        default: 100
        description: >
          Deepest depth shown on the ML and clustering echograms, in metres. Unset
          uses the deepest depth in the data.
      time_min:
        type: str
        description: >
          Start of the ML and clustering echogram window, as a UTC timestamp such
          as "2024-10-15T12:30". Unset starts at the first ping.
      time_max:
        type: str
        description: >
          End of the ML and clustering echogram window, as a UTC timestamp such as
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

      # HDBSCAN tuning, exposed so a parent recipe can sweep it.
      hdbscan_min_cluster_size_fraction:
        type: float
        default: 0.001
        description: >
          Smallest cluster HDBSCAN keeps, as a fraction of the points it is fitted
          on.
      hdbscan_min_samples:
        type: int
        default: 7
        description: >
          Neighbourhood size HDBSCAN uses to judge density; higher values leave
          more points unclustered as noise.
      hdbscan_cluster_selection_method:
        type: str
        default: "eom"
        description: >
          How HDBSCAN draws clusters from its condensed tree: "eom" (excess of
          mass) or "leaf" (the finest leaves).

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
          # [min_depth, max_depth]. time_min and time_max below window the plot
          # horizontally, so the ping slots are left off.
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

=== "Python"

    The function calls this part of the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_visualization.echogram import plot_flattened_data_echogram
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report

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

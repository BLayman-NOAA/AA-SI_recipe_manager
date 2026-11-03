=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-1" data-drill="inc:sub_recipes/processing_lvl_1_per_file.yaml" markdown>

    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)

    </div>

=== "YAML"

    [Download `processing_lvl_1_per_file.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_1_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_1_per_file.yaml

    # Processing level 1: raw ingest.
    #
    #   read_raw -> combine_raw
    #
    # The top of the per-file chain: one instance per file, streamed from NCEI,
    # ending at a lazily opened EchoData. It knows nothing about calibration, so
    # the survey recipes continue it in processing_lvl_2_survey.yaml and the raw
    # calibration variant in processing_lvl_2_raw_cal.yaml. Nothing here is
    # checkpointed: read_raw writes scratch stores the run deletes.
    #
    # WHAT THE STEPS MAP OVER
    #
    #   per_file_source, set by the including recipe: normally
    #   ${query_ncei.raw_urls}, or for dive-only runs that query filtered by
    #   file_names. It is substituted into map_over textually, so the fingerprint
    #   sees the step reference. Every level continuing the chain needs the same
    #   value, or the chain splits.
    #
    # THE STEPS BELOW ARE FROZEN
    #
    #   A fingerprint folds in wiring, params and parents, not the declaring file,
    #   so every recipe including this addresses the same per-file checkpoints, and
    #   editing anything here invalidates the whole survey Sv. Instances are keyed
    #   on the raw file URL, not list position, but the catalogue query is a
    #   parent.
    #
    # INCLUDE LEVELS 1 TO 3 BACK TO BACK
    #
    #   They are one chain mapped over per_file_source. A non-mapped step declared
    #   between two of them splits it.

    recipe:
      name: "hb1603_processing_lvl_1_per_file"
      description: "Per-file raw ingest, streamed from NCEI."
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
      sonar_model:
        type: str
        default: "EK60"
        description: >
          Echosounder model as echopype names it, such as "EK60" or "EK80".

    steps:

      # checkpoint: never because the output is a scratch store the run deletes.
      # Keyed on its own URL, each instance resumes independently.
      #
      # intermediate_format is the run's largest local disk consumer: "zarr" and
      # "netcdf" keep one store per catalogue file until the run ends unless
      # disposed (below). "zarr" can live on a bucket through a gs:// temp_dir;
      # "none" holds each file's EchoData in memory instead. It is a literal, not
      # an input, because wiring it to an input changes read_raw's hash.
      - id: read_raw
        op: read_raw_files
        map_over: ${inputs.per_file_source}
        checkpoint: never
        # Delete each file's store when its instance finishes. Safe only while
        # every chain member a fan-in collects is checkpointed; an uncheckpointed
        # one would read the deleted store back as NaN. Level 2a's crop and level
        # 3's per-file grid and cell statistics are checkpointed for this.
        dispose_outputs: [raw_stores]
        execution:
          dask_config:
            scheduler: processes
        inputs:
          raw_file_paths:
            - ${_item}
        params:
          sonar_model: ${inputs.sonar_model}
          include_bot: false
          intermediate_format: "zarr"

      - id: combine_raw
        op: combine_raw_files
        map_over: ${inputs.per_file_source}
        inputs:
          raw_stores: ${read_raw.raw_stores}
    ```

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores

    raw_stores = []
    echodata = []
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

        ...  # HB1603 level 2, calibrated Sv, masks and noise removal

        ...  # HB1603 level 3, gridding and per-cell statistics
    ```

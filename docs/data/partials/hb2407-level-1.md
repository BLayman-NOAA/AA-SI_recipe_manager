=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-1" data-drill="inc:sub_recipes/processing_lvl_1_per_file.yaml" markdown>

    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)

    </div>

=== "YAML"

    [Download `processing_lvl_1_per_file.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_1_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # sub_recipes/processing_lvl_1_per_file.yaml. Its first two steps moved to level
    # 0 and extract_cal_params to level 2, to match the HB1603 levels.

    # Processing level 1, per file: raw ingest.
    # Unlike processing_lvl_1_parallel.yaml this never combines; files stay split
    # until the fan-in in processing_lvl_2_per_file.yaml, which must be included
    # immediately after this level.

    recipe:
      name: "processing_lvl_1_per_file"
      description: "Per-file raw ingest (gs:// inputs)."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      sonar_model:
        type: str
        default: "EK80"
        description: >
          Echosounder model as echopype names it, such as "EK60" or "EK80".
      ping_time_chunk:
        type: int
        default: 10000
        description: >
          Chunk length along ping_time of each file's EchoData and its checkpoint.
          Larger chunks mean fewer, faster remote writes but more memory per chunk.
          The default keeps each file in one chunk; lower it if a file does not fit
          in memory.

    steps:

      # Per-file fan-out starts here. Every step through apply_mask in
      # processing_lvl_2_per_file.yaml shares this map_over source, so each file
      # runs the chain as its own instance under its own hash.

      # checkpoint: never: the output is an exe_temp path deleted at the end of
      # the run, so a cached copy would dangle.
      - id: read_raw
        op: read_raw_files
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: never
        execution:
          dask_config:
            scheduler: processes
        inputs:
          raw_file_paths:
            - ${_item}          # one-element list: just this file
        params:
          sonar_model: ${inputs.sonar_model}
          include_bot: false
          intermediate_format: "zarr"

      # Not a fan-in: given a one-file list, combine_raw_files just opens it.
      - id: combine_raw
        op: combine_raw_files
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          raw_stores: ${read_raw.raw_stores}
        params:
          ping_time_chunk: ${inputs.ping_time_chunk}
    ```

=== "Python"

    The function calls this part of the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores

    raw_stores = []
    echodata = []
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

        ...  # HB2407 level 2, calibrated Sv, masks and noise removal
    ```

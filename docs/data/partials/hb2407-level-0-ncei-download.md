=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-level-0-ncei-download" data-slug="hb2407-level-0-ncei-download" markdown>

    - `query_ncei`: [`query_ncei_data`](../ops/query_ncei_data.md)
    - `download_raw`: [`download_ncei_data`](../ops/download_ncei_data.md)

    </div>

=== "YAML"

    [Download `processing_lvl_0_ncei_download.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_0_ncei_download.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/sub_recipes/processing_lvl_0.yaml

    # Processing level 0: data retrieval.
    #
    # Queries NCEI and downloads the matching raw files into the output directory.

    recipe:
      name: "processing_lvl_0"
      description: "Query NCEI and download raw acoustic files."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        description: "Destination folder for downloaded raw files."
      file_time_start:
        type: str
        description: "Inclusive start time for the NCEI query."
      file_time_end:
        type: str
        description: "Inclusive end time for the NCEI query."
      cruise_name:
        type: str
        description: "Cruise name filter passed to the NCEI query."

    steps:

      - id: query_ncei
        op: query_ncei_data
        params:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          filters:
            CRUISE_NAME: ${inputs.cruise_name}

      - id: download_raw
        op: download_ncei_data
        inputs:
          results: ${query_ncei.ncei_results}
          query_label: ${query_ncei.query_label}
        params:
          output_dir: ${inputs.raw_input_folder}
          companion_extensions:
            - ".bot"
            - ".idx"
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/hb2407-level-0-ncei-download.ipynb), as `aa-recipe generate` writes it.

    ### processing_lvl_0

    Query NCEI and download raw acoustic files.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    ```

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_utils.data_retrieval import download_ncei_data
    ```

    ```python
    raw_input_folder = None  # TODO: set this value  # path
    file_time_start = None  # TODO: set this value  # str
    file_time_end = None  # TODO: set this value  # str
    cruise_name = None  # TODO: set this value  # str
    ```

    #### Step: `query_ncei`
    **Op:** `query_ncei_data`

    Query an NCEI acoustic data catalog for file listings within a time window. Returns a list of result records (file metadata), not the files themselves.

    **Parameters:**
    - `file_time_start` str - ISO 8601 start of window, e.g. '2016-07-25T20:58'
    - `file_time_end` str - ISO 8601 end of window
    - `filters` dict - Key-value filters applied to NCEI metadata (e.g. CRUISE_NAME)
    - `file_names` list - Keep only these files, matched on the D{date}-T{time} stamp in the name, so a bare D20160725-T205832.raw finds the archive's HB1603_L1- form and the reverse. Applied after the time window, client side, in catalogue order. A name the query did not return raises rather than silently shrinking the result. Wire it from a planning step, for example ${plan_dives.raw_files}, to map a per-file chain over an analysis's files alone. Empty keeps everything.

    ```python
    # --- Parameters ---
    _query_ncei__file_time_start = file_time_start
    _query_ncei__file_time_end = file_time_end

    _result = query_ncei_data(
        file_time_start=_query_ncei__file_time_start,
        file_time_end=_query_ncei__file_time_end,
        filters={'CRUISE_NAME': '${inputs.cruise_name}'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']
    ```

    #### Step: `download_raw`
    **Op:** `download_ncei_data`

    Download raw acoustic files identified by an NCEI query result, along with any specified companion file extensions (.bot, .idx, etc.).

    **Parameters:**
    - `output_dir` path - Local *base* directory. Each query writes into output_dir/<query_id or query_label>/.

    - `companion_extensions` list - Additional file extensions to download alongside each .raw file
    - `query_id` str - Optional explicit subfolder name. Overrides the query_label input. Useful when you want a stable, human-chosen folder name.

    ```python
    # --- Parameters ---
    _download_raw__output_dir = '${inputs.raw_input_folder}'
    _download_raw__companion_extensions = ['.bot', '.idx']

    _result = download_ncei_data(
        results=ncei_results,
        query_label=query_label,
        output_dir=_download_raw__output_dir,
        companion_extensions=_download_raw__companion_extensions,
    )
    downloaded_paths = _result['downloaded_paths']
    download_dir = _result['download_dir']
    ```

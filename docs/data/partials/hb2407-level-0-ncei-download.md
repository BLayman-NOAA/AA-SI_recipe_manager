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

    recipe:
      name: "processing_lvl_0"
      description: "Query NCEI and download raw acoustic files."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        description: "Local folder the NCEI download writes raw files into."
      file_time_start:
        type: str
        description: >
          Start of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T20:58". Every file overlapping the window is fetched.
      file_time_end:
        type: str
        description: >
          End of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T21:45". Every file overlapping the window is fetched.
      cruise_name:
        type: str
        description: >
          Cruise name searched for in the NCEI catalogue, such as "HB1603".

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

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/hb2407-level-0-ncei-download.ipynb).

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_utils.data_retrieval import download_ncei_data

    # Inputs
    file_time_start = None  # TODO: set this value  # str
    file_time_end = None  # TODO: set this value  # str

    # query_ncei: Query an NCEI acoustic data catalog for file listings within a
    # time window.
    _result = query_ncei_data(
        file_time_start=file_time_start,
        file_time_end=file_time_end,
        filters={'CRUISE_NAME': '${inputs.cruise_name}'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']

    # download_raw: Download raw acoustic files identified by an NCEI query
    # result, along with any specified companion file extensions (.bot, .idx,
    # etc.).
    _result = download_ncei_data(
        results=ncei_results,
        query_label=query_label,
        output_dir='${inputs.raw_input_folder}',
        companion_extensions=['.bot', '.idx'],
    )
    downloaded_paths = _result['downloaded_paths']
    download_dir = _result['download_dir']
    ```

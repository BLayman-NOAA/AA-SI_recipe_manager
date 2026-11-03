=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="quickstart" data-slug="quickstart" markdown>

    - `query_ncei`: [`query_ncei_data`](ops/query_ncei_data.md)
    - `read_raw`: [`read_raw_files`](ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](ops/combine_raw_files.md)
    - `compute_sv`: [`compute_sv`](ops/compute_sv.md)
    - `plot_sv`: [`plot_sv_echogram`](ops/plot_sv_echogram.md)

    </div>

=== "YAML"

    [Download `quickstart.yaml`](recipes/quickstart/quickstart.yaml)

    ```yaml
    # Quickstart: fetch a few raw files from NCEI and draw an Sv echogram.
    #
    #   query_ncei -> read_raw -> combine_raw -> compute_sv -> plot_sv
    #
    # A small, complete recipe to experiment with. It finds the HB1603 files that
    # overlap a 47 minute window (four files), reads them straight from NCEI,
    # computes volume backscattering strength (Sv) and saves an echogram. Nothing
    # needs to be downloaded first.
    #
    # Sv uses the calibration each raw file recorded. The processing level pages
    # show the calibrated version.
    #
    # Try:
    #   aa-recipe dry-run quickstart.yaml --visualize
    #   aa-recipe run quickstart.yaml
    #   aa-recipe run quickstart.yaml --input max_depth=500
    #   aa-recipe generate quickstart.yaml -o quickstart.ipynb

    recipe:
      name: "quickstart"
      description: "Fetch HB1603 raw files from NCEI, compute Sv and plot an echogram."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      cruise_name:
        type: str
        default: "HB1603"
        description: "NCEI CRUISE_NAME to search."
      file_time_start:
        type: str
        default: "2016-07-25T20:58:00"
        description: "Start of the window, UTC. Every file overlapping the window is fetched."
      file_time_end:
        type: str
        default: "2016-07-25T21:45:00"
        description: "End of the window, UTC. A wider window fetches more files."
      sonar_model:
        type: str
        default: "EK60"
        description: "Echosounder model, as echopype names it."
      max_depth:
        type: float
        default: 1000.0
        description: "Deepest depth drawn on the echogram, in metres."

    steps:

      # Searches the NCEI catalogue. raw_urls lists the matching files' HTTPS URLs.
      - id: query_ncei
        op: query_ncei_data
        params:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          filters:
            CRUISE_NAME: ${inputs.cruise_name}

      # Converts every raw file to echopype's format, reading from NCEI directly.
      - id: read_raw
        op: read_raw_files
        inputs:
          raw_file_paths: ${query_ncei.raw_urls}
        params:
          sonar_model: ${inputs.sonar_model}
          include_bot: false

      # Joins the converted files into one EchoData object along ping_time.
      - id: combine_raw
        op: combine_raw_files
        inputs:
          raw_stores: ${read_raw.raw_stores}

      # No cal_params or env_params: the raw files' own calibration is used.
      - id: compute_sv
        op: compute_sv
        inputs:
          echodata: ${combine_raw.echodata}

      - id: plot_sv
        op: plot_sv_echogram
        inputs:
          ds_Sv: ${compute_sv.ds_Sv}
        params:
          max_depth: ${inputs.max_depth}

    outputs:
      sv:
        step_id: compute_sv
        output_name: ds_Sv
        description: "Sv for every channel, uncalibrated beyond the raw files' own values."
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](data/notebooks/quickstart.ipynb).

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
    from echopype.calibrate import compute_Sv
    from aa_si_visualization.echogram import plot_sv_echogram

    # query_ncei: Query an NCEI acoustic data catalog for file listings within a
    # time window.
    _result = query_ncei_data(
        file_time_start='2016-07-25T20:58:00',
        file_time_end='2016-07-25T21:45:00',
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']

    # read_raw: Open raw echosounder files with echopype and write each one to an
    # intermediate store.
    raw_stores = read_raw_files_to_stores(
        raw_file_paths=raw_urls,
        sonar_model='EK60',
        include_bot=False,
    )

    # combine_raw: Combine the per-file stores or EchoData objects from
    # read_raw_files into one EchoData object.
    echodata = combine_raw_stores(raw_stores=raw_stores)

    # compute_sv: Compute volume backscattering strength (Sv) from raw EchoData,
    # applying cal_params and env_params when given and the raw file's own values
    # otherwise.
    ds_Sv = compute_Sv(echodata=echodata)

    # plot_sv: Render an Sv echogram of ds_Sv, optionally on the coordinate axes
    # of ds_Sv_source (for example MVBS on the original Sv grid).
    plot_sv_echogram(ds_Sv=ds_Sv, max_depth=1000.0)
    ```

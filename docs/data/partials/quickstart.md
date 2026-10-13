=== "Flow diagram"

    <div class="recipe-flow" data-recipe="quickstart" markdown>

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
    # A small, complete recipe to experiment with. It asks the NCEI water column
    # sonar archive for the HB1603 files that overlap a 47 minute window (four
    # files, about 90 minutes of data), reads them straight from their NCEI URLs,
    # computes volume backscattering strength (Sv) and saves an echogram. Nothing
    # needs to be downloaded first.
    #
    # Sv here uses the calibration values each raw file recorded when it was
    # collected: compute_sv is given no cal_params, so echopype falls back to
    # them. The processing level pages show the calibrated version.
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

=== "Notebook"

    [Download the notebook](data/notebooks/quickstart.ipynb), as `aa-recipe generate` writes it.

    ### quickstart

    Fetch HB1603 raw files from NCEI, compute Sv and plot an echogram.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install echopype>=0.6.0
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Visualization.git
    ```

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
    from echopype.calibrate import compute_Sv
    from aa_si_visualization.echogram import plot_sv_echogram
    ```

    ```python
    cruise_name = 'HB1603'  # str
    file_time_start = '2016-07-25T20:58:00'  # str
    file_time_end = '2016-07-25T21:45:00'  # str
    sonar_model = 'EK60'  # str
    max_depth = 1000.0  # float
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
    _query_ncei__file_time_start = '2016-07-25T20:58:00'
    _query_ncei__file_time_end = '2016-07-25T21:45:00'

    _result = query_ncei_data(
        file_time_start=_query_ncei__file_time_start,
        file_time_end=_query_ncei__file_time_end,
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']
    ```

    #### Step: `read_raw`
    **Op:** `read_raw_files`

    Open raw echosounder files with echopype and write each file as an intermediate per-file store. Returns a list of store-path strings for file-backed formats, or a list of in-memory EchoData objects when intermediate_format is "none". This is the future map_over target for parallel per-file processing. NOTE: use intermediate_format "netcdf" (default) or "none". The "zarr" option is not recommended; see the intermediate_format parameter description below.
    The per-file conversion is echopype.open_raw, and sonar_model, include_bot, and use_swap are forwarded to it. Around that call this step adds remote file staging, .bot companion handling, and writing each result to an intermediate store.

    **Parameters:**
    - `sonar_model` str - Sonar model string recognized by echopype (e.g. 'EK60', 'EK80')
    - `include_bot` bool - Include bottom-detection (.bot) data if available
    - `intermediate_format` str - Intermediate store format written to exe_temp/data between the read_raw and combine_raw steps. Supported values: "netcdf" (default), "zarr", "none".
    "netcdf" (default, recommended): each raw file is written as a temporary .nc file, reopened, and combined. After combining, all EchoData groups are rechunked to a single uniform chunk so the downstream zarr v2 checkpoint write (combine_raw with checkpoint: always) always succeeds.
    "none": all EchoData objects are kept in memory with no temp files. Use when the full dataset fits comfortably in RAM.
    "zarr": each raw file is written as a temporary zarr store (zarr_format=2, matching the checkpoint writer) and reopened lazily for combining. Unlike netcdf, zarr stores can live on remote object storage (e.g. a GCS bucket) by passing a gs:// --temp-dir, which is the intended path for surveys too large to stage on local disk. Combined with gs:// raw_file_paths, local disk usage stays at roughly one raw file for the whole run.

    - `use_swap` str - Passed to echopype open_raw as use_swap. "auto" (default) triggers per-file swap when the expanded data would exceed ~40% of total RAM.

    ```python
    # --- Parameters ---
    _read_raw__sonar_model = 'EK60'
    _read_raw__include_bot = False

    raw_stores = read_raw_files_to_stores(
        raw_file_paths=raw_urls,
        sonar_model=_read_raw__sonar_model,
        include_bot=_read_raw__include_bot,
    )
    ```

    #### Step: `combine_raw`
    **Op:** `combine_raw_files`

    Lazily open per-file intermediate stores (zarr or netcdf paths) produced by read_raw_files, or combine in-memory EchoData objects (none mode), into a single combined EchoData object. Mark this step checkpoint: always in your recipe so the checkpoint system writes the combined output once and reuses it on subsequent runs.
    The reading and the combine itself are echopype.open_converted and echopype.combine_echodata. Around them this step adds store-path handling, input ordering, and the ping_time chunking of the combined result.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk length along ping_time for the combined EchoData, which directly sets the chunking of the downstream zarr checkpoint. Larger values mean fewer, bigger objects: faster remote (gs://) checkpoint writes and fewer read requests, at the cost of more RAM per computed chunk and coarser lazy-read granularity for windowed access. 1000 is a balanced default; try 2000-4000 for bucket-backed caches if the combined checkpoint write is slow and memory allows.

    ```python
    echodata = combine_raw_stores(raw_stores=raw_stores)
    ```

    #### Step: `compute_sv`
    **Op:** `compute_sv`

    Compute volume backscattering strength (Sv) from raw EchoData. When cal_params and env_params are supplied they are applied to the computation. When omitted, echopype uses default environmental and calibration values embedded in the raw file.
    This step is echopype.calibrate.compute_Sv called directly, so the calibration model and its defaults are echopype's.

    ```python
    ds_Sv = compute_Sv(echodata=echodata)
    ```

    #### Step: `plot_sv`
    **Op:** `plot_sv_echogram`

    Render an Sv echogram. Supports two call patterns: (1) single dataset, plots ds_Sv directly; (2) dual dataset, plots ds_Sv using coordinate axes from ds_Sv_source (useful for MVBS overlaid on the original Sv grid). This is a sink step: it produces a side-effect (figure) with no data output.

    **Parameters:**
    - `min_depth` float - Minimum depth (y-axis) in meters
    - `max_depth` float - Maximum depth (y-axis) in meters. Omit to use the deepest depth present in the data.

    - `ping_min` int - First ping index to display
    - `ping_max` int - Last ping index to display. Omit to display through the last ping.
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of ping_min. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of ping_max. Omit to window by ping index.

    - `x_axis_units` str - Units for the x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'meters' (distance travelled, derived from the GPS track), or 'bins' (MVBS input only).

    - `y_axis_units` str - Units for the y-axis: 'meters' (default), 'range_sample', or 'bins' (MVBS input only).

    - `panel_width_to_height` float - Force each frequency panel to be this many times wider than tall (for example 8 for a long strip). Omit for automatic sizing.

    - `overlay_lines` list - Optional list of line overlays. Each entry is a dict with 'var' (variable name in ds_Sv) and 'style' (dict of matplotlib kwargs).

    - `save_image` bool - Save the rendered figure to an image file; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display the figure interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_sv__max_depth = 1000.0

    plot_sv_echogram(ds_Sv=ds_Sv, max_depth=_plot_sv__max_depth)
    ```

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- EchoData checkpoints to a remote (`gs://`) cache were silently written to a
  local relative directory instead of the bucket (echopype 0.11.1's
  `EchoData.to_zarr` hands the protocol-stripped fsspec mapper root to
  `xarray.to_zarr`). The checkpoint writer now streams the EchoData's xarray
  datatree directly to the bucket — the combined survey is never staged on
  local disk — with a local-write-then-upload fallback for EchoData stand-ins
  without a datatree. `xr.Dataset`/`DataArray` remote checkpoints were already
  correct (they use `xarray.to_zarr` with `storage_options`).

### Added
- Optional Google Cloud Storage backing for the three run storage locations —
  the checkpoint cache (`--output-dir`), the `exe_temp` scratch dir
  (`--temp-dir`), and the user-facing outputs dir (`--outputs-dir`) — each may
  now be a local path or a `gs://` URL. Set independently; all default to local.
  Install the `gcs` extra (`pip install aa-recipe-manager[gcs]`); credentials
  come from Application Default Credentials.
- `StorageLocation` seam (`aa_recipe_manager.storage`): one code path where a
  local path stays plain `pathlib` and an fsspec URL routes through the matching
  filesystem. Consumers that only understand local paths fail loudly on a remote
  location instead of silently writing to a mangled `gs:/bucket` directory.
- New `execute()` keyword arguments `temp_dir` and `storage_options`; new CLI
  flags `--temp-dir` and `--outputs-dir` on `aa-recipe run`.
- Remote-aware checkpoint fingerprinting and path-param validation: `gs://`
  inputs skip local existence/mkdir checks and are fingerprinted via fsspec,
  degrading to a warning (not a crash) when credentials/drivers are absent.
- Optional Google Cloud Storage backing for the three run storage locations —
  the checkpoint cache (`--output-dir`), the `exe_temp` scratch dir
  (`--temp-dir`), and the user-facing outputs dir (`--outputs-dir`).
- `gs://` support extended to **data inputs**: recipe path inputs
  (`raw_input_folder`, `cal_input_folder`, `line_file_path`) may be local paths
  or `gs://` URLs, detected by scheme (no recipe flag). The global
  `storage_options` dict now also reaches ops (via a new
  `ExecutionContext.storage_options` field) and authenticates remote-input
  fingerprinting; credentials default to Application Default Credentials.
  Remote raw files are downloaded one at a time to local scratch, processed,
  and deleted before the next — local disk holds ~1 raw file at a time, so a
  survey too large for the workstation disk can still be processed. New
  `example_recipes/gcs_bucket_example.yaml` demonstrates the flow.
- Optional filename-datetime filtering for provided raw folders
  (`file_time_start` / `file_time_end` on `initial_setup` and
  `generate_standardized_cal_mapping`): restrict processing to files whose
  `D{YYYYMMDD}-T{HHMMSS}` name stamp falls in a window. Name-based, so remote
  files outside the window are never downloaded. `api.clean()` gained a
  `storage_options` argument for parity with `execute()`.
- Project scaffold customized from AA-SI Python template

### Changed
- Checkpoint sidecar artifact paths are now stored relative to the cache root
  (POSIX separators), so a cache directory/prefix is relocatable (e.g. sync a
  bucket cache down and point `--output-dir` at the local copy). Legacy caches
  with absolute artifact paths still load.
- The `execute()` re-export now forwards `checkpoint_mode`, `checkpoint_steps`,
  and `checkpoint_format` (previously silently dropped).
- Remote outputs dir: per-step logs are captured in-memory and uploaded once at
  the end of the run (object stores cannot append); `checkpoint_format="netcdf"`
  is rejected for a remote cache (HDF5 needs seekable writes — use `zarr`).
- Package renamed to `aa-recipe-manager` (import as `aa_recipe_manager`)
- Core dependencies: pydantic, ruamel.yaml, click, nbformat
- CLI entry point (`aa-recipe`, with `aa-recipe-manager` compatibility alias)
- Full directory structure matching the layered architecture (model, parser, registry, resolver, provenance, tracker, generator, executor, orchestrator)
- Development tooling: ruff, mypy, pytest, pre-commit
- Optional dependency groups for dask and prefect executors

### Changed
- Preferred CLI command is now `aa-recipe`
- Legacy `aa-recipe-manager` command remains available as a compatibility alias

## [0.1.0] - YYYY-MM-DD

### Added
- Initial release
- Basic package structure with src layout
- Development tooling (pytest, ruff, mypy, pre-commit)

<!--
=============================================================================
CHANGELOG GUIDELINES
=============================================================================

When adding entries, use the following categories:
- Added: for new features
- Changed: for changes in existing functionality
- Deprecated: for soon-to-be removed features
- Removed: for now removed features
- Fixed: for any bug fixes
- Security: in case of vulnerabilities

Each release should have a version number and date in the format:
## [X.Y.Z] - YYYY-MM-DD

Link definitions should be added at the bottom (optional)

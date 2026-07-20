# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — regenerate missing outputs

A step can now regenerate its user-facing artifacts (plots, logs, reports) when
they are absent from the current run's outputs directory, even on a cache hit.

- **Per-step `regenerate` attribute** (sibling of `checkpoint:`): `if-missing`
  re-runs the step only when a recorded artifact is missing; `always` re-runs
  every run; unset / `never` keeps the cached-skip behavior. Works for sink
  steps (cheap — upstream loads from cache, only the render re-runs) and data
  steps (the step recomputes, since the artifact is a side effect of running).
- **`--regenerate [auto|off|sinks|all]`** (run-level, replaces the removed
  `--regenerate-outputs` flag / `regenerate_outputs=` API argument): `auto`
  (default) honors each step's attribute; `off` regenerates nothing; `sinks`
  forces every sink step (the old flag's behavior); `all` forces every
  artifact-emitting step, data steps included.
- Steps now record the artifact paths they wrote (relative to the outputs dir)
  in their checkpoint/marker sidecar (`artifacts` field), so a later run can
  verify presence before skipping. A sidecar with no recorded artifacts —
  either a pre-feature entry (`None`) or an empty list from an older plotting
  lib that didn't report its paths — is treated as unverifiable for a sink /
  side-effect step, so it regenerates and self-heals. A non-sink data step may
  legitimately emit nothing, so for it an empty list means "present".

### Changed — tiered content-addressed cache (Stage 7)

**BREAKING (cache layout): all existing checkpoint caches are invalidated.**
Checkpoint addressing flipped from a flat `step_id` scheme to content
addressing keyed on the per-step Merkle hash, and the hash policy changed (see
below). The on-disk/GCS layout is `<root>/<step_id>/<hash[:8]>/meta.json`
with artifacts under `<hash[:8]>/<run_id>/<zarr|json|other>/<out_name>.<ext>`.
The path components are kept deliberately short (an 8-hex hash key, a compact
`run_id`, `zarr`/`json` format subdirs, and an output-name-only artifact
filename) so deeply-nested Zarr stores with long internal variable names stay
under Windows' 260-char `MAX_PATH` limit; use a short local cache root, or
enable Windows long-path support, if you still hit the limit under a very deep
directory. Old per-`step_id` entries are ignored — run `aa-recipe clean --all`
once to sweep them, or start a fresh cache prefix.
The step id sits at the **top level** so the cache stays browsable by step
name (`ls` shows `combine_raw/`, `compute_sv/`, …); the short hash component
disambiguates different computations of the same step and keeps deeply-nested
Zarr stores under Windows' 260-char `MAX_PATH` limit. The *full* step hash is
the authoritative identity in each entry's sidecar and is validated on every
read, so content is still addressed by the full hash and a (astronomically
unlikely) short-hash collision degrades to a cache miss, never a wrong result.
Trade-off: two *differently named* steps with an identical computation no
longer share one entry (forks keep step ids, so shared subgraphs still dedupe).
Point your config's `output_dir` at a **per-user** cache root (e.g.
`gs://…/users/<you>/cache`) rather than a per-run directory so your own runs
and forked recipes dedupe against each other.

- **Hash policy hardening:**
  - Remote input fingerprints prefer content checksums (`md5Hash`/`crc32c`/
    `ETag` from the same fsspec HEAD/LIST) over size+mtime — re-uploading an
    identical raw file no longer invalidates downstream checkpoints (on GCS,
    mtime is upload time). Unverifiable remote inputs now degrade to a
    guaranteed *miss* (unique nonce) instead of a possible stale hit.
  - Op identity uses a new spec `cache_key` (pinned to the current op name in
    every builtin spec) plus explicit `version` fields on specs and
    implementations — renaming an op or moving its callable is now
    cache-neutral; bump `version` when behavior changes. `CustomSpec` gains
    the same fields.
  - New recipe-level `execution.cache_epoch` salt: bumping it deliberately
    invalidates every cached result for the recipe.
  - Fingerprint payloads are persisted in each sidecar (schema v2, with
    `run_id`/`created_at`/recipe identity), powering `explain-cache`.
- **Tiered `[user, survey]` cache:** new `survey_cache_dir` config key /
  `--survey-cache-dir` flag adds a shared read tier; first hit wins. Writes go
  to the user tier unless `--cache-write-tier survey` marks a *curated* run
  (reads+writes only the survey tier; rejects pickle artifacts; bucket IAM is
  the enforcement). Side-effect markers stay user-tier unconditionally. A
  fork of a curated recipe automatically reuses the unchanged upstream steps
  from the survey tier and stores only its changes in the user tier.
- **Run manifest:** every run writes `<outputs>/manifest.json` — per-step
  disposition (`computed` / `hit-user-cache` / `hit-survey-cache` / `pruned`
  / `marker`), absolute artifact URIs, timings, tier roots, and status
  (written on failures too).
- **`explain-cache`** (new CLI command + `api.explain_cache`): reports, per
  step, which tier hits — and on a miss, diffs the nearest stored entry's
  fingerprint payload against the recomputed one to name the exact divergent
  field (param value, input checksum, epoch, upstream change).
- **Curated provenance + warn-only environment check:** curated runs publish
  their provenance to `<survey_cache_dir>/provenance/{recipe}@{run_id}.json`
  and stamp every sidecar with the ref; user runs that hit the survey tier
  compare their environment against it and *warn* on version mismatches
  (prominently for op-implementing packages) — never blocking the hit.
  Mismatches are recorded in the result and manifest.
- `aa-recipe clean` now honors the run config (`--config` / auto-discovery)
  for `output_dir` and `storage_options`; `--stale` only removes entries
  belonging to the given recipe (a content-addressed root can host many);
  `--all` also sweeps legacy (pre-content-addressing) cache directories.
- Curated partial runs should use `--checkpoint-mode eager` (or per-step
  `checkpoint:` marks) so intermediate levels are actually persisted to the
  survey tier.

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
- Per-user run-config file (`aa_recipe_manager.config`): the `run` command reads
  `output_dir`, `temp_dir`, `outputs_dir`, `storage_options`, and input defaults
  from a git-ignored config file so environment-specific bucket paths stay out
  of the shared recipe. Auto-discovery (first found wins): `--config PATH` >
  `$AA_RECIPE_CONFIG` > per-recipe `<recipe_stem>.config.yaml` beside the recipe
  (lets recipes sharing a directory target different buckets) > generic
  `./aa-recipe.config.yaml` > `~/.config/aa-recipe/config.yaml`. Value
  precedence: CLI flag > config file > recipe default > built-in. See
  `example_recipes/aa-recipe.config.example.yaml`.
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

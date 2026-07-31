# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — `read_seafloor_line` op (Echoview `.evl` seabed lines)

Takes the seafloor from a hand-verified Echoview line file instead of detecting
it from the Sv data. It emits the same `seafloor_depth` output port, 1-D over
`ping_time`, as `detect_seafloor` and `ep_detect_seafloor`, so it is a drop-in
swap: only the step's `op` and `params` change, and `create_seafloor_mask` and
everything downstream stay as they are.

- `evl_path` is a `path` param with `fingerprint_mode: checksum`, so re-exporting
  or hand-editing the line invalidates the cached step while merely moving the
  file does not. Remote (`gs://`) line files are read in place.
- Alignment onto the ping grid is controlled by `max_gap_s` (widest hole in the
  line to interpolate across) and `edge_extend_s` (how far past the line's ends
  its depth is held, default `0.0` — no extrapolation). Pings the line does not
  cover come back NaN, and `create_seafloor_mask` then rejects every sample in
  those pings, so the step prints its ping coverage and `min_coverage` can turn
  a shortfall into a failed run.
- `vertical_reference` and `depth_offset_m` reconcile the line's vertical datum
  with `ds_Sv`. Backed by `aa_si_utils.utils.read_seafloor_line_evl`.

The HB2407 example recipe now uses it: `processing_lvl_2.yaml` takes a new
`seabed_line_path` input, its `detect_seafloor` step reads the line file (with
the `ep_detect_seafloor` version kept alongside, commented, as the alternative),
and the seafloor mask is back in `combine_masks`. Its `seafloor_buffer_m` drops
from 100 m to 5 m — the seabed in that line sits at 55-71 m, so a 100 m buffer
would have masked away the entire dataset.

### Changed — BREAKING: `output_dir` renamed to `user_cache_dir`

The per-user cache-root setting is now named `user_cache_dir` everywhere it was
`output_dir`: the run-config key, the `--user-cache-dir` CLI flag (formerly
`--output-dir`), the `api.execute` / `execute_batch` / `clean` / `explain_cache`
keyword argument, and `ExecutionResult.user_cache_dir`. This removes the
one-letter confusion with `outputs_dir` (the separate user-facing images/logs
directory). There is no backward-compatible alias: a config using `output_dir`
now errors with "unknown key", and `--output-dir` is no longer accepted — update
configs and scripts to `user_cache_dir` / `--user-cache-dir`. (The unrelated
`output_dir` parameter of the `download_ncei_data` op is unchanged.)

### Changed — BREAKING: `fingerprint_contents` replaced by `fingerprint_mode`

The boolean `fingerprint_contents` on a path input/param is gone, replaced by
`fingerprint_mode`, which picks *which* signal folds into the cache key: `off`
(default, path string only), `names`, `size`, `checksum`, or `auto` (names+size
locally, names+checksum remotely). The closest equivalent of the old
`fingerprint_contents: true` is `fingerprint_mode: auto`.

Two related changes to what a non-`off` mode hashes: the target's **path is no
longer part of the key** (its content identity travels separately), so the same
files under a moved or renamed folder now hash identically; and **mtime is never
included**, since re-uploading or re-copying identical bytes changes it and would
false-miss. Locally that means `auto` keys on size, so a same-size edit to a
local input no longer invalidates — use `fingerprint_mode: checksum` where that
matters.

There is no backward-compatible alias, and declaration blocks (`inputs:`,
`params:`, and spec ports) now **reject unknown keys** rather than ignoring them:
a recipe still saying `fingerprint_contents` fails validation instead of silently
falling back to path-only keying. This also catches ordinary typos in those
blocks. Not yet migrated: the AA-SI_Workbench builtin recipes
(`byo_folder_example.yaml`, `gcs_bucket_example.yaml`, `pipeline_modified.yaml`,
`processing_levels_pipeline_gcs.yaml`) still carry the old key.

### Added — faster remote checkpoint upload (staged parallel put)

Writing a zarr checkpoint to a bucket by streaming `to_zarr` object-by-object
measured ~0.9 MiB/s on a 2.8 MiB/s uplink — a third of the link — because
xarray writes chunks and the many small metadata objects mostly serially.

- Stores whose estimated size is under a threshold are now written to local
  scratch and bulk-uploaded with `fs.put(recursive=True)`, which parallelizes
  the per-object PUTs (~2.5x faster on a real 398 MiB EchoData checkpoint). The
  reloaded store is byte-identical; only the transport changes.
- Larger stores stream straight to the bucket with no local copy, so a survey
  bigger than local disk still writes. The threshold is estimated *uncompressed*
  bytes (actual disk use is smaller by the compression ratio), defaults to 8 GiB,
  and is set with `AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES` (`0` = always stream).
- Applies to EchoData, `xr.Dataset`, and `xr.DataArray` remote zarr writes.

### Added — run timing and concurrent-run debuggability

- `run` prints the total wall-clock time and the executor/concurrency actually
  in force; per-step and whole-run times are recorded in `manifest.json`
  (`elapsed_seconds`), and a step's checkpoint-write share is split out as
  `save_seconds`.
- The run log names the executor (and Dask dashboard URL), fences each step, and
  labels each mapped-chain instance; stdout/stderr capture is now thread-routed
  so concurrent steps no longer lose or cross-attribute each other's output, and
  every write is flushed so an interrupted run still shows where it stopped.
- `--dask-workers N` is now the concurrency (N slots), not `N x cores`.
- `run --keep-temp` leaves the run-scoped scratch (exe_temp) in place instead of
  deleting it at run end, so a slow step can be profiled against its real
  intermediate inputs.

### Added — segment-parallel & parameter-parallel execution (Stage 8)

Recipes can now express fan-out / fan-in with `map_over`, `sweep`, and
`collect`. All three were already modeled and folded into the cache hash; this
stage makes them execute, generate code, validate, and checkpoint.

- **`map_over: ${step.output}`** runs a step once per element of an upstream
  list output. Consecutive steps sharing the same source form a *mapped chain*:
  within it, `${_item}` is the current element and inter-step references
  resolve to that element's instance (a child element context over the runtime
  context). A non-list source runs the chain once (single-item transparency).
- **`sweep`** runs a step once per parameter combination — `zip` (positional)
  or `grid` (cartesian) — declared in the documented flat form
  (`sweep: {param: [...], mode: zip}`) or the explicit `param_lists:` form.
  `map_over` + `sweep` on one step takes the outer product.
- **`collect: ${step.output}`** gathers all instance outputs of a mapped/swept
  step into a list for a downstream fan-in step (wired explicitly or auto-bound
  to a `many: true` input port named after the collected output).
- **Per-instance content-addressed checkpoints:** each instance is its own
  `<step_id>/<instance_hash[:8]>/` entry, where the instance hash folds the
  step's base hash with a discriminator (the sweep params, and the mapped item
  value when JSON-serializable — e.g. a file path — else the ordinal index).
  Identical items/params dedupe across runs; on resume only missing instances
  recompute. Hashable-item map instances become independently survey-tier
  addressable.
- **Validation (FR-14.6 / FR-18.3):** `collect` must target a mapped/swept
  step; `map_over` warns when its source is not list-typed; sweep params must
  be declared and absent from `params`, and `zip` lists must be equal length;
  `${_item}` requires `map_over`. Dry-run emits a **sweep-purity warning**
  (FR-18.7) when a swept step declares the same type as both input and output.
- **Code generation & dry-run:** the notebook backend emits `for` loops for
  mapped/swept chains (accumulating each member's outputs into lists the
  collector reads); `dry-run --visualize` tags mapped/swept/collector nodes and
  draws the `map_over` / `collect` fan-out edges dotted.
- **`map_over` on an `include` entry (FR-14.4):** fans an entire included
  sub-workflow out once per segment — every included step inherits the source
  and the sub-recipe's entry input binds to `${_item}` via `input_overrides`.
- **New `merge_datasets` built-in op** (`aa_si_utils.utils.concat_datasets`):
  the reconsolidation / fan-in target that concatenates a collected list of
  Datasets along a dimension (default `ping_time`).
- **New example recipes:** `machine_learning_sweep.yaml` (a `min_cluster_size`
  sweep + `collect` ensemble over the existing HDBSCAN ops) and
  `parallel_per_file_mvbs.yaml` (per-file `map_over` Sv→MVBS with a
  `merge_datasets` fan-in — the segment-parallel analogue of
  `processing_levels_pipeline.yaml`).

Sequential executor only; distributed (Dask/Prefect) fan-out remains Stage 9.

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
Point your config's `user_cache_dir` at a **per-user** cache root (e.g.
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
  for `user_cache_dir` and `storage_options`; `--stale` only removes entries
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
  the checkpoint cache (`--user-cache-dir`), the `exe_temp` scratch dir
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
  the checkpoint cache (`--user-cache-dir`), the `exe_temp` scratch dir
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
  `user_cache_dir`, `temp_dir`, `outputs_dir`, `storage_options`, and input defaults
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
  bucket cache down and point `--user-cache-dir` at the local copy). Legacy caches
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

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Resolve and report the raw input files a recipe reads.

Re-resolves the recipe's raw file list by running only the step whose spec
tags an output ``provenance_role: raw_file_list`` (normally ``initial_setup``),
then builds the same ``RawInputsRecord`` the executor records in provenance.
Use it to inspect the list, or to repair a ``provenance.yaml`` whose
``raw_inputs`` block is missing or was recorded as an unexpanded template
(``${_item}``) because the reader step was pruned by a cache hit.

Usage:
    python scripts/raw_inputs_report.py RECIPE [--config CFG] [--input K=V]
        [--write PROVENANCE_YAML] [--verify-manifest MANIFEST_JSON]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from aa_recipe_manager import api, config
from aa_recipe_manager.executor.invocation import (
    RuntimeContext,
    build_kwargs,
    extract_outputs,
    import_callable,
)
from aa_recipe_manager.provenance.recorder import (
    RAW_FILE_LIST_ROLE,
    build_raw_inputs_record,
)


def _tagged_output_step(dag: Any) -> tuple[str, str]:
    """First step (topological) with an output port tagged as the raw list."""
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        for out_port, decl in (node.spec.outputs or {}).items():
            if getattr(decl, "provenance_role", None) == RAW_FILE_LIST_ROLE:
                return step_id, out_port
    raise SystemExit(
        f"no step in {dag.recipe.name!r} declares an output tagged "
        f"provenance_role: {RAW_FILE_LIST_ROLE}"
    )


def _resolve_raw_paths(
    dag: Any, step_id: str, out_port: str, pipeline_inputs: dict[str, Any]
) -> list[Any]:
    """Run just the tagged step and return its raw file path list.

    Side-effecting params are neutralized: log clearing is disabled and any
    calibration output directory is redirected to a temp dir, so reporting
    never touches the run's own artifacts.
    """
    node = dag.nodes[step_id]
    overrides: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="raw_inputs_report_") as scratch:
        if "clear_previous_json_logs" in (node.spec.params or {}):
            overrides["clear_previous_json_logs"] = False
        if "calibration_outputs" in (node.spec.params or {}):
            overrides["calibration_outputs"] = scratch
        kwargs = build_kwargs(
            node, RuntimeContext(), pipeline_inputs, param_overrides=overrides
        )
        fn = import_callable(node.implementation.callable_path)
        outputs = extract_outputs(node, fn(**kwargs))
    paths = outputs.get(out_port)
    if not paths:
        raise SystemExit(f"step {step_id!r} returned no {out_port}")
    return list(paths)


def _verify_manifest(
    dag: Any,
    pipeline_inputs: dict[str, Any],
    storage_options: dict[str, Any] | None,
    manifest_path: Path,
    step_id: str,
) -> None:
    """Report whether this resolution reproduces a past run's step hash.

    A match proves the reported file set is the one that run used: the raw
    folder listing is folded into the tagged step's fingerprint.
    """
    import json

    from aa_recipe_manager.executor.checkpoint import compute_step_fingerprints

    recorded = (
        (json.loads(manifest_path.read_text()).get("steps") or {})
        .get(step_id, {})
        .get("step_hash")
    )
    if not recorded:
        print(f"manifest has no step_hash for {step_id!r}; cannot verify")
        return
    current = compute_step_fingerprints(
        dag, pipeline_inputs, storage_options=storage_options
    ).hashes.get(step_id)
    if current == recorded:
        print(f"verified: {step_id} hash matches the manifest run ({recorded[:12]})")
    else:
        print(
            f"MISMATCH: {step_id} now hashes {str(current)[:12]}, manifest "
            f"recorded {recorded[:12]}. Any edit to the recipe, the inputs, or "
            f"the raw folder's contents since that run moves this hash, so it "
            f"is evidence of drift, not proof the file set differs. Cross-check "
            f"with --verify-cache."
        )


def _verify_cache(cache_dir: Path, record: Any) -> None:
    """Compare the resolved list against items recorded in checkpoint sidecars.

    A mapped step stamps the item it fanned out over into its sidecar's
    ``instance_discriminator``, so the cache holds the file names past runs
    actually processed. Offline, and unaffected by later recipe edits.
    """
    import json

    resolved = {entry.name for entry in record.files}
    by_run: dict[str, set[str]] = {}
    for meta_path in cache_dir.rglob("meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        item = (meta.get("instance_discriminator") or {}).get("item")
        if item:
            name = str(item).replace("\\", "/").rsplit("/", 1)[-1]
            by_run.setdefault(meta.get("run_id") or "unknown", set()).add(name)
    if not by_run:
        print(f"no fanned-out instances recorded under {cache_dir}")
        return
    for run_id, names in sorted(by_run.items()):
        extra = sorted(names - resolved)
        status = "all in resolved list" if not extra else f"NOT in list: {extra}"
        print(f"  run {run_id}: {len(names)} file(s), {status}")
    union = set().union(*by_run.values())
    missing = sorted(resolved - union)
    print(
        f"  union of cached items: {len(union)}; resolved: {len(resolved)}"
        + (f"; never cached: {missing}" if missing else "; every resolved file appears")
    )


def _write_raw_inputs(provenance_path: Path, record: Any) -> None:
    """Patch the ``raw_inputs`` block of an existing provenance YAML in place."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    data = yaml.load(provenance_path)
    if data is None:
        raise SystemExit(f"{provenance_path} is empty or not a YAML mapping")
    data["raw_inputs"] = record.model_dump(mode="json")
    with provenance_path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    print(f"wrote raw_inputs into {provenance_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recipe", help="Recipe YAML path.")
    parser.add_argument("--config", default=None, help="Run config path.")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Pipeline input override (repeatable).",
    )
    parser.add_argument(
        "--write",
        default=None,
        metavar="PROVENANCE_YAML",
        help="Patch this provenance.yaml's raw_inputs block with the result.",
    )
    parser.add_argument(
        "--verify-manifest",
        default=None,
        metavar="MANIFEST_JSON",
        help="Check the resolved list against a past run's manifest.json.",
    )
    parser.add_argument(
        "--verify-cache",
        default=None,
        metavar="CACHE_DIR",
        help="Cross-check against per-file items recorded in checkpoint sidecars.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run id to record as origin_run_id. Defaults to the verified "
        "manifest's run id, else left unset.",
    )
    args = parser.parse_args(argv)

    cli_inputs: dict[str, Any] = {}
    for item in args.input:
        name, sep, value = item.partition("=")
        if not sep:
            parser.error(f"--input must be NAME=VALUE, got {item!r}")
        cli_inputs[name.strip()] = value.strip()

    run_config = config.load_run_config(args.config, recipe_path=args.recipe)
    if run_config.source is not None:
        print(f"Using run config: {run_config.source}")
    pipeline_inputs = {**run_config.inputs, **cli_inputs}

    dag = api._load_dag(args.recipe, input_values=pipeline_inputs)
    step_id, out_port = _tagged_output_step(dag)
    print(f"Resolving raw file list from {step_id}.{out_port}")

    run_id = args.run_id
    if run_id is None and args.verify_manifest:
        import json

        run_id = json.loads(Path(args.verify_manifest).read_text()).get("run_id")

    paths = _resolve_raw_paths(dag, step_id, out_port, pipeline_inputs)
    record = build_raw_inputs_record(
        dag,
        {step_id: {out_port: paths}},
        pipeline_inputs,
        run_config.storage_options,
        run_id=run_id,
    )
    if record is None:
        raise SystemExit("could not build a raw inputs record")

    print()
    for index, entry in enumerate(record.files, 1):
        size = "unknown" if entry.size is None else f"{entry.size:,} bytes"
        print(f"{index:3}. {entry.name}  ({size})")
    print(f"\ncount:  {record.count}")
    print(f"digest: {record.digest}")

    if args.verify_manifest:
        print()
        _verify_manifest(
            dag,
            pipeline_inputs,
            run_config.storage_options,
            Path(args.verify_manifest),
            step_id,
        )

    if args.verify_cache:
        print()
        _verify_cache(Path(args.verify_cache), record)

    if args.write:
        _write_raw_inputs(Path(args.write), record)
    return 0


if __name__ == "__main__":
    sys.exit(main())

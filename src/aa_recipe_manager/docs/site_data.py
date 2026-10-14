# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Generate the data files the documentation site is built from.

A manifest lists the recipes the site documents. For each one this writes a
flow-diagram graph and a Markdown partial holding its Flow, YAML and Notebook
tabs, and for the registry it writes the op reference payload. Everything goes
under ``docs/data`` and is committed, so the site build itself needs neither
the scientific packages nor network access.
"""

from __future__ import annotations

import json
import posixpath
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML

from aa_recipe_manager.docs.notebook_slice import (
    cells_to_markdown,
    generate_parent_notebook,
    notebook_to_markdown,
    slice_steps,
)
from aa_recipe_manager.docs.payload import (
    build_payload,
    stale_link_ops,
    strip_environment,
    unresolved_ops,
)
from aa_recipe_manager.docs.recipe_links import (
    Home,
    declared_inputs,
    include_overrides,
    overview_graph,
    page_url,
    recipe_context,
)
from aa_recipe_manager.graph import build_recipe_graph, strings_in

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Recipe

MANIFEST_SCHEMA = 1


@dataclass
class ManifestEntry:
    """One documented recipe."""

    slug: str
    path: str
    title: str
    page: str
    """The page that embeds this recipe, which its tab links are relative to."""
    parent: str | None = None
    level: int | None = None
    section: str | None = None
    cruise: str | None = None
    description: str | None = None
    focus: list[str] | None = None
    """Steps the diagram shows. The recipe's other steps are drawn as context,
    for a recipe that repeats another's steps only to reuse their cache."""
    context: str | None = None
    """Slug of the recipe that documents the steps left out by ``focus``."""


@dataclass
class SiteDataResult:
    """What a site data build wrote and what it could not resolve."""

    written: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    """Files from recipes no longer in the manifest."""
    counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    stale_links: list[str] = field(default_factory=list)


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    """Read the site's recipe manifest.

    Args:
        path: The manifest YAML. Recipe and page paths in it are relative to
            the manifest's own folder.

    Returns:
        The entries in file order.

    Raises:
        ValueError: An entry is malformed, a slug repeats, a file is missing,
            or a parent is unknown or is itself included by another recipe.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = YAML(typ="safe").load(f) or {}

    entries = []
    for item in raw.get("recipes") or []:
        try:
            entries.append(ManifestEntry(**item))
        except TypeError as exc:
            raise ValueError(f"Bad manifest entry {item!r}: {exc}") from exc

    slugs = [entry.slug for entry in entries]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(f"Duplicate manifest slugs: {', '.join(duplicates)}")

    root = path.parent
    by_slug = {entry.slug: entry for entry in entries}
    for entry in entries:
        if not (root / entry.path).is_file():
            raise ValueError(f"'{entry.slug}': recipe not found: {entry.path}")
        if not (root / entry.page).is_file():
            raise ValueError(f"'{entry.slug}': page not found: {entry.page}")
        if entry.context is not None and entry.context not in by_slug:
            raise ValueError(f"'{entry.slug}': unknown context '{entry.context}'")
        if entry.focus is not None and entry.parent is not None:
            raise ValueError(
                f"'{entry.slug}': focus is for full recipes, not sub-recipes"
            )
        if entry.parent is None:
            continue
        parent = by_slug.get(entry.parent)
        if parent is None:
            raise ValueError(f"'{entry.slug}': unknown parent '{entry.parent}'")
        if parent.parent is not None:
            raise ValueError(
                f"'{entry.slug}': parent '{entry.parent}' is itself included by "
                f"'{parent.parent}'; a parent must be a full recipe."
            )
    return entries


def build_site_data(
    manifest: str | Path,
    out_dir: str | Path,
    *,
    resolve_sources: bool = True,
) -> SiteDataResult:
    """Write every data file the site needs.

    Files under ``partials/`` and ``recipes/`` for recipes the manifest no
    longer lists are deleted, so the folder always mirrors the manifest.

    Args:
        manifest: The site's recipe manifest.
        out_dir: Folder to write into, normally ``docs/data``.
        resolve_sources: Import each implementation to link its source on
            GitHub. Needs the scientific packages.

    Returns:
        A SiteDataResult listing the files written and removed.
    """
    from aa_recipe_manager.parser.yaml_reader import load_recipe
    from aa_recipe_manager.registry.loader import load_builtin_registry

    manifest = Path(manifest)
    root = manifest.parent
    out_dir = Path(out_dir)
    entries = load_manifest(manifest)
    by_slug = {entry.slug: entry for entry in entries}
    registry = load_builtin_registry()

    recipes = {entry.slug: load_recipe(root / entry.path) for entry in entries}
    children: dict[str, list[ManifestEntry]] = {}
    for entry in entries:
        if entry.parent is not None:
            children.setdefault(entry.parent, []).append(entry)

    graphs = {
        entry.slug: build_recipe_graph(
            _focused(entry, recipes[entry.slug]),
            registry,
            slug=entry.slug,
            title=entry.title,
            source=entry.path,
            parent=entry.parent,
            level=entry.level,
            cruise=entry.cruise or _parent_cruise(entry, by_slug),
        )
        for entry in entries
    }
    for entry in entries:
        context = _context(entry, root, recipes, graphs[entry.slug], by_slug, children)
        if context is not None:
            graphs[entry.slug]["context"] = context

    overviews = {
        slug: overview_graph(
            recipes[slug],
            _step_homes(by_slug[slug], recipes, children),
            slug=f"{slug}-overview",
            title=by_slug[slug].title,
            parent_slug=slug,
            parent_inputs=declared_inputs(root / by_slug[slug].path),
        )
        for slug in children
    }

    notebooks = {
        entry.slug: generate_parent_notebook(recipes[entry.slug], registry)
        for entry in entries
        if entry.parent is None
    }

    files: dict[str, str] = {}
    for entry in entries:
        notebook_md, note = _notebook_tab(entry, recipes, notebooks)
        yaml_text = (root / entry.path).read_text(encoding="utf-8")
        overview = overviews.get(entry.slug)
        files[f"partials/{entry.slug}.md"] = _tabs_partial(
            entry, graphs[entry.slug], yaml_text, notebook_md, note, overview, by_slug
        )
        files[f"recipes/{entry.slug}.json"] = _dump(graphs[entry.slug])
        if overview is not None:
            files[f"recipes/{overview['slug']}.json"] = _dump(overview)
        if entry.parent is None:
            files[f"notebooks/{entry.slug}.ipynb"] = _notebook_file(
                notebooks[entry.slug]
            )

    payload = build_payload(registry, resolve_sources=resolve_sources)
    _attach_usage(payload, entries, graphs)
    _drop_local_paths(payload)
    files["ops.json"] = _dump(payload)
    files["manifest.json"] = _dump(_manifest_document(entries, graphs, by_slug))

    result = SiteDataResult(
        counts={
            "ops": payload["counts"]["ops"],
            "recipes": len(entries),
            "sources_resolved": payload["counts"]["sources_resolved"],
        },
        unresolved=unresolved_ops(payload) if resolve_sources else [],
        stale_links=stale_link_ops(payload) if resolve_sources else [],
    )
    for name in sorted(files):
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[name], encoding="utf-8", newline="\n")
        result.written.append(target)
    for stale in _generated_files(out_dir):
        if stale.relative_to(out_dir).as_posix() not in files:
            stale.unlink()
            result.removed.append(stale)
    return result


def _generated_files(out_dir: Path) -> list[Path]:
    """The per-recipe files under an output folder."""
    return [
        path
        for sub in ("partials", "recipes", "notebooks")
        for path in sorted((out_dir / sub).glob("*"))
        if path.is_file()
    ]


def _focused(entry: ManifestEntry, recipe: Recipe) -> Recipe:
    """The part of a recipe its diagram shows: every step, or its ``focus``."""
    if not entry.focus:
        return recipe
    ids = {step.id for step in recipe.steps}
    missing = sorted(set(entry.focus) - ids)
    if missing:
        raise ValueError(f"'{entry.slug}': focus names unknown steps {missing}")
    steps = [step for step in recipe.steps if step.id in entry.focus]
    outputs = {
        name: decl
        for name, decl in (recipe.outputs or {}).items()
        if decl.step_id in entry.focus
    }
    text = " ".join(
        value
        for step in steps
        for part in (step.inputs, step.params, step.map_over, step.collect)
        for value in strings_in(part)
    )
    inputs = {
        name: decl
        for name, decl in recipe.inputs.items()
        if "${inputs." + name + "}" in text
    }
    return recipe.model_copy(
        update={"steps": steps, "outputs": outputs or None, "inputs": inputs}
    )


def _home(entry: ManifestEntry) -> Home:
    return Home(entry.slug, entry.title, page_url(entry.page), entry.level, entry.page)


def _step_homes(
    parent: ManifestEntry,
    recipes: dict[str, Recipe],
    children: dict[str, list[ManifestEntry]],
) -> dict[str, Home]:
    """Where each step of a full recipe is documented.

    A step in an included file belongs to the manifest entry for that file,
    matched by its step ids. Anything else is the full recipe's own.
    """
    homes = {step.id: _home(parent) for step in recipes[parent.slug].steps}
    for block in recipes[parent.slug].include_blocks:
        child = next(
            (
                c
                for c in children.get(parent.slug, [])
                if {s.id for s in recipes[c.slug].steps} == set(block.step_ids)
            ),
            None,
        )
        home = _home(child) if child else Home(None, block.source, None)
        for step_id in block.step_ids:
            homes[step_id] = home
    return homes


def _context(
    entry: ManifestEntry,
    root: Path,
    recipes: dict[str, Recipe],
    graph: dict[str, Any],
    by_slug: dict[str, ManifestEntry],
    children: dict[str, list[ManifestEntry]],
) -> dict[str, Any] | None:
    """What surrounds a recipe's diagram in the full recipe it runs in."""
    if entry.parent is not None:
        parent = by_slug[entry.parent]
        return recipe_context(
            graph,
            recipes[parent.slug],
            _step_homes(parent, recipes, children),
            include_overrides(root / parent.path, root / entry.path),
        )
    if entry.focus:
        documented_by = by_slug.get(entry.context, entry) if entry.context else entry
        homes = {
            step.id: _home(entry if step.id in entry.focus else documented_by)
            for step in recipes[entry.slug].steps
        }
        return recipe_context(graph, recipes[entry.slug], homes, {})
    return None


def _notebook_file(nb: Any) -> str:
    """A notebook as .ipynb text, with cell ids fixed so rebuilds match."""
    import nbformat

    nb = nbformat.from_dict(nb)
    for index, cell in enumerate(nb.cells):
        cell["id"] = f"cell-{index}"
    return nbformat.writes(nb) + "\n"


def _drop_local_paths(payload: dict[str, Any]) -> None:
    """Remove each source's absolute file path, which names the build machine.

    The site links to GitHub, so only the repository-relative path is kept.
    """
    for op in payload["ops"]:
        for impl in op["implementations"]:
            impl["source"].pop("file", None)


def check_site_data(manifest: str | Path, out_dir: str | Path) -> list[str]:
    """Compare committed site data with a fresh build.

    Source links are left out of the comparison, since resolving them needs
    the scientific packages.

    Args:
        manifest: The site's recipe manifest.
        out_dir: Folder holding the committed data.

    Returns:
        One line per difference. Empty when the data is current.
    """
    out_dir = Path(out_dir)
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        build_site_data(manifest, fresh, resolve_sources=False)
        for path in sorted(fresh.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(fresh).as_posix()
            committed = out_dir / name
            if not committed.is_file():
                problems.append(f"missing: {name}")
            elif not _same(name, path, committed):
                problems.append(f"out of date: {name}")
        for path in _generated_files(out_dir):
            name = path.relative_to(out_dir).as_posix()
            if not (fresh / name).exists():
                problems.append(f"no longer generated: {name}")
    return problems


def _same(name: str, fresh: Path, committed: Path) -> bool:
    """Compare two data files, ignoring line endings a Windows checkout adds."""
    if name == "ops.json":
        fresh_ops = json.loads(fresh.read_text(encoding="utf-8"))
        committed_ops = json.loads(committed.read_text(encoding="utf-8"))
        return strip_environment(fresh_ops) == strip_environment(committed_ops)
    return fresh.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")


def _parent_cruise(
    entry: ManifestEntry, by_slug: dict[str, ManifestEntry]
) -> str | None:
    parent = by_slug.get(entry.parent) if entry.parent else None
    return parent.cruise if parent else None


def _notebook_tab(
    entry: ManifestEntry,
    recipes: dict[str, Recipe],
    notebooks: dict[str, Any],
) -> tuple[str, str | None]:
    """The Notebook tab's Markdown and an optional note shown above it."""
    if entry.parent is None:
        return notebook_to_markdown(notebooks[entry.slug], heading_shift=2), None

    # The parent records each include by file name and step ids. Step ids are
    # unique across a recipe, so they identify the include even when two
    # included files share a name.
    step_ids = {step.id for step in recipes[entry.slug].steps}
    blocks = [
        b for b in recipes[entry.parent].include_blocks if set(b.step_ids) == step_ids
    ]
    if not blocks:
        raise ValueError(
            f"'{entry.slug}': parent '{entry.parent}' includes no file with steps "
            f"{sorted(step_ids)}"
        )

    sliced = slice_steps(notebooks[entry.parent], blocks[0].step_ids)
    note = None
    if sliced.shared_with:
        shared = ", ".join(f"`{step}`" for step in sliced.shared_with)
        note = (
            "Steps here run inside a per-file loop shared with steps from "
            f"another level ({shared}), so the whole loop is shown."
        )
    return cells_to_markdown(sliced.cells, heading_shift=1), note


def _tabs_partial(
    entry: ManifestEntry,
    graph: dict[str, Any],
    yaml_text: str,
    notebook_md: str,
    note: str | None,
    overview: dict[str, Any] | None,
    by_slug: dict[str, ManifestEntry],
) -> str:
    """The tabs a page pulls in with a snippet.

    Every recipe gets Flow diagram, YAML and Notebook tabs. A full recipe
    that includes others also gets a Levels tab first, drawing one box per
    included file.
    """
    page_dir = posixpath.dirname(entry.page) or "."

    def link(target: str) -> str:
        return posixpath.relpath(target, page_dir)

    tabs: list[tuple[str, list[str]]] = []
    if overview is not None:
        levels = []
        for node in overview["nodes"]:
            if node["kind"] != "level":
                continue
            if node["doc"]:
                levels.append(f"- [{node['label']}]({link(node['doc'])})")
            else:
                levels.append(f"- `{node['label']}`")
        tabs.append(("Levels", _flow_div(overview["slug"], levels)))

    steps = []
    for node in graph["nodes"]:
        if node["kind"] != "step":
            continue
        op = node["op"]
        if node["custom"]:
            label = f"custom `{node['callable_path']}`"
        else:
            label = f"[`{op}`]({link('ops/' + op + '.md')})"
        steps.append(f"- `{node['id']}`: {label}")
    tabs.append(
        ("All steps" if overview else "Flow diagram", _flow_div(entry.slug, steps))
    )

    fence = "```"
    while fence in yaml_text:
        fence += "`"
    tabs.append(
        (
            "YAML",
            [
                f"[Download `{Path(entry.path).name}`]({link(entry.path)})",
                "",
                f"{fence}yaml",
                yaml_text.rstrip("\n"),
                fence,
            ],
        )
    )

    if notebook_md.strip():
        notebook = []
        if entry.parent is None:
            notebook_path = f"data/notebooks/{entry.slug}.ipynb"
            notebook += [
                f"[Download the notebook]({link(notebook_path)}), as "
                "`aa-recipe generate` writes it.",
                "",
            ]
        else:
            parent = by_slug[entry.parent]
            notebook_path = f"data/notebooks/{parent.slug}.ipynb"
            notebook += [
                f"These are the cells that run this level, taken from the "
                f"[{parent.title} notebook]({link(notebook_path)}). They use "
                "variables earlier levels define, so run them from the full "
                "notebook.",
                "",
            ]
        if note:
            notebook += ['!!! note "Shared loop"', "", f"    {note}", ""]
        notebook.append(notebook_md.rstrip("\n"))
        tabs.append(("Notebook", notebook))

    parts: list[str] = []
    for label, lines in tabs:
        if parts:
            parts.append("")
        parts += [f'=== "{label}"', "", _indent(lines)]
    return "\n".join(parts) + "\n"


def _flow_div(slug: str, fallback: list[str]) -> list[str]:
    """A flow diagram placeholder, with a plain list shown until it draws."""
    return [
        f'<div class="recipe-flow" data-recipe="{slug}" markdown>',
        "",
        *fallback,
        "",
        "</div>",
    ]


def _indent(lines: list[str]) -> str:
    """Indent a block four spaces so it sits inside a content tab."""
    text = "\n".join(lines)
    return "\n".join(f"    {line}" if line.strip() else "" for line in text.split("\n"))


def _attach_usage(
    payload: dict[str, Any],
    entries: list[ManifestEntry],
    graphs: dict[str, dict[str, Any]],
) -> None:
    """Record on each op which documented recipes use it.

    A recipe that other entries are included from is skipped, since its steps
    are already listed under the sub-recipes.
    """
    parents = {entry.parent for entry in entries if entry.parent}
    usage: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.slug in parents:
            continue
        for node in graphs[entry.slug]["nodes"]:
            if node["kind"] != "step":
                continue
            op = node["extends"] if node["custom"] else node["op"]
            if op is None:
                continue
            usage.setdefault(op, []).append(
                {
                    "recipe": entry.slug,
                    "title": entry.title,
                    "page": entry.page,
                    "step": node["id"],
                }
            )
    for op in payload["ops"]:
        op["used_by"] = usage.get(op["op"], [])


def _manifest_document(
    entries: list[ManifestEntry],
    graphs: dict[str, dict[str, Any]],
    by_slug: dict[str, ManifestEntry],
) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "recipes": [
            {
                "slug": entry.slug,
                "title": entry.title,
                "path": entry.path,
                "page": entry.page,
                "parent": entry.parent,
                "level": entry.level,
                "section": entry.section,
                "cruise": entry.cruise or _parent_cruise(entry, by_slug),
                "description": entry.description,
                "steps": sum(
                    1 for n in graphs[entry.slug]["nodes"] if n["kind"] == "step"
                ),
            }
            for entry in entries
        ],
    }


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

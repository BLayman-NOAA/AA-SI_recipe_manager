# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Generate the data files the documentation site is built from.

A manifest lists the recipes the site documents. For each full recipe this
writes the flow-diagram graph the site's viewer draws, and for every entry a
Markdown partial holding its Flow diagram, YAML and Notebook tabs. A sub-recipe
is drawn as its full recipe's graph drilled into its include. For the registry
it writes the op reference payload. Everything goes under ``docs/data`` and is
committed, so the site build itself needs neither the scientific packages nor
network access.
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
from aa_recipe_manager.graph import build_recipe_graph

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Recipe

MANIFEST_SCHEMA = 2


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

    Graphs are written for full recipes only; a sub-recipe's page draws its
    parent's graph. Files under ``partials/``, ``recipes/`` and
    ``notebooks/`` that this build does not write are deleted, so the folder
    always mirrors the manifest.

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
    graphs = {
        entry.slug: build_recipe_graph(
            recipes[entry.slug],
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
    includes = {
        entry.slug: _include_id(entry, root, by_slug, graphs) for entry in entries
    }
    children: dict[str, dict[str, ManifestEntry]] = {}
    for entry in entries:
        if entry.parent is None:
            continue
        include = includes[entry.slug]
        other = children.setdefault(entry.parent, {}).setdefault(include, entry)
        if other is not entry:
            raise ValueError(
                f"'{entry.slug}' and '{other.slug}' both document include "
                f"'{include}' of '{entry.parent}'"
            )

    notebooks = {
        entry.slug: generate_parent_notebook(recipes[entry.slug], registry)
        for entry in entries
        if entry.parent is None
    }

    files: dict[str, str] = {}
    for entry in entries:
        notebook_md, note = _notebook_tab(entry, recipes, notebooks)
        yaml_text = (root / entry.path).read_text(encoding="utf-8")
        files[f"partials/{entry.slug}.md"] = _tabs_partial(
            entry,
            graphs[entry.slug],
            includes[entry.slug],
            children.get(entry.slug, {}),
            yaml_text,
            notebook_md,
            note,
            by_slug,
        )
        if entry.parent is None:
            files[f"recipes/{entry.slug}.json"] = _dump(graphs[entry.slug])
            files[f"notebooks/{entry.slug}.ipynb"] = _notebook_file(
                notebooks[entry.slug]
            )

    payload = build_payload(registry, resolve_sources=resolve_sources)
    _attach_usage(payload, entries, graphs)
    _drop_local_paths(payload)
    files["ops.json"] = _dump(payload)
    files["manifest.json"] = _dump(
        _manifest_document(entries, graphs, includes, by_slug)
    )

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


def page_url(page: str | None) -> str | None:
    """The site URL of a docs page, relative to the site root."""
    if page is None:
        return None
    path = page[:-3] if page.endswith(".md") else page
    if path == "index":
        return ""
    if path.endswith("/index"):
        return path[: -len("index")]
    return path + "/"


def _include_id(
    entry: ManifestEntry,
    root: Path,
    by_slug: dict[str, ManifestEntry],
    graphs: dict[str, dict[str, Any]],
) -> str | None:
    """The id of this entry's include in its root graph, or None for a root.

    The include of the entry's own file is preferred. Failing that, the one
    holding exactly the entry's steps: step ids are unique across a recipe,
    and a file that only includes another is listed after the inner one.

    Raises:
        ValueError: The parent includes no file with this entry's steps.
    """
    if entry.parent is None:
        return None
    graph = graphs[entry.parent]
    parent_dir = (root / by_slug[entry.parent].path).parent
    target = (root / entry.path).resolve()
    for include in graph["includes"]:
        if (parent_dir / include["source"]).resolve() == target:
            return include["id"]
    step_ids = {n["id"] for n in graphs[entry.slug]["nodes"] if n["kind"] == "step"}
    ids = [inc["id"] for inc in graph["includes"] if set(inc["steps"]) == step_ids]
    if not ids:
        raise ValueError(
            f"'{entry.slug}': parent '{entry.parent}' includes no file with steps "
            f"{sorted(step_ids)}"
        )
    return ids[-1]


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

    step_ids = [step.id for step in recipes[entry.slug].steps]
    sliced = slice_steps(notebooks[entry.parent], step_ids)
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
    include: str | None,
    children: dict[str, ManifestEntry],
    yaml_text: str,
    notebook_md: str,
    note: str | None,
    by_slug: dict[str, ManifestEntry],
) -> str:
    """The tabs a page pulls in with a snippet: Flow diagram, YAML, Notebook.

    Args:
        entry: The recipe the tabs document.
        graph: The recipe's own graph, which the fallback list is drawn from.
        include: The entry's include id in its root graph, None for a root.
        children: For a full recipe, its sub-recipe entries by include id.
        yaml_text: The recipe file's text.
        notebook_md: The Notebook tab's Markdown.
        note: A note shown above the notebook cells.
        by_slug: Every manifest entry by slug.
    """
    page_dir = posixpath.dirname(entry.page) or "."

    def link(target: str) -> str:
        return posixpath.relpath(target, page_dir)

    lines = []
    # A full recipe with documented sub-recipes lists each top level include
    # once, linked to its page, as the viewer draws it collapsed.
    top = {
        step: inc
        for inc in (graph["includes"] if children else [])
        if inc["parent"] is None
        for step in inc["steps"]
    }
    listed = set()
    for node in graph["nodes"]:
        if node["kind"] != "step":
            continue
        inc = top.get(node["id"])
        if inc is None:
            if node["custom"]:
                label = f"custom `{node['callable_path']}`"
            else:
                label = f"[`{node['op']}`]({link('ops/' + node['op'] + '.md')})"
            lines.append(f"- `{node['id']}`: {label}")
        elif inc["id"] not in listed:
            listed.add(inc["id"])
            child = children.get(inc["id"])
            if child is None:
                lines.append(f"- `{inc['title']}`")
            elif child.page == entry.page:
                lines.append(f"- {child.title}")
            else:
                lines.append(f"- [{child.title}]({link(child.page)})")
    tabs = [
        (
            "Flow diagram",
            _viewer_div(entry.parent or entry.slug, entry.slug, include, lines),
        )
    ]

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


def _viewer_div(
    root: str, slug: str, drill: str | None, fallback: list[str]
) -> list[str]:
    """The viewer placeholder, with a plain list shown until it draws."""
    drill_attr = f' data-drill="{drill}"' if drill else ""
    return [
        f'<div class="recipe-viewer" data-recipe="{root}" data-slug="{slug}"'
        f"{drill_attr} markdown>",
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
    includes: dict[str, str | None],
    by_slug: dict[str, ManifestEntry],
) -> dict[str, Any]:
    recipes = []
    for entry in entries:
        root = entry.parent or entry.slug
        recipes.append(
            {
                "slug": entry.slug,
                "title": entry.title,
                "path": entry.path,
                "page": entry.page,
                "url": page_url(entry.page),
                "parent": entry.parent,
                "level": entry.level,
                "section": entry.section,
                "cruise": entry.cruise or _parent_cruise(entry, by_slug),
                "description": entry.description,
                "steps": sum(
                    1 for n in graphs[entry.slug]["nodes"] if n["kind"] == "step"
                ),
                "root": root,
                "include": includes[entry.slug],
                "recipe_dir": posixpath.dirname(by_slug[root].path),
            }
        )
    return {"schema": MANIFEST_SCHEMA, "recipes": recipes}


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

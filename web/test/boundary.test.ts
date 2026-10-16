/// <reference types="node" />
// The docs site viewer mounts the shared diagram, inspector and YAML pieces
// without a server. This walks their source imports (type only imports left
// out, as the bundler drops them) and checks none reaches the app's query
// client, run store, shell or dock.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SHARED = [
  'src/diagram/DiagramView.tsx',
  'src/inspector/InspectorBody.tsx',
  'src/env.ts',
  'src/state/view.ts',
  'src/yaml/followSelection.ts',
  'src/yaml/YamlText.tsx',
  'src/yaml/YamlFiles.tsx',
  'src/yaml/findLine.ts',
];

const FORBIDDEN_PACKAGES = [/^@tanstack\/react-query/, /^dockview/];
const FORBIDDEN_FILES = [
  /^src\/api\/queries\.ts$/,
  /^src\/runs\/(store|usePreviewIntent|useRunControls|RunHint)\.tsx?$/,
  /^src\/shell\//,
  /^src\/recipes\//,
];

const IMPORT = /^\s*(import|export)\s+(type\s+)?([^'";]*?)\s*from\s*['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]/gm;

/** Whether named imports are all `type` ones, so the bundler drops the import. */
function typeOnly(clause: string): boolean {
  const named = clause.match(/^\{([^}]*)\}$/s);
  if (!named) return false;
  const names = named[1].split(',').map((name) => name.trim()).filter(Boolean);
  return names.length > 0 && names.every((name) => name.startsWith('type '));
}

function resolve(from: string, specifier: string): string | null {
  const base = join(dirname(from), specifier.replace(/\?.*$/, ''));
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, 'index.ts')]) {
    if (/\.(ts|tsx)$/.test(candidate) && existsSync(candidate)) return candidate.replace(/\\/g, '/');
  }
  return null;
}

/** Every source file and package the entries import at run time. */
function runtimeImports(entries: string[]): { files: Set<string>; packages: Set<string> } {
  const files = new Set<string>();
  const packages = new Set<string>();
  const queue = [...entries];
  while (queue.length > 0) {
    const file = queue.pop()!;
    if (files.has(file)) continue;
    files.add(file);
    for (const match of readFileSync(file, 'utf8').matchAll(IMPORT)) {
      const [, , typeKeyword, clause, fromSpecifier, bareSpecifier] = match;
      const specifier = fromSpecifier ?? bareSpecifier;
      if (typeKeyword || (clause && typeOnly(clause.trim()))) continue;
      if (!specifier.startsWith('.')) {
        packages.add(specifier);
        continue;
      }
      const target = resolve(file, specifier);
      if (target) queue.push(relative('.', target).replace(/\\/g, '/'));
    }
  }
  return { files, packages };
}

describe('the shared diagram, inspector and YAML pieces', () => {
  const { files, packages } = runtimeImports(SHARED);

  it('follows the import graph', () => {
    expect(files).toContain('src/diagram/nodes/StepNode.tsx');
    expect(files).toContain('src/diagram/locate.ts');
    expect(packages).toContain('@xyflow/react');
  });

  it('imports no query client, dock, run store, shell or recipes code', () => {
    const badPackages = [...packages].filter((name) => FORBIDDEN_PACKAGES.some((rule) => rule.test(name)));
    const badFiles = [...files].filter((file) => FORBIDDEN_FILES.some((rule) => rule.test(file)));
    expect(badPackages).toEqual([]);
    expect(badFiles).toEqual([]);
  });

  it('the viewer entry imports none of them either', () => {
    const viewer = runtimeImports(['src/viewer/main.tsx']);
    expect(viewer.files).toContain('src/viewer/SiteViewer.tsx');
    expect(viewer.files).toContain('src/diagram/DiagramView.tsx');
    expect(viewer.files).toContain('src/inspector/InspectorBody.tsx');
    expect(viewer.files).toContain('src/yaml/YamlText.tsx');
    const badPackages = [...viewer.packages].filter((name) => FORBIDDEN_PACKAGES.some((rule) => rule.test(name)));
    const badFiles = [...viewer.files].filter((file) => FORBIDDEN_FILES.some((rule) => rule.test(file)));
    expect(badPackages).toEqual([]);
    expect(badFiles).toEqual([]);
  });

  it('would notice the app modules that do import them', () => {
    const app = runtimeImports(['src/diagram/Diagram.tsx', 'src/inspector/Inspector.tsx']);
    expect(app.packages).toContain('@tanstack/react-query');
    expect(app.files).toContain('src/runs/store.ts');
    expect(app.files).toContain('src/shell/dock.ts');
  });
});

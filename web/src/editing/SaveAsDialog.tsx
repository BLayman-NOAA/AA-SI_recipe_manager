import { useQueryClient } from '@tanstack/react-query';
import { useMemo, useState, type ReactNode } from 'react';
import { errorPaths, isApiError } from '../api/client';
import { stepNodes } from '../api/graph';
import type { SaveAsRequest, SaveAsResult, SessionDocument } from '../api/models';
import { heldPreview, useMutationError, useSaveAs } from '../api/queries';
import { fileName } from '../diagram/runConfig';
import { Dialog } from '../shell/Dialog';
import { andList, plural } from '../text';
import { useEditingStore } from './store';
import { surveyHitsLost } from './survey';

/** The folder of a path, with forward slashes and no trailing one. */
export function folderOf(path: string): string {
  const posix = path.replace(/\\/g, '/');
  const cut = posix.lastIndexOf('/');
  return cut < 0 ? '' : posix.slice(0, cut);
}

/** The Save As path offered first: `<origin folder>/<stem>_copy.yaml`. */
export function defaultSaveAsPath(origin: string): string {
  const posix = origin.replace(/\\/g, '/');
  const name = posix.slice(posix.lastIndexOf('/') + 1);
  const stem = name.replace(/\.ya?ml$/i, '');
  const folder = folderOf(posix);
  return `${folder ? `${folder}/` : ''}${stem}_copy.yaml`;
}

/** Whether a Save As path names the recipe's own file, which Save writes instead. */
export function isOwnFile(path: string, origin: string): boolean {
  return path.trim().replace(/\\/g, '/') === origin.replace(/\\/g, '/');
}

/** What the copy does to step hashes, from a Save As result's `rehashed`. */
export function rehashText(rehashed: string[] | null, total: number): string {
  if (rehashed === null) return 'The step hashes of the copy could not be computed.';
  if (rehashed.length === 0) return 'All steps hash as in the original.';
  return `${rehashed.length} of ${total} steps hash differently from the original and start a new cache: ${rehashed.join(', ')}.`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rc-saveas-section">
      <div className="rc-field-name">{title}</div>
      {children}
    </div>
  );
}

/** The dry run report: what a Save As with these settings would write and change. */
function SaveAsReport({ result, session, lost }: { result: SaveAsResult; session: SessionDocument; lost: string[] }) {
  const rewrites = Object.entries(result.rewrites).filter(([, list]) => list.length > 0);
  return (
    <div className="rc-saveas-report">
      <Section title="Files to write">
        <ul className="rc-dialog-list">
          {result.written.map((path) => (
            <li key={path} className="rc-mono">
              {path}
            </li>
          ))}
        </ul>
      </Section>
      {rewrites.length > 0 && (
        <Section title="Paths rewritten">
          {rewrites.map(([file, list]) => (
            <div key={file}>
              <div className="rc-mono">{file}</div>
              <ul className="rc-dialog-list">
                {list.map((rewrite) => (
                  <li key={`${rewrite.where} ${rewrite.old}`}>
                    <span className="rc-mono">{rewrite.where}</span>: <span className="rc-mono">{rewrite.old}</span>
                    {' → '}
                    <span className="rc-mono">{rewrite.new}</span>
                    {rewrite.absolute && <span className="rc-row-sub"> (absolute)</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Section>
      )}
      <p>{result.config ? `The run config is copied to ${result.config}.` : 'No run config is copied.'}</p>
      {result.absolute.length > 0 && (
        <p className="rc-status-warning">{`Absolute paths the copy keeps as written: ${andList(result.absolute)}.`}</p>
      )}
      <p>{rehashText(result.rehashed, stepNodes(session.graph).length)}</p>
      {lost.length > 0 && (
        <p className="rc-status-warning">
          {`${plural(lost.length, 'survey backed step')} among them would be recomputed in your user cache: ${lost.join(', ')}.`}
        </p>
      )}
    </div>
  );
}

/**
 * File > Save As: a path, the path and config choices, a dry run Preview of
 * what would be written and rehashed, then Save. A target that exists asks
 * for Overwrite first.
 */
export function SaveAsDialog({ session, onClose }: { session: SessionDocument; onClose: () => void }) {
  const client = useQueryClient();
  const saveAs = useSaveAs(session.id);
  const error = useMutationError(saveAs);
  const setNotice = useEditingStore((state) => state.setNotice);
  const [path, setPath] = useState(() => defaultSaveAsPath(session.origin));
  const [keepPaths, setKeepPaths] = useState(false);
  const [copyConfig, setCopyConfig] = useState(true);
  const [overwrite, setOverwrite] = useState(false);
  const [report, setReport] = useState<SaveAsResult | null>(null);
  /** Files a Save found in the way, after a 409 TargetExists. */
  const [inTheWay, setInTheWay] = useState<string[] | null>(null);
  const held = useMemo(() => heldPreview(client, session.id), [client, session.id]);

  // Overwrite was ticked for the files of the settings shown; new settings ask again.
  const changed = <T,>(set: (value: T) => void) => (value: T) => {
    set(value);
    setReport(null);
    setInTheWay(null);
    setOverwrite(false);
  };

  const request = (dryRun: boolean): SaveAsRequest => ({
    path: path.trim(),
    keep_paths: keepPaths,
    copy_config: copyConfig,
    overwrite,
    dry_run: dryRun,
  });

  const preview = () => saveAs.mutate(request(true), { onSuccess: (response) => setReport(response.result) });
  const save = () =>
    saveAs.mutate(request(false), {
      onSuccess: (response) => {
        setNotice(`Saved as ${response.result.root}`);
        onClose();
      },
      onError: (failure) => {
        if (!isApiError(failure, 'TargetExists')) return;
        setOverwrite(true);
        setInTheWay(errorPaths(failure));
      },
    });

  const ownFolder = folderOf(path.trim()) !== folderOf(session.origin);
  const ownFile = isOwnFile(path, session.origin);
  const blocked = saveAs.isPending || !path.trim() || ownFile;
  const existing = inTheWay ?? report?.existing ?? [];
  const canKeepPaths = keepPaths || (report?.rehashed?.length ?? 0) > 0;
  const lost = report ? surveyHitsLost(report.rehashed, held) : [];
  const showError = error && !isApiError(saveAs.error, 'TargetExists');

  return (
    <Dialog title="Save As" onClose={onClose}>
      <label className="rc-field">
        <div className="rc-field-name">Save the recipe as</div>
        <input
          className="rc-input rc-mono"
          aria-label="Save As path"
          value={path}
          onChange={(event) => changed(setPath)(event.target.value)}
        />
      </label>
      {ownFile ? (
        <p className="rc-status-warning">{`'${fileName(session.origin)}' is the recipe's own file; use Save.`}</p>
      ) : (
        <p className="rc-row-sub">
          {ownFolder
            ? 'The copy runs from its own folder.'
            : 'Beside the original, edited sub-recipes are written in place.'}
        </p>
      )}
      <label className="rc-check">
        <input
          type="checkbox"
          checked={keepPaths}
          disabled={!canKeepPaths}
          onChange={(event) => changed(setKeepPaths)(event.target.checked)}
        />
        Keep relative paths as written so the copy keeps its caches
      </label>
      <label className="rc-check">
        <input type="checkbox" checked={copyConfig} onChange={(event) => changed(setCopyConfig)(event.target.checked)} />
        Copy the run config
      </label>
      {existing.length > 0 && (
        <>
          <p className="rc-status-warning">{`Would replace: ${andList(existing)}.`}</p>
          <label className="rc-check">
            <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />
            Overwrite
          </label>
        </>
      )}
      {inTheWay && <p>These files exist. Overwrite is ticked; Save again to replace them.</p>}
      {report && <SaveAsReport result={report} session={session} lost={lost} />}
      {showError && <p className="rc-error-text">{error}</p>}
      <div className="rc-dialog-actions">
        <button type="button" className="rc-button" disabled={blocked} onClick={preview}>
          Preview
        </button>
        <button type="button" className="rc-button rc-primary" disabled={blocked} onClick={save}>
          Save
        </button>
        <button type="button" className="rc-button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}

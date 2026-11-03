import { useEffect, useMemo, useState } from 'react';
import { fileEditable, type SessionDocument } from '../api/models';
import { useCurrentSession, useFileText, useReload } from '../api/queries';
import { conflictText } from '../editing/DiskBanner';
import { useEditingStore } from '../editing/store';
import { openConflicts } from '../editing/useDiskSync';
import { useSessionStore } from '../state/session';
import { useViewStore } from '../state/view';
import { stepLine } from './findLine';
import { yamlTarget } from './followSelection';
import { editorStatus, lf } from './editorState';
import { useYamlEditors } from './editors';
import { YamlFiles, type YamlEditing } from './YamlFiles';

/** The disk conflict of a file the session did not take in, as the editor's bar shows it. */
function useDiskBar(session: SessionDocument | undefined, path: string | null, onReload: () => void) {
  const kept = useEditingStore((state) => state.kept);
  const keep = useEditingStore((state) => state.keep);
  const conflict = session && path ? openConflicts(session.conflicts, kept).find((item) => item.path === path) : undefined;
  if (!conflict) return null;
  return {
    message: conflictText(conflict),
    onReload: conflict.error === null ? onReload : null,
    onKeep: () => keep(conflict.path, conflict.disk_sha256),
  };
}

/**
 * The session's recipe files, one tab each, edited in place; follows the
 * selected step. The typing is sent by the Shell's sender (editors.ts).
 */
export function YamlPane() {
  const sid = useSessionStore((state) => state.sid);
  const session = useCurrentSession();
  const selected = useViewStore()((state) => state.selected);
  const data = session.data;
  const files = data?.files ?? [];
  const graph = data?.graph;
  const [active, setActive] = useState<string | null>(null);
  const editors = useYamlEditors((state) => state.files);
  const dispatch = useYamlEditors((state) => state.dispatch);
  const reload = useReload(sid);

  const target = useMemo(() => (graph ? yamlTarget(graph, selected) : null), [graph, selected]);

  useEffect(() => {
    if (target) setActive(target.path);
  }, [target]);

  const path = files.some((file) => file.path === active) ? active : files[0]?.path ?? null;
  const editable = Boolean(data && path && fileEditable(data, path));
  const file = useFileText(sid, path);
  const serverText = file.data?.text ?? null;

  useEffect(() => {
    if (path && editable && serverText !== null) dispatch(path, { type: 'load', text: lf(serverText) });
  }, [path, editable, serverText, dispatch]);

  const state = path && editable ? editors[path] : undefined;
  const diskBar = useDiskBar(data, path, () => {
    if (!path) return;
    dispatch(path, { type: 'reload' });
    reload.mutate([path]);
  });
  const text = state ? state.text : serverText;
  const highlight =
    text !== null && target?.stepId && target.path === path ? stepLine(text, target.stepId) : null;

  if (files.length === 0) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane to see its YAML.</div>;
  }
  const editor: YamlEditing | undefined =
    state && path
      ? {
          text: state.text,
          onChange: (value) => dispatch(path, { type: 'type', text: value }),
          status: editorStatus(state),
          error: state.error,
          external: state.external,
          onReload: () => dispatch(path, { type: 'reload' }),
          onKeep: () => dispatch(path, { type: 'keep' }),
          disk: diskBar,
        }
      : undefined;
  return (
    <YamlFiles
      files={files.map((item) => ({ path: item.path, title: item.origin, dirty: item.dirty }))}
      active={path}
      onSelect={setActive}
      text={text}
      loading={file.isLoading}
      error={file.error?.message ?? null}
      highlight={highlight}
      editor={editor}
    />
  );
}

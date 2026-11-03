/** A recipe's files as tabs over the text of the one shown: read only, or an editor. */
import type { EditorStatus } from './editorState';
import { YamlEditor } from './YamlEditor';
import { YamlText } from './YamlText';

export interface YamlFile {
  path: string;
  /** Hover text of the tab, such as where the file lives. */
  title?: string;
  /** The session holds unsaved edits to it; its tab shows a dot. */
  dirty?: boolean;
}

/** The shown file's editor, from the app; the docs site passes none. */
export interface YamlEditing {
  text: string;
  onChange: (text: string) => void;
  status: EditorStatus;
  error: string | null;
  /** The file changed outside the editor (a form edit, a reload) while typing was pending. */
  external: boolean;
  onReload: () => void;
  onKeep: () => void;
  /** The file changed on disk and the session did not take it in: the sentence, and its actions. */
  disk?: { message: string; onReload: (() => void) | null; onKeep: () => void } | null;
}

export interface YamlFilesProps {
  files: YamlFile[];
  /** The path shown. */
  active: string | null;
  onSelect: (path: string) => void;
  text: string | null;
  loading: boolean;
  error: string | null;
  highlight: number | null;
  editor?: YamlEditing;
}

const STATUS_TEXT: Record<EditorStatus, string> = {
  saved: 'editing',
  typing: 'editing',
  sending: 'sending...',
  rejected: 'not applied',
};

/** The bars over the editor: the server's refusal, a form edit, a change on disk. */
function EditorBars({ editor }: { editor: YamlEditing }) {
  const disk = editor.disk;
  return (
    <>
      {editor.error && (
        <div className="rc-yaml-bar rc-yaml-error" role="alert">
          <span className="rc-row-main">{editor.error}</span>
          {editor.status === 'rejected' && (
            <span className="rc-muted">The diagram shows the last text that loaded.</span>
          )}
        </div>
      )}
      {editor.external && (
        <div className="rc-yaml-bar" role="status">
          <span className="rc-row-main">This file was changed outside the editor.</span>
          <button type="button" className="rc-button rc-small" onClick={editor.onReload}>
            Reload
          </button>
          <button type="button" className="rc-button rc-small" onClick={editor.onKeep}>
            Keep my text
          </button>
        </div>
      )}
      {disk && (
        <div className="rc-yaml-bar" role="status">
          <span className="rc-row-main">{disk.message}</span>
          {disk.onReload && (
            <button type="button" className="rc-button rc-small" onClick={disk.onReload}>
              Reload from disk
            </button>
          )}
          <button type="button" className="rc-button rc-small" onClick={disk.onKeep}>
            Keep the session's version
          </button>
        </div>
      )}
    </>
  );
}

/** File tabs, a read only or editing badge, and the shown file's text, loading state or error. */
export function YamlFiles({ files, active, onSelect, text, loading, error, highlight, editor }: YamlFilesProps) {
  return (
    <div className="rc-yaml-pane">
      <div className="rc-tabs" role="tablist" aria-label="Recipe files">
        {files.map((file) => (
          <button
            key={file.path}
            type="button"
            role="tab"
            aria-selected={file.path === active}
            className="rc-tab"
            title={file.dirty ? `${file.title ?? file.path} (unsaved edits)` : file.title}
            onClick={() => onSelect(file.path)}
          >
            {file.path}
            {file.dirty && <span className="rc-tab-dot" aria-hidden="true" />}
          </button>
        ))}
        <span className="rc-spacer" />
        <span className={`rc-badge${editor?.status === 'rejected' ? ' rc-badge-error' : ''}`}>
          {editor ? STATUS_TEXT[editor.status] : 'read only'}
        </span>
      </div>
      {editor && <EditorBars editor={editor} />}
      {loading && <div className="rc-empty rc-yaml-status">Loading...</div>}
      {error && (
        <div className="rc-error-text rc-yaml-status">
          Could not load {active}: {error}
        </div>
      )}
      {editor ? (
        <YamlEditor text={editor.text} onChange={editor.onChange} highlight={highlight} label={`${active} text`} />
      ) : (
        text !== null && <YamlText text={text} highlight={highlight} />
      )}
    </div>
  );
}

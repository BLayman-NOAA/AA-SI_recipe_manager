/** A recipe's files as tabs over the read only text of the one shown. */
import { YamlText } from './YamlText';

export interface YamlFile {
  path: string;
  /** Hover text of the tab, such as where the file lives. */
  title?: string;
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
}

/** File tabs, a read only badge, and the shown file's text, loading state or error. */
export function YamlFiles({ files, active, onSelect, text, loading, error, highlight }: YamlFilesProps) {
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
            title={file.title}
            onClick={() => onSelect(file.path)}
          >
            {file.path}
          </button>
        ))}
        <span className="rc-spacer" />
        <span className="rc-badge">read only</span>
      </div>
      {loading && <div className="rc-empty rc-yaml-status">Loading...</div>}
      {error && (
        <div className="rc-error-text rc-yaml-status">
          Could not load {active}: {error}
        </div>
      )}
      {text !== null && <YamlText text={text} highlight={highlight} />}
    </div>
  );
}

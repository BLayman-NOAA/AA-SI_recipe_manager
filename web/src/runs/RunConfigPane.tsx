/**
 * The Run Config pane: the run config file the session uses and the recipes
 * sharing it, Choose Config and Create Config, and a form over the file's
 * inputs and run settings that tries values in the session or saves them to
 * the file (FR-85, FR-86).
 */
import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { errorPaths, isApiError } from '../api/client';
import { sourceLabel } from '../api/fields';
import type { RunConfigBlock, SaveConfigRequest, SessionDocument } from '../api/models';
import {
  useChangeError,
  useChooseConfig,
  useCreateConfig,
  useCurrentSession,
  useSaveConfig,
  useSetInputs,
  useSetRunConfig,
} from '../api/queries';
import { fileName } from '../diagram/runConfig';
import { isUnapplied, toDraft } from '../inputs/inputForm';
import { ValueInput } from '../inputs/InputsPane';
import { EditField, FieldButton } from '../inspector/formParts';
import { Facts, Mono } from '../inspector/InspectorBody';
import { SharedWith } from '../inspector/RunConfigInspector';
import { controlForValue, valueType } from '../inspector/paramForm';
import { Dialog } from '../shell/Dialog';
import { andList, plural, showValue } from '../text';
import {
  addableInputs,
  configInputFields,
  EXECUTORS,
  inputDraft,
  isUnsavedSetting,
  NO_EDITS,
  nonFiniteKept,
  parseInput,
  planEdits,
  saveRequest,
  sessionRequests,
  SETTING_LABELS,
  settingDraft,
  settingFields,
  type ConfigEdits,
  type ConfigInputEdit,
  type ConfigInputField,
  type SettingField,
  type SettingName,
} from './runConfigForm';

/** The file in use, who shares it, and Choose Config and Create Config. */
function ConfigFile({ sid, block }: { sid: string; block: RunConfigBlock }) {
  const choose = useChooseConfig(sid);
  const create = useCreateConfig(sid);
  const [path, setPath] = useState('');
  const chooseError = useChangeError(choose);
  const createError = useChangeError(create);
  const existing = isApiError(create.error, 'TargetExists') ? errorPaths(create.error) : [];
  const busy = choose.isPending || create.isPending;
  const id = useId();
  const candidate = fileName(block.candidate);
  const pick = (target: string | null) =>
    choose.mutateAsync(target).then(
      () => {
        setPath('');
        create.reset();
      },
      () => undefined,
    );

  return (
    <section>
      <h3>File</h3>
      {block.path ? (
        <Facts
          rows={[
            ['file', <Mono>{block.path}</Mono>],
            ['found', block.chosen ? 'chosen for this session' : 'as the CLI finds it'],
            ['shared with', <SharedWith paths={block.shared_with} />],
          ]}
        />
      ) : (
        <p className="rc-field-help">
          No run config file is found. The CLI looks for <Mono>{candidate}</Mono> beside the recipe, then{' '}
          <Mono>aa-recipe.config.yaml</Mono>.
        </p>
      )}
      {block.error && <div className="rc-field-error">{block.error}</div>}
      <div className="rc-field">
        <label className="rc-field-name" htmlFor={`${id}choose`}>
          Choose Config
        </label>
        <div className="rc-pane-toolbar">
          <input
            id={`${id}choose`}
            className="rc-input"
            type="text"
            value={path}
            placeholder="A config file, relative to the recipe's folder or absolute"
            disabled={busy}
            onChange={(event) => setPath(event.target.value)}
          />
          <button
            type="button"
            className="rc-button"
            disabled={busy || path.trim() === ''}
            onClick={() => void pick(path.trim())}
          >
            Use this file
          </button>
          {block.chosen && (
            <button type="button" className="rc-button" disabled={busy} onClick={() => void pick(null)}>
              Find it as the CLI does
            </button>
          )}
        </div>
        {chooseError && <div className="rc-field-error">{chooseError}</div>}
      </div>
      <div className="rc-field">
        <div className="rc-pane-toolbar">
          <button
            type="button"
            className="rc-button"
            disabled={busy || block.path === block.candidate}
            title={`Write ${candidate} beside the recipe, listing its required inputs to fill in`}
            onClick={() => create.mutate()}
          >
            Create Config
          </button>
          <span className="rc-field-help">
            Writes <Mono>{candidate}</Mono> beside the recipe.
          </span>
        </div>
        {existing.length > 0 ? (
          <div className="rc-field-error">
            {fileName(existing[0])} exists already.{' '}
            <button type="button" className="rc-button rc-small" disabled={busy} onClick={() => void pick(existing[0])}>
              Use it
            </button>
          </div>
        ) : (
          createError && <div className="rc-field-error">{createError}</div>
        )}
      </div>
    </section>
  );
}

/** The badge of a field whose text differs from the file's value and is not saved yet. */
function UnsavedBadge() {
  return (
    <span className="rc-badge" title="Save to config file writes this change.">
      not saved
    </span>
  );
}

interface InputRowProps {
  field: ConfigInputField;
  edit: ConfigInputEdit | undefined;
  error: string | undefined;
  idPrefix: string;
  onEdit: (edit: ConfigInputEdit | undefined) => void;
}

/** One config input: its control, what gives the value a run uses, and Remove. */
function ConfigInputRow({ field, edit, error, idPrefix, onEdit }: InputRowProps) {
  const id = `${idPrefix}input-${field.name}`;
  const removed = edit !== undefined && 'remove' in edit;
  const source = field.field?.source;
  const badges: ReactNode[] = [];
  if (field.type) badges.push(<span key="type" className="rc-badge">{field.type}</span>);
  if (!field.field) badges.push(<span key="unused" className="rc-badge">not used by this recipe</span>);
  else if (source === 'session') {
    badges.push(
      <span key="source" className="rc-badge rc-badge-session" title="The session overrides the file's value">
        session: {showValue(field.field.value) || 'null'}
      </span>,
    );
  } else if (source === 'missing') {
    badges.push(<span key="source" className="rc-badge rc-badge-error">required</span>);
  } else if (source) {
    badges.push(<span key="source" className={`rc-badge rc-badge-${source}`}>{sourceLabel(source)}</span>);
  }
  if (!field.inFile) badges.push(<span key="added" className="rc-badge">added</span>);
  if (removed) badges.push(<span key="removed" className="rc-badge">removed on save</span>);
  if (isUnapplied(edit, field.inFile ? field.fileValue : undefined, (draft) => parseInput(field, draft))) {
    badges.push(<UnsavedBadge key="unsaved" />);
  }
  const action = field.inFile ? (
    <FieldButton label={removed ? 'Keep' : 'Remove'} onClick={() => onEdit(removed ? undefined : { remove: true })} />
  ) : (
    <FieldButton label="Cancel" onClick={() => onEdit(undefined)} />
  );
  return (
    <EditField
      id={id}
      name={field.name}
      badges={badges}
      action={action}
      error={error}
      description={field.field?.description}
    >
      <ValueInput
        id={id}
        kind={field.kind}
        draft={inputDraft(field, edit)}
        invalid={Boolean(error)}
        integer={field.type === 'int'}
        disabled={removed}
        onChange={(draft) => onEdit({ draft })}
      />
    </EditField>
  );
}

interface SettingRowProps {
  field: SettingField;
  text: string;
  /** The typed text differs from the file's value. */
  unsaved: boolean;
  error: string | undefined;
  idPrefix: string;
  onEdit: (text: string | undefined) => void;
}

/** One run setting: its control, the session's override and whether the file has it. */
function SettingRow({ field, text, unsaved, error, idPrefix, onEdit }: SettingRowProps) {
  const id = `${idPrefix}setting-${field.name}`;
  const badges: ReactNode[] = [];
  if (field.overridden) {
    badges.push(
      <span key="session" className="rc-badge rc-badge-session" title="The session overrides the file's value">
        session: {showValue(field.effective)}
      </span>,
    );
  }
  if (field.fileValue === undefined) badges.push(<span key="absent" className="rc-badge">not in the file</span>);
  if (unsaved) badges.push(<UnsavedBadge key="unsaved" />);
  const className = `rc-input${error ? ' rc-invalid' : ''}`;
  let control: ReactNode;
  if (field.name === 'executor') {
    control = (
      <select id={id} className={className} value={text} onChange={(event) => onEdit(event.target.value)}>
        <option value="">not set (sequential)</option>
        {EXECUTORS.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    );
  } else if (field.name === 'storage_options') {
    control = (
      <textarea
        id={id}
        className={`${className} rc-mono`}
        value={text}
        placeholder="not set"
        onChange={(event) => onEdit(event.target.value)}
      />
    );
  } else {
    control = (
      <input
        id={id}
        className={className}
        type={field.name === 'dask_workers' ? 'number' : 'text'}
        min={field.name === 'dask_workers' ? 1 : undefined}
        value={text}
        placeholder="not set"
        onChange={(event) => onEdit(event.target.value)}
      />
    );
  }
  return (
    <div className="rc-field">
      <div className="rc-field-head">
        <label className="rc-field-name" htmlFor={id}>
          {SETTING_LABELS[field.name]}
        </label>
        <span className="rc-badge rc-mono">{field.name}</span>
        {badges}
      </div>
      {control}
      {error && <div className="rc-field-error">{error}</div>}
    </div>
  );
}

type SaveScope = SaveConfigRequest['scope'];

interface ScopeDialogProps {
  doc: Document;
  block: RunConfigBlock;
  onChoose: (scope: SaveScope) => void;
  onCancel: () => void;
}

/** Where a save to a shared config file goes: the shared file, or a copy for this recipe alone. */
function ScopeDialog({ doc, block, onChoose, onCancel }: ScopeDialogProps) {
  const file = fileName(block.path ?? '');
  const own = fileName(block.candidate);
  const others = block.shared_with.length;
  return (
    <Dialog doc={doc} title="Save to config file" onClose={onCancel}>
      <p>Apply these changes to:</p>
      <p className="rc-field-help">
        Only this recipe writes <Mono>{own}</Mono> beside it as a copy of <Mono>{file}</Mono> with these changes;
        the recipe then takes every setting from that copy and no longer follows <Mono>{file}</Mono>.
      </p>
      <div className="rc-dialog-actions">
        <button type="button" className="rc-button" autoFocus onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="rc-button" onClick={() => onChoose('recipe')}>
          Only this recipe
        </button>
        <button type="button" className="rc-button rc-primary" onClick={() => onChoose('shared')}>
          All recipes in this folder ({others} {others === 1 ? 'other uses' : 'others use'} {file})
        </button>
      </div>
    </Dialog>
  );
}

/** The form over the file's inputs and settings, with its two ways to apply them. */
function ConfigForm({ session }: { session: SessionDocument }) {
  const sid = session.id;
  const block = session.run_config;
  const declared = session.inputs.declared;
  const save = useSaveConfig(sid);
  const setInputs = useSetInputs(sid);
  const setConfig = useSetRunConfig(sid);
  const [edits, setEdits] = useState<ConfigEdits>(NO_EDITS);
  const [asking, setAsking] = useState<Document | null>(null);
  const idPrefix = useId();
  const saveError = useChangeError(save);
  const inputsError = useChangeError(setInputs);
  const configError = useChangeError(setConfig);

  const fields = configInputFields(block, declared, edits);
  const settings = settingFields(block);
  const plan = planEdits(fields, settings, edits);
  const invalid = Object.keys(plan.errors).length > 0;
  const toSave = saveRequest(fields, settings, plan);
  const nonFinite = toSave?.inputs ? nonFiniteKept(fields, plan) : [];
  const toTry = sessionRequests(fields, settings, plan, declared, block.overrides);
  const busy = save.isPending || setInputs.isPending || setConfig.isPending;
  const edited = Object.keys(edits.inputs).length + Object.keys(edits.settings).length > 0;
  const addable = addableInputs(declared, fields);

  const editInput = (name: string, edit: ConfigInputEdit | undefined) =>
    setEdits((current) => {
      const inputs = { ...current.inputs };
      if (edit === undefined) delete inputs[name];
      else inputs[name] = edit;
      return { ...current, inputs };
    });
  const editSetting = (name: SettingName, text: string | undefined) =>
    setEdits((current) => {
      const next = { ...current.settings };
      if (text === undefined) delete next[name];
      else next[name] = text;
      return { ...current, settings: next };
    });
  const add = (name: string) => {
    const field = declared.find((candidate) => candidate.name === name);
    if (!field) return;
    const type = valueType(field.type, field.value);
    editInput(name, { draft: toDraft(field.value, controlForValue(type, field.value)) });
  };

  const trySession = async () => {
    try {
      if (toTry.inputs) await setInputs.mutateAsync(toTry.inputs);
      if (toTry.config) await setConfig.mutateAsync(toTry.config);
    } catch {
      // Shown below the buttons.
    }
  };
  const write = (scope: SaveScope) => {
    if (!toSave) return;
    save.mutateAsync({ ...toSave, scope }).then(
      () => setEdits(NO_EDITS),
      () => undefined,
    );
  };
  const shared = block.shared_with.length;
  // A file other recipes use asks first whether the change is for them too.
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (invalid || !toSave || nonFinite.length > 0) return;
    if (shared > 0) setAsking(event.currentTarget.ownerDocument);
    else write('shared');
  };

  return (
    <form onSubmit={submit}>
      <section>
        <h3>Inputs</h3>
        {fields.length === 0 && <div className="rc-empty">The file sets no inputs.</div>}
        {fields.map((field) => (
          <ConfigInputRow
            key={field.name}
            field={field}
            edit={edits.inputs[field.name]}
            error={plan.errors[`input:${field.name}`]}
            idPrefix={idPrefix}
            onEdit={(edit) => editInput(field.name, edit)}
          />
        ))}
        {addable.length > 0 && (
          <select
            className="rc-input"
            aria-label="Add an input"
            value=""
            onChange={(event) => add(event.target.value)}
          >
            <option value="">Add an input...</option>
            {addable.map((field) => (
              <option key={field.name} value={field.name}>
                {field.name}
              </option>
            ))}
          </select>
        )}
      </section>
      <section>
        <h3>Run settings</h3>
        {settings.map((field) => (
          <SettingRow
            key={field.name}
            field={field}
            text={settingDraft(field, edits)}
            unsaved={isUnsavedSetting(field, edits.settings[field.name])}
            error={plan.errors[`setting:${field.name}`]}
            idPrefix={idPrefix}
            onEdit={(text) => editSetting(field.name, text)}
          />
        ))}
      </section>
      <div className="rc-pane-toolbar">
        <button
          type="button"
          className="rc-button"
          disabled={busy || invalid || (!toTry.inputs && !toTry.config)}
          title="Use these values in this session only; the file is not changed"
          onClick={() => void trySession()}
        >
          Try in this session
        </button>
        <button
          type="submit"
          className="rc-button rc-primary"
          disabled={busy || invalid || !toSave || nonFinite.length > 0}
        >
          Save to config file
        </button>
        <button type="button" className="rc-button" disabled={!edited} onClick={() => setEdits(NO_EDITS)}>
          Discard changes
        </button>
      </div>
      {nonFinite.length > 0 && (
        <div className="rc-field-error">
          Saving the inputs would turn the NaN or infinity of {andList(nonFinite)} into text; change the file's
          inputs in the file itself.
        </div>
      )}
      {toTry.skipped.length > 0 && (
        <div className="rc-field-help">
          The recipe declares no input {toTry.skipped.join(', ')}, so the session cannot try it; it can still be
          saved to the file.
        </div>
      )}
      {[saveError, inputsError, configError].filter(Boolean).map((message) => (
        <div key={message} className="rc-field-error">
          {message}
        </div>
      ))}
      <p className="rc-field-help">
        Save to config file writes <Mono>{fileName(block.path ?? '')}</Mono> now. It is not one of the recipe's
        unsaved edits, and Undo does not take it back.
        {shared > 0 && ` The file also applies to ${plural(shared, 'other recipe')} in this folder.`}
      </p>
      {asking && (
        <ScopeDialog
          doc={asking}
          block={block}
          onCancel={() => setAsking(null)}
          onChoose={(scope) => {
            setAsking(null);
            write(scope);
          }}
        />
      )}
    </form>
  );
}

export function RunConfigPane() {
  const session = useCurrentSession().data;
  if (!session) return <div className="rc-placeholder">No session is open.</div>;
  const block = session.run_config;
  return (
    <div className="rc-pane">
      <ConfigFile key={`${session.id}-file`} sid={session.id} block={block} />
      {block.path && block.contents && <ConfigForm key={`${session.id}-${block.path}`} session={session} />}
    </div>
  );
}

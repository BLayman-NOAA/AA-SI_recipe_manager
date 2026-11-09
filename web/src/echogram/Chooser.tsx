import type { Choice } from 'aa-si-echogram-gl';
import { useState } from 'react';

interface ChooserProps {
  step: string;
  choices: Choice[];
  selected: number;
  /** The line choices among them; with several, one click adds them all. */
  lines?: Choice[];
  onAdd: (choice: Choice) => Promise<void> | void;
  onDone: () => void;
}

/** Which outputs and variables of a step to draw, when it offers more than one. */
export function Chooser({ step, choices, selected, lines = [], onAdd, onDone }: ChooserProps) {
  const [index, setIndex] = useState(selected);
  // One after another, so the first line's value is in place before the next asks for it.
  const addAll = async () => {
    for (const line of lines) await onAdd(line);
  };
  return (
    <div className="rc-echogram-chooser">
      <label>
        <span>{step} offers several things to draw:</span>{' '}
        <select
          className="rc-input"
          aria-label={`What of ${step} to draw`}
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
        >
          {choices.map((choice, at) => (
            <option key={`${choice.output}/${choice.var ?? ''}`} value={at}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="rc-button rc-small rc-primary" onClick={() => void onAdd(choices[index])}>
        Add
      </button>
      {lines.length > 1 && (
        <button type="button" className="rc-button rc-small" onClick={() => void addAll()}>
          Add all lines
        </button>
      )}
      <button type="button" className="rc-button rc-small" onClick={onDone}>
        Done
      </button>
    </div>
  );
}

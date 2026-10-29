import type { Choice } from 'aa-si-echogram-gl';
import { useState } from 'react';

interface ChooserProps {
  step: string;
  choices: Choice[];
  selected: number;
  onAdd: (choice: Choice) => void;
  onDone: () => void;
}

/** Which outputs and variables of a step to draw, when it offers more than one. */
export function Chooser({ step, choices, selected, onAdd, onDone }: ChooserProps) {
  const [index, setIndex] = useState(selected);
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
      <button type="button" className="rc-button rc-small rc-primary" onClick={() => onAdd(choices[index])}>
        Add
      </button>
      <button type="button" className="rc-button rc-small" onClick={onDone}>
        Done
      </button>
    </div>
  );
}

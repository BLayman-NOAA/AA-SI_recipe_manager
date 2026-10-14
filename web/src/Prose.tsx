import { splitCode } from './text';

/** Description text, with RST inline code (``name``) shown as code. */
export function Prose({ text }: { text: string }) {
  return (
    <>
      {splitCode(text).map((part, index) =>
        part.code ? <code key={index}>{part.text}</code> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}

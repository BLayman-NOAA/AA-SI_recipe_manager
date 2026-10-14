/** localStorage access that never throws (private windows, blocked storage). */

export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Storage is a convenience; the client works without it.
  }
}

/** Parse a stored JSON value, or null when it is missing or malformed. */
export function readStoredJson<T>(key: string): T | null {
  const text = readStored(key);
  if (text === null) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

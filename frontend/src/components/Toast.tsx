import { useEffect } from "react";

interface Props {
  message: string | null;
  onDone: () => void;
}

const SHOW_MS = 6000;

/** A short notice in the corner of the map; screen readers announce it too. */
export function Toast({ message, onDone }: Props) {
  useEffect(() => {
    if (!message) return;
    const id = setTimeout(onDone, SHOW_MS);
    return () => clearTimeout(id);
  }, [message, onDone]);

  return (
    <div className="toast-region" aria-live="polite">
      {message && (
        <div className="toast" role="status">
          {message}
          <button type="button" aria-label="Dismiss" onClick={onDone}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}

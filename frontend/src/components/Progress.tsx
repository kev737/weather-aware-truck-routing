import { useEffect, useState } from "react";
import type { Phase, Retry } from "../usePlanner";

interface Props {
  phase: Phase;
  estimatedS: number | null; // weather download estimate from step 1
  startedAt: number | null;
  retry: Retry | null;
  onRetryNow: () => void;
}

/** Re-render every half second while something is counting. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

export function Progress({ phase, estimatedS, startedAt, retry, onRetryNow }: Props) {
  const busy = phase === "routing" || phase === "weather" || retry !== null;
  const now = useNow(busy);
  if (!busy) return null;

  if (retry) {
    const left = Math.max(0, Math.ceil((retry.at - now) / 1000));
    const what = retry.missing ? `weather for ${retry.missing} more points` : "the weather";
    return (
      <div className="progress" role="status">
        <p>
          {left > 0 ? `Fetching ${what} in ${left} s` : `Fetching ${what} now…`}
          {retry.attempt > 1 ? ` (attempt ${retry.attempt} of ${retry.of})` : ""}
        </p>
        {left === 0 && <div className="bar indeterminate" />}
        {retry.message && <p className="hint">{retry.message}</p>}
        <p className="hint">Everything else is ready to use; the rest fills in automatically.</p>
        {/* A paced wait ends when the quota frees up; only a failure is worth retrying early. */}
        {!retry.paced && (
          <button type="button" className="link" onClick={onRetryNow}>
            Retry now
          </button>
        )}
      </div>
    );
  }

  if (phase === "routing") {
    return (
      <div className="progress" role="status">
        <p>Finding three truck routes… usually 3–20 s, longer for cross-country trips.</p>
        <div className="bar indeterminate" />
      </div>
    );
  }

  // Weather: fill toward the estimate but never claim 100% before the answer arrives.
  const elapsed = startedAt ? (now - startedAt) / 1000 : 0;
  const total = Math.max(estimatedS ?? 0, 2);
  const pct = Math.min(95, (elapsed / total) * 100);
  const left = Math.max(0, Math.ceil(total - elapsed));
  return (
    <div className="progress" role="status">
      <p>{left > 0 ? `Checking the weather along each route, about ${left} s left.` : "Almost done…"}</p>
      <div className="bar">
        <div style={{ width: `${pct}%` }} />
      </div>
      <p className="hint">The routes are on the map: explore them while you wait. Risk colours appear automatically.</p>
    </div>
  );
}

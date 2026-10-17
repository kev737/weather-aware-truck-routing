import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, createTrip, getPlan, type Plan, type PlanRequest, type Trip } from "./api";

// Exponential backoff for filling in weather the free API couldn't send yet.
const RETRY_BASE_S = 5;
const RETRY_MAX_S = 60;
const MAX_RETRIES = 5;

export type Phase = "idle" | "routing" | "weather" | "done";

export interface Retry {
  attempt: number; // 1-based
  of: number;
  at: number; // epoch ms of the next attempt
  missing: number | null; // points still missing, if known
}

export interface PlannerState {
  phase: Phase;
  trip: Trip | null;
  plan: Plan | null;
  weatherStartedAt: number | null; // epoch ms, for the progress bar
  retry: Retry | null;
  error: string | null;
  notice: string | null; // short "it's ready" message for the toast
}

const initial: PlannerState = {
  phase: "idle",
  trip: null,
  plan: null,
  weatherStartedAt: null,
  retry: null,
  error: null,
  notice: null,
};

function readyNotice(plan: Plan, partial: boolean, wasRetry: boolean): string {
  if (partial) return "Weather partly loaded. Filling in the rest automatically.";
  const rec = plan.recommendation;
  const head = wasRetry ? "All weather data loaded." : "Weather loaded.";
  if (rec.no_safe_route) return `${head} No route is safe at your departure time; see the suggestion.`;
  const r = plan.routes.find((x) => x.id === rec.route_id);
  return `${head} ★ Route ${r?.id} (${r?.via}) is recommended.`;
}


export function backoffSeconds(attempt: number, retryAfterS?: number): number {
  return Math.min(retryAfterS ?? RETRY_BASE_S * 2 ** (attempt - 1), RETRY_MAX_S);
}

/**
 * The two-step flow: routes first (fast), then weather (slower). If the weather
 * comes back incomplete or rate-limited, retry in the background with backoff.
 * Each retry only downloads what is still missing (the server caches the rest),
 * and the fuller plan replaces the old one without resetting the user's view.
 */
export function usePlanner() {
  const [state, setState] = useState<PlannerState>(initial);
  const run = useRef(0); // bumps on every new search, so stale responses are ignored
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);

  const cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    controller.current?.abort();
  };
  useEffect(() => cancel, []);

  const fetchPlan = useCallback(async (tripId: string, attempt: number, runId: number) => {
    const ctrl = new AbortController();
    controller.current = ctrl;
    const scheduleRetry = (retryAfterS: number | undefined, missing: number | null, error: string | null) => {
      if (attempt > MAX_RETRIES) {
        setState((s) => ({ ...s, phase: s.plan ? "done" : "idle", retry: null, error }));
        return;
      }
      const wait = backoffSeconds(attempt, retryAfterS);
      setState((s) => ({ ...s, retry: { attempt, of: MAX_RETRIES, at: Date.now() + wait * 1000, missing } }));
      timer.current = setTimeout(() => fetchPlan(tripId, attempt + 1, runId), wait * 1000);
    };

    try {
      const plan = await getPlan(tripId, ctrl.signal);
      if (run.current !== runId) return;
      const partial = plan.warnings.find((w) => w.kind === "weather_partial");
      const notice = readyNotice(plan, Boolean(partial), attempt > 1);
      setState((s) => ({ ...s, phase: "done", plan, retry: null, error: null, notice }));
      if (partial) scheduleRetry(undefined, partial.missing_points, partial.message);
    } catch (e) {
      if (run.current !== runId || ctrl.signal.aborted) return;
      const err = e as ApiError;
      if (err.status === 503) scheduleRetry(err.retryAfterS, null, err.message);
      else setState((s) => ({ ...s, phase: s.plan ? "done" : "idle", retry: null, error: err.message }));
    }
  }, []);

  const submit = useCallback(
    async (req: PlanRequest) => {
      cancel();
      const runId = ++run.current;
      setState({ ...initial, phase: "routing" });
      const ctrl = new AbortController();
      controller.current = ctrl;
      try {
        const trip = await createTrip(req, ctrl.signal);
        if (run.current !== runId) return;
        setState((s) => ({ ...s, phase: "weather", trip, weatherStartedAt: Date.now() }));
        await fetchPlan(trip.id, 1, runId);
      } catch (e) {
        if (run.current !== runId || ctrl.signal.aborted) return;
        setState({ ...initial, error: (e as Error).message });
      }
    },
    [fetchPlan],
  );

  /** Try again now, skipping the wait (the "Retry now" button). */
  const retryNow = useCallback(() => {
    if (!state.trip) return;
    if (timer.current) clearTimeout(timer.current);
    fetchPlan(state.trip.id, state.retry?.attempt ?? 1, run.current);
  }, [state.trip, state.retry, fetchPlan]);

  const dismissNotice = useCallback(() => setState((s) => ({ ...s, notice: null })), []);

  return { ...state, submit, retryNow, dismissNotice };
}

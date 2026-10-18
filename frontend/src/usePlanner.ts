import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, createTrip, getPlan, getTrip, type Plan, type PlanRequest, type Trip } from "./api";

// Exponential backoff for filling in weather the free API couldn't send yet.
const RETRY_BASE_S = 5;
const RETRY_MAX_S = 60;
const MAX_RETRIES = 5;

// While the server finds the last route(s), the first ones are already on the map.
const ROUTE_POLL_MS = 1000;
const ROUTE_POLL_MAX = 120;

export type Phase = "idle" | "routing" | "weather" | "done";

export interface Retry {
  attempt: number; // 1-based; only goes up when a round made no progress
  of: number;
  at: number; // epoch ms of the next attempt
  missing: number | null; // points still missing, if known
  message: string | null; // the server's explanation
  paced: boolean; // waiting on the free tier's per-minute quota: retrying early can't help
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
  const gap = plan.warnings.find((w) => w.kind === "weather_partial");
  if (partial && gap?.kind === "weather_partial") {
    return gap.retry
      ? "Weather partly loaded. Filling in the rest automatically."
      : "Weather partly loaded: today's free weather quota is used up.";
  }
  const rec = plan.recommendation;
  const head = wasRetry ? "All weather data loaded." : "Weather loaded.";
  if (rec.no_safe_route) return `${head} No route is safe at your departure time; see the suggestion.`;
  const r = plan.routes.find((x) => x.id === rec.route_id);
  return `${head} ★ Route ${r?.id} (${r?.via}) is recommended.`;
}


const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function newRoutesNotice(before: Trip, after: Trip): string | null {
  const added = after.routes.filter((r) => !before.routes.some((b) => b.id === r.id));
  if (added.length === 0) return null;
  return added.map((r) => `${r.name} found (${r.via}).`).join(" ");
}

export function backoffSeconds(attempt: number, retryAfterS?: number): number {
  return Math.min(retryAfterS ?? RETRY_BASE_S * 2 ** (attempt - 1), RETRY_MAX_S);
}

/**
 * The two-step flow: routes first (fast), then weather (slower). Routes are shown as
 * they come: the first ones at once, the last one when the server finds it. If the weather
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

  const fetchPlan = useCallback(async (tripId: string, attempt: number, runId: number, lastMissing?: number) => {
    const ctrl = new AbortController();
    controller.current = ctrl;
    const scheduleRetry = (retryAfterS: number | undefined, missing: number | null, message: string | null) => {
      // A round that filled in more points (the first one always does) doesn't use up an
      // attempt: long trips on the free tier take a few paced rounds, and that's progress.
      const progressed = missing !== null && (lastMissing === undefined || missing < lastMissing);
      const next = progressed ? attempt : attempt + 1;
      if (next > MAX_RETRIES + 1) {
        setState((s) => ({ ...s, phase: s.plan ? "done" : "idle", retry: null, error: message }));
        return;
      }
      const wait = backoffSeconds(attempt, retryAfterS);
      setState((s) => ({
        ...s,
        retry: {
          attempt: next,
          of: MAX_RETRIES,
          at: Date.now() + wait * 1000,
          missing,
          message,
          paced: retryAfterS !== undefined,
        },
      }));
      timer.current = setTimeout(() => fetchPlan(tripId, next, runId, missing ?? undefined), wait * 1000);
    };

    try {
      const plan = await getPlan(tripId, ctrl.signal);
      if (run.current !== runId) return;
      const partial = plan.warnings.find((w) => w.kind === "weather_partial");
      const notice = readyNotice(plan, Boolean(partial), attempt > 1);
      setState((s) => ({ ...s, phase: "done", plan, retry: null, error: null, notice }));
      // Retry only when it can help: not when today's quota is used up.
      if (partial?.kind === "weather_partial" && partial.retry) {
        scheduleRetry(partial.retry_after_s, partial.missing_points, partial.message);
      }
    } catch (e) {
      if (run.current !== runId || ctrl.signal.aborted) return;
      const err = e as ApiError;
      if (err.status === 503 && !err.quotaExhausted) scheduleRetry(err.retryAfterS, null, err.message);
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
        let trip = await createTrip(req, ctrl.signal);
        if (run.current !== runId) return;
        const first = trip;
        for (let polls = 0; !trip.routes_complete; polls++) {
          setState((s) => ({ ...s, trip }));
          if (polls >= ROUTE_POLL_MAX) throw new Error("Finding the last route took too long. Try again.");
          await sleep(ROUTE_POLL_MS);
          if (run.current !== runId) return;
          trip = await getTrip(trip.id, ctrl.signal);
          if (run.current !== runId) return;
        }
        const notice = newRoutesNotice(first, trip);
        setState((s) => ({ ...s, phase: "weather", trip, weatherStartedAt: Date.now(), notice: notice ?? s.notice }));
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

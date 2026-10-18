// Types mirror the Django API (backend/planner/services/planner.py).

export type RiskKey = "low" | "moderate" | "high" | "severe" | "no_travel";

export interface Place {
  name: string;
  lat: number;
  lon: number;
}

export interface Checkpoint {
  mile: number;
  covers: [number, number];
  lat: number;
  lon: number;
  eta: string;
  hours_from_departure: number;
  risk: RiskKey | null; // null = beyond the forecast horizon
  risk_label: string;
  reasons: string[];
  weather?: {
    source: "hrrr" | "gfs" | "open-meteo" | null; // which forecast model this checkpoint's weather came from
    wind_mph: number;
    gust_mph: number | null;
    rain_in_hr: number;
    snow_in_hr: number;
  };
}

/** A route as step 1 returns it: geometry and timing, no weather yet. */
export interface RouteBasics {
  id: string;
  name: string;
  via: string; // the roads that set this route apart from the other two
  distance_mi: number;
  hours: number; // driving time from the routing engine
  arrival: string;
  geometry: { type: "LineString"; coordinates: [number, number][] };
}

/** A route as step 2 returns it: with risk, checkpoints and the ranking. */
export interface RouteOption extends RouteBasics {
  rank: number;
  recommended: boolean;
  summary: {
    miles_by_risk: Record<RiskKey, number>;
    no_forecast_mi: number;
    severe_mi: number;
    high_mi: number;
    avg_risk: number;
    risk_min: number; // weather risk as minutes of driving it's worth avoiding
    score_h: number; // travel hours + risk: what the recommendation minimises
    max_risk: RiskKey | null;
  };
  segments: { risk: RiskKey | null; from_mi: number; to_mi: number; coordinates: [number, number][] }[];
  checkpoints: Checkpoint[];
}

export interface Heatmap {
  hours: string[]; // 49 timestamps: departure + 0..48 h
  cell_deg: number;
  points: [number, number][]; // [lon, lat]
  values: (number | null)[][]; // risk index 0-4, per point per hour
}

export interface BestWindow {
  route_id: string;
  delay_h: number;
  departure: string;
  arrival: string;
  no_travel_mi: number;
  severe_mi: number;
  high_mi: number;
  reason: string;
}

export interface Trip {
  id: string;
  origin: Place;
  destination: Place;
  departure: string;
  load_lb: number;
  interval_mi: number;
  routes: RouteBasics[];
  routes_complete: boolean; // false while the server is still finding the last route(s)
  weather: { points: number; estimated_s: number };
}

export type Warning =
  | {
      kind: "weather_partial";
      missing_points: number;
      checkpoints_waiting?: number; // 0 = the recommendation is final, only the heatmap is filling in
      total_points: number;
      rate_limited: boolean;
      retry: boolean; // false when retrying soon can't help (daily quota)
      retry_after_s?: number; // when the rest can be fetched (the free tier's per-minute pacing)
      message: string;
    }
  | { kind: "quota_low"; message: string }
  | { kind: "far_forecast"; message: string };

export interface Plan extends Omit<Trip, "routes"> {
  routes: RouteOption[];
  warnings: Warning[];
  recommendation: { route_id: string | null; reason: string; no_safe_route: boolean; provisional: boolean };
  best_window: BestWindow | null;
  window_hours: number; // how far ahead leaving later was tried
  heatmap: Heatmap;
}

export interface PlanRequest {
  origin: string | Place;
  destination: string | Place;
  departure: string;
  load_lb: number;
  interval_mi: 10 | 25 | 50;
}

export class ApiError extends Error {
  readonly status: number; // 0 = network error
  readonly retryAfterS?: number; // set when the server says to wait (rate limit)
  readonly quotaExhausted: boolean; // daily quota used up: don't retry today

  constructor(message: string, status: number, retryAfterS?: number, quotaExhausted = false) {
    super(message);
    this.status = status;
    this.retryAfterS = retryAfterS;
    this.quotaExhausted = quotaExhausted;
  }
}

function describe(body: unknown): string {
  // DRF errors are {detail} or {field: [messages]}.
  if (body && typeof body === "object") {
    if ("detail" in body) return String((body as { detail: unknown }).detail);
    return Object.values(body as Record<string, unknown>)
      .flatMap((msgs) => ([] as unknown[]).concat(msgs))
      .join(" ");
  }
  return "Something went wrong.";
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(describe(body), resp.status, body?.retry_after_s, Boolean(body?.quota_exhausted));
  return body as T;
}

/** Step 1: routes (seconds). */
export function createTrip(req: PlanRequest, signal?: AbortSignal): Promise<Trip> {
  return call<Trip>("/api/trips/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
}

/** Step 1, polled: the routes found so far. */
export function getTrip(tripId: string, signal?: AbortSignal): Promise<Trip> {
  return call<Trip>(`/api/trips/${tripId}/`, { signal });
}

export const planTripUrl = (tripId: string) => `/api/trips/${tripId}/plan/`;

/** Step 2: weather, risk and the recommendation. Safe to call again: it only downloads what's missing. */
export function getPlan(tripId: string, signal?: AbortSignal): Promise<Plan> {
  return call<Plan>(planTripUrl(tripId), { signal });
}

export function searchPlaces(q: string, signal?: AbortSignal): Promise<Place[]> {
  return call<Place[]>(`/api/geocode/?q=${encodeURIComponent(q)}`, { signal });
}

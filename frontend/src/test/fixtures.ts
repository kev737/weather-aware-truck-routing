import type { Checkpoint, Plan, RouteOption } from "../api";

export function checkpoint(over: Partial<Checkpoint> = {}): Checkpoint {
  return {
    mile: 50,
    covers: [37.5, 62.5],
    lat: 30,
    lon: -97,
    eta: "2026-10-18T15:00:00Z",
    hours_from_departure: 1,
    risk: "low",
    risk_label: "Low",
    reasons: [],
    weather: { source: "hrrr", wind_mph: 12, gust_mph: 20, rain_in_hr: 0, snow_in_hr: 0 },
    ...over,
  };
}

/** A route due east along latitude `lat`, with checkpoints at the given risks. */
export function route(id: string, lat: number, over: Partial<RouteOption> = {}, risks: Checkpoint["risk"][] = ["low", "low"]): RouteOption {
  const coords: [number, number][] = Array.from({ length: 21 }, (_, i) => [-100 + i * 0.5, lat]);
  return {
    id,
    name: `Route ${id}`,
    via: `I ${id.charCodeAt(0)}`,
    distance_mi: 600,
    hours: 10,
    arrival: "2026-10-19T00:00:00Z",
    geometry: { type: "LineString", coordinates: coords },
    rank: 1,
    recommended: false,
    summary: {
      miles_by_risk: { low: 600, moderate: 0, high: 0, severe: 0, no_travel: 0 },
      no_forecast_mi: 0,
      severe_mi: 0,
      high_mi: 0,
      avg_risk: 0,
      risk_min: 0,
      score_h: 10,
      max_risk: "low",
    },
    segments: [{ risk: "low", from_mi: 0, to_mi: 600, coordinates: coords }],
    checkpoints: risks.map((risk, i) => checkpoint({ mile: i * 25, lat, lon: -100 + i, risk, risk_label: risk ?? "No forecast" })),
    ...over,
  };
}

export function plan(over: Partial<Plan> = {}): Plan {
  const a = route("A", 30, { recommended: true, hours: 9, summary: { ...route("A", 30).summary, score_h: 9 } });
  const b = route("B", 33, { hours: 10, summary: { ...route("B", 33).summary, severe_mi: 12, risk_min: 120, score_h: 12 } }, ["low", "severe"]);
  const c = route("C", 36, { hours: 11 });
  return {
    id: "trip1",
    origin: { name: "Here", lat: 30, lon: -100 },
    destination: { name: "There", lat: 30, lon: -90 },
    departure: "2026-10-18T14:00:00Z",
    load_lb: 40000,
    interval_mi: 25,
    routes: [a, b, c],
    routes_complete: true,
    weather: { points: 100, estimated_s: 3 },
    warnings: [],
    recommendation: { route_id: "A", reason: "Fastest and clear.", no_safe_route: false, provisional: false },
    best_window: null,
    window_hours: 48,
    heatmap: {
      hours: Array.from({ length: 49 }, (_, h) => new Date(Date.UTC(2026, 9, 18, 14 + h)).toISOString()),
      cell_deg: 0.1,
      points: [[-99, 30], [-98, 30]],
      values: [Array.from({ length: 49 }, (_, h) => (h === 12 ? 3.5 : 0.2)), Array.from({ length: 49 }, () => null)],
    },
    ...over,
  };
}

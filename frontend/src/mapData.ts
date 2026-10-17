/**
 * Pure builders for what the map draws: GeoJSON for routes, risk-coloured segments,
 * checkpoints, badges and the heatmap, the paint expressions, and popup text. Kept out
 * of the component so the spec's map requirements can be tested without WebGL.
 */
import type { ExpressionSpecification } from "maplibre-gl";
import type { Checkpoint, Heatmap, RouteBasics, RouteOption } from "./api";
import { hours, miles, NO_FORECAST_COLOR, RISK_COLOR, RISK_LABEL, when } from "./format";

export type MapRoute = RouteBasics | RouteOption;

type FC = GeoJSON.FeatureCollection;
export const empty: FC = { type: "FeatureCollection", features: [] };
export const hasRisk = (r: RouteBasics | RouteOption): r is RouteOption => "segments" in r;

// MapLibre expression: risk key -> colour (grey for "no forecast").
export const riskColorExpr: ExpressionSpecification = [
  "match",
  ["coalesce", ["get", "risk"], "none"],
  ...Object.entries(RISK_COLOR).flat(),
  NO_FORECAST_COLOR,
] as unknown as ExpressionSpecification;

export function routeLines(routes: MapRoute[]): FC {
  return {
    type: "FeatureCollection",
    features: routes.map((r) => ({ type: "Feature", properties: { id: r.id }, geometry: r.geometry })),
  };
}

/** Rough distance in degrees, scaled for longitude; plenty for "which point is farthest away". */
export function apart(a: number[], b: number[]): number {
  const dx = (a[0] - b[0]) * Math.cos((a[1] * Math.PI) / 180);
  return Math.hypot(dx, a[1] - b[1]);
}

/**
 * One letter badge per route, on the point of that route farthest from the other
 * routes. On a shared stretch the badge would sit on two lines at once, and hovering
 * it could show the other route.
 */
export function routeLabels(routes: MapRoute[]): FC {
  const sample = (coords: [number, number][], n: number) =>
    coords.filter((_, i) => i % Math.max(1, Math.floor(coords.length / n)) === 0);
  return {
    type: "FeatureCollection",
    features: routes.map((r) => {
      const others = routes.filter((o) => o.id !== r.id).flatMap((o) => sample(o.geometry.coordinates, 300));
      const candidates = sample(r.geometry.coordinates, 150).slice(1, -1); // never on the shared ends
      let best = r.geometry.coordinates[Math.floor(r.geometry.coordinates.length / 2)];
      let bestGap = -1;
      for (const c of candidates) {
        const gap = others.length ? Math.min(...others.map((o) => apart(c, o))) : 0;
        if (gap > bestGap) [best, bestGap] = [c, gap];
      }
      return { type: "Feature", properties: { id: r.id }, geometry: { type: "Point", coordinates: best } };
    }),
  };
}

export function riskSegments(route: RouteOption | undefined): FC {
  if (!route) return empty;
  return {
    type: "FeatureCollection",
    features: route.segments.map((s) => ({
      type: "Feature",
      properties: { risk: s.risk },
      geometry: { type: "LineString", coordinates: s.coordinates },
    })),
  };
}

/** Checkpoints of every route; `selected` decides which layer draws them. */
export function checkpointPoints(routes: MapRoute[], selectedId: string | null): FC {
  return {
    type: "FeatureCollection",
    features: routes.filter(hasRisk).flatMap((r) =>
      r.checkpoints.map((c, i) => ({
        type: "Feature" as const,
        properties: { route: r.id, i, risk: c.risk, selected: r.id === selectedId },
        geometry: { type: "Point" as const, coordinates: [c.lon, c.lat] },
      })),
    ),
  };
}

/**
 * Heatmap points with all 49 hourly values as properties h0..h48. The slider only
 * changes which property the layer reads (a paint property), so moving it never
 * re-uploads data: the GPU just repaints.
 */
export function heatPoints(heat: Heatmap | null): FC {
  if (!heat) return empty;
  return {
    type: "FeatureCollection",
    features: heat.points.map((coordinates, p) => {
      const properties: Record<string, number> = {};
      heat.values[p].forEach((v, h) => {
        if (v !== null) properties[`h${h}`] = v;
      });
      return { type: "Feature", properties, geometry: { type: "Point", coordinates } };
    }),
  };
}

// Risk index 0-4 -> weight 0-1; hours with no data weigh nothing.
export const heatWeight = (hour: number): ExpressionSpecification => ["/", ["coalesce", ["get", `h${hour}`], 0], 4];

/** Blob radius ~2 grid cells at every zoom: neighbours blend into one band wider than the route line. */
export function heatRadius(cellDeg: number): ExpressionSpecification {
  const atZoom0 = ((cellDeg * 512) / 360) * 2; // MapLibre tiles are 512 px at zoom 0
  return ["interpolate", ["exponential", 2], ["zoom"], 0, atZoom0, 10, atZoom0 * 1024];
}

// Density 0-1 -> the same colours as the risk levels, transparent where calm.
export const heatColor: ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["heatmap-density"],
  0, "rgba(47, 158, 68, 0)",
  0.05, "rgba(47, 158, 68, 0.3)",
  0.25, "rgba(242, 194, 0, 0.75)",
  0.5, "rgba(247, 103, 7, 0.85)",
  0.75, "rgba(224, 49, 49, 0.9)",
  1, "rgba(112, 72, 232, 0.95)",
];

export function routeTooltip(r: RouteBasics | RouteOption): string {
  const risk = hasRisk(r)
    ? `<div>Severe ${miles(r.summary.severe_mi)} · High ${miles(r.summary.high_mi)}</div>`
    : "<div>Weather loading…</div>";
  return `<strong>Route ${r.id}</strong> · ${r.via}
    <div>${miles(r.distance_mi)} · ${hours(r.hours)}</div>${risk}
    <div class="muted">Click to select</div>`;
}

export function checkpointPopup(c: Checkpoint): string {
  const w = c.weather;
  const label = c.risk ? RISK_LABEL[c.risk] : "No forecast";
  const weather = w
    ? `<div>Wind ${w.wind_mph.toFixed(0)} mph${w.gust_mph != null ? ` (gusts ${w.gust_mph.toFixed(0)})` : ""}</div>
       <div>Rain ${w.rain_in_hr.toFixed(2)} in/hr · Snow ${w.snow_in_hr.toFixed(2)} in/hr</div>`
    : "";
  const why = c.reasons.length ? `<div class="muted">Why: ${c.reasons.join("; ")}</div>` : "";
  const sources = { hrrr: "NOAA HRRR (3 km)", gfs: "NOAA GFS (13 km)", "open-meteo": "Open-Meteo" } as const;
  const source = w?.source ? `<div class="muted">Forecast: ${sources[w.source]}</div>` : "";
  return `<strong>Mile ${Math.round(c.mile)}</strong> · <span class="pill" style="background:${
    c.risk ? RISK_COLOR[c.risk] : NO_FORECAST_COLOR
  }">${label}</span>
    <div>Arrives ${when(c.eta)} (+${hours(c.hours_from_departure)})</div>${weather}${why}${source}`;
}

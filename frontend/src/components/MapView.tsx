import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import type { Checkpoint, Heatmap, RouteBasics, RouteOption } from "../api";
import { hours, miles, NO_FORECAST_COLOR, RISK_COLOR, RISK_LABEL, when } from "../format";

// Free vector basemap, no key needed.
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const USA: [number, number, number, number] = [-125, 24, -66, 50];
const FONT = ["Noto Sans Bold"];

interface Props {
  tripId: string | null; // the map re-zooms only when this changes
  routes: (RouteBasics | RouteOption)[];
  selectedId: string | null;
  onSelect: (routeId: string) => void;
  heatmap: Heatmap | null;
  hour: number; // 0-48, which forecast hour the heatmap shows
  showHeatmap: boolean;
}

type FC = GeoJSON.FeatureCollection;
const empty: FC = { type: "FeatureCollection", features: [] };
const hasRisk = (r: RouteBasics | RouteOption): r is RouteOption => "segments" in r;

// MapLibre expression: risk key -> colour (grey for "no forecast").
const riskColorExpr: ExpressionSpecification = [
  "match",
  ["coalesce", ["get", "risk"], "none"],
  ...Object.entries(RISK_COLOR).flat(),
  NO_FORECAST_COLOR,
] as unknown as ExpressionSpecification;

function routeLines(routes: Props["routes"]): FC {
  return {
    type: "FeatureCollection",
    features: routes.map((r) => ({ type: "Feature", properties: { id: r.id }, geometry: r.geometry })),
  };
}

/** Rough distance in degrees, scaled for longitude; plenty for "which point is farthest away". */
function apart(a: number[], b: number[]): number {
  const dx = (a[0] - b[0]) * Math.cos((a[1] * Math.PI) / 180);
  return Math.hypot(dx, a[1] - b[1]);
}

/**
 * One letter badge per route, on the point of that route farthest from the other
 * routes. On a shared stretch the badge would sit on two lines at once, and hovering
 * it could show the other route.
 */
function routeLabels(routes: Props["routes"]): FC {
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

function riskSegments(route: RouteOption | undefined): FC {
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
function checkpointPoints(routes: Props["routes"], selectedId: string | null): FC {
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
function heatPoints(heat: Heatmap | null): FC {
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
const heatWeight = (hour: number): ExpressionSpecification => ["/", ["coalesce", ["get", `h${hour}`], 0], 4];

/** Blob radius ~2 grid cells at every zoom: neighbours blend into one band wider than the route line. */
function heatRadius(cellDeg: number): ExpressionSpecification {
  const atZoom0 = ((cellDeg * 512) / 360) * 2; // MapLibre tiles are 512 px at zoom 0
  return ["interpolate", ["exponential", 2], ["zoom"], 0, atZoom0, 10, atZoom0 * 1024];
}

// Density 0-1 -> the same colours as the risk levels, transparent where calm.
const heatColor: ExpressionSpecification = [
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

function routeTooltip(r: RouteBasics | RouteOption): string {
  const risk = hasRisk(r)
    ? `<div>Severe ${miles(r.summary.severe_mi)} · High ${miles(r.summary.high_mi)}</div>`
    : "<div>Weather loading…</div>";
  return `<strong>Route ${r.id}</strong> · ${r.via}
    <div>${miles(r.distance_mi)} · ${hours(r.hours)}</div>${risk}
    <div class="muted">Click to select</div>`;
}

function checkpointPopup(c: Checkpoint): string {
  const w = c.weather;
  const label = c.risk ? RISK_LABEL[c.risk] : "No forecast";
  const weather = w
    ? `<div>Wind ${w.wind_mph.toFixed(0)} mph${w.gust_mph != null ? ` (gusts ${w.gust_mph.toFixed(0)})` : ""}</div>
       <div>Rain ${w.rain_in_hr.toFixed(2)} in/hr · Snow ${w.snow_in_hr.toFixed(2)} in/hr</div>`
    : "";
  const why = c.reasons.length ? `<div class="muted">Why: ${c.reasons.join("; ")}</div>` : "";
  return `<strong>Mile ${Math.round(c.mile)}</strong> · <span class="pill" style="background:${
    c.risk ? RISK_COLOR[c.risk] : NO_FORECAST_COLOR
  }">${label}</span>
    <div>Arrives ${when(c.eta)} (+${hours(c.hours_from_departure)})</div>${weather}${why}`;
}

/**
 * The map is created once; afterwards we only swap data in its sources and change
 * paint properties. Rebuilding layers on every React render would make it flicker.
 */
export function MapView({ tripId, routes, selectedId, onSelect, heatmap, hour, showHeatmap }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef<Promise<void> | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const routesRef = useRef(routes);
  routesRef.current = routes;
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;

  // Create the map, its (empty) layers and the mouse handlers once.
  useEffect(() => {
    const m = new maplibregl.Map({ container: container.current!, style: STYLE_URL, bounds: USA });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = m;
    const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "map-tip" });
    let popup: maplibregl.Popup | null = null; // the open checkpoint popup, if any
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") popup?.remove(); // keyboard users can dismiss it too
    };
    window.addEventListener("keydown", closeOnEscape);

    ready.current = new Promise((resolve) =>
      m.on("load", () => {
        for (const id of ["heat", "routes", "labels", "segments", "checkpoints"]) {
          m.addSource(id, { type: "geojson", data: empty });
        }

        // Weather heatmap under everything else, so routes stay readable on top.
        m.addLayer({
          id: "heat",
          type: "heatmap",
          source: "heat",
          paint: { "heatmap-weight": heatWeight(0), "heatmap-intensity": 1, "heatmap-color": heatColor, "heatmap-opacity": 0.9 },
        });

        // Unselected routes in grey; the selected one is drawn on top by the risk layers.
        m.addLayer({
          id: "routes",
          type: "line",
          source: "routes",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#868e96", "line-width": 4, "line-opacity": 0.6 },
        });
        m.addLayer({
          id: "checkpoints-other",
          type: "circle",
          source: "checkpoints",
          filter: ["==", ["get", "selected"], false],
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2, 9, 4],
            "circle-color": riskColorExpr,
            "circle-opacity": 0.55,
          },
        });
        m.addLayer({
          id: "segments-casing",
          type: "line",
          source: "segments",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#ffffff", "line-width": 9 },
        });
        m.addLayer({
          id: "segments",
          type: "line",
          source: "segments",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": riskColorExpr, "line-width": 6 },
        });
        m.addLayer({
          id: "checkpoints",
          type: "circle",
          source: "checkpoints",
          filter: ["==", ["get", "selected"], true],
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 3, 9, 6],
            "circle-color": riskColorExpr,
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 1.5,
          },
        });
        m.addLayer({
          id: "labels-bg",
          type: "circle",
          source: "labels",
          paint: { "circle-radius": 12, "circle-color": "#343a40", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
        });
        m.addLayer({
          id: "labels",
          type: "symbol",
          source: "labels",
          layout: { "text-field": ["get", "id"], "text-font": FONT, "text-size": 13, "text-allow-overlap": true },
          paint: { "text-color": "#ffffff" },
        });

        const routeAt = (e: MapLayerMouseEvent) => {
          const props = e.features?.[0]?.properties;
          const id = props?.id ?? props?.route; // route lines/badges carry `id`, checkpoints `route`
          return routesRef.current.find((r) => r.id === id);
        };
        for (const layer of ["routes", "labels-bg", "checkpoints-other"]) {
          m.on("click", layer, (e) => {
            const r = routeAt(e);
            if (r) onSelectRef.current(r.id);
          });
          m.on("mousemove", layer, (e) => {
            const r = routeAt(e);
            if (!r) return;
            m.getCanvas().style.cursor = "pointer";
            hover.setLngLat(e.lngLat).setHTML(routeTooltip(r)).addTo(m);
          });
          m.on("mouseleave", layer, () => {
            m.getCanvas().style.cursor = "";
            hover.remove();
          });
        }
        m.on("click", "checkpoints", (e) => {
          const route = routesRef.current.find((r) => r.id === selectedRef.current);
          const i = e.features?.[0]?.properties?.i;
          if (!route || !hasRisk(route) || i === undefined) return;
          popup?.remove();
          popup = new maplibregl.Popup({ className: "map-tip" })
            .setLngLat(e.lngLat)
            .setHTML(checkpointPopup(route.checkpoints[i]))
            .addTo(m);
        });

        m.on("mouseenter", "checkpoints", () => (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", "checkpoints", () => (m.getCanvas().style.cursor = ""));
        resolve();
      }),
    );
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      m.remove();
    };
  }, []);

  // New or fuller data, or another route selected: swap the data without touching the camera.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current!;
      const selected = routes.find((r) => r.id === selectedId);
      const risky = selected && hasRisk(selected) ? selected : undefined;
      (m.getSource("routes") as GeoJSONSource).setData(routeLines(routes));
      (m.getSource("labels") as GeoJSONSource).setData(routeLabels(routes));
      (m.getSource("segments") as GeoJSONSource).setData(riskSegments(risky));
      (m.getSource("checkpoints") as GeoJSONSource).setData(checkpointPoints(routes, selectedId));
      // Before the weather arrives, show the selected route in blue so it still stands out.
      const isSelected = ["==", ["get", "id"], selectedId ?? ""] as ExpressionSpecification;
      m.setPaintProperty("routes", "line-color", risky ? "#868e96" : ["case", isSelected, "#1c7ed6", "#868e96"]);
      m.setPaintProperty("routes", "line-width", ["case", isSelected, 6, 4]);
      m.setPaintProperty("labels-bg", "circle-color", ["case", isSelected, "#1c7ed6", "#343a40"]);
    });
  }, [routes, selectedId]);

  // New heatmap data (once per plan): upload it and size the blobs to its grid.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current!;
      (m.getSource("heat") as GeoJSONSource).setData(heatPoints(heatmap));
      if (heatmap) m.setPaintProperty("heat", "heatmap-radius", heatRadius(heatmap.cell_deg));
    });
  }, [heatmap]);

  // Slider moved / heatmap toggled: only paint properties change.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current!;
      m.setPaintProperty("heat", "heatmap-weight", heatWeight(hour));
      m.setLayoutProperty("heat", "visibility", showHeatmap ? "visible" : "none");
    });
  }, [hour, showHeatmap]);

  // New trip: zoom to it once. Weather retries for the same trip keep the user's view.
  useEffect(() => {
    if (!tripId) return;
    ready.current?.then(() => {
      const bounds = new maplibregl.LngLatBounds();
      routesRef.current.forEach((r) => r.geometry.coordinates.forEach((c) => bounds.extend(c)));
      // Extra bottom padding keeps the trip clear of the forecast slider.
      const padding = { top: 70, right: 60, bottom: 170, left: 60 };
      // maxZoom: a very short trip still shows its streets, not an empty close-up.
      if (!bounds.isEmpty()) map.current!.fitBounds(bounds, { padding, duration: 800, maxZoom: 12 });
    });
  }, [tripId]);

  return <div ref={container} className="map" />;
}

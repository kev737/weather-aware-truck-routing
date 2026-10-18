import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import type { Heatmap } from "../api";
import {
  checkpointPoints,
  checkpointPopup,
  empty,
  hasRisk,
  heatColor,
  heatPoints,
  heatRadius,
  heatWeight,
  riskColorExpr,
  riskSegments,
  routeLabels,
  routeLines,
  routeTooltip,
  type MapRoute,
} from "../mapData";

// Free vector basemap, no key needed.
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const USA: [number, number, number, number] = [-125, 24, -66, 50];
const FONT = ["Noto Sans Bold"];

interface Props {
  tripId: string | null; // the map re-zooms when this changes or the trip gains a route
  routes: MapRoute[];
  selectedId: string | null;
  onSelect: (routeId: string) => void;
  heatmap: Heatmap | null;
  hour: number; // 0-48, which forecast hour the heatmap shows
  showHeatmap: boolean;
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
      m.setPaintProperty("routes", "line-color", risky ? "#868e96" : ["case", isSelected, "#008080", "#868e96"]);
      m.setPaintProperty("routes", "line-width", ["case", isSelected, 6, 4]);
      m.setPaintProperty("labels-bg", "circle-color", ["case", isSelected, "#008080", "#343a40"]);
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

  // New trip, or a route found after the first ones: zoom to fit. Weather retries for the
  // same trip keep the user's view.
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
  }, [tripId, routes.length]);

  return <div ref={container} className="map" />;
}

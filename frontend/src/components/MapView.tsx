import maplibregl, { type ExpressionSpecification, type FilterSpecification, type GeoJSONSource, type MapLayerMouseEvent } from "maplibre-gl";
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
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;

  // Create the map, its (empty) layers and the mouse handlers once.
  useEffect(() => {
    const m = new maplibregl.Map({ container: container.current!, style: STYLE_URL, bounds: USA });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = m;
    const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "map-tip" });
    const popup = popupRef; // the open checkpoint popup, if any
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") popup.current?.remove(); // keyboard users can dismiss it too
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
          filter: ["==", ["get", "route"], ""], // set to "not the selected route" below
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
          filter: ["==", ["get", "route"], ""], // set to the selected route below
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
        const ROUTE_LAYERS = ["labels-bg", "checkpoints-other", "routes"];
        const hitsAt = (point: maplibregl.PointLike, layers: string[]) =>
          m.queryRenderedFeatures(point, { layers }).map((f) => f.properties);

        // One handler for every click, so a click where things overlap does one thing:
        // the selected route's checkpoint opens its popup; its line does nothing. To switch,
        // click a letter marker, or a grey route's line or dots (not a stretch it shares
        // with the selected route).
        m.on("click", (e) => {
          const cp = hitsAt(e.point, ["checkpoints"])[0];
          const route = routesRef.current.find((r) => r.id === selectedRef.current);
          if (cp && route && hasRisk(route)) {
            popup.current?.remove();
            popup.current = new maplibregl.Popup({ className: "map-tip" })
              .setLngLat(e.lngLat)
              .setHTML(checkpointPopup(route.checkpoints[cp.i]))
              .addTo(m);
            return;
          }
          const badge = hitsAt(e.point, ["labels-bg"])[0]?.id; // a letter marker always switches
          if (badge && badge !== selectedRef.current) return onSelectRef.current(badge);
          // A grey line or dot switches, unless the selected route runs there too.
          const ids = hitsAt(e.point, ["checkpoints-other", "routes"]).map((p) => p?.id ?? p?.route);
          if (ids.length && !ids.includes(selectedRef.current)) onSelectRef.current(ids[0]);
        });

        for (const layer of ROUTE_LAYERS) {
          m.on("mousemove", layer, (e) => {
            const r = routeAt(e);
            if (!r || hitsAt(e.point, ["checkpoints"]).length) return; // a checkpoint has its own popup
            m.getCanvas().style.cursor = "pointer";
            hover.setLngLat(e.lngLat).setHTML(routeTooltip(r)).addTo(m);
          });
          m.on("mouseleave", layer, () => {
            m.getCanvas().style.cursor = "";
            hover.remove();
          });
        }

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

  // New or fuller data: upload every route's lines, segments and checkpoints once, without
  // touching the camera.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current!;
      (m.getSource("routes") as GeoJSONSource).setData(routeLines(routes));
      (m.getSource("labels") as GeoJSONSource).setData(routeLabels(routes));
      (m.getSource("segments") as GeoJSONSource).setData(riskSegments(routes));
      (m.getSource("checkpoints") as GeoJSONSource).setData(checkpointPoints(routes));
    });
  }, [routes]);

  // A checkpoint popup belongs to the route it was opened on: close it when another route is
  // selected, so it can't seem to describe that route's checkpoint at the same spot.
  useEffect(() => {
    popupRef.current?.remove();
    popupRef.current = null;
  }, [selectedId]);

  // Another route selected: only filters, paint and draw order change, so nothing is re-uploaded.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current!;
      const selected = routes.find((r) => r.id === selectedId);
      const risky = Boolean(selected && hasRisk(selected));
      const sel = selectedId ?? "";
      const ofSelected = ["==", ["get", "route"], sel] as FilterSpecification;
      for (const layer of ["segments-casing", "segments", "checkpoints"]) m.setFilter(layer, ofSelected);
      m.setFilter("checkpoints-other", ["!=", ["get", "route"], sel]);
      // Before the weather arrives, show the selected route in teal so it still stands out.
      const isSelected = ["==", ["get", "id"], sel] as ExpressionSpecification;
      m.setPaintProperty("routes", "line-color", risky ? "#868e96" : ["case", isSelected, "#008080", "#868e96"]);
      m.setPaintProperty("routes", "line-width", ["case", isSelected, 6, 4]);
      m.setPaintProperty("labels-bg", "circle-color", ["case", isSelected, "#008080", "#343a40"]);
      // Where routes overlap, the selected one (and its badge) is drawn last, on top.
      const onTop = ["case", isSelected, 1, 0] as ExpressionSpecification;
      m.setLayoutProperty("routes", "line-sort-key", onTop);
      m.setLayoutProperty("labels-bg", "circle-sort-key", onTop);
      m.setLayoutProperty("labels", "symbol-sort-key", onTop);
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

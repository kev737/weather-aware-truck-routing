/**
 * The map component with MapLibre replaced by a recorder: jsdom has no WebGL, and what
 * matters is what the component tells the map to do.
 */
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { plan } from "../test/fixtures";

type Call = [string, ...unknown[]];

const calls: Call[] = [];
const sources = new Map<string, { data: unknown; setData: (d: unknown) => void }>();
let loadHandler: (() => void) | null = null;
const layerHandlers = new Map<string, (e: unknown) => void>(); // "mousemove:routes" -> handler
let clickHandler: ((e: unknown) => void) | null = null;
let hits: Record<string, Record<string, unknown>[]> = {}; // layer -> features under the cursor

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor() {}
    addControl() {}
    on(event: string, ...args: unknown[]) {
      if (event === "load") loadHandler = args[0] as () => void;
      else if (event === "click" && typeof args[0] === "function") clickHandler = args[0] as (e: unknown) => void;
      else if (typeof args[0] === "string") layerHandlers.set(`${event}:${args[0]}`, args[1] as (e: unknown) => void);
    }
    queryRenderedFeatures(_point: unknown, opts: { layers: string[] }) {
      return opts.layers.flatMap((l) => (hits[l] ?? []).map((properties) => ({ properties })));
    }
    addSource(id: string) {
      const src = { data: null as unknown, setData: (d: unknown) => { src.data = d; calls.push(["setData", id, d]); } };
      sources.set(id, src);
      calls.push(["addSource", id]);
    }
    addLayer(layer: { id: string; type: string }) {
      calls.push(["addLayer", layer.id, layer.type]);
    }
    getSource(id: string) {
      return sources.get(id);
    }
    setPaintProperty(layer: string, prop: string, value: unknown) {
      calls.push(["paint", layer, prop, value]);
    }
    setFilter(layer: string, filter: unknown) {
      calls.push(["filter", layer, filter]);
    }
    setLayoutProperty(layer: string, prop: string, value: unknown) {
      calls.push(["layout", layer, prop, value]);
    }
    fitBounds(...args: unknown[]) {
      calls.push(["fitBounds", ...args]);
    }
    getCanvas() {
      return { style: {} };
    }
    remove() {}
  }
  class LngLatBounds {
    points: unknown[] = [];
    extend(p: unknown) {
      this.points.push(p);
      return this;
    }
    isEmpty() {
      return this.points.length === 0;
    }
  }
  class Popup {
    setLngLat() { return this; }
    setHTML() { return this; }
    addTo() { return this; }
    remove() {
      calls.push(["popup-removed"]);
    }
  }
  const NavigationControl = class {};
  return { default: { Map: FakeMap, LngLatBounds, Popup, NavigationControl }, Map: FakeMap };
});

const { MapView } = await import("./MapView");

async function mount(props: Partial<Parameters<typeof MapView>[0]> = {}) {
  const p = plan();
  const all = { tripId: "t1", routes: p.routes, selectedId: "A", onSelect: () => {}, heatmap: p.heatmap, hour: 0, showHeatmap: true, ...props };
  const view = render(<MapView {...all} />);
  await act(async () => {
    loadHandler?.();
    await Promise.resolve();
  });
  return { view, props: all };
}

const of = (kind: string) => calls.filter((c) => c[0] === kind);

beforeEach(() => {
  calls.length = 0;
  sources.clear();
  layerHandlers.clear();
  clickHandler = null;
  hits = {};
  loadHandler = null;
});

describe("MapView", () => {
  it("sets up the heatmap, routes, risk segments, checkpoints and badges once", async () => {
    await mount();
    expect(of("addSource").map((c) => c[1])).toEqual(["heat", "routes", "labels", "segments", "checkpoints"]);
    const layers = of("addLayer").map((c) => `${c[1]}:${c[2]}`);
    expect(layers).toEqual(expect.arrayContaining(["heat:heatmap", "routes:line", "segments:line", "checkpoints:circle", "checkpoints-other:circle", "labels:symbol"]));
    expect(layers[0]).toBe("heat:heatmap"); // drawn first, under the routes
  });

  it("draws all three routes and every route's checkpoints", async () => {
    await mount();
    const routes = sources.get("routes")!.data as GeoJSON.FeatureCollection;
    expect(routes.features).toHaveLength(3);
    const cps = sources.get("checkpoints")!.data as GeoJSON.FeatureCollection;
    expect(new Set(cps.features.map((f) => f.properties?.route))).toEqual(new Set(["A", "B", "C"]));
  });

  it("moving the slider repaints without re-sending heatmap data (that's the smoothness)", async () => {
    const { view, props } = await mount();
    const heatUploads = () => calls.filter((c) => c[0] === "setData" && c[1] === "heat").length;
    const before = heatUploads();
    for (const hour of [6, 12, 24, 48]) {
      view.rerender(<MapView {...props} hour={hour} />);
      await act(async () => await Promise.resolve());
    }
    expect(heatUploads()).toBe(before);
    const weights = of("paint").filter((c) => c[1] === "heat" && c[2] === "heatmap-weight");
    expect(weights[weights.length - 1][3]).toEqual([
      "/",
      ["coalesce", ["get", "h48"], 0],
      4,
    ]);
  });

  it("the heatmap toggle hides and shows the layer", async () => {
    const { view, props } = await mount();
    view.rerender(<MapView {...props} showHeatmap={false} />);
    await act(async () => await Promise.resolve());
    const layout = of("layout");
    expect(layout[layout.length - 1]).toEqual(["layout", "heat", "visibility", "none"]);
  });

  it("zooms to a new trip once, not again when fuller data for the same trip arrives", async () => {
    const { view, props } = await mount();
    expect(of("fitBounds")).toHaveLength(1);
    view.rerender(<MapView {...props} routes={[...props.routes]} />); // same trip, new data
    await act(async () => await Promise.resolve());
    expect(of("fitBounds")).toHaveLength(1);
    view.rerender(<MapView {...props} tripId="t2" />);
    await act(async () => await Promise.resolve());
    expect(of("fitBounds")).toHaveLength(2);
  });

  it("zooms again when the last route arrives for the same trip, so it's in view", async () => {
    const { view, props } = await mount({ routes: plan().routes.slice(0, 2) });
    expect(of("fitBounds")).toHaveLength(1);
    view.rerender(<MapView {...props} routes={plan().routes} />);
    await act(async () => await Promise.resolve());
    expect(of("fitBounds")).toHaveLength(2);
  });

  it("closes an open checkpoint popup when another route is selected", async () => {
    const { view, props } = await mount();
    const removed = () => of("popup-removed").length;
    hits = { checkpoints: [{ route: "A", i: 0 }] };
    act(() => clickHandler!({ point: [0, 0], lngLat: [-100, 30] }));
    const before = removed();
    view.rerender(<MapView {...props} selectedId="B" />);
    await act(async () => await Promise.resolve());
    expect(removed()).toBe(before + 1);
  });

  it("a click on the selected route's checkpoint opens it, even where another route runs underneath", async () => {
    const onSelect = vi.fn();
    await mount({ onSelect });
    hits = { checkpoints: [{ route: "A", i: 0 }], routes: [{ id: "B" }], "checkpoints-other": [{ route: "B" }] };
    act(() => clickHandler!({ point: [0, 0], lngLat: [-100, 30] }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("a click on another route selects it, but not on a stretch shared with the selected route", async () => {
    const onSelect = vi.fn();
    await mount({ onSelect });
    hits = { routes: [{ id: "B" }] };
    act(() => clickHandler!({ point: [0, 0], lngLat: [-100, 30] }));
    expect(onSelect).toHaveBeenCalledWith("B");
    onSelect.mockClear();
    hits = { routes: [{ id: "B" }, { id: "A" }] }; // A is selected and runs here too
    act(() => clickHandler!({ point: [0, 0], lngLat: [-100, 30] }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("a letter marker always switches, even on top of the selected route's line", async () => {
    const onSelect = vi.fn();
    await mount({ onSelect });
    hits = { "labels-bg": [{ id: "C" }], routes: [{ id: "A" }, { id: "C" }] };
    act(() => clickHandler!({ point: [0, 0], lngLat: [-100, 30] }));
    expect(onSelect).toHaveBeenCalledWith("C");
  });

  it("draws the selected route and its badge on top where routes overlap", async () => {
    const { view, props } = await mount();
    view.rerender(<MapView {...props} selectedId="B" />);
    await act(async () => await Promise.resolve());
    const onTop = ["case", ["==", ["get", "id"], "B"], 1, 0];
    const last = (layer: string, prop: string) => of("layout").filter((c) => c[1] === layer && c[2] === prop).pop()?.[3];
    expect(last("routes", "line-sort-key")).toEqual(onTop);
    expect(last("labels-bg", "circle-sort-key")).toEqual(onTop);
    expect(last("labels", "symbol-sort-key")).toEqual(onTop);
  });

  it("selecting another route moves the risk colours to it without re-uploading any data", async () => {
    const { view, props } = await mount();
    const uploads = of("setData").length;
    view.rerender(<MapView {...props} selectedId="B" />);
    await act(async () => await Promise.resolve());
    expect(of("setData")).toHaveLength(uploads); // only filters and paint changed
    const lastFilter = (layer: string) => of("filter").filter((c) => c[1] === layer).pop()?.[2];
    expect(lastFilter("segments")).toEqual(["==", ["get", "route"], "B"]);
    expect(lastFilter("checkpoints")).toEqual(["==", ["get", "route"], "B"]);
    expect(lastFilter("checkpoints-other")).toEqual(["!=", ["get", "route"], "B"]);
    const segs = sources.get("segments")!.data as GeoJSON.FeatureCollection;
    expect(new Set(segs.features.map((f) => f.properties?.route))).toEqual(new Set(["A", "B", "C"])); // all uploaded once
  });
});

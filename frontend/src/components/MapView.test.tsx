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

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor() {}
    addControl() {}
    on(event: string, ...args: unknown[]) {
      if (event === "load") loadHandler = args[0] as () => void;
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
    remove() {}
  }
  const NavigationControl = class {};
  return { default: { Map: FakeMap, LngLatBounds, Popup, NavigationControl }, Map: FakeMap };
});

const { MapView } = await import("./MapView");

async function mount(props: Partial<Parameters<typeof MapView>[0]> = {}) {
  const p = plan();
  fixtureRoutes = p.routes;
  const all = { tripId: "t1", routes: p.routes, selectedId: "A", onSelect: () => {}, heatmap: p.heatmap, hour: 0, showHeatmap: true, ...props };
  const view = render(<MapView {...all} />);
  await act(async () => {
    loadHandler?.();
    await Promise.resolve();
  });
  return { view, props: all };
}

const of = (kind: string) => calls.filter((c) => c[0] === kind);
let fixtureRoutes = plan().routes;

beforeEach(() => {
  calls.length = 0;
  sources.clear();
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

  it("selecting another route moves the risk colours to it", async () => {
    const { view, props } = await mount();
    view.rerender(<MapView {...props} selectedId="B" />);
    await act(async () => await Promise.resolve());
    const segs = sources.get("segments")!.data as GeoJSON.FeatureCollection;
    expect(segs.features).toEqual(fixtureRoutes[1].segments.map((s) => expect.objectContaining({ properties: { risk: s.risk } })));
  });
});

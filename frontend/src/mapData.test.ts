/**
 * The brief: "Show all routes, checkpoints, risk levels, ETAs, and route summaries on a map"
 * and "a weather heatmap along the trip corridor with a 0-48 hour forecast slider".
 * These test what the map is given to draw.
 */
import { describe, expect, it } from "vitest";
import {
  checkpointPoints,
  checkpointPopup,
  heatPoints,
  heatWeight,
  riskColorExpr,
  riskSegments,
  routeLabels,
  routeLines,
  routeTooltip,
} from "./mapData";
import { RISK_COLOR } from "./format";
import { checkpoint, plan, route } from "./test/fixtures";

describe("routes on the map", () => {
  it("draws every route", () => {
    const p = plan();
    const fc = routeLines(p.routes);
    expect(fc.features.map((f) => f.properties?.id)).toEqual(["A", "B", "C"]);
    expect(fc.features[0].geometry).toEqual(p.routes[0].geometry);
  });

  it("colours each route by risk along its length, tagged so a filter can pick the selected one", () => {
    const r = route("B", 33, {
      segments: [
        { risk: "low", from_mi: 0, to_mi: 300, coordinates: [[-100, 33], [-97, 33]] },
        { risk: "severe", from_mi: 300, to_mi: 600, coordinates: [[-97, 33], [-94, 33]] },
      ],
    });
    const fc = riskSegments([r]);
    expect(fc.features.map((f) => f.properties)).toEqual([
      { route: "B", risk: "low" },
      { route: "B", risk: "severe" },
    ]);
    // The colour rule maps each risk level to its legend colour, grey when there's no forecast.
    expect(riskColorExpr).toEqual(expect.arrayContaining(["severe", RISK_COLOR.severe, "no_travel", RISK_COLOR.no_travel]));
  });

  it("puts each letter badge on its own route, away from the others", () => {
    const p = plan(); // routes 3 degrees of latitude apart
    const fc = routeLabels(p.routes);
    fc.features.forEach((f, k) => {
      const [, lat] = (f.geometry as GeoJSON.Point).coordinates;
      expect(lat).toBe(p.routes[k].geometry.coordinates[0][1]);
    });
  });
});

describe("checkpoints on the map", () => {
  it("shows the checkpoints of every route, with their risk and route", () => {
    const p = plan();
    const fc = checkpointPoints(p.routes);
    expect(fc.features).toHaveLength(p.routes.reduce((n, r) => n + r.checkpoints.length, 0));
    const byRoute = (id: string) => fc.features.filter((f) => f.properties?.route === id);
    expect(byRoute("B").map((f) => f.properties?.risk)).toEqual(["low", "severe"]);
  });

  it("each popup gives the mile, risk level, ETA, the weather and why", () => {
    const html = checkpointPopup(
      checkpoint({
        mile: 125,
        risk: "severe",
        risk_label: "Severe",
        eta: "2026-10-18T19:30:00Z",
        hours_from_departure: 5.5,
        reasons: ["rain 0.60 in/hr"],
        weather: { source: "hrrr", wind_mph: 30, gust_mph: 41, rain_in_hr: 0.6, snow_in_hr: 0 },
      }),
    );
    expect(html).toContain("Mile 125");
    expect(html).toContain("Severe");
    expect(html).toMatch(/Arrives .+\(\+5 h 30 min\)/);
    expect(html).toContain("Wind 30 mph (gusts 41)");
    expect(html).toContain("Rain 0.60 in/hr");
    expect(html).toContain("Why: rain 0.60 in/hr");
    expect(html).toContain("NOAA HRRR (3 km)");
  });

  it("a checkpoint with no forecast says so instead of inventing weather", () => {
    const html = checkpointPopup(checkpoint({ risk: null, risk_label: "No forecast", weather: undefined }));
    expect(html).toContain("No forecast");
    expect(html).not.toContain("Wind");
  });
});

describe("route summaries on the map", () => {
  it("the hover tooltip summarises the route", () => {
    const r = route("B", 33, { distance_mi: 612, hours: 10.5 });
    r.summary = { ...r.summary, severe_mi: 12, high_mi: 30 };
    const html = routeTooltip(r);
    expect(html).toContain("Route B");
    expect(html).toContain(r.via);
    expect(html).toContain("612 mi");
    expect(html).toContain("10 h 30 min");
    expect(html).toContain("Severe 12 mi · High 30 mi");
  });
});

describe("heatmap and the 0-48 h slider", () => {
  it("each point carries every hour, so the slider never has to re-send data", () => {
    const fc = heatPoints(plan().heatmap);
    const props = fc.features[0].properties!;
    expect(Object.keys(props)).toHaveLength(49); // h0 .. h48
    expect(props.h12).toBe(3.5);
  });

  it("hours without data are left out rather than drawn as calm", () => {
    const fc = heatPoints(plan().heatmap);
    expect(Object.keys(fc.features[1].properties!)).toHaveLength(0);
  });

  it("moving the slider only changes which hour the weight reads (risk 0-4 -> 0-1)", () => {
    expect(heatWeight(0)).toEqual(["/", ["coalesce", ["get", "h0"], 0], 4]);
    expect(heatWeight(48)).toEqual(["/", ["coalesce", ["get", "h48"], 0], 4]);
  });
});

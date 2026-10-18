/** The planner hook with the API faked: routes are shown as they arrive, weather waits for all of them. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { plan } from "./test/fixtures";
import type { Trip } from "./api";

const api = vi.hoisted(() => ({ createTrip: vi.fn(), getTrip: vi.fn(), getPlan: vi.fn() }));
vi.mock("./api", async (importOriginal) => ({ ...(await importOriginal<typeof import("./api")>()), ...api }));

const { usePlanner } = await import("./usePlanner");

function trip(ids: string[], complete: boolean): Trip {
  const p = plan();
  return { ...p, routes: p.routes.filter((r) => ids.includes(r.id)), routes_complete: complete };
}

const request = { origin: "A", destination: "B", departure: "2026-10-18T14:00:00Z", load_lb: 40000, interval_mi: 25 as const };

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("usePlanner", () => {
  it("shows the first routes at once, adds the last one when it's found, then loads weather", async () => {
    api.createTrip.mockResolvedValue(trip(["A", "B"], false));
    api.getTrip.mockResolvedValue(trip(["A", "B", "C"], true));
    api.getPlan.mockResolvedValue(plan());
    const { result } = renderHook(() => usePlanner());

    act(() => void result.current.submit(request));
    await waitFor(() => expect(result.current.trip?.routes.map((r) => r.id)).toEqual(["A", "B"]));
    expect(result.current.phase).toBe("routing");
    expect(api.getPlan).not.toHaveBeenCalled(); // no weather until all three routes exist

    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(result.current.trip?.routes.map((r) => r.id)).toEqual(["A", "B", "C"]);
    expect(api.getPlan).toHaveBeenCalledTimes(1);
  });

  it("goes straight to the weather when all routes come back at once", async () => {
    api.createTrip.mockResolvedValue(trip(["A", "B", "C"], true));
    api.getPlan.mockResolvedValue(plan());
    const { result } = renderHook(() => usePlanner());
    act(() => void result.current.submit(request));
    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(api.getTrip).not.toHaveBeenCalled();
  });
});

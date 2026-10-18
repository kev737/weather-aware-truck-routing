import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getPlan, planTripUrl } from "./api";
import { hours, miles } from "./format";
import { backoffSeconds } from "./usePlanner";

const reply = (status: number, body: unknown) =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.restoreAllMocks());

describe("API errors", () => {
  it("shows the server's message, without raw field names", async () => {
    reply(400, { departure: ["That time has passed. Pick a departure from now on."] });
    await expect(getPlan("t1")).rejects.toThrow("That time has passed. Pick a departure from now on.");
  });

  it("keeps each field's message so the form can show it next to the field", async () => {
    reply(400, { destination: ['We couldn\'t find "Daytn".'], load_lb: ["Enter 0 to 80,000 lb."] });
    const err = (await getPlan("t1").catch((e) => e)) as ApiError;
    expect(err.fields).toEqual({ destination: 'We couldn\'t find "Daytn".', load_lb: "Enter 0 to 80,000 lb." });
  });

  it("knows when the daily weather quota is used up (so it won't retry)", async () => {
    reply(503, { detail: "Today's free weather quota is used up.", quota_exhausted: true });
    const err = (await getPlan("t1").catch((e) => e)) as ApiError;
    expect(err.status).toBe(503);
    expect(err.quotaExhausted).toBe(true);
  });

  it("passes on when to retry after a per-minute limit", async () => {
    reply(503, { detail: "Busy", retry_after_s: 37 });
    const err = (await getPlan("t1").catch((e) => e)) as ApiError;
    expect(err.retryAfterS).toBe(37);
    expect(err.quotaExhausted).toBe(false);
  });

  it("a network failure is a clear message", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(getPlan("t1")).rejects.toThrow("Can't reach the server");
  });

  it("asks the right endpoint", () => {
    expect(planTripUrl("abc")).toBe("/api/trips/abc/plan/");
  });
});

describe("helpers", () => {
  it("formats times and distances", () => {
    expect(hours(0.5)).toBe("30 min");
    expect(hours(2)).toBe("2 h");
    expect(hours(45.22)).toBe("45 h 13 min");
    expect(miles(2848.4)).toBe("2,848 mi");
  });

  it("backs off 5, 10, 20, 40 s, never past 60, and honours the server's wait", () => {
    expect([1, 2, 3, 4, 5, 6].map((a) => backoffSeconds(a))).toEqual([5, 10, 20, 40, 60, 60]);
    expect(backoffSeconds(3, 12)).toBe(12);
  });
});

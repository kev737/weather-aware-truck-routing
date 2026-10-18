import { describe, expect, it } from "vitest";
import { validate } from "./validation";

const inAnHour = new Date(Date.now() + 3_600_000).toISOString();
const ok = { origin: "Dallas, TX", destination: { name: "Houston, TX", lat: 29.76, lon: -95.37 }, departure: inAnHour, load: "40000" };

describe("form checks", () => {
  it("accepts a normal trip", () => {
    expect(validate(ok)).toEqual({});
  });

  it("says which place is missing", () => {
    expect(validate({ ...ok, origin: "  ", destination: "" })).toEqual({
      origin: "Enter where the trip starts.",
      destination: "Enter where the trip ends.",
    });
  });

  it("rejects a departure that has passed or is more than 7 days out", () => {
    expect(validate({ ...ok, departure: "2020-01-01T08:00" }).departure).toMatch(/That time has passed/);
    const farOut = new Date(Date.now() + 8 * 86_400_000).toISOString();
    expect(validate({ ...ok, departure: farOut }).departure).toMatch(/up to 7 days ahead/);
    expect(validate({ ...ok, departure: "" }).departure).toBe("Pick a departure date and time.");
  });

  it("wants a whole-pound load from 0 to 80,000", () => {
    for (const load of ["", "-5", "90000", "1.5"]) expect(validate({ ...ok, load }).load_lb).toBe("Enter 0 to 80,000 lb.");
    expect(validate({ ...ok, load: "0" })).toEqual({});
  });
});

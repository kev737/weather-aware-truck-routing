import type { FieldErrors, Place } from "./api";

export const MAX_DAYS_AHEAD = 7; // the API rejects departures further out (forecast quality)
export const MAX_LOAD_LB = 80_000;
const PAST_GRACE_MS = 3_600_000; // like the API: "now" may be up to an hour ago (slow form filling)

const placeText = (p: string | Place) => (typeof p === "string" ? p : p.name).trim();

/** The same checks as the API, so most mistakes are caught before a request. */
export function validate(form: { origin: string | Place; destination: string | Place; departure: string; load: string }): FieldErrors {
  const errors: FieldErrors = {};
  if (!placeText(form.origin)) errors.origin = "Enter where the trip starts.";
  if (!placeText(form.destination)) errors.destination = "Enter where the trip ends.";
  const when = new Date(form.departure).getTime();
  if (!form.departure || Number.isNaN(when)) errors.departure = "Pick a departure date and time.";
  else if (when < Date.now() - PAST_GRACE_MS) errors.departure = "That time has passed. Pick a departure from now on.";
  else if (when > Date.now() + MAX_DAYS_AHEAD * 86_400_000)
    errors.departure = `You can plan up to ${MAX_DAYS_AHEAD} days ahead. Pick an earlier departure.`;
  const load = Number(form.load);
  if (form.load.trim() === "" || !Number.isInteger(load) || load < 0 || load > MAX_LOAD_LB)
    errors.load_lb = "Enter 0 to 80,000 lb.";
  return errors;
}

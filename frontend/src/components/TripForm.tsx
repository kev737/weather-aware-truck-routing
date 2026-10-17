import { useState } from "react";
import type { Place, PlanRequest } from "../api";
import { PlaceInput } from "./PlaceInput";

const INTERVALS = [10, 25, 50] as const;

const MAX_DAYS_AHEAD = 7; // the API rejects departures further out (forecast quality)

/** A Date as <input type="datetime-local"> wants it, in the user's own time zone. */
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function nextHourLocal(): string {
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
  return toLocalInput(d);
}

interface Props {
  loading: boolean;
  onSubmit: (req: PlanRequest) => void;
}

export function TripForm({ loading, onSubmit }: Props) {
  const [origin, setOrigin] = useState<string | Place>("Chicago, IL");
  const [destination, setDestination] = useState<string | Place>("Denver, CO");
  const [departure, setDeparture] = useState(nextHourLocal);
  const [loadLb, setLoadLb] = useState(40_000);
  const [interval, setInterval] = useState<PlanRequest["interval_mi"]>(25);

  return (
    <form
      className="trip-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          origin,
          destination,
          // The browser reads the local time as the user's zone; send it as UTC with an offset.
          departure: new Date(departure).toISOString(),
          load_lb: loadLb,
          interval_mi: interval,
        });
      }}
    >
      <PlaceInput label="From" value={origin} onChange={setOrigin} placeholder="City, address or lat, lon" />
      <PlaceInput label="To" value={destination} onChange={setDestination} placeholder="City, address or lat, lon" />
      <div className="row">
        <label className="field">
          <span>Departure</span>
          <input
            type="datetime-local"
            value={departure}
            min={toLocalInput(new Date())}
            max={toLocalInput(new Date(Date.now() + MAX_DAYS_AHEAD * 86_400_000))}
            onChange={(e) => setDeparture(e.target.value)}
            required
          />
        </label>
        <label className="field narrow">
          <span>Load (lb)</span>
          <input
            type="number"
            min={0}
            max={80_000}
            step={1}
            value={loadLb}
            onChange={(e) => setLoadLb(Number(e.target.value))}
            required
          />
        </label>
      </div>
      <div className="field">
        <span>Check weather every</span>
        <div className="segmented" role="radiogroup">
          {INTERVALS.map((mi) => (
            <button
              key={mi}
              type="button"
              role="radio"
              aria-checked={interval === mi}
              className={interval === mi ? "active" : ""}
              onClick={() => setInterval(mi)}
            >
              {mi} mi
            </button>
          ))}
        </div>
      </div>
      <button className="primary" type="submit" disabled={loading}>
        {loading ? "Planning…" : "Plan routes"}
      </button>
    </form>
  );
}

import { useEffect, useId, useState } from "react";
import type { FieldErrors, FormField, Place, PlanRequest } from "../api";
import { MAX_DAYS_AHEAD, MAX_LOAD_LB, validate } from "../validation";
import { FieldError } from "./FieldError";
import { PlaceInput } from "./PlaceInput";

const INTERVALS = [10, 25, 50] as const;


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
  serverErrors?: FieldErrors; // problems the server found, e.g. a place it couldn't find
}

export function TripForm({ loading, onSubmit, serverErrors }: Props) {
  const [origin, setOrigin] = useState<string | Place>("Chicago, IL");
  const [destination, setDestination] = useState<string | Place>("Denver, CO");
  const [departure, setDeparture] = useState(nextHourLocal);
  const [load, setLoad] = useState("40000"); // text, so an empty field is "empty", not 0
  const [interval, setInterval] = useState<PlanRequest["interval_mi"]>(25);
  const [errors, setErrors] = useState<FieldErrors>({});
  const departureErrorId = useId();
  const loadErrorId = useId();

  useEffect(() => setErrors(serverErrors ?? {}), [serverErrors]);

  /** Editing a field clears its error. */
  const edited =
    <T,>(field: FormField, set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setErrors((prev) => {
        const next = { ...prev };
        delete next[field];
        return next;
      });
    };

  return (
    <form
      className="trip-form"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const found = validate({ origin, destination, departure, load });
        setErrors(found);
        if (Object.keys(found).length) return;
        onSubmit({
          origin,
          destination,
          // The browser reads the local time as the user's zone; send it as UTC with an offset.
          departure: new Date(departure).toISOString(),
          load_lb: Number(load),
          interval_mi: interval,
        });
      }}
    >
      <PlaceInput
        label="From"
        value={origin}
        onChange={edited("origin", setOrigin)}
        placeholder="City, address or lat, lon"
        error={errors.origin}
      />
      <PlaceInput
        label="To"
        value={destination}
        onChange={edited("destination", setDestination)}
        placeholder="City, address or lat, lon"
        error={errors.destination}
      />
      <div className="row">
        <label className="field">
          <span>Departure</span>
          <input
            type="datetime-local"
            value={departure}
            min={toLocalInput(new Date())}
            max={toLocalInput(new Date(Date.now() + MAX_DAYS_AHEAD * 86_400_000))}
            onChange={(e) => edited("departure", setDeparture)(e.target.value)}
            aria-invalid={Boolean(errors.departure)}
            aria-describedby={errors.departure ? departureErrorId : undefined}
          />
          <FieldError id={departureErrorId} message={errors.departure} />
        </label>
        <label className="field narrow">
          <span>Load (lb)</span>
          <input
            type="number"
            inputMode="numeric"
            min={0}
            max={MAX_LOAD_LB}
            step={1}
            value={load}
            onChange={(e) => edited("load_lb", setLoad)(e.target.value)}
            aria-invalid={Boolean(errors.load_lb)}
            aria-describedby={errors.load_lb ? loadErrorId : undefined}
          />
          <FieldError id={loadErrorId} message={errors.load_lb} />
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

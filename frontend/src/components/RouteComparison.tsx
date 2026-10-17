import type { Plan, RouteBasics, RouteOption } from "../api";
import { hours, miles, RISK_COLOR, RISK_LABEL, RISK_ORDER, when } from "../format";

interface Props {
  routes: RouteBasics[]; // before the weather arrives
  plan: Plan | null; // after
  selectedId: string | null;
  onSelect: (id: string) => void;
}

interface Row {
  label: string;
  hint?: string;
  value: (r: RouteOption) => number;
  show: (v: number) => string;
}

// The ranking: No Travel and missing data first, then the score (time + weather).
const RISK_ROWS: Row[] = [
  { label: "No Travel", hint: "Never recommended while any route avoids it", value: (r) => r.summary.miles_by_risk.no_travel, show: miles },
  { label: "No forecast", hint: "Missing data never makes a route look safer", value: (r) => r.summary.no_forecast_mi, show: miles },
  { label: "Severe", value: (r) => r.summary.severe_mi, show: miles },
  { label: "High", value: (r) => r.summary.high_mi, show: miles },
  { label: "Average risk", hint: "0 = Low … 4 = No Travel, weighted by miles", value: (r) => r.summary.avg_risk, show: (v) => v.toFixed(2) },
  { label: "Travel time", value: (r) => r.hours, show: hours },
  {
    label: "Score",
    hint: "What the recommendation minimises: travel time plus weather, where 1 Severe mile = 10 min, 1 High mile = 2 min, 1 Moderate mile = 0.5 min",
    value: (r) => r.summary.score_h,
    show: hours,
  },
];

function RiskBar({ route }: { route: RouteOption }) {
  const total = route.distance_mi || 1;
  return (
    <div className="risk-bar" aria-label="Miles by risk level">
      {RISK_ORDER.map((k) => {
        const mi = route.summary.miles_by_risk[k];
        return mi > 0 ? (
          <span key={k} style={{ width: `${(mi / total) * 100}%`, background: RISK_COLOR[k] }} title={`${RISK_LABEL[k]}: ${miles(mi)}`} />
        ) : null;
      })}
      {route.summary.no_forecast_mi > 0 && (
        <span className="no-data" style={{ width: `${(route.summary.no_forecast_mi / total) * 100}%` }} title="No forecast" />
      )}
    </div>
  );
}

/** One line, only when leaving later is actually safer (the backend decides). */
function LeaveLater({ plan }: { plan: Plan }) {
  const w = plan.best_window;
  if (!w) return null;
  return (
    <p className="leave-later">
      ⏱ Safer if you wait: leave {when(w.departure)} on Route {w.route_id}. {w.reason}
    </p>
  );
}

function Banner({ plan }: { plan: Plan }) {
  const rec = plan.recommendation;
  if (rec.no_safe_route) {
    return (
      <div className="banner danger" role="alert">
        <strong>No safe route at your departure time.</strong> Every route crosses No Travel conditions.
        {plan.best_window ? "" : " No safer departure in the next 48 hours either."}
      </div>
    );
  }
  const r = plan.routes.find((x) => x.id === rec.route_id)!;
  return (
    <div className={`banner ${rec.provisional ? "provisional" : "ok"}`}>
      <strong>
        {rec.provisional && <span className="tag">Provisional</span>}★ Route {r.id} · {r.via}
      </strong>
      <span>{rec.reason}</span>
      {rec.provisional && <span className="hint">Some checkpoints are still waiting for weather; this may change.</span>}
    </div>
  );
}

/** Coarse spacing can step over a storm cell; say so rather than let a clean result mislead. */
function SpacingNote({ plan }: { plan: Plan }) {
  if (plan.interval_mi <= 10) return null;
  return (
    <p className="note">
      Checkpoints are {plan.interval_mi} mi apart, so storms between them aren't counted. 10 mi is safest for
      thunderstorms.
    </p>
  );
}

/**
 * Weather data warnings from the server: missing stretches, or the daily quota running low.
 * Gaps that are filling in automatically are explained by the progress line instead.
 */
function Warnings({ plan }: { plan: Plan }) {
  const shown = plan.warnings.filter((w) => !(w.kind === "weather_partial" && w.retry));
  return (
    <>
      {shown.map((w) => (
        <p key={w.kind + w.message} className={w.kind === "quota_low" ? "note" : "warning"} role="status">
          {w.message}
        </p>
      ))}
    </>
  );
}

export function RouteComparison({ routes, plan, selectedId, onSelect }: Props) {
  if (routes.length === 0) return null;
  const cols = plan?.routes ?? null;
  const best = (row: Row) => (cols ? Math.min(...cols.map(row.value)) : null);

  const header = (r: RouteBasics) => {
    const rec = plan?.recommendation.route_id === r.id;
    return (
      <th key={r.id} scope="col" className={r.id === selectedId ? "selected" : ""}>
        <button type="button" onClick={() => onSelect(r.id)} aria-pressed={r.id === selectedId}>
          <span className="badge">{r.id}</span>
          {rec && <span className="star" title="Recommended">★</span>}
          <span className="via">{r.via}</span>
        </button>
      </th>
    );
  };

  return (
    <section className="results">
      {plan && <Banner plan={plan} />}
      {plan && <LeaveLater plan={plan} />}
      {plan && <Warnings plan={plan} />}
      <table className="compare">
        <thead>
          <tr>
            <th scope="col" className="row-label">
              <span className="sr-only">Criterion</span>
            </th>
            {routes.map(header)}
          </tr>
        </thead>
        <tbody>
          {cols && (
            <tr>
              <th scope="row" className="row-label">Risk mix</th>
              {cols.map((r) => (
                <td key={r.id} className={r.id === selectedId ? "selected" : ""}>
                  <RiskBar route={r} />
                </td>
              ))}
            </tr>
          )}
          {RISK_ROWS.map((row) => {
            const b = best(row);
            return (
              <tr key={row.label}>
                <th scope="row" className="row-label" title={row.hint}>
                  {row.label}
                </th>
                {routes.map((basic) => {
                  const r = cols?.find((x) => x.id === basic.id);
                  const cls = basic.id === selectedId ? "selected" : "";
                  if (!r) {
                    // Weather not loaded yet: time is known from step 1, risk isn't.
                    const text = row.label === "Travel time" ? hours(basic.hours) : "…"; // the rest needs weather
                    return <td key={basic.id} className={cls}>{text}</td>;
                  }
                  const v = row.value(r);
                  const isBest = b !== null && v === b && cols!.some((o) => row.value(o) !== b);
                  return (
                    <td key={r.id} className={`${cls} ${isBest ? "best" : ""}`}>
                      {row.show(v)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr className="secondary">
            <th scope="row" className="row-label">Distance</th>
            {routes.map((r) => (
              <td key={r.id} className={r.id === selectedId ? "selected" : ""}>{miles(r.distance_mi)}</td>
            ))}
          </tr>
          <tr className="secondary">
            <th scope="row" className="row-label">Arrives</th>
            {routes.map((r) => (
              <td key={r.id} className={r.id === selectedId ? "selected" : ""}>{when(r.arrival)}</td>
            ))}
          </tr>
        </tbody>
      </table>
      {plan && <SpacingNote plan={plan} />}
    </section>
  );
}

import type { RiskKey } from "./api";

export const RISK_ORDER: RiskKey[] = ["low", "moderate", "high", "severe", "no_travel"];

export const RISK_LABEL: Record<RiskKey, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
  no_travel: "No Travel",
};

// Green -> yellow -> orange -> red -> purple, readable on the light basemap.
export const RISK_COLOR: Record<RiskKey, string> = {
  low: "#2f9e44",
  moderate: "#f2c200",
  high: "#f76707",
  severe: "#e03131",
  no_travel: "#7048e8",
};

export const NO_FORECAST_COLOR = "#868e96";

export function riskColor(risk: RiskKey | null): string {
  return risk ? RISK_COLOR[risk] : NO_FORECAST_COLOR;
}

export function hours(h: number): string {
  const whole = Math.floor(h);
  const min = Math.round((h - whole) * 60);
  if (whole === 0) return `${min} min`;
  return min ? `${whole} h ${min} min` : `${whole} h`;
}

const dateTime = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
});

export function when(iso: string): string {
  return dateTime.format(new Date(iso));
}

export function miles(mi: number): string {
  return `${Math.round(mi).toLocaleString()} mi`;
}

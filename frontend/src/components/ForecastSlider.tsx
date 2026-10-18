import { useEffect } from "react";
import type { Heatmap } from "../api";
import { RISK_COLOR, RISK_LABEL, RISK_ORDER, when } from "../format";

interface Props {
  heatmap: Heatmap;
  hour: number;
  onHour: (h: number) => void;
  playing: boolean;
  onPlaying: (p: boolean) => void;
  visible: boolean;
  onVisible: (v: boolean) => void;
}

const STEP_MS = 400; // play speed: one forecast hour every 0.4 s

/** The 0-48 h forecast slider over the map, with play/pause and a legend. */
export function ForecastSlider({ heatmap, hour, onHour, playing, onPlaying, visible, onVisible }: Props) {
  const maxHour = heatmap.hours.length - 1; // the server decides how far the heatmap reaches
  // Play: advance an hour at a time, stop at the end.
  useEffect(() => {
    if (!playing) return;
    if (hour >= maxHour) {
      onPlaying(false);
      return;
    }
    const id = setTimeout(() => onHour(hour + 1), STEP_MS);
    return () => clearTimeout(id);
  }, [playing, hour, maxHour, onHour, onPlaying]);

  return (
    <div className="forecast" role="group" aria-label="Weather heatmap forecast">
      <div className="forecast-row">
        <button
          type="button"
          className="play"
          onClick={() => {
            if (!playing && hour >= maxHour) onHour(0); // replay from the start
            onPlaying(!playing);
          }}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <label className="forecast-time">
          <strong>+{hour} h</strong> · {when(heatmap.hours[hour])}
        </label>
        <label className="toggle">
          <input type="checkbox" checked={visible} onChange={(e) => onVisible(e.target.checked)} /> Heatmap
        </label>
      </div>
      <input
        type="range"
        min={0}
        max={maxHour}
        step={1}
        value={hour}
        onChange={(e) => {
          onPlaying(false);
          onHour(Number(e.target.value));
        }}
        aria-label="Hours after departure"
      />
      <div className="legend">
        {RISK_ORDER.map((k) => (
          <span key={k}>
            <i style={{ background: RISK_COLOR[k] }} /> {RISK_LABEL[k]}
          </span>
        ))}
      </div>
    </div>
  );
}

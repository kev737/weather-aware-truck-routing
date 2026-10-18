import { useEffect, useState } from "react";
import { ForecastSlider } from "./components/ForecastSlider";
import { MapView } from "./components/MapView";
import { Progress } from "./components/Progress";
import { RouteComparison } from "./components/RouteComparison";
import { Toast } from "./components/Toast";
import { TripForm } from "./components/TripForm";
import { usePlanner } from "./usePlanner";

export default function App() {
  const planner = usePlanner();
  const { trip, plan, phase } = planner;
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(true);

  // A new trip starts the slider at departure.
  useEffect(() => {
    setHour(0);
    setPlaying(false);
  }, [trip?.id]);

  // Until the user clicks a route, follow the recommendation (or the first route).
  const routes = plan?.routes ?? trip?.routes ?? [];
  const selectedId = pickedId ?? plan?.recommendation.route_id ?? routes[0]?.id ?? null;

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>
          Weather-Aware <span className="accent">Truck Routing</span>
        </h1>
        <TripForm
          loading={phase === "routing" || phase === "weather"}
          onSubmit={(req) => {
            setPickedId(null);
            planner.submit(req);
          }}
        />
        <Progress
          phase={phase}
          routesFound={trip?.routes.length ?? 0}
          estimatedS={trip?.weather.estimated_s ?? null}
          startedAt={planner.weatherStartedAt}
          retry={planner.retry}
          onRetryNow={planner.retryNow}
        />
        <RouteComparison routes={routes} plan={plan} selectedId={selectedId} onSelect={setPickedId} />
        {planner.error && (
          <p className="error" role="alert">
            {planner.error}
          </p>
        )}
      </aside>
      <main className="map-area">
        <MapView
          tripId={trip?.id ?? null}
          routes={routes}
          selectedId={selectedId}
          onSelect={setPickedId}
          heatmap={plan?.heatmap ?? null}
          hour={hour}
          showHeatmap={showHeatmap}
        />
        {plan && (
          <ForecastSlider
            heatmap={plan.heatmap}
            hour={hour}
            onHour={setHour}
            playing={playing}
            onPlaying={setPlaying}
            visible={showHeatmap}
            onVisible={setShowHeatmap}
          />
        )}
        <Toast message={planner.notice} onDone={planner.dismissNotice} />
      </main>
    </div>
  );
}

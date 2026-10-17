# Weather-Aware Truck Routing

A React + Django app that helps a truck driver pick the safest of three routes, based on the
weather the truck will meet at each point along the way, its load weight and travel time.

**Live app:** https://52-24-82-112.sslip.io · **Walkthrough:** _Loom link_

## What it does

1. You enter an origin, a destination, a departure time and a load weight, and pick how often to
   check the weather (every 10, 25 or 50 miles).
2. The app finds **three truck routes**, places a checkpoint at the start, every N miles and at the
   destination, and works out when the truck reaches each checkpoint.
3. For each checkpoint it looks up the **forecast for the hour the truck gets there** (not the
   weather right now) and rates it Low, Moderate, High, Severe or No Travel with the risk and load
   rules from the brief.
4. It ranks the routes and recommends one, with a sentence saying why.
5. The map shows all three routes, their checkpoints, risk colours and ETAs, plus a weather
   heatmap along the corridor with a 0–48 hour slider.

If leaving later would be safer (say a rain band clears in three hours), one extra line suggests
when to go. If every route crosses No Travel weather, nothing is recommended.

## How the recommendation works

Routes are compared on these criteria, in this order. A later criterion only matters when the
earlier ones tie:

| # | Criterion | Note |
|---|---|---|
| 1 | Fewest **No Travel** miles | Added: a route you're told not to drive never beats one that's only Severe |
| 2 | Fewest miles **without a forecast** | Added: missing data never makes a route look safer |
| 3 | Fewest **Severe** miles | Brief #1 (No Travel miles count here too) |
| 4 | Fewest **High** miles | Brief #2 |
| 5 | Lowest **average risk** | Brief #3: levels 0–4, weighted by miles |
| 6 | Shortest **travel time** | Brief #4 |

This is a strict priority order (lexicographic), the way the brief lists it: safety always
comes before speed. Miles are compared to the nearest mile, average risk to two decimals and time
to the minute, so rounding noise never decides between two routes.

Each checkpoint stands for the road halfway to its neighbours on each side, so checkpoint miles
add up exactly to the route length. That's what makes "Severe miles" a real distance.

Sampling follows the brief: weather is read at the checkpoints only. At 25 or 50 mi spacing a
thunderstorm cell can sit between two checkpoints and go uncounted (Miami → Orlando through a
West Palm Beach downpour shows it: No Travel at 10 mi spacing, clean at 50), so the app says so
under the comparison whenever the spacing is coarser than 10 mi.

## Risk rules and the decisions behind them

The thresholds are the brief's table (`backend/planner/domain/risk.py`). A checkpoint's level is
the worst of wind (after the load rules), rain and snow.

- **Shared boundaries go to the riskier band.** The table's ranges share endpoints (rain 0.25 is
  in both "0.10–0.25" and "0.25–0.50"), so 0.25 in/hr is High. The exception is No Travel, which
  the table writes as ">1.00" and ">3.0": exactly 1.00 in/hr of rain is Severe.
- **Load rules apply to wind only**, and "over 30,000 lb" means over: exactly 30,000 lb doesn't
  trigger the rule.
- **Wind is sustained wind at 10 m.** Gusts are shown in each checkpoint popup but don't change
  the rating.
- **Rain** is Open-Meteo's `rain + showers` and **snow** is `snowfall`, both per hour.
- Worth noting: in practice light or empty trailers are the ones most at risk of blowing over in
  crosswinds. The app implements the brief's rule as written.

## Architecture

```
React (Vite, TypeScript, MapLibre)             Django REST Framework
──────────────────────────────────             ─────────────────────────────────────────
TripForm ── POST /api/trips/ ───────────────►  geocode (Photon) → 3 truck routes (Valhalla)
   routes drawn right away                     → checkpoints → trip cached, weather estimate
Progress ── GET /api/trips/<id>/plan/ ──────►  weather at each ETA (Open-Meteo, HRRR + GFS)
   retries with backoff if partial             → risk → ranking → leave-later → heatmap
RouteComparison · MapView · ForecastSlider
```

- `backend/planner/domain/`: pure logic with no I/O (risk rules, ranking, geometry, checkpoints).
  It's the part most worth reading.
- `backend/planner/services/`: the third-party APIs and the planning pipeline.
- `frontend/src/`: `usePlanner.ts` runs the two-step flow; components are the form, comparison
  table, map and slider.

### Why two requests

Routing takes 2–20 seconds and the weather a few more. Splitting the work lets the routes appear
on the map in seconds while the weather loads, with an honest time estimate (the server knows how
many forecast points it still has to download). The browser stays usable the whole time, so a job
queue wasn't needed.

### Three routes, always

The public Valhalla server returns at most two alternates, often fewer. The rest come from the
**penalty method**: ask again while avoiding a few points along the routes already found, and let
the engine find its own way around. Candidates must be mostly on different roads and at most 1.5×
slower. When the road network has no third such route (e.g. Seattle → Portland), the least
overlapping candidate up to 2× slower fills the slot. Each route is named by the roads that set it
apart from the other two.

Valhalla's public server refuses trips over ~1,500 km in straight line, so long trips are split
into legs. The split points are vertices of an OSRM route, which keeps them on interstates. (A
straight line between the cities once sent a truck over a mountain pass full of hairpins.)

### Weather

- [Open-Meteo](https://open-meteo.com/) hourly forecast: in the US it serves NOAA's HRRR (3 km,
  hourly) for about the first 43 hours, then GFS.
- Checkpoints are snapped to a grid (0.1°, coarser on very long trips), so nearby points share one
  forecast, overlapping routes reuse each other's data, and one download serves every route, every
  departure time and the heatmap.
- Each forecast cell is cached for 30 minutes. If the free tier's rate limit cuts a download
  short, the plan comes back with what arrived, missing stretches shown in grey. The browser then
  retries with exponential backoff (5, 10, 20, 40, 60 s), and each retry only fetches what's still
  missing.
- Each (cell, hour) risk is computed once and reused for every route and departure time that
  needs it.

### Heatmap performance

Each heatmap point carries all 49 hourly risk values (`h0` … `h48`). Moving the slider only
changes which value the layer's `heatmap-weight` reads, a paint property, so no data is
re-uploaded and the GPU just repaints. Blob size follows the forecast grid at every zoom, so the
cells blend into one band.

The route colours and the heatmap answer different questions: route colours show the weather **when
the truck reaches** each point; the heatmap shows the weather **everywhere along the corridor** at
the slider's hour.

### ETAs

ETA = departure + the routing engine's driving time to that point. Driver breaks and traffic are
not modelled (the public Valhalla server has no traffic data).

## Running it locally

Requirements: Python 3.13, Node 22.

```bash
# Backend (http://127.0.0.1:18731)
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend && DJANGO_DEBUG=1 python manage.py runserver 18731

# Frontend (http://localhost:5173, proxies /api to the backend)
cd frontend && npm ci && npm run dev
```

No API keys are needed. All services are free: Valhalla and OSRM (OpenStreetMap routing), Photon
(geocoding), Open-Meteo (weather) and OpenFreeMap (map tiles).

## Tests

```bash
cd backend && DJANGO_DEBUG=1 pytest -q      # 82 tests
cd frontend && npm run lint && npm run build # type check + lint
```

The tests cover every boundary in the risk table, each load rule, each step of the ranking, the
checkpoint maths, the two-step API (with fake routes and weather), partial weather and the
rate-limit retry, and the leave-later search. CI runs them on every push.

## Deployment

One Docker image (`Dockerfile`): Node builds the React app, then Django serves it and the API from the
same origin through WhiteNoise (pre-compressed, immutable-cached assets). It runs on a single AWS EC2
`t4g.micro` behind Caddy for HTTPS; see [`deploy/ec2/`](deploy/ec2/). A dedicated IP matters here:
Open-Meteo's free tier is limited per IP, and shared hosting IPs (tried on Render first) were already
over the limit. CI runs the tests, lint and build on every push.

## What I'd do next

- **Route around No Travel zones inside the routing engine.** Valhalla supports
  `exclude_polygons`, but the public server caps them at 10 km of perimeter. A self-hosted Valhalla
  would let the engine's A* treat No Travel cells as walls for the hours the truck would be there.
- **Waiting mid-trip.** "Drive to Amarillo, wait out the wind for four hours, continue" is a
  dynamic-programming problem over (checkpoint, arrival hour), and would beat both "leave later"
  and a detour in many storms.
- **Rest breaks and sleep.** Federal hours-of-service rules require a 30-minute break after
  8 hours of driving and 10 hours off duty after 11. On longer trips that shifts every later ETA,
  and with it the weather each checkpoint meets. Planning where the driver stops would also let
  the app place the rest where it sits out the worst weather.
- **A weighted cost** with tunable weights, so a dispatcher can trade a few High miles for hours
  saved. The brief's "balancing" wording allows it; this version follows its numbered order.
- **15-minute forecasts** (Open-Meteo has them for the US) and **NWS warning polygons**
  (api.weather.gov) as an official overlay.
- **A paid Open-Meteo key** (`OPEN_METEO_API_KEY`) to remove the free tier's rate limit.

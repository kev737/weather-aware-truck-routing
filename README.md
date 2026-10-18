# Weather-Aware Truck Routing

A React + Django app that helps a truck driver pick the safest of three routes, based on the
weather the truck will meet at each point along the way, its load weight and travel time.

**Live app:** https://52-24-82-112.sslip.io · **Walkthrough video:** https://52-24-82-112.sslip.io/walkthrough

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

The brief asks to recommend the best route **balancing weather risk and travel time**, preferring
fewer Severe miles, then fewer High miles, then lower average risk, then shorter travel time. The
app turns that into one score, in hours, and recommends the lowest:

```
score = travel hours + weather minutes / 60
```

A mile's weather minutes are what its extra crash risk is worth in driving time. The crash cost is
the **full cost of a crash to society** (FMCSA's figure: injuries, lives and property, mostly in the
other vehicles), not just the carrier's insurance bill: a route recommendation is a safety decision.

| Input | Value | Basis |
|---|---|---|
| Truck time | $1.50 / min | ATRI operating cost, about $91 per hour |
| Expected crash cost of an ordinary mile | $0.24 | ~$150k full cost of an average truck crash (FMCSA, $91k in 2005 dollars) × ~1.6 police-reported crashes per million truck miles |
| Crash rate in Moderate / High / Severe weather | 1.7× / 3× / 10× | Rain and snow raise crash rates by about 70–85%; single-truck crashes in snow by about 9.7× |
| Risk aversion | 7× | The average hides the rare crash that kills someone; the carrier's own worst case, an award above its policy limit, can end the company |

```
weather minutes per mile = $0.24 / $1.50 × 7 × (crash multiplier − 1)
```

Hazards on the same mile **multiply**: crash models treat weather factors as multipliers, and a wet
road lowers the wind it takes to push a truck sideways. One hazard alone comes to about 0.8 min per
Moderate mile, 2.2 per High mile and 10 per Severe mile; Severe wind in High rain is 30× the crash
rate, about 32 min per mile. So a 30-minute detour is worth it to avoid 3 Severe miles, but a 3-hour
detour isn't worth it to avoid 2. The displayed level stays the brief's table (the worst hazard);
stacking only changes the score. The inputs are estimates and easy to tune
(`backend/planner/domain/recommend.py`); on average crash cost alone (risk aversion 1) a Severe mile
would be worth only about 1.4 minutes of detour.

Sources: [ATRI operating costs (FleetOwner)](https://www.fleetowner.com/operations/article/55392569/atri-report-breaks-down-class-8-truck-operating-costs-by-region-and-expense-category),
[FMCSA unit costs of truck crashes](https://www.fmcsa.dot.gov/sites/fmcsa.dot.gov/files/docs/UnitCostsTruck%20Crashes2007.pdf),
[FMCSA Large Truck and Bus Crash Facts](https://www.fmcsa.dot.gov/safety/data-and-statistics/large-truck-and-bus-crash-facts-2022-1),
[Qiu & Nixon 2008, weather and crashes meta-analysis](https://journals.sagepub.com/doi/10.3141/2055-16),
[Becker et al. 2022, weather and crash types incl. trucks](https://link.springer.com/article/10.1186/s12544-022-00561-2),
[FHWA truck braking on wet pavement](https://www.fhwa.dot.gov/reports/tswstudy/TSWwp5.pdf).

Two rules sit above the score, both cautious:

- **No Travel miles always lose**: a route you're told not to drive is never recommended while
  another route avoids it; if none does, nothing is recommended.
- **Miles without a forecast come next**: missing data never makes a route look safer.

Each mile counts once, in its own level. The explanation states the trade-off, e.g. *"30 min slower
than the next option, but avoids weather worth 60 min (no Severe or High miles vs 6 Severe mi)."*

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
TripForm ── POST /api/trips/ ───────────────►  geocode (Photon) → first truck routes (Valhalla)
   routes drawn right away                     → checkpoints → trip cached, weather estimate
         ── GET /api/trips/<id>/ (polled) ──►  the last route, found in the background
Progress ── GET /api/trips/<id>/plan/ ──────►  weather at each ETA (NOAA HRRR + GFS grids)
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

The third route can take most of the routing time (the penalty rounds below), so the first request
returns as soon as the engine's first answer is in. The routes it has go on the map straight away;
a background thread finds the rest and the page polls `GET /api/trips/<id>/` once a second until
they're all there, then asks for the weather.

### Three routes, always

The public Valhalla server returns at most two alternates, often fewer. The rest come from the
**penalty method**: ask again while avoiding a few points along the routes already found, and let
the engine find its own way around. Candidates must be mostly on different roads and at most 1.5×
slower. When the road network has no third such route (e.g. Seattle → Portland), the least
overlapping candidate up to 2× slower fills the slot. Each route is named by the roads that set it
apart from the other two.

Valhalla's public server refuses trips over ~1,500 km in straight line, so long trips are split
into legs. The split points are vertices of an OSRM route, which keeps them on interstates.

**Domestic trips stay in the US.** Crossing into Canada needs passports and customs, and many US
loads can't legally go through, but the shortest road often does (Detroit → Buffalo via Ontario,
Seattle → Maine via the Trans-Canada). Valhalla gets its maximum border penalty (12 h; it silently
ignores larger values), split points are kept inside a simplified US outline, and because the
public server's border data doesn't stop every crossing, each leg is checked: any stretch abroad
is rerouted through a point inside the US level with it (further in if that point is a lake).
Trips that start or end abroad cross normally, with a 2-hour customs penalty. (A
straight line between the cities once sent a truck over a mountain pass full of hairpins.)

### Weather

The app reads NOAA's forecast models from its own copy of their grids, so a checkpoint lookup is an
array index, not an API call, and 10, 25 or 50 mi spacing costs the same on any trip length:

- **HRRR (3 km)** wherever it reaches: its **hourly runs** for the next 18 hours (so the near-term
  forecast is at most ~2 hours old), then its 6-hourly runs out to 48 hours. It's the only free model that simulates
  thunderstorms directly: on a Florida evening it showed 0.5–1.1 in/hr bursts at West Palm Beach
  where GFS showed a steady 0.05 in/hr (4.2 vs 0.5 inches over 24 h), the difference between
  Severe / No Travel and Low.
- **GFS (13 km, hourly to 120 h, 3-hourly to 240 h)** after that. When the departure is more than
  48 hours away the app says so and suggests rechecking within 48 hours of leaving.
- A job (`manage.py refresh_grids`, hourly via a systemd timer) downloads each new run, our five
  fields only: 10 m wind, gusts, precipitation rate and frozen share, as byte ranges from NOAA's
  AWS mirror (HRRR) or the continental-US box from NOAA's GRIB filter (GFS). It converts them to the
  brief's units, turns GFS's 6-hour running averages into hourly rates, and keeps the newest run of
  each model as memory-mapped arrays (~1.1 GB in all). It runs at background CPU priority, never
  starts a download that would leave less than 3 GB free, and swaps a new run in only once it's
  complete. Rain is the liquid share of the precipitation rate; snow is the
  frozen share at the standard 10:1 snow-to-water ratio.
- HRRR is on a Lambert conformal grid; checkpoints find their cell with the projection formulas
  (verified against the GRIB file's own coordinates: 2,000 of 2,000 random points).
- Each checkpoint's forecast is read **at that checkpoint** and at the truck's ETA; the popup names
  the model it came from. Points outside the grids (outside the US) fall back to Open-Meteo, paced
  to its free tier (600 lookups a minute), checkpoints first.

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
(geocoding), NOAA HRRR and GFS (weather; Open-Meteo outside the US) and OpenFreeMap (map tiles).
Locally, fill the grids once with `python manage.py refresh_grids` (about 1 GB; without them the app
uses Open-Meteo).

## Tests

```bash
cd backend && DJANGO_DEBUG=1 pytest -q          # 113 tests
cd frontend && npm test && npm run lint && npm run build  # 38 tests, lint, type check
```

Backend tests cover every boundary in the risk table, each load rule, the balanced score, the
checkpoint maths, the two-step API (with fake routes and weather), the NOAA grids (projection,
units, HRRR-before-GFS), partial weather, pacing and quotas, and the leave-later search.

Frontend tests cover what the brief asks the map to show, with MapLibre replaced by a recorder
(jsdom has no WebGL): all routes, every route's checkpoints with their risk, popups with mile,
risk, ETA, weather and reason, route summaries, the heatmap carrying all 49 hours, and the slider
repainting without re-sending data. Plus place suggestions (keyboard, Escape, never covering the
Plan button), loading and retry states, the comparison table, and API errors. CI runs both suites
on every push.

## Deployment

One Docker image (`Dockerfile`): Node builds the React app, then Django serves it and the API from the
same origin through WhiteNoise (pre-compressed, immutable-cached assets). It runs on a single AWS EC2
`t4g.micro` behind Caddy for HTTPS; see [`deploy/ec2/`](deploy/ec2/), and [`deploy/terraform/`](deploy/terraform/) for the AWS resources. A dedicated IP matters here:
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
- **Dispatcher-tunable weights**: let a fleet set how many minutes a Severe or High mile is worth.
- **NWS warning polygons** (api.weather.gov) as an official overlay.
- **A self-hosted truck router.** The public Valhalla server answers one request at a time (a new
  cross-country trip is ~20 requests, ~10 s; repeats are cached), its OpenStreetMap data blocks
  trucks on the road to Key West, and its border data isn't complete.

"""NOAA forecast grids, downloaded by a job and read locally: no per-point limits.

- HRRR (3 km) from NOAA's AWS mirror: the only free model that simulates
  thunderstorms directly, so it's used wherever it reaches. Its hourly runs
  (0-18 h, at most ~2 h old) come first, then the 6-hourly runs (0-48 h).
- GFS (13 km "sflux", hourly to 120 h, 3-hourly to 240 h) for later hours, the
  continental-US box cut out by NOAA's GRIB filter. It has no gusts (shown as
  unknown); the risk rules use sustained wind anyway.

`refresh()` (run hourly by `manage.py refresh_grids`) downloads any newer run,
converts it to the brief's units (mph, in/hr) and saves compact arrays; the two
newest runs per model are kept. `Grids.current()` reads them memory-mapped, so a
checkpoint lookup is an array index, not an API call.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from django.conf import settings

log = logging.getLogger(__name__)

MPH_PER_MS = 2.23694
MM_PER_IN = 25.4  # 1 kg/m^2 of water = 1 mm
SNOW_TO_LIQUID = 10  # standard 10:1 snow-to-water ratio
KEEP_RUNS = 1  # the new run is complete before the old one goes; open readers keep working
MIN_FREE_BYTES = 3 * 1024**3  # never let a download fill the disk
HRRR_HOURS = 48
# HRRR runs every hour out to 18 h, and every 6 h (00/06/12/18z) out to 48 h. We keep both:
# the hourly run is fresher for the next 18 hours, the 6-hourly one reaches further.
HRRR_MODELS = {"hrrr_hourly": (18, 1), "hrrr": (HRRR_HOURS, 6)}  # model -> (hours, runs every N h)
LOOKUP_ORDER = ("hrrr_hourly", "hrrr", "gfs")  # freshest storm-resolving forecast first
SOURCE = {"hrrr_hourly": "hrrr", "hrrr": "hrrr", "gfs": "gfs"}  # what the popup names
GFS_HOURS = list(range(0, 121)) + list(range(123, 241, 3))
CONUS = {"toplat": 50, "bottomlat": 24, "leftlon": -125, "rightlon": -66}
FIELDS = ("wind", "gust", "rain", "snow")  # what we store, per hour, in the brief's units


def _root() -> Path:
    return Path(settings.CACHE_DIR) / "grids"


# --- GRIB helpers --------------------------------------------------------------


def _decode(raw: bytes):
    """All GRIB2 messages in `raw`, as eccodes handles (caller releases them).

    Each GRIB2 message starts with b"GRIB" and states its total length in bytes 8-15.
    """
    import eccodes

    handles, pos = [], 0
    while (pos := raw.find(b"GRIB", pos)) != -1 and pos + 16 <= len(raw):
        length = int.from_bytes(raw[pos + 8 : pos + 16], "big")
        handles.append(eccodes.codes_new_from_message(raw[pos : pos + length]))
        pos += length
    return handles


def _get(s: requests.Session, url: str, **kw) -> requests.Response:
    """GET with a few retries for flaky responses; a 404 fails at once."""
    last: Exception | None = None
    for wait in (2, 5, 15, 0):
        try:
            r = s.get(url, timeout=60, **kw)
        except requests.RequestException as exc:
            last = exc
        else:
            if r.status_code in (200, 206):
                return r
            last = requests.HTTPError(f"HTTP {r.status_code} for {url}")
            if r.status_code == 404:
                break
        time.sleep(wait)
    raise last


# --- Map projections --------------------------------------------------------------


@dataclass(frozen=True)
class Lambert:
    """HRRR's Lambert conformal grid: lat/lon -> fractional (i, j) grid indices."""

    lat1: float  # first grid point
    lon1: float
    lov: float  # orientation longitude
    lad: float  # latitude where dx/dy hold
    latin1: float
    latin2: float
    dx: float  # metres
    dy: float
    nx: int
    ny: int
    radius: float = 6371229.0

    @classmethod
    def from_handle(cls, h) -> "Lambert":
        import eccodes

        g = lambda k: eccodes.codes_get(h, k)  # noqa: E731
        return cls(
            g("latitudeOfFirstGridPointInDegrees"),
            g("longitudeOfFirstGridPointInDegrees"),
            g("LoVInDegrees"),
            g("LaDInDegrees"),
            g("Latin1InDegrees"),
            g("Latin2InDegrees"),
            g("DxInMetres"),
            g("DyInMetres"),
            g("Nx"),
            g("Ny"),
        )

    def _xy(self, lat: float, lon: float) -> tuple[float, float]:
        p1, p2, p0 = (math.radians(v) for v in (self.latin1, self.latin2, self.lad))
        t = lambda p: math.tan(math.pi / 4 + p / 2)  # noqa: E731
        n = math.sin(p1) if abs(p1 - p2) < 1e-9 else math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
        f = math.cos(p1) * t(p1) ** n / n
        rho = self.radius * f / t(math.radians(lat)) ** n
        rho0 = self.radius * f / t(p0) ** n
        dlon = ((lon - self.lov + 180) % 360) - 180
        theta = n * math.radians(dlon)
        return rho * math.sin(theta), rho0 - rho * math.cos(theta)

    def index(self, lat: float, lon: float) -> tuple[int, int] | None:
        """(row, col) of the nearest grid point, or None outside the grid."""
        x, y = self._xy(lat, lon)
        x1, y1 = self._xy(self.lat1, self.lon1)
        i, j = round((x - x1) / self.dx), round((y - y1) / self.dy)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return j, i
        return None

    def as_dict(self) -> dict:
        return {"type": "lambert", **self.__dict__}


class LatLon:
    """GFS's (Gaussian) latitude/longitude grid, as 1-D coordinate arrays."""

    def __init__(self, lats, lons):
        self.lats = np.asarray(lats, dtype=np.float64)  # in stored row order
        self.lons = np.asarray(lons, dtype=np.float64)  # -180..180

    @classmethod
    def from_handle(cls, h) -> "LatLon":
        import eccodes

        lats = sorted(float(v) for v in eccodes.codes_get_array(h, "distinctLatitudes"))
        if eccodes.codes_get(h, "jScansPositively") == 0:
            lats.reverse()  # rows run north to south
        lons = [((float(v) + 180) % 360) - 180 for v in eccodes.codes_get_array(h, "distinctLongitudes")]
        return cls(lats, lons)

    def index(self, lat: float, lon: float) -> tuple[int, int] | None:
        if not (self.lats.min() - 0.2 <= lat <= self.lats.max() + 0.2 and self.lons.min() - 0.2 <= lon <= self.lons.max() + 0.2):
            return None
        return int(np.abs(self.lats - lat).argmin()), int(np.abs(self.lons - lon).argmin())

    def as_dict(self) -> dict:
        return {"type": "latlon", "lats": self.lats.tolist(), "lons": self.lons.tolist()}


def _projection(meta: dict):
    m = dict(meta)
    kind = m.pop("type")
    return Lambert(**m) if kind == "lambert" else LatLon(m["lats"], m["lons"])


# --- Converting a forecast hour to the brief's units --------------------------------


def _fields(u, v, gust, precip_mm_hr, cpofp) -> dict[str, np.ndarray]:
    """`precip_mm_hr`: liquid-equivalent precipitation that fell in the hour (mm = kg/m^2)."""
    frozen = np.clip(np.where(cpofp < 0, 0, cpofp), 0, 100) / 100  # -50 = no precipitation
    rate = np.clip(precip_mm_hr, 0, None) / MM_PER_IN
    return {
        "wind": np.hypot(u, v) * MPH_PER_MS,
        "gust": gust * MPH_PER_MS,
        "rain": rate * (1 - frozen),
        "snow": rate * frozen * SNOW_TO_LIQUID,
    }


# --- Writing a run ---------------------------------------------------------------


class DiskTooFull(RuntimeError):
    pass


class _RunWriter:
    """Builds one run's arrays in a temp folder, then publishes it atomically.

    The download arrives an hour at a time (one US-wide sheet per hour), but trips read one
    point's whole forecast, so on publish the arrays are rewritten point-major: shape
    (rows, cols, hours), each point's hours next to each other on disk. A cold read of a
    checkpoint is then one small read instead of one per hour.
    """

    BLOCK_ROWS = 64  # rows rewritten at a time when publishing (~10 MB of memory)

    def __init__(self, model: str, run: datetime, valid: list[datetime], shape: tuple[int, int]):
        need = 2 * len(valid) * shape[0] * shape[1] * 2 * len(FIELDS)  # float16, hour- and point-major
        free = shutil.disk_usage(_root().parent if _root().exists() else Path(settings.CACHE_DIR)).free
        if free - need < MIN_FREE_BYTES:
            raise DiskTooFull(f"{model}: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free; skipping this run")
        self.model, self.run = model, run
        self.tmp = _root() / model / f".{run:%Y%m%d%H}.partial"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)
        self.valid = valid
        self.shape = shape
        self.arrays = {
            f: np.lib.format.open_memmap(self.tmp / f"{f}.hours.npy", "w+", np.float16, (len(valid), *shape))
            for f in FIELDS
        }

    def put(self, t: int, fields: dict[str, np.ndarray]) -> None:
        for f in FIELDS:
            self.arrays[f][t] = fields[f].astype(np.float16)

    def _point_major(self, f: str) -> None:
        hours = self.arrays[f]
        hours.flush()
        out = np.lib.format.open_memmap(self.tmp / f"{f}.npy", "w+", np.float16, (*self.shape, len(self.valid)))
        for r in range(0, self.shape[0], self.BLOCK_ROWS):
            out[r : r + self.BLOCK_ROWS] = np.moveaxis(np.asarray(hours[:, r : r + self.BLOCK_ROWS]), 0, -1)
        out.flush()
        del out
        self.arrays[f] = None
        del hours
        (self.tmp / f"{f}.hours.npy").unlink()

    def publish(self, projection) -> None:
        for f in FIELDS:
            self._point_major(f)
        meta = {
            "model": self.model,
            "run": self.run.isoformat(),
            "valid": [t.isoformat() for t in self.valid],
            "projection": projection.as_dict(),
            "layout": "point-major",
        }
        (self.tmp / "meta.json").write_text(json.dumps(meta))
        final = _root() / self.model / f"{self.run:%Y%m%d%H}"
        shutil.rmtree(final, ignore_errors=True)
        self.tmp.rename(final)
        runs = sorted(p for p in (_root() / self.model).iterdir() if p.is_dir() and not p.name.startswith("."))
        for old in runs[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)
        log.info("published %s run %s (%d hours)", self.model, self.run, len(self.valid))


def _latest_published(model: str) -> datetime | None:
    folder = _root() / model
    runs = sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")) if folder.exists() else []
    return datetime.strptime(runs[-1], "%Y%m%d%H").replace(tzinfo=timezone.utc) if runs else None


# --- HRRR -----------------------------------------------------------------------

HRRR_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{d:%Y%m%d}/conus/hrrr.t{d:%H}z.wrfsfcf{h:02d}.grib2"
HRRR_VARS = (":UGRD:10 m above ground:", ":VGRD:10 m above ground:", ":GUST:surface:", ":CPOFP:surface:")


def _hrrr_wanted(h: int) -> tuple[str, ...]:
    """Our fields, plus the precipitation that fell in the last hour: an instantaneous rate at
    the top of the hour can miss a thunderstorm burst that peaked at :40."""
    return HRRR_VARS + ((f":APCP:surface:{h - 1}-{h} hour acc fcst:",) if h > 0 else ())


def _idx_ranges(text: str, wanted: tuple[str, ...]) -> list[tuple[int, int | None]]:
    """Byte ranges of the wanted messages, from a GRIB .idx file. Averaged or accumulated
    fields only match when asked for by their exact time range."""
    lines = [ln.split(":", 2) for ln in text.strip().splitlines()]
    out = []
    for k, (_, start, rest) in enumerate(lines):
        line = f":{rest}"
        exact = any(w in line and ("acc" in w or "ave" in w) for w in wanted)
        plain = any(w in line for w in wanted) and "ave" not in rest and "acc" not in rest
        if exact or plain:
            end = int(lines[k + 1][1]) - 1 if k + 1 < len(lines) else None
            out.append((int(start), end))
    return out


def latest_hrrr_run(s: requests.Session, now: datetime, hours: int = HRRR_HOURS, every: int = 6) -> datetime | None:
    """Newest run (one every `every` hours) whose last forecast hour is already published."""
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % every)
    for _ in range(max(8, 12 // every)):
        url = HRRR_URL.format(d=t, h=hours) + ".idx"
        if s.head(url, timeout=30).status_code == 200:
            return t
        t -= timedelta(hours=every)
    return None


def refresh_hrrr(s: requests.Session, now: datetime, force: bool = False, model: str = "hrrr") -> bool:
    import eccodes

    hours, every = HRRR_MODELS[model]
    run = latest_hrrr_run(s, now, hours, every)
    if run is None or (run == _latest_published(model) and not force):
        return False
    writer = proj = None
    for h in range(hours + 1):
        url = HRRR_URL.format(d=run, h=h)
        ranges = _idx_ranges(_get(s, url + ".idx").text, _hrrr_wanted(h))
        raw = b"".join(_get(s, url, headers={"Range": f"bytes={a}-{'' if b is None else b}"}).content for a, b in ranges)
        handles = _decode(raw)
        try:
            by_name = {}
            for hd in handles:
                name = eccodes.codes_get(hd, "shortName")
                by_name[name] = eccodes.codes_get_values(hd).reshape(eccodes.codes_get(hd, "Ny"), eccodes.codes_get(hd, "Nx"))
            if writer is None:
                proj = Lambert.from_handle(handles[0])
                valid = [run + timedelta(hours=k) for k in range(hours + 1)]
                writer = _RunWriter(model, run, valid, (proj.ny, proj.nx))
            precip = by_name.get("tp", np.zeros_like(by_name["10u"]))  # APCP decodes as "tp"; none at hour 0
            writer.put(h, _fields(by_name["10u"], by_name["10v"], by_name["gust"], precip, by_name["cpofp"]))
        finally:
            for hd in handles:
                eccodes.codes_release(hd)
    writer.publish(proj)
    return True


# --- GFS ------------------------------------------------------------------------

GFS_FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_sflux.pl"
GFS_IDX = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{d:%Y%m%d}/{d:%H}/atmos/gfs.t{d:%H}z.sfluxgrbf{h:03d}.grib2.idx"
NOMADS_PAUSE_S = 0.6  # NOMADS blocks clients over ~120 requests a minute


def latest_gfs_run(s: requests.Session, now: datetime) -> datetime | None:
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % 6)
    for _ in range(8):
        if s.head(GFS_IDX.format(d=t, h=GFS_HOURS[-1]), timeout=30).status_code == 200:
            return t
        t -= timedelta(hours=6)
    return None


def _gfs_file(s: requests.Session, run: datetime, h: int) -> bytes:
    params = {
        "dir": f"/gfs.{run:%Y%m%d}/{run:%H}/atmos",
        "file": f"gfs.t{run:%H}z.sfluxgrbf{h:03d}.grib2",
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_PRATE": "on",
        "var_CPOFP": "on",
        "lev_10_m_above_ground": "on",
        "lev_surface": "on",
        "subregion": "",
        **CONUS,
    }
    time.sleep(NOMADS_PAUSE_S)
    return _get(s, GFS_FILTER, params=params).content


def refresh_gfs(s: requests.Session, now: datetime, force: bool = False) -> bool:
    """GFS precipitation is an average since the last 6-hour reset; convert it to hourly rates."""
    import eccodes

    run = latest_gfs_run(s, now)
    if run is None or (run == _latest_published("gfs") and not force):
        return False
    writer = proj = None
    prev_avg, prev_range = None, None
    for t, h in enumerate(GFS_HOURS):
        handles = _decode(_gfs_file(s, run, h))
        try:
            by_name, step = {}, None
            for hd in handles:
                name = eccodes.codes_get(hd, "shortName")
                vals = eccodes.codes_get_values(hd).reshape(eccodes.codes_get(hd, "Nj"), eccodes.codes_get(hd, "Ni"))
                by_name[name] = vals
                if name == "prate":
                    step = eccodes.codes_get(hd, "stepRange")  # e.g. "6-7" or "0"
            if writer is None:
                proj = LatLon.from_handle(handles[0])
                writer = _RunWriter("gfs", run, [run + timedelta(hours=k) for k in GFS_HOURS], by_name["10u"].shape)
            avg = by_name.get("prate", np.zeros_like(by_name["10u"]))
            a, b = (int(x) for x in step.split("-")) if step and "-" in step else (h, h)
            if prev_avg is not None and prev_range and prev_range[0] == a and b > prev_range[1]:
                # avg over [a,b] and [a,b'] share a start: rate over (b',b] = difference of accumulations
                rate = (avg * (b - a) - prev_avg * (prev_range[1] - a)) / (b - prev_range[1])
            else:
                rate = avg  # the averaging window starts here: the average is the rate
            prev_avg, prev_range = avg, (a, b)
            gust = np.full_like(avg, np.nan)  # the 13 km GFS files have no gusts; shown as unknown
            cpofp = by_name.get("cpofp", np.zeros_like(avg))
            writer.put(t, _fields(by_name["10u"], by_name["10v"], gust, rate * 3600, cpofp))  # kg/m^2/s -> mm/hr
        finally:
            for hd in handles:
                eccodes.codes_release(hd)
    writer.publish(proj)
    return True


def refresh(now: datetime | None = None, force: bool = False) -> dict[str, bool]:
    """Download any newer HRRR / GFS run. Safe to run often: unchanged runs are skipped
    (unless `force`, which rebuilds the current ones)."""
    now = now or datetime.now(timezone.utc)
    s = requests.Session()
    s.headers["User-Agent"] = settings.HTTP_USER_AGENT
    out = {}
    jobs = {
        "hrrr_hourly": lambda: refresh_hrrr(s, now, force, "hrrr_hourly"),
        "hrrr": lambda: refresh_hrrr(s, now, force, "hrrr"),
        "gfs": lambda: refresh_gfs(s, now, force),
    }
    for model, job in jobs.items():
        try:
            out[model] = job()
        except Exception:  # noqa: BLE001 - one model failing must not stop the other
            log.exception("refreshing %s failed", model)
            out[model] = False
    return out


# --- Reading --------------------------------------------------------------------


@dataclass
class _Run:
    model: str
    run: datetime
    valid: list[datetime]
    projection: Lambert | LatLon
    arrays: dict[str, np.ndarray]
    point_major: bool = True  # runs written before the layout change are hour-major

    def __post_init__(self):
        self._by_time = {t: k for k, t in enumerate(self.valid)}

    def step(self, when: datetime) -> int | None:
        """Index of the stored hour nearest `when` (within 2 h: GFS is 3-hourly after 120 h)."""
        if not (self.valid[0] <= when <= self.valid[-1]):
            return None
        for off in (0, -1, 1, -2, 2):
            if (k := self._by_time.get(when + timedelta(hours=off))) is not None:
                return k
        return None

    @classmethod
    def load(cls, folder: Path) -> "_Run":
        meta = json.loads((folder / "meta.json").read_text())
        return cls(
            meta["model"],
            datetime.fromisoformat(meta["run"]),
            [datetime.fromisoformat(t) for t in meta["valid"]],
            _projection(meta["projection"]),
            {f: np.load(folder / f"{f}.npy", mmap_mode="r") for f in FIELDS},
            meta.get("layout") == "point-major",
        )

    def series(self, lat: float, lon: float) -> dict[str, np.ndarray] | None:
        idx = self.projection.index(lat, lon)
        if idx is None:
            return None
        j, i = idx
        if self.point_major:
            return {f: np.asarray(a[j, i], dtype=np.float32) for f, a in self.arrays.items()}
        return {f: np.asarray(a[:, j, i], dtype=np.float32) for f, a in self.arrays.items()}


class Grids:
    """The newest HRRR and GFS runs on disk."""

    def __init__(self, runs: dict[str, _Run]):
        self.runs = runs

    @classmethod
    def current(cls) -> "Grids":
        runs = {}
        for model in LOOKUP_ORDER:
            folder = _root() / model
            if folder.exists():
                done = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
                if done:
                    runs[model] = _Run.load(done[-1])
        return cls(runs)

    def available(self) -> bool:
        return bool(self.runs)

    def describe(self) -> dict:
        return {m: {"run": r.run.isoformat(), "until": r.valid[-1].isoformat()} for m, r in self.runs.items()}

    def hourly(self, lat: float, lon: float, start: datetime, hours: int):
        """Per-hour (wind, gust, rain, snow, source) from `start`: HRRR where it reaches, else GFS.

        Returns None if the point is outside both grids (e.g. outside the US).
        """
        found = {m: (r, r.series(lat, lon)) for m, r in self.runs.items()}
        found = {m: v for m, v in found.items() if v[1] is not None}
        if not found:
            return None
        out = []
        for k in range(hours):
            when = start + timedelta(hours=k)
            value = None
            for model in LOOKUP_ORDER:  # freshest HRRR first: it sees storms
                if model in found and (t := found[model][0].step(when)) is not None:
                    series = found[model][1]
                    value = tuple(float(series[f][t]) for f in FIELDS) + (SOURCE[model],)
                    break
            out.append(value)
        return out

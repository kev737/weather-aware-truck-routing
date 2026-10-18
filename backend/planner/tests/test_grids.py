"""The NOAA grid store: writing a run, reading points, HRRR-before-GFS, and unit conversions."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from planner.services import grids as G
from planner.services import weather

RUN = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def write_run(model, run, hours, projection, wind, rain=0.0):
    shape = (len(projection.lats), len(projection.lons))
    w = G._RunWriter(model, run, [run + timedelta(hours=h) for h in hours], shape)
    for t, _ in enumerate(hours):
        w.put(t, {"wind": np.full(shape, wind), "gust": np.full(shape, np.nan), "rain": np.full(shape, rain), "snow": np.zeros(shape)})
    w.publish(projection)


@pytest.fixture
def box():
    return G.LatLon(lats=[30.0, 31.0, 32.0], lons=[-100.0, -99.0, -98.0])


def test_points_read_hrrr_where_it_reaches_then_gfs(box, settings):
    write_run("hrrr", RUN, range(0, 3), box, wind=40)  # HRRR: 12z-14z
    write_run("gfs", RUN, range(0, 6), box, wind=10)  # GFS: 12z-17z
    series = G.Grids.current().hourly(31.1, -99.1, RUN, 5)
    assert [v[0] for v in series] == [40, 40, 40, 10, 10]
    assert [v[4] for v in series] == ["hrrr", "hrrr", "hrrr", "gfs", "gfs"]


def test_points_outside_every_grid_are_none(box):
    write_run("gfs", RUN, range(0, 3), box, wind=10)
    assert G.Grids.current().hourly(48.85, 2.35, RUN, 3) is None  # Paris


def test_gfs_three_hourly_steps_cover_the_hours_between(box):
    write_run("gfs", RUN, [0, 3, 6], box, wind=10)
    series = G.Grids.current().hourly(31.0, -99.0, RUN, 7)
    assert all(v is not None for v in series)


def test_only_the_newest_run_is_kept(box):
    for k in range(4):
        write_run("gfs", RUN + timedelta(hours=6 * k), range(0, 2), box, wind=k)
    runs = sorted(p.name for p in (G._root() / "gfs").iterdir())
    assert runs == ["2026101906"]


def test_the_freshest_hrrr_run_wins_for_the_hours_it_covers(box):
    write_run("hrrr", RUN, range(0, 6), box, wind=30)  # 6-hourly run from 12z
    write_run("hrrr_hourly", RUN + timedelta(hours=2), range(0, 2), box, wind=45)  # 14z hourly run: 14z-15z
    series = G.Grids.current().hourly(31.0, -99.0, RUN, 5)
    assert [v[0] for v in series] == [30, 30, 45, 45, 30]  # 14z-15z from the fresher run
    assert {v[4] for v in series} == {"hrrr"}  # both are HRRR to the user


def test_a_download_that_would_fill_the_disk_is_skipped(box, monkeypatch):
    monkeypatch.setattr(G, "MIN_FREE_BYTES", 10**18)
    with pytest.raises(G.DiskTooFull):
        write_run("gfs", RUN, range(0, 2), box, wind=1)


def test_weather_uses_the_grids_and_says_which_model(box, settings):
    settings.USE_NOAA_GRIDS = True
    write_run("hrrr", RUN, range(0, 4), box, wind=50, rain=0.6)
    res = weather.forecasts({(31.0, -99.0)}, RUN, 3)
    fc = res.forecasts[(31.0, -99.0)]
    assert fc.at_index(1).wind_mph == pytest.approx(50, abs=0.1)
    assert fc.source_at(1) == "hrrr"
    assert fc.at_index(1).gust_mph is None  # no gusts stored: unknown, not invented


def test_unit_conversions():
    u, v = np.array([3.0]), np.array([4.0])  # 5 m/s
    f = G._fields(u, v, np.array([10.0]), precip_mm_hr=np.array([1.0]), cpofp=np.array([25.0]))
    assert f["wind"][0] == pytest.approx(5 * 2.23694)
    total_in_hr = 1 / 25.4  # 1 mm in an hour
    assert f["rain"][0] == pytest.approx(total_in_hr * 0.75)
    assert f["snow"][0] == pytest.approx(total_in_hr * 0.25 * 10)  # 10:1 snow-to-water
    assert G._fields(u, v, u, np.array([0.001]), np.array([-50.0]))["snow"][0] == 0  # -50 = no precip


def test_idx_ranges_pick_the_last_hours_precipitation():
    idx = "\n".join([
        "1:0:d=2026101812:UGRD:10 m above ground:6 hour fcst:",
        "2:100:d=2026101812:APCP:surface:0-6 hour acc fcst:",
        "3:250:d=2026101812:APCP:surface:5-6 hour acc fcst:",
        "4:400:d=2026101812:TMP:2 m above ground:6 hour fcst:",
    ])
    wanted = (":UGRD:10 m above ground:", ":APCP:surface:5-6 hour acc fcst:")
    assert G._idx_ranges(idx, wanted) == [(0, 99), (250, 399)]  # not the 0-6 h total
    assert G._hrrr_wanted(0)[-1] == ":CPOFP:surface:"  # no precipitation field at hour 0


def test_lambert_projection_round_trip():
    """HRRR's grid (values from a real file): its first point is cell (0, 0)."""
    p = G.Lambert(21.138123, 237.280472 - 360, 262.5 - 360, 38.5, 38.5, 38.5, 3000.0, 3000.0, 1799, 1059)
    assert p.index(21.138123, 237.280472 - 360) == (0, 0)
    assert p.index(48.85, 2.35) is None  # Paris is off the grid


def test_runs_are_stored_point_major_and_read_the_same(box):
    write_run("gfs", RUN, range(0, 4), box, wind=12)
    run = G.Grids.current().runs["gfs"]
    assert run.point_major and run.arrays["wind"].shape == (3, 3, 4)  # rows, cols, hours
    assert list(run.series(31.0, -99.0)["wind"]) == [12, 12, 12, 12]
    assert not list((G._root() / "gfs").glob("*/*.hours.npy"))  # the hour-major copy is gone

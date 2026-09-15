import json
from pathlib import Path

from motopark_bot.nearest import find_nearest
from motopark_bot.static_data import _parse_record

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_hdb_carpark.json").read_text())
CARPARKS = [cp for rec in FIXTURE if (cp := _parse_record(rec)) is not None]


def test_nearest_sorted_ascending_by_distance():
    # Somewhere in Ang Mo Kio, close to several AK*/AM* fixture carparks.
    results = find_nearest(1.370, 103.845, CARPARKS, limit=10)
    distances = [r.distance_km for r in results]
    assert distances == sorted(distances)


def test_nearest_respects_limit():
    results = find_nearest(1.370, 103.845, CARPARKS, limit=2)
    assert len(results) == 2


def test_max_radius_excludes_far_carparks():
    # Point far from every fixture carpark (out at sea, east of Changi).
    results = find_nearest(1.30, 104.20, CARPARKS, limit=10, max_radius_km=5.0)
    assert results == []


def test_closest_to_albert_centre_is_albert_centre_itself():
    acb = next(cp for cp in CARPARKS if cp.car_park_no == "ACB")
    results = find_nearest(acb.lat, acb.lon, CARPARKS, limit=1)
    assert results[0].info.car_park_no == "ACB"
    assert results[0].distance_km < 0.001

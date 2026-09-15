import json
from pathlib import Path

import pytest

from motopark_bot.static_data import _parse_record

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_hdb_carpark.json").read_text())


def _record(car_park_no: str) -> dict:
    for rec in FIXTURE:
        if rec["car_park_no"] == car_park_no:
            return rec
    raise KeyError(car_park_no)


def test_parses_basement_carpark_as_sheltered_and_paid():
    info = _parse_record(_record("ACB"))
    assert info is not None
    assert info.car_park_type == "BASEMENT CAR PARK"
    assert info.is_sheltered is True
    assert info.free_parking == "NO"
    assert info.is_free_anytime is False
    assert info.price_label == "paid"
    assert info.shelter_label == "sheltered"


def test_parses_surface_carpark_as_unsheltered():
    info = _parse_record(_record("AK19"))
    assert info is not None
    assert info.car_park_type == "SURFACE CAR PARK"
    assert info.is_sheltered is False
    assert info.shelter_label == "unsheltered"


def test_parses_conditional_free_parking():
    info = _parse_record(_record("ACM"))
    assert info is not None
    assert info.free_parking == "SUN & PH FR 7AM-10.30PM"
    assert info.is_free_anytime is True
    assert "sun & ph" in info.price_label.lower()


def test_multi_storey_is_sheltered():
    info = _parse_record(_record("AM14"))
    assert info is not None
    assert info.is_sheltered is True


def test_coords_convert_to_plausible_singapore_lat_lon():
    info = _parse_record(_record("ACB"))
    assert info is not None
    assert 1.1 < info.lat < 1.5  # Singapore's latitude band
    assert 103.6 < info.lon < 104.1  # Singapore's longitude band


def test_missing_coords_returns_none():
    broken = {"car_park_no": "XXX", "address": "NOWHERE"}
    assert _parse_record(broken) is None


def test_night_parking_flag_preserved():
    info = _parse_record(_record("AK19"))
    assert info is not None
    assert info.night_parking == "NO"
    info2 = _parse_record(_record("AK52"))
    assert info2 is not None
    assert info2.night_parking == "YES"

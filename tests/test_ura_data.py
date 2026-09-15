import json
from pathlib import Path

from motopark_bot.datagovsg import parse_geojson
from motopark_bot.ura_data import _build_capacity_index, _build_carparks

FIXTURES = Path(__file__).parent / "fixtures"

LOCATIONS = parse_geojson(json.loads((FIXTURES / "sample_ura_parking_lot.geojson.json").read_text()))
CAPACITY_FEATURES = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))


def test_capacity_index_keyed_by_pp_code():
    index = _build_capacity_index(CAPACITY_FEATURES)
    assert index["SP0001"] == (250, 5, 40)
    assert index["SP0002"] == (180, 2, 0)


def test_join_produces_carparks_with_capacity():
    index = _build_capacity_index(CAPACITY_FEATURES)
    carparks = _build_carparks(LOCATIONS, index)
    assert len(carparks) == 3

    orchard = next(cp for cp in carparks if cp.pp_code == "SP0001")
    assert orchard.name == "ORCHARD ROAD CARPARK"
    assert orchard.motorcycle_capacity == 40
    assert orchard.car_capacity == 250
    assert abs(orchard.lat - 1.3005) < 1e-9
    assert abs(orchard.lon - 103.8480) < 1e-9


def test_carpark_with_zero_motorcycle_capacity_is_zero_not_none():
    index = _build_capacity_index(CAPACITY_FEATURES)
    carparks = _build_carparks(LOCATIONS, index)
    marina = next(cp for cp in carparks if cp.pp_code == "SP0002")
    assert marina.motorcycle_capacity == 0


def test_carpark_with_no_matching_capacity_record_gets_none():
    index = _build_capacity_index(CAPACITY_FEATURES)
    carparks = _build_carparks(LOCATIONS, index)
    unmatched = next(cp for cp in carparks if cp.pp_code == "SP0003")
    assert unmatched.motorcycle_capacity is None
    assert unmatched.car_capacity is None


def test_car_park_no_and_address_aliases_for_matching_compat():
    index = _build_capacity_index(CAPACITY_FEATURES)
    carparks = _build_carparks(LOCATIONS, index)
    cp = carparks[0]
    assert cp.car_park_no == cp.pp_code
    assert cp.address == cp.name


def test_features_missing_pp_code_or_name_are_skipped():
    bad_locations = parse_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"PARKING_PL": "NO CODE"}},
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"PP_CODE": "X1"}},
            ],
        }
    )
    carparks = _build_carparks(bad_locations, {})
    assert carparks == []

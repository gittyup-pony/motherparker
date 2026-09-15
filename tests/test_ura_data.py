import asyncio
import json
from pathlib import Path

import pytest

from motopark_bot import ura_data
from motopark_bot.datagovsg import parse_geojson
from motopark_bot.ura_data import UraCarpark, UraCarparkStore, _build_capacity_index, _build_carparks

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


# --- UraCarparkStore backoff behavior ---------------------------------
#
# Regression coverage for a real production bug: when the URA dataset
# fails to parse (0 usable carparks), the store's cache stays permanently
# empty, which made _is_stale() permanently True. Without the retry
# backoff, that meant every single /check or /nearest call re-attempted
# the network fetch and re-raised the same RuntimeError, unhandled, all
# the way up through bot.py's location handler - see responses.py's
# _safe() for the other half of this fix.

_DUMMY_CARPARK = UraCarpark(
    pp_code="SP0001",
    name="ORCHARD ROAD CARPARK",
    lat=1.3005,
    lon=103.848,
    motorcycle_capacity=40,
    car_capacity=250,
    heavy_vehicle_capacity=5,
)


@pytest.mark.asyncio
async def test_store_raises_but_does_not_refetch_within_backoff_window(monkeypatch):
    call_count = 0

    async def fake_fetch_all_ura_carparks():
        nonlocal call_count
        call_count += 1
        return []  # simulates the real failure: 0 usable carparks parsed

    monkeypatch.setattr(ura_data, "fetch_all_ura_carparks", fake_fetch_all_ura_carparks)
    store = UraCarparkStore(retry_backoff_seconds=300)

    with pytest.raises(RuntimeError):
        await store.all()
    assert call_count == 1

    # A second call still within the backoff window must not hit the
    # network again - it should fail fast instead.
    with pytest.raises(RuntimeError):
        await store.all()
    assert call_count == 1


@pytest.mark.asyncio
async def test_store_retries_again_once_backoff_window_passes(monkeypatch):
    call_count = 0

    async def fake_fetch_all_ura_carparks():
        nonlocal call_count
        call_count += 1
        return [] if call_count == 1 else [_DUMMY_CARPARK]

    monkeypatch.setattr(ura_data, "fetch_all_ura_carparks", fake_fetch_all_ura_carparks)
    store = UraCarparkStore(retry_backoff_seconds=0.05)

    with pytest.raises(RuntimeError):
        await store.all()

    await asyncio.sleep(0.1)

    carparks = await store.all()
    assert call_count == 2
    assert carparks == [_DUMMY_CARPARK]


@pytest.mark.asyncio
async def test_forced_refresh_ignores_backoff(monkeypatch):
    call_count = 0

    async def fake_fetch_all_ura_carparks():
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(ura_data, "fetch_all_ura_carparks", fake_fetch_all_ura_carparks)
    store = UraCarparkStore(retry_backoff_seconds=300)

    with pytest.raises(RuntimeError):
        await store.refresh()
    with pytest.raises(RuntimeError):
        await store.refresh(force=True)
    assert call_count == 2

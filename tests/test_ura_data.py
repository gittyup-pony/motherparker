import asyncio
import json
from pathlib import Path

import pytest

from motopark_bot import ura_data
from motopark_bot.datagovsg import parse_geojson
from motopark_bot.ura_data import UraCarpark, UraCarparkStore, _build_carparks

FIXTURES = Path(__file__).parent / "fixtures"

# Confirmed against a real production sample (see ura_data.py's module
# docstring): the Capacity dataset alone has everything needed - name,
# location, and all three capacity counts - as one Point-geometry feature
# per carpark facility. The old "URA Parking Lot" dataset (see
# sample_ura_parking_lot.geojson.json, now repurposed below) turned out to
# be a per-lot Polygon layer and is no longer joined against.
CAPACITY_FEATURES = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))


def test_build_carparks_from_capacity_dataset():
    carparks = _build_carparks(CAPACITY_FEATURES)
    assert len(carparks) == 3

    orchard = next(cp for cp in carparks if cp.pp_code == "SP0001")
    assert orchard.name == "ORCHARD ROAD CARPARK"
    assert orchard.motorcycle_capacity == 40
    assert orchard.car_capacity == 250
    assert orchard.heavy_vehicle_capacity == 5
    assert abs(orchard.lat - 1.3005) < 1e-9
    assert abs(orchard.lon - 103.8480) < 1e-9


def test_carpark_with_zero_motorcycle_capacity_is_zero_not_none():
    carparks = _build_carparks(CAPACITY_FEATURES)
    marina = next(cp for cp in carparks if cp.pp_code == "SP0002")
    assert marina.motorcycle_capacity == 0


def test_carpark_with_missing_capacity_field_gets_none():
    # SP0003 in the fixture only has NO_CAR - NO_H_VEHIC/NO_MCYCLE are
    # simply absent from its properties, as can genuinely happen per-record.
    carparks = _build_carparks(CAPACITY_FEATURES)
    partial = next(cp for cp in carparks if cp.pp_code == "SP0003")
    assert partial.car_capacity == 60
    assert partial.heavy_vehicle_capacity is None
    assert partial.motorcycle_capacity is None


def test_carpark_name_is_stripped_of_whitespace():
    # Real data has trailing whitespace on some PARKING_PL values (e.g.
    # "Marsiling Crescent Heavy Vehicle Park ") - the fixture's SP0003
    # mirrors that.
    carparks = _build_carparks(CAPACITY_FEATURES)
    partial = next(cp for cp in carparks if cp.pp_code == "SP0003")
    assert partial.name == "PARTIAL DATA CARPARK"


def test_car_park_no_and_address_aliases_for_matching_compat():
    carparks = _build_carparks(CAPACITY_FEATURES)
    cp = carparks[0]
    assert cp.car_park_no == cp.pp_code
    assert cp.address == cp.name


def test_features_missing_pp_code_or_name_are_skipped():
    bad_features = parse_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"PARKING_PL": "NO CODE"}},
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"PP_CODE": "X1"}},
            ],
        }
    )
    carparks = _build_carparks(bad_features)
    assert carparks == []


def test_real_parking_lot_shape_parses_to_zero_point_features():
    """Regression test for the actual production bug.

    sample_ura_parking_lot.geojson.json is a verbatim copy of a real
    feature from the "URA Parking Lot" dataset: Polygon geometry (one
    physical lot), not Point (one carpark facility). parse_geojson()
    only keeps Point geometries, so this - correctly - parses to zero
    features. This is exactly why the original two-dataset join produced
    "Fetched 0 usable URA carparks": every feature from this dataset was
    silently dropped before _build_carparks ever saw it. Kept as a
    regression guard now that the join has been removed in favor of using
    the Capacity dataset alone.
    """
    raw = json.loads((FIXTURES / "sample_ura_parking_lot.geojson.json").read_text())
    assert raw["features"][0]["geometry"]["type"] == "Polygon"
    features = parse_geojson(raw)
    assert features == []


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
async def test_log_raw_feature_sample_logs_ground_truth_for_both_datasets(monkeypatch, caplog):
    # Regression coverage for the Render-free-tier-has-no-Shell problem:
    # `python -m motopark_bot.ura_data` (the __main__ smoke test) can't be
    # run interactively there, so this diagnostic logs the same
    # information at WARNING level instead, for the (free) Logs tab.
    seen_dataset_ids = []

    async def fake_fetch_raw_geojson(dataset_id):
        seen_dataset_ids.append(dataset_id)
        return {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"SOME_KEY": "some_value"}}],
        }

    monkeypatch.setattr(ura_data, "fetch_raw_geojson", fake_fetch_raw_geojson)

    with caplog.at_level("WARNING"):
        await ura_data.log_raw_feature_sample()

    assert seen_dataset_ids == [ura_data.PARKING_LOT_DATASET_ID, ura_data.CAPACITY_DATASET_ID]
    assert "URA diagnostic" in caplog.text
    assert "SOME_KEY" in caplog.text


@pytest.mark.asyncio
async def test_log_raw_feature_sample_survives_fetch_failure(monkeypatch, caplog):
    async def fake_fetch_raw_geojson(dataset_id):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(ura_data, "fetch_raw_geojson", fake_fetch_raw_geojson)

    with caplog.at_level("WARNING"):
        await ura_data.log_raw_feature_sample()  # must not raise

    assert "raw fetch itself failed" in caplog.text


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

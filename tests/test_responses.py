"""Integration-style tests for the /check and /nearest merge logic.

Each store is pre-populated directly (bypassing its network refresh) using
the same fixture data the other test modules already validated in
isolation, so these tests focus purely on the three-source merge and
live-data-joining behavior in responses.py.
"""
import json
import time
from pathlib import Path

import pytest

from motopark_bot import lta_client
from motopark_bot.carpark_rates_data import CarparkRatesStore, _parse_row as parse_rate_row
from motopark_bot.datagovsg import parse_csv_text, parse_geojson
from motopark_bot.lta_client import LiveAvailabilityStore, LiveLot, _parse_location
from motopark_bot.responses import build_check_response, build_nearest_response
from motopark_bot.static_data import StaticCarparkStore, _parse_record
from motopark_bot.ura_data import UraCarparkStore, _build_capacity_index, _build_carparks

FIXTURES = Path(__file__).parent / "fixtures"


def _fresh_static_store() -> StaticCarparkStore:
    records = json.loads((FIXTURES / "sample_hdb_carpark.json").read_text())
    carparks = {cp.car_park_no: cp for rec in records if (cp := _parse_record(rec)) is not None}
    store = StaticCarparkStore()
    store._by_no = carparks
    store._fetched_at = time.monotonic()
    return store


def _fresh_ura_store() -> UraCarparkStore:
    locations = parse_geojson(json.loads((FIXTURES / "sample_ura_parking_lot.geojson.json").read_text()))
    capacity = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))
    carparks = _build_carparks(locations, _build_capacity_index(capacity))
    store = UraCarparkStore()
    store._carparks = carparks
    store._fetched_at = time.monotonic()
    return store


def _fresh_rates_store() -> CarparkRatesStore:
    rows = parse_csv_text((FIXTURES / "sample_carpark_rates.csv").read_text())
    entries = [e for row in rows if (e := parse_rate_row(row)) is not None]
    store = CarparkRatesStore()
    store._entries = entries
    store._fetched_at = time.monotonic()
    return store


def _fresh_live_store(lots_by_id: dict[str, LiveLot]) -> LiveAvailabilityStore:
    store = LiveAvailabilityStore(account_key="fake")
    store._by_carpark_id = lots_by_id
    store._fetched_at = time.monotonic()
    return store


def _empty_live_store(monkeypatch: pytest.MonkeyPatch) -> LiveAvailabilityStore:
    """A live store with no data for anything.

    An empty `_by_carpark_id` dict makes LiveAvailabilityStore.refresh()
    treat itself as "never successfully fetched" (its own truthiness guard
    against caching a broken empty fetch - see lta_client.py), so it would
    otherwise try a real network call on the next .get()/find_by_... call.
    Patch the underlying fetch to a real empty result instead, so "genuinely
    fetched, nothing available" is what actually gets exercised.
    """

    async def fake_fetch_all_live_lots(account_key: str):
        return []

    monkeypatch.setattr(lta_client, "fetch_all_live_lots", fake_fetch_all_live_lots)
    return _fresh_live_store({})


@pytest.mark.asyncio
async def test_check_combines_all_three_sources(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    # "orchard" matches ORCHARD ROAD CARPARK (URA) directly; won't match
    # anything in the HDB or rates fixtures, which is fine - just proves
    # partial-source matches don't break the combined response.
    text = await build_check_response("orchard", static_store, ura_store, rates_store, live_store)
    assert "ORCHARD ROAD CARPARK" in text
    assert "SP0001" in text


@pytest.mark.asyncio
async def test_check_hdb_match_includes_live_data_when_carpark_id_matches():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live = LiveLot(car_park_id="ACB", development="ALBERT CENTRE", lat=1.3, lon=103.85, available_lots=7, lot_type="Y", agency="HDB")
    live_store = _fresh_live_store({"ACB": live})

    text = await build_check_response("albert centre", static_store, ura_store, rates_store, live_store)
    assert "ALBERT CENTRE" in text
    assert "*7*" in text


@pytest.mark.asyncio
async def test_check_ura_match_falls_back_to_capacity_without_live_join(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)  # SP0001 has no live entry -> capacity fallback

    text = await build_check_response("orchard road carpark", static_store, ura_store, rates_store, live_store)
    assert "40 motorcycle bays" in text
    assert "capacity" in text


@pytest.mark.asyncio
async def test_check_rate_entry_gets_live_data_via_fuzzy_development_name_match():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    # Suntec City is in the rates fixture with no ID; live feed has a
    # matching Development name for it to fuzzy-match against.
    live = LiveLot(car_park_id="SUN1", development="SUNTEC CITY MALL", lat=1.29, lon=103.86, available_lots=12, lot_type="Y", agency="LTA")
    live_store = _fresh_live_store({"SUN1": live})

    text = await build_check_response("suntec", static_store, ura_store, rates_store, live_store)
    assert "Suntec City" in text
    assert "*12*" in text


@pytest.mark.asyncio
async def test_check_no_matches_anywhere_returns_no_match_message(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    text = await build_check_response("totally nonexistent zzz place", static_store, ura_store, rates_store, live_store)
    assert "No carparks matched" in text


@pytest.mark.asyncio
async def test_nearest_combines_hdb_and_ura_sorted_by_distance(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    # Query point right on top of ORCHARD ROAD CARPARK (URA, SP0001).
    text = await build_nearest_response(
        1.3005, 103.8480, static_store, ura_store, live_store, limit=5, max_radius_km=50.0
    )
    assert "ORCHARD ROAD CARPARK" in text
    # First block should be the URA carpark since it's the closest (0 km away).
    assert text.split("\n\n")[0].startswith("*ORCHARD ROAD CARPARK*")


@pytest.mark.asyncio
async def test_nearest_respects_max_radius(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    # Middle of the ocean, nowhere near any fixture carpark.
    text = await build_nearest_response(
        1.0, 103.0, static_store, ura_store, live_store, limit=5, max_radius_km=1.0
    )
    assert "No carparks found" in text


# --- Resilience: a broken optional source must not take down the whole
# reply. This is a direct regression test for a real production incident:
# the URA dataset failed to parse (see ura_data.py's backoff comment), and
# without the _safe() wrapping in responses.py, that RuntimeError
# propagated unhandled through build_nearest_response -> bot.py's location
# handler, so /nearest (and /check) stopped replying at all - even though
# HDB data (the bot's actual core) was working the whole time.


class _BrokenStore:
    """Stands in for ura_store/rates_store when their data source is down."""

    async def all(self):
        raise RuntimeError("simulated data source failure")


class _BrokenLiveStore:
    """Stands in for live_store when the LTA feed is down/misconfigured."""

    async def get(self, car_park_id: str):
        raise RuntimeError("simulated LTA failure")

    async def find_by_development_name(self, query: str, limit: int = 3):
        raise RuntimeError("simulated LTA failure")


@pytest.mark.asyncio
async def test_check_survives_broken_ura_store(monkeypatch):
    static_store = _fresh_static_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    text = await build_check_response("albert centre", static_store, _BrokenStore(), rates_store, live_store)
    assert "ALBERT CENTRE" in text


@pytest.mark.asyncio
async def test_check_survives_broken_rates_store(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    text = await build_check_response("orchard road carpark", static_store, ura_store, _BrokenStore(), live_store)
    assert "ORCHARD ROAD CARPARK" in text


@pytest.mark.asyncio
async def test_check_survives_broken_live_store():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()

    text = await build_check_response("albert centre", static_store, ura_store, rates_store, _BrokenLiveStore())
    assert "ALBERT CENTRE" in text
    assert "no live data" in text


@pytest.mark.asyncio
async def test_nearest_survives_broken_ura_store(monkeypatch):
    static_store = _fresh_static_store()
    live_store = _empty_live_store(monkeypatch)

    # Right on top of ALBERT CENTRE's fixture coordinates.
    text = await build_nearest_response(
        1.301059, 103.855409, static_store, _BrokenStore(), live_store, limit=5, max_radius_km=50.0
    )
    assert "ALBERT CENTRE" in text


@pytest.mark.asyncio
async def test_nearest_survives_broken_live_store():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()

    text = await build_nearest_response(
        1.301059, 103.855409, static_store, ura_store, _BrokenLiveStore(), limit=5, max_radius_km=50.0
    )
    assert "ALBERT CENTRE" in text
    assert "no live data" in text

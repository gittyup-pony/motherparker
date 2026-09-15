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
from motopark_bot.ura_data import UraCarparkStore, _build_carparks

FIXTURES = Path(__file__).parent / "fixtures"


def _fresh_static_store() -> StaticCarparkStore:
    records = json.loads((FIXTURES / "sample_hdb_carpark.json").read_text())
    carparks = {cp.car_park_no: cp for rec in records if (cp := _parse_record(rec)) is not None}
    store = StaticCarparkStore()
    store._by_no = carparks
    store._fetched_at = time.monotonic()
    return store


def _fresh_ura_store() -> UraCarparkStore:
    # Confirmed against real production data: the Capacity dataset alone
    # has everything (name, location, capacity) - see ura_data.py's module
    # docstring for why the old two-dataset join was dropped.
    capacity = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))
    carparks = _build_carparks(capacity)
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
    reply = await build_check_response("orchard", static_store, ura_store, rates_store, live_store)
    assert "ORCHARD ROAD CARPARK" in reply.text


@pytest.mark.asyncio
async def test_check_hdb_match_includes_live_data_when_carpark_id_matches():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live = LiveLot(car_park_id="ACB", development="ALBERT CENTRE", lat=1.3, lon=103.85, available_lots=7, lot_type="Y", agency="HDB")
    live_store = _fresh_live_store({"ACB": live})

    reply = await build_check_response("albert centre", static_store, ura_store, rates_store, live_store)
    # Exactly the four streamlined fields - address, paid/free, availability
    # state - and specifically NOT the raw available_lots count (7).
    assert reply.text == "*BLK 270/271 ALBERT CENTRE BASEMENT CAR PARK*\n💰 Paid\n✅ Available"


@pytest.mark.asyncio
async def test_check_ura_match_without_live_join_shows_no_live_data(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)  # SP0001 has no live entry

    reply = await build_check_response("orchard road carpark", static_store, ura_store, rates_store, live_store)
    assert "no live data" in reply.text
    assert "40" not in reply.text  # capacity number never shown


@pytest.mark.asyncio
async def test_check_rate_entry_gets_live_data_via_fuzzy_development_name_match():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    # Suntec City is in the rates fixture with no ID; live feed has a
    # matching Development name for it to fuzzy-match against.
    live = LiveLot(car_park_id="SUN1", development="SUNTEC CITY MALL", lat=1.29, lon=103.86, available_lots=12, lot_type="Y", agency="LTA")
    live_store = _fresh_live_store({"SUN1": live})

    reply = await build_check_response("suntec", static_store, ura_store, rates_store, live_store)
    assert "Suntec City" in reply.text
    assert "Available" in reply.text
    assert "12" not in reply.text  # raw lot count never shown, just the state


@pytest.mark.asyncio
async def test_check_no_matches_anywhere_returns_no_match_message(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    reply = await build_check_response("totally nonexistent zzz place", static_store, ura_store, rates_store, live_store)
    assert "No carparks matched" in reply.text
    assert reply.nav_targets == []


@pytest.mark.asyncio
async def test_nearest_combines_hdb_and_ura_sorted_by_distance(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    # Query point right on top of ORCHARD ROAD CARPARK (URA, SP0001).
    reply = await build_nearest_response(
        1.3005, 103.8480, static_store, ura_store, live_store, limit=5, max_radius_km=50.0
    )
    assert "ORCHARD ROAD CARPARK" in reply.text
    # First block should be the URA carpark since it's the closest (0 km away).
    assert reply.text.split("\n\n")[0].startswith("*ORCHARD ROAD CARPARK*")


@pytest.mark.asyncio
async def test_nearest_respects_max_radius(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    # Middle of the ocean, nowhere near any fixture carpark.
    reply = await build_nearest_response(
        1.0, 103.0, static_store, ura_store, live_store, limit=5, max_radius_km=1.0
    )
    assert "No carparks found" in reply.text
    assert reply.nav_targets == []


# --- Nav targets: one "Navigate" button's worth of data per result that
# has coordinates. Carpark Rates entries never contribute one (no lat/lon
# in that dataset), everything else does. bot.py turns these into Google
# Maps deep-link buttons - see build_nav_keyboard() in bot.py.


@pytest.mark.asyncio
async def test_check_nav_targets_cover_hdb_and_ura_but_not_rates(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    # "a" is broad enough to plausibly match across sources; what matters
    # here is just that HDB/URA matches get nav targets and rate matches
    # don't, not the exact match set.
    reply = await build_check_response("albert centre", static_store, ura_store, rates_store, live_store)
    assert len(reply.nav_targets) >= 1
    albert = next(t for t in reply.nav_targets if "ALBERT CENTRE" in t.label)
    # Not pinning the exact SVY21->WGS84 conversion output here (test_geo.py
    # already covers that) - just confirming it's a real, plausible
    # Singapore coordinate rather than 0/None/something unconverted.
    assert 1.0 < albert.lat < 1.5
    assert 103.5 < albert.lon < 104.5


@pytest.mark.asyncio
async def test_check_rate_only_match_has_no_nav_targets(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    # "suntec" only matches the Carpark Rates fixture entry - no HDB/URA
    # carpark named that, so there's nothing with coordinates to link to.
    reply = await build_check_response("suntec", static_store, ura_store, rates_store, live_store)
    assert "Suntec City" in reply.text
    assert reply.nav_targets == []


@pytest.mark.asyncio
async def test_nearest_nav_targets_match_ranked_results():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _fresh_live_store({})

    reply = await build_nearest_response(
        1.3005, 103.8480, static_store, ura_store, live_store, limit=5, max_radius_km=50.0
    )
    assert reply.nav_targets
    orchard = next(t for t in reply.nav_targets if "ORCHARD ROAD CARPARK" in t.label)
    assert abs(orchard.lat - 1.3005) < 1e-9
    assert abs(orchard.lon - 103.8480) < 1e-9


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

    reply = await build_check_response("albert centre", static_store, _BrokenStore(), rates_store, live_store)
    assert "ALBERT CENTRE" in reply.text


@pytest.mark.asyncio
async def test_check_survives_broken_rates_store(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    live_store = _empty_live_store(monkeypatch)

    reply = await build_check_response("orchard road carpark", static_store, ura_store, _BrokenStore(), live_store)
    assert "ORCHARD ROAD CARPARK" in reply.text


@pytest.mark.asyncio
async def test_check_survives_broken_live_store():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()

    reply = await build_check_response("albert centre", static_store, ura_store, rates_store, _BrokenLiveStore())
    assert "ALBERT CENTRE" in reply.text
    assert "no live data" in reply.text


@pytest.mark.asyncio
async def test_nearest_survives_broken_ura_store(monkeypatch):
    static_store = _fresh_static_store()
    live_store = _empty_live_store(monkeypatch)

    # Right on top of ALBERT CENTRE's fixture coordinates.
    reply = await build_nearest_response(
        1.301059, 103.855409, static_store, _BrokenStore(), live_store, limit=5, max_radius_km=50.0
    )
    assert "ALBERT CENTRE" in reply.text


@pytest.mark.asyncio
async def test_nearest_survives_broken_live_store():
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()

    reply = await build_nearest_response(
        1.301059, 103.855409, static_store, ura_store, _BrokenLiveStore(), limit=5, max_radius_km=50.0
    )
    assert "ALBERT CENTRE" in reply.text
    assert "no live data" in reply.text


# --- Postal-code search: /check with a bare 6-digit postal code geocodes
# it via OneMap and then delegates entirely to build_nearest_response. Uses
# a fake stand-in for OneMapClient (same _Broken*Store pattern as above)
# rather than a real OneMapClient, since real credentials/network aren't
# available in tests - see onemap.py's module docstring.


class _FakeOneMapClient:
    """Stands in for onemap.OneMapClient - geocode_postal_code() only."""

    def __init__(self, point):
        self._point = point

    async def geocode_postal_code(self, postal_code: str):
        return self._point


class _BrokenOneMapClient:
    async def geocode_postal_code(self, postal_code: str):
        raise RuntimeError("simulated OneMap failure")


@pytest.mark.asyncio
async def test_check_postal_code_not_configured_without_onemap_client(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    reply = await build_check_response("238801", static_store, ura_store, rates_store, live_store)
    assert "isn't set up" in reply.text
    assert reply.nav_targets == []


@pytest.mark.asyncio
async def test_check_postal_code_not_found(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    reply = await build_check_response(
        "999999", static_store, ura_store, rates_store, live_store, _FakeOneMapClient(None)
    )
    assert "Couldn't find that postal code" in reply.text
    assert reply.nav_targets == []


@pytest.mark.asyncio
async def test_check_postal_code_geocodes_then_searches_nearby(monkeypatch):
    from motopark_bot.onemap import GeoPoint

    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    # Right on top of ALBERT CENTRE's fixture coordinates.
    onemap_client = _FakeOneMapClient(GeoPoint(lat=1.301059, lon=103.855409))
    reply = await build_check_response(
        "  238801  ", static_store, ura_store, rates_store, live_store, onemap_client
    )
    assert "ALBERT CENTRE" in reply.text
    assert reply.nav_targets  # a /nearest-style reply carries nav targets


@pytest.mark.asyncio
async def test_check_postal_code_survives_broken_onemap_client(monkeypatch):
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _empty_live_store(monkeypatch)

    reply = await build_check_response(
        "238801", static_store, ura_store, rates_store, live_store, _BrokenOneMapClient()
    )
    assert "Couldn't find that postal code" in reply.text


@pytest.mark.asyncio
async def test_check_non_postal_code_query_is_unaffected_by_onemap_client():
    # A name query must never be routed through OneMap, even when a client
    # is configured - is_postal_code() gates the whole branch.
    static_store = _fresh_static_store()
    ura_store = _fresh_ura_store()
    rates_store = _fresh_rates_store()
    live_store = _fresh_live_store({})

    reply = await build_check_response(
        "albert centre", static_store, ura_store, rates_store, live_store, _BrokenOneMapClient()
    )
    assert "ALBERT CENTRE" in reply.text

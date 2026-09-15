import json
from pathlib import Path

import pytest

from motopark_bot import lta_client
from motopark_bot.lta_client import LiveAvailabilityStore, _parse_location

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_lta_availability.json").read_text())


def test_parse_location_valid():
    assert _parse_location("1.301059 103.855409") == (1.301059, 103.855409)


def test_parse_location_malformed_returns_none():
    assert _parse_location("") is None
    assert _parse_location("1.3") is None
    assert _parse_location("1.3 103.8 extra") is None
    assert _parse_location("abc def") is None


@pytest.mark.asyncio
async def test_live_store_groups_by_carpark_id_and_filters_motorcycle(monkeypatch):
    async def fake_fetch_all_live_lots(account_key: str):
        # Reuse the real parsing path so this also exercises record parsing,
        # not just the store's grouping logic.
        records = FIXTURE["value"]
        lots = []
        for rec in records:
            loc = _parse_location(rec["Location"])
            lots.append(
                lta_client.LiveLot(
                    car_park_id=rec["CarParkID"],
                    development=rec["Development"],
                    lat=loc[0],
                    lon=loc[1],
                    available_lots=int(rec["AvailableLots"]),
                    lot_type=rec["LotType"],
                    agency=rec["Agency"],
                )
            )
        return lots

    monkeypatch.setattr(lta_client, "fetch_all_live_lots", fake_fetch_all_live_lots)

    store = LiveAvailabilityStore(account_key="fake", ttl_seconds=45)
    await store.refresh(force=True)

    acb = await store.get("ACB")
    assert acb is not None
    assert acb.available_lots == 3  # the Y (motorcycle) record, not the C (car) one
    assert acb.lot_type == "Y"

    ak19 = await store.get("AK19")
    assert ak19 is not None
    assert ak19.available_lots == 0  # full, but still present (not None)

    # AK31 only has an H (heavy vehicle) record in the fixture -> no
    # motorcycle data, so it should NOT show up.
    assert await store.get("AK31") is None


@pytest.mark.asyncio
async def test_live_store_raises_if_no_motorcycle_lots_seen_at_all(monkeypatch):
    async def fake_fetch_all_live_lots(account_key: str):
        return [
            lta_client.LiveLot(
                car_park_id="X",
                development="X",
                lat=1.3,
                lon=103.8,
                available_lots=5,
                lot_type="C",
                agency="HDB",
            )
        ]

    monkeypatch.setattr(lta_client, "fetch_all_live_lots", fake_fetch_all_live_lots)

    store = LiveAvailabilityStore(account_key="fake")
    with pytest.raises(RuntimeError, match="MOTORCYCLE_LOT_TYPES"):
        await store.refresh(force=True)


@pytest.mark.asyncio
async def test_find_by_development_name_matches_and_ranks(monkeypatch):
    # Same fake fetch as the grouping test above - reused inline since it's
    # only a few lines and keeps this test self-contained.
    async def fake_fetch_all_live_lots(account_key: str):
        records = FIXTURE["value"]
        lots = []
        for rec in records:
            loc = _parse_location(rec["Location"])
            lots.append(
                lta_client.LiveLot(
                    car_park_id=rec["CarParkID"],
                    development=rec["Development"],
                    lat=loc[0],
                    lon=loc[1],
                    available_lots=int(rec["AvailableLots"]),
                    lot_type=rec["LotType"],
                    agency=rec["Agency"],
                )
            )
        return lots

    monkeypatch.setattr(lta_client, "fetch_all_live_lots", fake_fetch_all_live_lots)

    store = LiveAvailabilityStore(account_key="fake")

    # "ALBERT CENTRE" is in ACB's Development string in the fixture.
    results = await store.find_by_development_name("albert centre")
    assert results
    assert results[0].car_park_id == "ACB"

    # No match anywhere in any Development string.
    assert await store.find_by_development_name("nonexistent mall xyz") == []

    # Empty query never matches everything.
    assert await store.find_by_development_name("") == []

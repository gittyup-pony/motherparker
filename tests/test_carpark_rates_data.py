import asyncio
from pathlib import Path

import pytest

from motopark_bot import carpark_rates_data
from motopark_bot.carpark_rates_data import CarparkRatesStore, RateEntry, _parse_row
from motopark_bot.datagovsg import parse_csv_text

FIXTURES = Path(__file__).parent / "fixtures"
ROWS = parse_csv_text((FIXTURES / "sample_carpark_rates.csv").read_text())


def test_parses_all_rows():
    entries = [e for row in ROWS if (e := _parse_row(row)) is not None]
    assert len(entries) == 3
    names = {e.name for e in entries}
    assert names == {"ION Orchard", "Suntec City", "Tampines Mall"}


def test_rate_fields_populated():
    entries = [e for row in ROWS if (e := _parse_row(row)) is not None]
    ion = next(e for e in entries if e.name == "ION Orchard")
    assert ion.category == "Orchard"
    assert "1.20" in ion.weekday_rate_1
    assert "1.20" in ion.sunday_ph_rate


def test_car_park_no_always_empty_and_address_aliases_name():
    entries = [e for row in ROWS if (e := _parse_row(row)) is not None]
    for e in entries:
        assert e.car_park_no == ""
        assert e.address == e.name


def test_row_with_no_name_is_skipped():
    assert _parse_row({"Category": "Nowhere"}) is None
    assert _parse_row({"Carpark": "", "Category": "Nowhere"}) is None


# --- CarparkRatesStore backoff behavior --------------------------------
# Same regression class as UraCarparkStore's backoff tests in
# test_ura_data.py - see that file's comment for the production bug this
# guards against.

_DUMMY_ENTRY = RateEntry(
    name="Suntec City",
    category="Marina",
    weekday_rate_1="$1.00 per 30 min",
    weekday_rate_2="$1.00 per 30 min",
    saturday_rate="$1.20 per 30 min",
    sunday_ph_rate="$1.20 per 30 min",
)


@pytest.mark.asyncio
async def test_store_raises_but_does_not_refetch_within_backoff_window(monkeypatch):
    call_count = 0

    async def fake_fetch_all_rate_entries():
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(carpark_rates_data, "fetch_all_rate_entries", fake_fetch_all_rate_entries)
    store = CarparkRatesStore(retry_backoff_seconds=300)

    with pytest.raises(RuntimeError):
        await store.all()
    assert call_count == 1

    with pytest.raises(RuntimeError):
        await store.all()
    assert call_count == 1


@pytest.mark.asyncio
async def test_store_retries_again_once_backoff_window_passes(monkeypatch):
    call_count = 0

    async def fake_fetch_all_rate_entries():
        nonlocal call_count
        call_count += 1
        return [] if call_count == 1 else [_DUMMY_ENTRY]

    monkeypatch.setattr(carpark_rates_data, "fetch_all_rate_entries", fake_fetch_all_rate_entries)
    store = CarparkRatesStore(retry_backoff_seconds=0.05)

    with pytest.raises(RuntimeError):
        await store.all()

    await asyncio.sleep(0.1)

    entries = await store.all()
    assert call_count == 2
    assert entries == [_DUMMY_ENTRY]

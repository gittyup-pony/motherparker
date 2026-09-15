from pathlib import Path

from motopark_bot.carpark_rates_data import _parse_row
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

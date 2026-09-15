import json
from pathlib import Path

from motopark_bot.carpark_rates_data import _parse_row as parse_rate_row
from motopark_bot.datagovsg import parse_csv_text, parse_geojson
from motopark_bot.matching import rank_matches
from motopark_bot.static_data import _parse_record
from motopark_bot.ura_data import _build_capacity_index, _build_carparks

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "sample_hdb_carpark.json").read_text())
CARPARKS = [cp for rec in FIXTURE if (cp := _parse_record(rec)) is not None]


def test_exact_street_match_ranks_first():
    results = rank_matches("aljunied crescent", CARPARKS, limit=3)
    assert results
    assert results[0].car_park_no == "ACM"


def test_carpark_code_matches_directly():
    results = rank_matches("AK19", CARPARKS, limit=3)
    assert results
    assert results[0].car_park_no == "AK19"


def test_no_match_returns_empty():
    results = rank_matches("this matches nothing at all zzz", CARPARKS)
    assert results == []


def test_empty_query_returns_empty():
    assert rank_matches("", CARPARKS) == []
    assert rank_matches("   ", CARPARKS) == []


def test_partial_token_match_finds_ang_mo_kio_carparks():
    results = rank_matches("ang mo kio", CARPARKS, limit=10)
    codes = {r.car_park_no for r in results}
    # All AK*/AM* fixture records are Ang Mo Kio addresses.
    assert {"AK19", "AK31", "AK52", "AK9", "AM14"}.issubset(codes)


# --- Cross-type reuse: rank_matches() also has to work, unmodified, on
# UraCarpark and RateEntry (via their car_park_no/address alias properties)
# since bot.py runs the same function against all three static sources. ---


def test_rank_matches_works_on_ura_carparks_via_aliases():
    locations = parse_geojson(json.loads((FIXTURES / "sample_ura_parking_lot.geojson.json").read_text()))
    capacity = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))
    ura_carparks = _build_carparks(locations, _build_capacity_index(capacity))

    results = rank_matches("orchard road", ura_carparks, limit=3)
    assert results
    assert results[0].pp_code == "SP0001"

    # Code-match path too (relies on car_park_no alias == pp_code).
    results2 = rank_matches("SP0002", ura_carparks, limit=3)
    assert results2
    assert results2[0].pp_code == "SP0002"


def test_rank_matches_works_on_rate_entries_via_aliases():
    rows = parse_csv_text((FIXTURES / "sample_carpark_rates.csv").read_text())
    entries = [e for row in rows if (e := parse_rate_row(row)) is not None]

    results = rank_matches("suntec", entries, limit=3)
    assert results
    assert results[0].name == "Suntec City"

    # RateEntry.car_park_no is always "" - querying an empty string must
    # never accidentally "match" every entry via the code-match branch.
    assert rank_matches("", entries) == []

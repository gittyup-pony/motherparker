import json
from dataclasses import dataclass
from pathlib import Path

from motopark_bot.carpark_rates_data import _parse_row as parse_rate_row
from motopark_bot.datagovsg import parse_csv_text, parse_geojson
from motopark_bot.matching import rank_matches, score_text_match
from motopark_bot.static_data import _parse_record
from motopark_bot.ura_data import _build_carparks

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
    capacity = parse_geojson(json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text()))
    ura_carparks = _build_carparks(capacity)

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


# --- Multi-word queries are one phrase, not a bag of independently-scored
# words. A query like "ang mo kio" must only match addresses containing
# that whole sequence together - not addresses that merely contain "ang"
# and "kio" scattered in unrelated places (the old token-overlap scoring's
# behavior, which this was explicitly changed to stop doing).


@dataclass
class _Item:
    address: str
    car_park_no: str = ""


def test_multiword_query_matches_only_the_contiguous_phrase():
    contiguous = _Item(address="ANG MO KIO AVENUE 3 MULTI-STOREY CAR PARK")
    only_first_word = _Item(address="ANG SIANG HILL CAR PARK")
    only_last_word = _Item(address="TOA PAYOH KIO CAR PARK")
    both_words_but_scattered = _Item(address="ANG SIANG HILL - TOA PAYOH KIO CAR PARK")

    results = rank_matches(
        "ang mo kio",
        [contiguous, only_first_word, only_last_word, both_words_but_scattered],
        limit=10,
    )

    assert contiguous in results
    assert only_first_word not in results
    assert only_last_word not in results
    assert both_words_but_scattered not in results


def test_multiword_query_matches_across_punctuation_differences():
    # "BLK 270/271" vs a query written "blk 270 271" - still one phrase,
    # just with different punctuation joining the same words.
    item = _Item(address="BLK 270/271 ALBERT CENTRE")
    assert score_text_match("BLK 270 271", item.address) > 0
    assert score_text_match("270 271 ALBERT", item.address) > 0


def test_single_word_query_still_matches_as_substring():
    # Single-word behavior is unchanged: a short query still matches as a
    # substring of a longer word, e.g. "jur" inside "JURONG".
    item = _Item(address="JURONG POINT CAR PARK")
    results = rank_matches("jur", [item], limit=5)
    assert results == [item]

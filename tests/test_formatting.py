from motopark_bot.formatting import format_carpark, format_check_results, format_nearest_results
from motopark_bot.lta_client import LiveLot
from motopark_bot.nearest import RankedCarpark
from motopark_bot.static_data import CarparkInfo

INFO = CarparkInfo(
    car_park_no="ACB",
    address="BLK 270/271 ALBERT CENTRE BASEMENT CAR PARK",
    lat=1.301059,
    lon=103.855409,
    car_park_type="BASEMENT CAR PARK",
    parking_system="ELECTRONIC PARKING",
    short_term_parking="WHOLE DAY",
    free_parking="NO",
    night_parking="YES",
)

LIVE = LiveLot(
    car_park_id="ACB",
    development="ALBERT CENTRE",
    lat=1.301059,
    lon=103.855409,
    available_lots=3,
    lot_type="Y",
    agency="HDB",
)


def test_format_carpark_with_live_data():
    text = format_carpark(INFO, LIVE, distance_km=0.42)
    assert "ALBERT CENTRE" in text
    assert "sheltered" in text
    assert "paid" in text
    assert "0.42 km" in text
    assert "3" in text
    assert "night parking" in text


def test_format_carpark_without_live_data():
    text = format_carpark(INFO, None)
    assert "no live data" in text


def test_format_carpark_full_lots():
    full = LiveLot(**{**LIVE.__dict__, "available_lots": 0})
    text = format_carpark(INFO, full)
    assert "FULL" in text


def test_format_nearest_results_empty():
    text = format_nearest_results([], {})
    assert "No carparks found" in text


def test_format_nearest_results_nonempty():
    ranked = [RankedCarpark(info=INFO, distance_km=0.1)]
    text = format_nearest_results(ranked, {"ACB": LIVE})
    assert "ALBERT CENTRE" in text
    assert "0.10 km" in text


def test_format_check_results_empty():
    text = format_check_results([], {})
    assert "No carparks matched" in text

from motopark_bot.carpark_rates_data import RateEntry
from motopark_bot.formatting import (
    NO_MATCH_MESSAGE,
    NO_NEARBY_MESSAGE,
    format_carpark,
    format_rate_entry,
    format_ura_carpark,
    join_blocks,
)
from motopark_bot.lta_client import LiveLot
from motopark_bot.static_data import CarparkInfo
from motopark_bot.ura_data import UraCarpark

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

URA_INFO = UraCarpark(
    pp_code="SP0001",
    name="ORCHARD ROAD CARPARK",
    lat=1.3005,
    lon=103.848,
    motorcycle_capacity=40,
    car_capacity=250,
    heavy_vehicle_capacity=5,
)

RATE_ENTRY = RateEntry(
    name="Suntec City",
    category="Marina",
    weekday_rate_1="$1.00 per 30 min",
    weekday_rate_2="$1.00 per 30 min",
    saturday_rate="$1.20 per 30 min",
    sunday_ph_rate="$1.20 per 30 min",
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


def test_format_ura_carpark_with_live_match():
    text = format_ura_carpark(URA_INFO, LIVE, distance_km=1.5)
    assert "ORCHARD ROAD CARPARK" in text
    assert "SP0001" in text
    assert "1.50 km" in text
    assert "*3*" in text  # live count wins over capacity when both exist


def test_format_ura_carpark_falls_back_to_capacity_when_no_live_match():
    text = format_ura_carpark(URA_INFO, None)
    assert "40 motorcycle bays" in text
    assert "capacity" in text
    assert "live count unavailable" in text


def test_format_ura_carpark_unknown_capacity_and_no_live():
    unknown = UraCarpark(
        pp_code="SP0099",
        name="UNKNOWN CARPARK",
        lat=1.3,
        lon=103.8,
        motorcycle_capacity=None,
        car_capacity=None,
        heavy_vehicle_capacity=None,
    )
    text = format_ura_carpark(unknown, None)
    assert "unknown" in text.lower()


def test_format_rate_entry_with_live_match():
    text = format_rate_entry(RATE_ENTRY, LIVE)
    assert "Suntec City" in text
    assert "Marina" in text
    assert "$1.00 per 30 min" in text
    assert "*3*" in text


def test_format_rate_entry_without_live_match():
    text = format_rate_entry(RATE_ENTRY, None)
    assert "no live data" in text
    assert "rate listing only" in text


def test_join_blocks_empty_uses_message():
    assert join_blocks([], NO_NEARBY_MESSAGE) == NO_NEARBY_MESSAGE
    assert join_blocks([], NO_MATCH_MESSAGE) == NO_MATCH_MESSAGE


def test_join_blocks_nonempty_joins_with_blank_line():
    result = join_blocks(["a", "b"], "unused")
    assert result == "a\n\nb"

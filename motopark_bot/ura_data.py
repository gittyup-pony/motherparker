"""URA carparks: coordinates + motorcycle bay capacity (not live occupancy).

Two data.gov.sg GeoJSON datasets, joined on PP_CODE:
  - "URA Parking Lot" (d_d959102fa76d58f2de276bfbb7e8f68e) — locations
  - "Capacity of URA Parking Places" (d_9bf8620ecfdc8a5f8f77e3f02160af5c) —
    NO_CAR / NO_H_VEHIC / NO_MCYCLE per PP_CODE

Important difference from static_data.py's HDB carparks: this gives total
motorcycle BAY COUNT (how many exist), not live occupancy (how many are
free right now) — URA doesn't publish a live-occupancy feed for these the
way HDB/LTA's CarParkAvailabilityv2 does. A carpark here only gets a LIVE
count if its PP_CODE happens to also be a CarParkID in the LTA feed, which
is unverified (see README's "things to verify" section — same class of
assumption as lta_client.py's CarParkID-matching note for HDB).

`car_park_no` / `address` properties exist purely so this slots into
matching.py's rank_matches() and nearest.py's find_nearest() unchanged —
both only ever touch those attribute names (plus .lat/.lon), so a
UraCarpark works everywhere a CarparkInfo does for search/ranking purposes,
even though the two aren't the same class. formatting.py still needs its
own function for these since the *display* fields genuinely differ
(no shelter/free-paid info here, capacity instead of live-lots-by-default).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from motopark_bot.datagovsg import fetch_geojson_features

PARKING_LOT_DATASET_ID = "d_d959102fa76d58f2de276bfbb7e8f68e"
CAPACITY_DATASET_ID = "d_9bf8620ecfdc8a5f8f77e3f02160af5c"

# Field names as documented on data.gov.sg's dataset pages. Both datasets
# are classic ArcGIS/SHP exports (short truncated attribute names), so
# these are near-certainly right, but weren't confirmed against a live
# sample — see datagovsg.py's HTML-table fallback and the module docstring
# above.
_PP_CODE_KEYS = ("PP_CODE", "PP_Code", "pp_code")
_NAME_KEYS = ("PARKING_PL", "PARKING_PLACE", "Parking_Place")
_NO_MCYCLE_KEYS = ("NO_MCYCLE", "No_MCycle")
_NO_CAR_KEYS = ("NO_CAR", "No_Car")
_NO_H_VEHIC_KEYS = ("NO_H_VEHIC", "No_H_Vehic")


def _first(props: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in props:
            return props[k]
    return None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


@dataclass(frozen=True)
class UraCarpark:
    pp_code: str
    name: str
    lat: float
    lon: float
    motorcycle_capacity: int | None
    car_capacity: int | None
    heavy_vehicle_capacity: int | None

    @property
    def car_park_no(self) -> str:
        """Alias so rank_matches()/formatting can treat this like CarparkInfo."""
        return self.pp_code

    @property
    def address(self) -> str:
        """Alias so rank_matches() can search this like CarparkInfo.address."""
        return self.name


def _build_capacity_index(features: list) -> dict[str, tuple[int | None, int | None, int | None]]:
    """Pure: GeoFeatures from the capacity dataset -> {pp_code: (car, heavy, mc)}."""
    out: dict[str, tuple[int | None, int | None, int | None]] = {}
    for feat in features:
        pp_code = _first(feat.properties, _PP_CODE_KEYS)
        if not pp_code:
            continue
        out[pp_code] = (
            _to_int(_first(feat.properties, _NO_CAR_KEYS)),
            _to_int(_first(feat.properties, _NO_H_VEHIC_KEYS)),
            _to_int(_first(feat.properties, _NO_MCYCLE_KEYS)),
        )
    return out


def _build_carparks(
    location_features: list,
    capacity_by_code: dict[str, tuple[int | None, int | None, int | None]],
) -> list[UraCarpark]:
    """Pure: joins location GeoFeatures with the capacity index -> UraCarparks.

    Separated from the network-fetching fetch_all_ura_carparks() below so
    the actual join/parse logic is directly unit-testable against fixture
    data, without needing to fake out the HTTP layer.
    """
    carparks: list[UraCarpark] = []
    for feat in location_features:
        pp_code = _first(feat.properties, _PP_CODE_KEYS)
        name = _first(feat.properties, _NAME_KEYS)
        if not pp_code or not name:
            continue
        car_cap, heavy_cap, mc_cap = capacity_by_code.get(pp_code, (None, None, None))
        carparks.append(
            UraCarpark(
                pp_code=pp_code,
                name=name,
                lat=feat.lat,
                lon=feat.lon,
                motorcycle_capacity=mc_cap,
                car_capacity=car_cap,
                heavy_vehicle_capacity=heavy_cap,
            )
        )
    return carparks


async def fetch_all_ura_carparks() -> list[UraCarpark]:
    locations = await fetch_geojson_features(PARKING_LOT_DATASET_ID)
    capacity_features = await fetch_geojson_features(CAPACITY_DATASET_ID)
    capacity_by_code = _build_capacity_index(capacity_features)
    return _build_carparks(locations, capacity_by_code)


class UraCarparkStore:
    """In-memory cache of joined URA carpark data, long TTL (rarely changes).

    If the dataset can't be parsed at all (see the field-name caveat at the
    top of this file), `_carparks` stays permanently empty, which makes
    `_is_stale()` permanently True — without `_last_attempt_at`/
    `retry_backoff_seconds` below, that means every single /check or
    /nearest call would re-attempt the network fetch and re-raise, instead
    of failing once and quietly staying "unavailable" for a while. This bit
    the bot for real once (see git history) before this backoff was added.
    """

    def __init__(self, ttl_seconds: int = 24 * 60 * 60, retry_backoff_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._retry_backoff = retry_backoff_seconds
        self._carparks: list[UraCarpark] = []
        self._fetched_at: float = 0.0
        self._last_attempt_at: float = 0.0

    def _is_stale(self) -> bool:
        return not self._carparks or (time.monotonic() - self._fetched_at) > self._ttl

    async def refresh(self, force: bool = False) -> None:
        if not force:
            if not self._is_stale():
                return
            if not self._carparks and self._last_attempt_at and (
                time.monotonic() - self._last_attempt_at
            ) < self._retry_backoff:
                raise RuntimeError("URA carpark data unavailable (recent fetch failed, backing off).")
        self._last_attempt_at = time.monotonic()
        carparks = await fetch_all_ura_carparks()
        if not carparks:
            raise RuntimeError("Fetched 0 usable URA carparks — refusing to update cache.")
        self._carparks = carparks
        self._fetched_at = time.monotonic()

    async def all(self) -> list[UraCarpark]:
        await self.refresh()
        return list(self._carparks)


if __name__ == "__main__":
    # Manual smoke test: `python -m motopark_bot.ura_data`
    # Confirms whether the field-name guesses above (PP_CODE, PARKING_PL,
    # NO_MCYCLE, ...) and the clean-properties-vs-HTML-table parsing
    # actually match what these two datasets return for real.
    import asyncio

    async def main() -> None:
        carparks = await fetch_all_ura_carparks()
        print(f"Fetched {len(carparks)} URA carparks.")
        with_mc = [cp for cp in carparks if cp.motorcycle_capacity]
        print(f"{len(with_mc)} have a non-zero/non-null motorcycle capacity.")
        for cp in carparks[:5]:
            print(" ", cp)
        if not carparks:
            print(
                "Zero carparks parsed — the field-name guesses in ura_data.py "
                "are probably wrong for the real dataset shape. Add a raw-feature "
                "print here to inspect actual property keys."
            )

    asyncio.run(main())

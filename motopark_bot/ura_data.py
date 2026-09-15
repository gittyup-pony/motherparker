"""URA carparks: coordinates + motorcycle bay capacity (not live occupancy).

**Confirmed against real production data** (previously this was built
against documentation only — see git history / README for the saga):

  - "Capacity of URA Parking Places" (d_9bf8620ecfdc8a5f8f77e3f02160af5c)
    is exactly what was originally assumed: one Point-geometry feature per
    carpark FACILITY, with `PP_CODE`, `PARKING_PL` (name), and
    `NO_CAR`/`NO_H_VEHIC`/`NO_MCYCLE` capacity counts. This is the only
    dataset actually used now.
  - "URA Parking Lot" (d_d959102fa76d58f2de276bfbb7e8f68e) turned out to
    be something else entirely: ~38,700 Polygon-geometry features, one per
    individual physical parking LOT (tagged `TYPE`, e.g. "Motorcycle
    Lots"), not one point per facility. That's why the original
    two-dataset join produced 0 usable carparks — `datagovsg.parse_geojson`
    only keeps Point geometries, so every one of those Polygon features
    was silently dropped, leaving nothing to join against. Since the
    Capacity dataset alone already has everything this bot needs (name,
    location, capacity), the join was dropped rather than fixed — this
    constant is kept only as a documented dead end, in case per-lot detail
    is ever wanted for something else.

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

import asyncio
import logging
import time
from dataclasses import dataclass

from motopark_bot.datagovsg import fetch_geojson_features, fetch_raw_geojson

log = logging.getLogger(__name__)

CAPACITY_DATASET_ID = "d_9bf8620ecfdc8a5f8f77e3f02160af5c"

# Not used for parsing anymore (see module docstring) - kept only so the
# diagnostic below can still confirm its shape if that's ever useful again.
PARKING_LOT_DATASET_ID = "d_d959102fa76d58f2de276bfbb7e8f68e"

# data.gov.sg's poll-download endpoint rate-limits (429) rapid successive
# calls, confirmed in production. This is a courtesy delay between calls to
# the same endpoint, not a substitute for fetch_download_url's
# retry-with-backoff.
_INTER_REQUEST_DELAY_SECONDS = 1.5

# Field names as they actually appear in the Capacity dataset - confirmed
# against a real production sample (see module docstring), not just
# documentation.
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


def _build_carparks(features: list) -> list[UraCarpark]:
    """Pure: Capacity-dataset GeoFeatures -> UraCarparks, one per facility.

    Separated from the network-fetching fetch_all_ura_carparks() below so
    the actual parse logic is directly unit-testable against fixture data,
    without needing to fake out the HTTP layer.
    """
    carparks: list[UraCarpark] = []
    for feat in features:
        pp_code = _first(feat.properties, _PP_CODE_KEYS)
        name = _first(feat.properties, _NAME_KEYS)
        if not pp_code or not name:
            continue
        carparks.append(
            UraCarpark(
                pp_code=pp_code,
                name=name.strip(),  # real data has trailing whitespace on some names
                lat=feat.lat,
                lon=feat.lon,
                motorcycle_capacity=_to_int(_first(feat.properties, _NO_MCYCLE_KEYS)),
                car_capacity=_to_int(_first(feat.properties, _NO_CAR_KEYS)),
                heavy_vehicle_capacity=_to_int(_first(feat.properties, _NO_H_VEHIC_KEYS)),
            )
        )
    return carparks


async def fetch_all_ura_carparks() -> list[UraCarpark]:
    features = await fetch_geojson_features(CAPACITY_DATASET_ID)
    return _build_carparks(features)


class UraCarparkStore:
    """In-memory cache of URA carpark data, long TTL (rarely changes).

    If the dataset can't be parsed at all, `_carparks` stays permanently
    empty, which makes `_is_stale()` permanently True — without
    `_last_attempt_at`/`retry_backoff_seconds` below, that means every
    single /check or /nearest call would re-attempt the network fetch and
    re-raise, instead of failing once and quietly staying "unavailable"
    for a while. This bit the bot for real once (see git history) before
    this backoff was added.
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


async def log_raw_feature_sample() -> None:
    """Diagnostic-only: log the ground-truth raw GeoJSON for both datasets.

    Only the Capacity dataset is actually parsed now (see module
    docstring), but this still checks the Parking Lot dataset too, purely
    so a future schema change there would show up here rather than being
    silently irrelevant.

    Render's free tier has no Shell tab, so `python -m motopark_bot.ura_data`
    (the smoke test below) isn't runnable interactively there - only the
    Logs tab is free. This does the same fetch but writes WARNING-level log
    lines instead of printing, so the raw feature count/geometry type/
    properties are visible from Render's dashboard after a
    restart/redeploy, without needing paid Shell access. Called from
    bot.py when priming ura_store fails at startup.

    Called right after the main refresh already made a poll-download call,
    so this staggers its own 2 calls too (see _INTER_REQUEST_DELAY_SECONDS)
    - confirmed in production that firing requests back-to-back trips
    data.gov.sg's rate limit (429), which would otherwise make the
    diagnostic itself fail before showing us anything useful.
    """
    for i, (label, dataset_id) in enumerate((("Parking Lot", PARKING_LOT_DATASET_ID), ("Capacity", CAPACITY_DATASET_ID))):
        if i > 0:
            await asyncio.sleep(_INTER_REQUEST_DELAY_SECONDS)
        try:
            raw = await fetch_raw_geojson(dataset_id)
            features = raw.get("features", [])
            first = features[0] if features else None
            log.warning(
                "URA diagnostic [%s dataset]: %d raw feature(s). Top-level keys=%s. First feature=%r",
                label,
                len(features),
                sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                first,
            )
        except Exception:
            log.exception("URA diagnostic [%s dataset]: raw fetch itself failed.", label)


if __name__ == "__main__":
    # Manual smoke test: `python -m motopark_bot.ura_data`
    # Confirms the Capacity dataset still parses as expected (field names,
    # Point geometry) - now confirmed against real data once already, but
    # worth re-running if data.gov.sg ever changes the dataset shape.
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

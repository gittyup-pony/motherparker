"""Static HDB carpark metadata: shelter type, free/paid, lat/lon.

Source: data.gov.sg "HDB Carpark Information" dataset (resource id
d_23f946fa557947f93a8043bbef41dd09). This barely changes (new carparks are
rare), so we cache it in memory with a long TTL rather than hitting the API
on every Telegram message.

This dataset gives no live lot counts — that's lta_client.py's job. This
module is purely the "what kind of carpark is this" layer: sheltered vs
open-air, free vs paid, night parking, etc.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from motopark_bot.geo import svy21_to_wgs84

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
RESOURCE_ID = "d_23f946fa557947f93a8043bbef41dd09"
PAGE_SIZE = 1000


@dataclass(frozen=True)
class CarparkInfo:
    car_park_no: str
    address: str
    lat: float
    lon: float
    car_park_type: str  # e.g. "BASEMENT CAR PARK", "SURFACE CAR PARK", "MULTI-STOREY CAR PARK"
    parking_system: str  # "ELECTRONIC PARKING" / "COUPON PARKING"
    short_term_parking: str  # e.g. "WHOLE DAY", "7AM-10.30PM", "NO"
    free_parking: str  # e.g. "NO", "SUN & PH FR 7AM-10.30PM"
    night_parking: str  # "YES" / "NO"

    @property
    def is_sheltered(self) -> bool:
        # Basement and multi-storey carparks are covered; surface carparks
        # are open-air. This is the best proxy the dataset gives us — it
        # doesn't have an explicit "sheltered" flag.
        return self.car_park_type in {"BASEMENT CAR PARK", "MULTI-STOREY CAR PARK"}

    @property
    def is_free_anytime(self) -> bool:
        return self.free_parking not in {"NO", ""}

    @property
    def shelter_label(self) -> str:
        return "sheltered" if self.is_sheltered else "unsheltered"

    @property
    def price_label(self) -> str:
        if self.free_parking == "NO":
            return "paid"
        return f"free ({self.free_parking.lower()})"


def _parse_record(rec: dict) -> CarparkInfo | None:
    try:
        x = float(rec["x_coord"])
        y = float(rec["y_coord"])
    except (KeyError, TypeError, ValueError):
        return None
    lat, lon = svy21_to_wgs84(x, y)
    return CarparkInfo(
        car_park_no=rec.get("car_park_no", "").strip(),
        address=rec.get("address", "").strip(),
        lat=lat,
        lon=lon,
        car_park_type=(rec.get("car_park_type") or "").strip(),
        parking_system=(rec.get("type_of_parking_system") or "").strip(),
        short_term_parking=(rec.get("short_term_parking") or "").strip(),
        free_parking=(rec.get("free_parking") or "NO").strip(),
        night_parking=(rec.get("night_parking") or "").strip(),
    )


async def _fetch_all_records(client: httpx.AsyncClient) -> list[dict]:
    records: list[dict] = []
    offset = 0
    while True:
        resp = await client.get(
            DATASTORE_URL,
            params={"resource_id": RESOURCE_ID, "limit": PAGE_SIZE, "offset": offset},
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(f"data.gov.sg datastore_search returned success=false: {payload}")
        result = payload["result"]
        batch = result.get("records", [])
        records.extend(batch)
        total = result.get("total", len(records))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return records


class StaticCarparkStore:
    """In-memory cache of the HDB Carpark Information dataset, keyed by car_park_no."""

    def __init__(self, ttl_seconds: int = 24 * 60 * 60) -> None:
        self._ttl = ttl_seconds
        self._by_no: dict[str, CarparkInfo] = {}
        self._fetched_at: float = 0.0

    def _is_stale(self) -> bool:
        return not self._by_no or (time.monotonic() - self._fetched_at) > self._ttl

    async def refresh(self, force: bool = False) -> None:
        if not force and not self._is_stale():
            return
        async with httpx.AsyncClient() as client:
            records = await _fetch_all_records(client)
        by_no: dict[str, CarparkInfo] = {}
        for rec in records:
            info = _parse_record(rec)
            if info and info.car_park_no:
                by_no[info.car_park_no] = info
        if not by_no:
            # Don't clobber a good cache with an empty/broken fetch.
            raise RuntimeError("Fetched 0 usable carpark records from data.gov.sg — refusing to update cache.")
        self._by_no = by_no
        self._fetched_at = time.monotonic()

    async def all(self) -> list[CarparkInfo]:
        await self.refresh()
        return list(self._by_no.values())

    async def get(self, car_park_no: str) -> CarparkInfo | None:
        await self.refresh()
        return self._by_no.get(car_park_no)

    async def search_by_address(self, query: str, limit: int = 8) -> list[CarparkInfo]:
        """Simple case-insensitive substring match over the address field.

        Good enough for "/check jurong point" style lookups; matching.py
        layers fuzzier scoring on top if the caller wants ranked results.
        """
        await self.refresh()
        q = query.strip().lower()
        if not q:
            return []
        matches = [info for info in self._by_no.values() if q in info.address.lower()]
        return matches[:limit]

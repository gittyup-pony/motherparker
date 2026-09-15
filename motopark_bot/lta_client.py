"""Live carpark lot availability via LTA DataMall's CarParkAvailabilityv2.

Endpoint refreshes roughly every 1 minute on LTA's side. Response is a flat
list of records; each record is one (carpark, lot-type) pair — a single
physical carpark can appear multiple times, once per LotType (car / heavy
vehicle / motorcycle), so we group by CarParkID after fetching.

Docs (LTA DataMall API User Guide): AccountKey goes in the request headers,
no query params needed, ~500 records returned per call, endpoint paginates
via $skip if a single call doesn't return everything.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from motopark_bot.matching import score_text_match

BASE_URL = "https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2"
PAGE_SIZE = 500

# LTA's official user guide documents LotType as C=Car, H=Heavy Vehicle,
# Y=Motorcycle. Some third-party mirrors of this API have been seen using
# "M" instead of "Y" for motorcycles, so we treat both as motorcycle to be
# safe — see README's "known unknowns" section. If a real response ever
# turns up neither, `python -m motopark_bot.lta_client` (see __main__ below)
# will print the raw LotType values so this can be corrected.
MOTORCYCLE_LOT_TYPES = {"Y", "M"}


@dataclass(frozen=True)
class LiveLot:
    car_park_id: str
    development: str
    lat: float
    lon: float
    available_lots: int
    lot_type: str
    agency: str

    @property
    def is_motorcycle(self) -> bool:
        return self.lot_type in MOTORCYCLE_LOT_TYPES


def _parse_location(loc: str) -> tuple[float, float] | None:
    # LTA returns Location as a single "lat lon" space-separated string.
    parts = loc.split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


async def _fetch_page(client: httpx.AsyncClient, account_key: str, skip: int) -> list[dict]:
    resp = await client.get(
        BASE_URL,
        headers={"AccountKey": account_key, "accept": "application/json"},
        params={"$skip": skip} if skip else None,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


async def fetch_all_live_lots(account_key: str) -> list[LiveLot]:
    """Fetch every (carpark, lot-type) record from the live feed."""
    lots: list[LiveLot] = []
    async with httpx.AsyncClient() as client:
        skip = 0
        while True:
            batch = await _fetch_page(client, account_key, skip)
            if not batch:
                break
            for rec in batch:
                loc = _parse_location(rec.get("Location", ""))
                if loc is None:
                    continue
                try:
                    available = int(rec.get("AvailableLots", 0))
                except (TypeError, ValueError):
                    continue
                lots.append(
                    LiveLot(
                        car_park_id=str(rec.get("CarParkID", "")).strip(),
                        development=str(rec.get("Development", "")).strip(),
                        lat=loc[0],
                        lon=loc[1],
                        available_lots=available,
                        lot_type=str(rec.get("LotType", "")).strip(),
                        agency=str(rec.get("Agency", "")).strip(),
                    )
                )
            if len(batch) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
    return lots


class LiveAvailabilityStore:
    """Short-TTL cache over fetch_all_live_lots, keyed by CarParkID -> motorcycle LiveLot."""

    def __init__(self, account_key: str, ttl_seconds: int = 45) -> None:
        self._account_key = account_key
        self._ttl = ttl_seconds
        self._by_carpark_id: dict[str, LiveLot] = {}
        self._fetched_at: float = 0.0

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self._ttl

    async def refresh(self, force: bool = False) -> None:
        if not force and self._by_carpark_id and not self._is_stale():
            return
        all_lots = await fetch_all_live_lots(self._account_key)
        by_id = {lot.car_park_id: lot for lot in all_lots if lot.is_motorcycle}
        if not by_id and all_lots:
            # We got data but none of it matched our motorcycle LotType
            # guess — don't silently cache an empty motorcycle view.
            raise RuntimeError(
                "LTA feed returned data but no records matched MOTORCYCLE_LOT_TYPES "
                f"({sorted(MOTORCYCLE_LOT_TYPES)}). Seen LotTypes: "
                f"{sorted({lot.lot_type for lot in all_lots})}. Update lta_client.MOTORCYCLE_LOT_TYPES."
            )
        self._by_carpark_id = by_id
        self._fetched_at = time.monotonic()

    async def get(self, car_park_id: str) -> LiveLot | None:
        await self.refresh()
        return self._by_carpark_id.get(car_park_id)

    async def all_motorcycle_lots(self) -> list[LiveLot]:
        await self.refresh()
        return list(self._by_carpark_id.values())

    async def find_by_development_name(self, query: str, limit: int = 3) -> list[LiveLot]:
        """Best-effort live lookup for carparks with no CarParkID to join on.

        Used for carpark_rates_data.py's RateEntry (Carpark Rates has no
        carpark ID at all) — fuzzy-matches `query` against each live
        record's `development` name using the same scoring as matching.py's
        rank_matches. Only ever finds something for carparks LTA's own feed
        covers directly (Orchard/Marina/HarbourFront/Jurong Lake District
        malls, per LTA's dataset description — see README).
        """
        await self.refresh()
        q = query.strip()
        if not q:
            return []
        q_upper = q.upper()

        scored: list[tuple[float, LiveLot]] = []
        for lot in self._by_carpark_id.values():
            score = score_text_match(q_upper, lot.development)
            if score > 0:
                scored.append((score, lot))
        scored.sort(key=lambda t: -t[0])
        return [lot for _, lot in scored[:limit]]


if __name__ == "__main__":
    # Quick manual smoke test: `LTA_ACCOUNT_KEY=xxx python -m motopark_bot.lta_client`
    # Prints the distinct LotType values actually seen, to confirm whether
    # motorcycles are "Y" or "M" (or something else) before you rely on it.
    import asyncio
    import os
    import sys

    key = os.environ.get("LTA_ACCOUNT_KEY")
    if not key:
        print("Set LTA_ACCOUNT_KEY to run this smoke test.", file=sys.stderr)
        raise SystemExit(1)

    async def main() -> None:
        lots = await fetch_all_live_lots(key)
        lot_types = sorted({lot.lot_type for lot in lots})
        print(f"Fetched {len(lots)} records. Distinct LotType values: {lot_types}")
        mc = [lot for lot in lots if lot.lot_type in MOTORCYCLE_LOT_TYPES]
        print(f"{len(mc)} records matched MOTORCYCLE_LOT_TYPES {sorted(MOTORCYCLE_LOT_TYPES)}.")
        for lot in mc[:5]:
            print(" ", lot)

    asyncio.run(main())

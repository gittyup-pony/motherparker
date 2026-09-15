"""Carpark Rates: named malls/hotels/attractions, price reference only.

Source: data.gov.sg "Carpark Rates (Major Shopping Malls, Attractions and
Hotels)", published by LTA. Columns per the dataset page: Carpark,
Category, Weekdays Rate 1, Weekdays Rate 2, Saturday Rate, Sunday and
Public Holiday Rate.

What this can and can't do:
  - No coordinates -> can't feed /nearest, only /check (name search).
  - No motorcycle-specific rate -> shown as general parking price context,
    not a motorcycle rate.
  - No live availability, and no clean ID to join against LTA's live feed
    by CarParkID -> live_lookup.py's fuzzy name-matching against LTA's
    `Development` field is the only way this ever gets a live count, and
    only for entries that happen to be in Orchard/Marina/HarbourFront/
    Jurong Lake District (LTA's own coverage for non-HDB carparks).
  - The rates themselves are static reference data last substantively
    updated 2018 per the dataset page — treat as "roughly what to expect",
    not current pricing.

Like ura_data.py, `car_park_no` / `address` aliases exist purely so this
slots into matching.py's rank_matches() unchanged. `car_park_no` is always
"" here (there's no ID), which bot.py uses as the signal to skip the
direct live_store.get() lookup and fall back to the name-fuzzy live search
instead.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from motopark_bot.datagovsg import fetch_csv_rows

DATASET_ID = "d_9f6056bdb6b1dfba57f063593e4f34ae"

_NAME_KEYS = ("Carpark", "carpark", "CARPARK")
_CATEGORY_KEYS = ("Category", "category", "CATEGORY")
_WEEKDAY1_KEYS = ("Weekdays Rate 1", "Weekdays_Rate_1")
_WEEKDAY2_KEYS = ("Weekdays Rate 2", "Weekdays_Rate_2")
_SAT_KEYS = ("Saturday Rate", "Saturday_Rate")
_SUN_PH_KEYS = ("Sunday and Public Holiday Rate", "Sunday_PH_Rate", "Sunday and PH Rate")


def _first(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in row and row[k]:
            return row[k].strip()
    return ""


@dataclass(frozen=True)
class RateEntry:
    name: str
    category: str
    weekday_rate_1: str
    weekday_rate_2: str
    saturday_rate: str
    sunday_ph_rate: str

    @property
    def car_park_no(self) -> str:
        """Always empty — this dataset has no carpark ID to join live data on."""
        return ""

    @property
    def address(self) -> str:
        """Alias so rank_matches() can search this like CarparkInfo.address."""
        return self.name


def _parse_row(row: dict[str, str]) -> RateEntry | None:
    name = _first(row, _NAME_KEYS)
    if not name:
        return None
    return RateEntry(
        name=name,
        category=_first(row, _CATEGORY_KEYS),
        weekday_rate_1=_first(row, _WEEKDAY1_KEYS),
        weekday_rate_2=_first(row, _WEEKDAY2_KEYS),
        saturday_rate=_first(row, _SAT_KEYS),
        sunday_ph_rate=_first(row, _SUN_PH_KEYS),
    )


async def fetch_all_rate_entries() -> list[RateEntry]:
    rows = await fetch_csv_rows(DATASET_ID)
    entries = [_parse_row(row) for row in rows]
    return [e for e in entries if e is not None]


class CarparkRatesStore:
    """In-memory cache of the Carpark Rates dataset, long TTL (barely changes)."""

    def __init__(self, ttl_seconds: int = 24 * 60 * 60) -> None:
        self._ttl = ttl_seconds
        self._entries: list[RateEntry] = []
        self._fetched_at: float = 0.0

    def _is_stale(self) -> bool:
        return not self._entries or (time.monotonic() - self._fetched_at) > self._ttl

    async def refresh(self, force: bool = False) -> None:
        if not force and not self._is_stale():
            return
        entries = await fetch_all_rate_entries()
        if not entries:
            raise RuntimeError("Fetched 0 usable rate entries — refusing to update cache.")
        self._entries = entries
        self._fetched_at = time.monotonic()

    async def all(self) -> list[RateEntry]:
        await self.refresh()
        return list(self._entries)


if __name__ == "__main__":
    # Manual smoke test: `python -m motopark_bot.carpark_rates_data`
    import asyncio

    async def main() -> None:
        entries = await fetch_all_rate_entries()
        print(f"Fetched {len(entries)} rate entries.")
        for e in entries[:5]:
            print(" ", e)
        if not entries:
            print("Zero entries parsed — the column-name guesses are probably wrong.")

    asyncio.run(main())

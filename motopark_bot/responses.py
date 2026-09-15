"""Builds the actual reply text for /check and /nearest.

Pulled out of bot.py (which just wires these to aiogram handlers) so the
merge-three-sources logic — genuinely the most complex thing in this
codebase now — is directly unit-testable without simulating Telegram
Message/Update objects.
"""
from __future__ import annotations

from motopark_bot.carpark_rates_data import CarparkRatesStore
from motopark_bot.formatting import (
    NO_MATCH_MESSAGE,
    NO_NEARBY_MESSAGE,
    format_carpark,
    format_rate_entry,
    format_ura_carpark,
    join_blocks,
)
from motopark_bot.lta_client import LiveAvailabilityStore
from motopark_bot.matching import rank_matches
from motopark_bot.nearest import find_nearest
from motopark_bot.static_data import CarparkInfo, StaticCarparkStore
from motopark_bot.ura_data import UraCarparkStore

# How many results to pull from each source per /check, before combining.
# Kept small per-source so a query that matches broadly (e.g. "orchard")
# doesn't return an unreadably long combined message.
CHECK_LIMIT_HDB = 3
CHECK_LIMIT_URA = 2
CHECK_LIMIT_RATES = 2


async def build_check_response(
    query: str,
    static_store: StaticCarparkStore,
    ura_store: UraCarparkStore,
    rates_store: CarparkRatesStore,
    live_store: LiveAvailabilityStore,
) -> str:
    hdb_carparks = await static_store.all()
    ura_carparks = await ura_store.all()
    rate_entries = await rates_store.all()

    hdb_matches = rank_matches(query, hdb_carparks, limit=CHECK_LIMIT_HDB)
    ura_matches = rank_matches(query, ura_carparks, limit=CHECK_LIMIT_URA)
    rate_matches = rank_matches(query, rate_entries, limit=CHECK_LIMIT_RATES)

    blocks: list[str] = []
    for cp in hdb_matches:
        live = await live_store.get(cp.car_park_no)
        blocks.append(format_carpark(cp, live))
    for ucp in ura_matches:
        # ucp.car_park_no aliases pp_code - whether that ever matches an
        # LTA CarParkID is unverified, see README. get() just returns None
        # if it doesn't, and format_ura_carpark falls back to showing
        # capacity instead of a live count in that case.
        live = await live_store.get(ucp.car_park_no)
        blocks.append(format_ura_carpark(ucp, live))
    for entry in rate_matches:
        # No ID to join on at all here - best-effort fuzzy name match
        # against the live feed's Development names instead.
        live_candidates = await live_store.find_by_development_name(entry.name, limit=1)
        live = live_candidates[0] if live_candidates else None
        blocks.append(format_rate_entry(entry, live))

    return join_blocks(blocks, NO_MATCH_MESSAGE)


async def build_nearest_response(
    lat: float,
    lon: float,
    static_store: StaticCarparkStore,
    ura_store: UraCarparkStore,
    live_store: LiveAvailabilityStore,
    *,
    limit: int,
    max_radius_km: float,
) -> str:
    hdb_carparks = await static_store.all()
    ura_carparks = await ura_store.all()
    # Carpark Rates entries have no coordinates, so they can't appear here -
    # /check is the only place they show up.
    combined = [*hdb_carparks, *ura_carparks]

    ranked = find_nearest(lat, lon, combined, limit=limit, max_radius_km=max_radius_km)

    live_by_id = {}
    for r in ranked:
        live = await live_store.get(r.info.car_park_no)
        if live is not None:
            live_by_id[r.info.car_park_no] = live

    blocks = []
    for r in ranked:
        live = live_by_id.get(r.info.car_park_no)
        if isinstance(r.info, CarparkInfo):
            blocks.append(format_carpark(r.info, live, distance_km=r.distance_km))
        else:
            blocks.append(format_ura_carpark(r.info, live, distance_km=r.distance_km))

    return join_blocks(blocks, NO_NEARBY_MESSAGE)

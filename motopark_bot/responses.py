"""Builds the actual reply text (and nav-button targets) for /check and /nearest.

Pulled out of bot.py (which just wires these to aiogram handlers) so the
merge-three-sources logic — genuinely the most complex thing in this
codebase now — is directly unit-testable without simulating Telegram
Message/Update objects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, TypeVar

from motopark_bot.carpark_rates_data import CarparkRatesStore
from motopark_bot.formatting import (
    NO_MATCH_MESSAGE,
    NO_NEARBY_MESSAGE,
    POSTAL_CODE_NOT_CONFIGURED_MESSAGE,
    POSTAL_CODE_NOT_FOUND_MESSAGE,
    format_carpark,
    format_rate_entry,
    format_ura_carpark,
    join_blocks,
)
from motopark_bot.lta_client import LiveAvailabilityStore
from motopark_bot.matching import rank_matches
from motopark_bot.nearest import find_nearest
from motopark_bot.onemap import OneMapClient, is_postal_code
from motopark_bot.static_data import CarparkInfo, StaticCarparkStore
from motopark_bot.ura_data import UraCarparkStore

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class NavTarget:
    """One "navigate here" button's worth of data: a label and coordinates.

    bot.py turns these into Google Maps deep-link buttons (see maps.py) -
    kept as plain data here (no aiogram types) so responses.py stays
    testable without simulating Telegram objects, per the module docstring.
    """

    label: str
    lat: float
    lon: float


@dataclass(frozen=True)
class BotReply:
    """What build_check_response()/build_nearest_response() hand back.

    `text` is the message body (unchanged from before nav buttons existed).
    `nav_targets` is one entry per result that has coordinates - Carpark
    Rates entries never contribute one (see build_check_response below),
    so this list can be shorter than the number of blocks in `text`.
    """

    text: str
    nav_targets: list[NavTarget] = field(default_factory=list)


async def _safe(source_name: str, coro: Awaitable[T], default: T) -> T:
    """Await a store call, treating any failure as `default` instead of crashing the reply.

    ura_store and rates_store are speculative extensions built against
    unverified dataset schemas (see README) — if a data source is broken,
    its store raises (see ura_data.py/carpark_rates_data.py). This bit
    once for real: a broken URA dataset took down every /nearest reply
    with an unhandled RuntimeError, even though HDB data (the bot's core)
    was working fine. live_store lookups can fail the same way (LTA
    outage, bad AccountKey, a wrong LotType guess). Wrapping every
    external call here means one broken source degrades that part of the
    reply instead of losing the whole thing. Each store's own
    retry-backoff (see ura_data.py) keeps a persistent failure from
    hammering the network on every single message.
    """
    try:
        return await coro
    except Exception as exc:
        log.warning("%s unavailable for this request, continuing without it: %s", source_name, exc)
        return default


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
    onemap_client: OneMapClient | None = None,
    *,
    nearest_result_count: int = 5,
    nearest_max_radius_km: float = 3.0,
) -> BotReply:
    q = query.strip()
    if is_postal_code(q):
        # A 6-digit postal code isn't a name to text-search for - geocode
        # it, then it's exactly a /nearest search from that point. Reuses
        # build_nearest_response wholesale (live joins, resilience, nav
        # targets, all of it) rather than duplicating any of that here.
        if onemap_client is None:
            return BotReply(text=POSTAL_CODE_NOT_CONFIGURED_MESSAGE)
        point = await _safe("OneMap geocoding", onemap_client.geocode_postal_code(q), None)
        if point is None:
            return BotReply(text=POSTAL_CODE_NOT_FOUND_MESSAGE)
        return await build_nearest_response(
            point.lat,
            point.lon,
            static_store,
            ura_store,
            live_store,
            limit=nearest_result_count,
            max_radius_km=nearest_max_radius_km,
        )

    hdb_carparks = await _safe("HDB carpark data", static_store.all(), [])
    ura_carparks = await _safe("URA carpark data", ura_store.all(), [])
    rate_entries = await _safe("Carpark Rates data", rates_store.all(), [])

    hdb_matches = rank_matches(query, hdb_carparks, limit=CHECK_LIMIT_HDB)
    ura_matches = rank_matches(query, ura_carparks, limit=CHECK_LIMIT_URA)
    rate_matches = rank_matches(query, rate_entries, limit=CHECK_LIMIT_RATES)

    blocks: list[str] = []
    nav_targets: list[NavTarget] = []
    for cp in hdb_matches:
        live = await _safe("live lot data", live_store.get(cp.car_park_no), None)
        blocks.append(format_carpark(cp, live))
        nav_targets.append(NavTarget(label=cp.address, lat=cp.lat, lon=cp.lon))
    for ucp in ura_matches:
        # ucp.car_park_no aliases pp_code - whether that ever matches an
        # LTA CarParkID is unverified, see README. get() just returns None
        # if it doesn't, and format_ura_carpark falls back to showing
        # capacity instead of a live count in that case.
        live = await _safe("live lot data", live_store.get(ucp.car_park_no), None)
        blocks.append(format_ura_carpark(ucp, live))
        nav_targets.append(NavTarget(label=ucp.name, lat=ucp.lat, lon=ucp.lon))
    for entry in rate_matches:
        # No ID to join on at all here - best-effort fuzzy name match
        # against the live feed's Development names instead.
        live_candidates = await _safe(
            "live lot data", live_store.find_by_development_name(entry.name, limit=1), []
        )
        live = live_candidates[0] if live_candidates else None
        blocks.append(format_rate_entry(entry, live))
        # No coordinates on Carpark Rates entries - no nav button for these.

    return BotReply(text=join_blocks(blocks, NO_MATCH_MESSAGE), nav_targets=nav_targets)


async def build_nearest_response(
    lat: float,
    lon: float,
    static_store: StaticCarparkStore,
    ura_store: UraCarparkStore,
    live_store: LiveAvailabilityStore,
    *,
    limit: int,
    max_radius_km: float,
) -> BotReply:
    hdb_carparks = await _safe("HDB carpark data", static_store.all(), [])
    ura_carparks = await _safe("URA carpark data", ura_store.all(), [])
    # Carpark Rates entries have no coordinates, so they can't appear here -
    # /check is the only place they show up.
    combined = [*hdb_carparks, *ura_carparks]

    ranked = find_nearest(lat, lon, combined, limit=limit, max_radius_km=max_radius_km)

    live_by_id = {}
    for r in ranked:
        live = await _safe("live lot data", live_store.get(r.info.car_park_no), None)
        if live is not None:
            live_by_id[r.info.car_park_no] = live

    blocks = []
    nav_targets: list[NavTarget] = []
    for r in ranked:
        live = live_by_id.get(r.info.car_park_no)
        if isinstance(r.info, CarparkInfo):
            blocks.append(format_carpark(r.info, live, distance_km=r.distance_km))
            nav_targets.append(NavTarget(label=r.info.address, lat=r.info.lat, lon=r.info.lon))
        else:
            blocks.append(format_ura_carpark(r.info, live, distance_km=r.distance_km))
            nav_targets.append(NavTarget(label=r.info.name, lat=r.info.lat, lon=r.info.lon))

    return BotReply(text=join_blocks(blocks, NO_NEARBY_MESSAGE), nav_targets=nav_targets)

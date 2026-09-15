"""Shared Telegram message formatting for carpark results.

Three source types can appear in one /check or /nearest reply now (HDB via
static_data.CarparkInfo, URA via ura_data.UraCarpark, and — /check only —
Carpark Rates via carpark_rates_data.RateEntry), each with different fields
available, so each gets its own formatter. bot.py builds the combined list
of blocks (deciding which formatter to call per result) and passes it to
join_blocks() here.
"""
from __future__ import annotations

from motopark_bot.carpark_rates_data import RateEntry
from motopark_bot.lta_client import LiveLot
from motopark_bot.static_data import CarparkInfo
from motopark_bot.ura_data import UraCarpark


def _lots_line(live: LiveLot | None) -> str:
    if live is None:
        return "🏍 motorcycle lots: _no live data_"
    if live.available_lots <= 0:
        return "🏍 *FULL* (0 motorcycle lots)"
    return f"🏍 *{live.available_lots}* motorcycle lots available"


def format_carpark(info: CarparkInfo, live: LiveLot | None, distance_km: float | None = None) -> str:
    lines = [f"*{info.address}*", f"`{info.car_park_no}` · {info.shelter_label} · {info.price_label}"]
    if distance_km is not None:
        lines.append(f"📍 {distance_km:.2f} km away")
    lines.append(_lots_line(live))
    if info.night_parking == "YES":
        lines.append("🌙 night parking available")
    return "\n".join(lines)


def format_ura_carpark(info: UraCarpark, live: LiveLot | None, distance_km: float | None = None) -> str:
    lines = [f"*{info.name}*", f"`{info.pp_code}` · URA carpark"]
    if distance_km is not None:
        lines.append(f"📍 {distance_km:.2f} km away")
    if live is not None:
        lines.append(_lots_line(live))
    elif info.motorcycle_capacity is not None:
        # No live match (see README's URA join caveat) - fall back to total
        # bay count, clearly labeled as capacity, not current availability.
        lines.append(f"🏍 {info.motorcycle_capacity} motorcycle bays (capacity) — live count unavailable")
    else:
        lines.append("🏍 motorcycle capacity: _unknown_")
    return "\n".join(lines)


def format_rate_entry(entry: RateEntry, live: LiveLot | None) -> str:
    lines = [f"*{entry.name}*"]
    subtitle_bits = [b for b in (entry.category, f"from {entry.weekday_rate_1}" if entry.weekday_rate_1 else None) if b]
    if subtitle_bits:
        lines.append(" · ".join(subtitle_bits))
    if live is not None:
        lines.append(_lots_line(live))
    else:
        lines.append("🏍 motorcycle lots: _no live data (rate listing only)_")
    return "\n".join(lines)


def join_blocks(blocks: list[str], empty_message: str) -> str:
    return "\n\n".join(blocks) if blocks else empty_message


NO_NEARBY_MESSAGE = (
    "No carparks found nearby. Try sending a location closer to town, "
    "or use /check <carpark name> to search by name instead."
)

NO_MATCH_MESSAGE = (
    "No carparks matched that. Try a shorter search term (e.g. just the "
    "mall or street name), or share your location instead with /nearest."
)

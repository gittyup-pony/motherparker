"""Shared Telegram message formatting for carpark results."""
from __future__ import annotations

from motopark_bot.lta_client import LiveLot
from motopark_bot.nearest import RankedCarpark
from motopark_bot.static_data import CarparkInfo


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


def format_nearest_results(ranked: list[RankedCarpark], live_by_id: dict[str, LiveLot]) -> str:
    if not ranked:
        return (
            "No carparks found nearby. Try sending a location closer to town, "
            "or use /check <carpark name> to search by name instead."
        )
    blocks = [
        format_carpark(r.info, live_by_id.get(r.info.car_park_no), distance_km=r.distance_km)
        for r in ranked
    ]
    return "\n\n".join(blocks)


def format_check_results(matches: list[CarparkInfo], live_by_id: dict[str, LiveLot]) -> str:
    if not matches:
        return (
            "No carparks matched that. Try a shorter search term (e.g. just the "
            "mall or street name), or share your location instead with /nearest."
        )
    blocks = [format_carpark(cp, live_by_id.get(cp.car_park_no)) for cp in matches]
    return "\n\n".join(blocks)

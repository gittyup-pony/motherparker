"""Shared Telegram message formatting for carpark results.

Streamlined to four things per result, per explicit request: address,
paid/free, distance (when known), and availability — availability is
always a state (available / full / no live data), never a raw lot count
or bay-capacity number. Three source types can appear in one /check or
/nearest reply (HDB via static_data.CarparkInfo, URA via
ura_data.UraCarpark, and — /check only — Carpark Rates via
carpark_rates_data.RateEntry); each gets its own formatter since the
underlying fields differ, but they all share _availability_line() so the
"no raw numbers" rule can't drift between them.

Shelter info, carpark codes, and night-parking used to be shown here too
— dropped along with the raw numbers to keep the reply to just the four
requested fields. Navigation is unaffected by any of this: bot.py builds
"🧭 Navigate" buttons separately, from responses.py's NavTarget list, not
from anything in this module.
"""
from __future__ import annotations

from motopark_bot.carpark_rates_data import RateEntry
from motopark_bot.lta_client import LiveLot
from motopark_bot.static_data import CarparkInfo
from motopark_bot.ura_data import UraCarpark


def _availability_line(live: LiveLot | None) -> str:
    """Available / full / unknown — deliberately never a specific lot
    count or bay-capacity number, per explicit request."""
    if live is None:
        return "❓ Availability: no live data"
    if live.available_lots <= 0:
        return "🔴 Full"
    return "✅ Available"


def format_carpark(info: CarparkInfo, live: LiveLot | None, distance_km: float | None = None) -> str:
    lines = [f"*{info.address}*", f"💰 {info.price_label.capitalize()}"]
    if distance_km is not None:
        lines.append(f"📍 {distance_km:.2f} km away")
    lines.append(_availability_line(live))
    return "\n".join(lines)


def format_ura_carpark(info: UraCarpark, live: LiveLot | None, distance_km: float | None = None) -> str:
    # No pricing field exists in this dataset at all (see ura_data.py) -
    # nothing honest to show for "paid or free" here, unlike HDB/Carpark
    # Rates, so that line is simply omitted rather than guessed at.
    lines = [f"*{info.name}*"]
    if distance_km is not None:
        lines.append(f"📍 {distance_km:.2f} km away")
    lines.append(_availability_line(live))
    return "\n".join(lines)


def format_rate_entry(entry: RateEntry, live: LiveLot | None) -> str:
    # Every Carpark Rates entry is inherently paid (it's a listing of
    # parking rates) - shown with the rate itself as the "paid" evidence,
    # not a separate lot count.
    lines = [f"*{entry.name}*"]
    price_bit = f"💰 Paid — from {entry.weekday_rate_1}" if entry.weekday_rate_1 else "💰 Paid"
    lines.append(price_bit)
    lines.append(_availability_line(live))
    return "\n".join(lines)


def join_blocks(blocks: list[str], empty_message: str) -> str:
    return "\n\n".join(blocks) if blocks else empty_message


NO_NEARBY_MESSAGE = (
    "No carparks found nearby. Try sending a location closer to town, "
    "or use /check <carpark name or postal code> to search instead."
)

NO_MATCH_MESSAGE = (
    "No carparks matched that. Try a shorter search term (e.g. just the "
    "mall or street name), a 6-digit postal code, or share your location "
    "instead with /nearest."
)

POSTAL_CODE_NOT_CONFIGURED_MESSAGE = (
    "Postal code search isn't set up on this bot yet. Try searching by "
    "carpark or mall name instead, e.g. `/check jurong point`."
)

POSTAL_CODE_NOT_FOUND_MESSAGE = (
    "Couldn't find that postal code. Double-check the 6 digits, or try "
    "searching by carpark or mall name instead."
)

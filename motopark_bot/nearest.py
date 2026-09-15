"""Nearest-carpark search: static dataset + haversine distance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from motopark_bot.geo import haversine_km


class Locatable(Protocol):
    """What find_nearest() needs: CarparkInfo and UraCarpark both satisfy
    this, so one ranking function works across both HDB and URA carparks
    (RateEntry doesn't — it has no coordinates, so it's /check-only)."""

    lat: float
    lon: float


T = TypeVar("T", bound=Locatable)


@dataclass(frozen=True)
class RankedCarpark(Generic[T]):
    info: T
    distance_km: float


def find_nearest(
    lat: float,
    lon: float,
    carparks: list[T],
    limit: int = 5,
    max_radius_km: float | None = None,
) -> list[RankedCarpark]:
    """Return the `limit` closest carparks to (lat, lon), nearest first.

    If max_radius_km is set, carparks farther than that are excluded
    entirely (rather than just sorted last) — a "nothing nearby" result is
    more honest than the 47th-closest carpark 8km away.
    """
    ranked = [
        RankedCarpark(info=cp, distance_km=haversine_km(lat, lon, cp.lat, cp.lon))
        for cp in carparks
    ]
    if max_radius_km is not None:
        ranked = [r for r in ranked if r.distance_km <= max_radius_km]
    ranked.sort(key=lambda r: r.distance_km)
    return ranked[:limit]

"""Geo helpers: SVY21 -> WGS84 conversion and great-circle distance.

The HDB Carpark Information dataset gives coordinates in SVY21 (Singapore's
local projected grid, in metres), not lat/long. Telegram location pins and
the LTA CarParkAvailabilityv2 feed use WGS84 lat/long, so everything needs
to be converted to lat/long before we can compare or compute distances.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# SVY21 projection constants (Singapore's official transverse mercator grid).
# Source: SLA's published SVY21 specification.
_A = 6378137.0  # WGS84 semi-major axis (m)
_F = 1 / 298.257223563  # WGS84 flattening
_OLAT = math.radians(1.366666)  # origin latitude
_OLON = math.radians(103.833333)  # origin longitude
_N0 = 38744.572  # false northing (m)
_E0 = 28001.642  # false easting (m)
_K0 = 1.0  # central scale factor


def svy21_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert SVY21 (x=easting, y=northing, metres) to (lat, lon) in degrees.

    Implements the standard SVY21 inverse transverse-mercator formula.
    Accurate to a few metres, which is more than enough for "which carpark
    is nearest" — we're not landing a plane.
    """
    e2 = 2 * _F - _F**2
    e4 = e2**2
    e6 = e2**3
    a = _A

    # Meridional arc length at the origin latitude.
    def meridian_arc(lat: float) -> float:
        return a * (
            (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
            - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
            + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
            - (35 * e6 / 3072) * math.sin(6 * lat)
        )

    m0 = meridian_arc(_OLAT)
    m = m0 + (y - _N0) / _K0

    n = a / math.sqrt(1 - e2)  # placeholder, refined below via mu iteration
    mu = m / (a * (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    e2p = e2 / (1 - e2)
    c1 = e2p * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    d = (x - _E0) / (n1 * _K0)

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * e2p) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * e2p - 3 * c1**2) * d**6 / 720
    )
    lon = _OLON + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * e2p + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)

    return math.degrees(lat), math.degrees(lon)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    r = 6371.0088  # mean Earth radius, km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float

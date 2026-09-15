"""OneMap Singapore geocoding: postal code -> (lat, lon), for /check.

OneMap's Search API now requires a free account: logging in with email +
password (POST /api/auth/post/getToken) returns a bearer token documented
as valid for ~72 hours, which must be sent on every subsequent
/api/common/elastic/search call. This is a real, current requirement,
confirmed against OneMap's own OpenAPI spec/API-catalog documentation
while building this — a few years ago the equivalent endpoint
(developers.onemap.sg/commonapi/search) was fully public with no signup,
which is why some older blog posts/tutorials describe it differently.

**Not tested against a live OneMap account** — this sandbox has no
ONEMAP_EMAIL/ONEMAP_PASSWORD and no network access to verify. Built
strictly from documented field names (`access_token`, `results`,
`LATITUDE`, `LONGITUDE`). Run the __main__ smoke test below once deployed
with real credentials to confirm; see README's verification section for
what to do if it doesn't match.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

AUTH_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"

_POSTAL_CODE_RE = re.compile(r"^\d{6}$")

# OneMap documents tokens as valid for 72h. Refresh a bit before that so a
# request never lands right on the expiry boundary.
_TOKEN_LIFETIME_SECONDS = 72 * 60 * 60
_TOKEN_REFRESH_MARGIN_SECONDS = 60 * 60


def is_postal_code(query: str) -> bool:
    """True for a bare 6-digit Singapore postal code, e.g. "238801"."""
    return bool(_POSTAL_CODE_RE.match(query.strip()))


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


class OneMapClient:
    """Logs in once, caches the token, and geocodes postal codes.

    Only the login token is cached (in-memory, per this TTL-less but
    time-bounded scheme) — postal-code -> coordinates lookups themselves
    are NOT cached here, unlike the TTL stores elsewhere in this codebase.
    They're one-off per /check call, not repeatedly re-fetched data.
    """

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._token: str | None = None
        self._token_expires_at: float = 0.0  # time.monotonic()-based

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        resp = await client.post(
            AUTH_URL, json={"email": self._email, "password": self._password}, timeout=30.0
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"OneMap login did not return an access_token: {payload}")
        self._token = token
        self._token_expires_at = (
            time.monotonic() + _TOKEN_LIFETIME_SECONDS - _TOKEN_REFRESH_MARGIN_SECONDS
        )
        return token

    async def geocode_postal_code(self, postal_code: str) -> GeoPoint | None:
        """Look up a 6-digit postal code. None if OneMap has no match for it."""
        async with httpx.AsyncClient() as client:
            token = await self._get_token(client)
            resp = await client.get(
                SEARCH_URL,
                params={
                    "searchVal": postal_code,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            payload = resp.json()

        results = payload.get("results") or []
        if not results:
            return None
        first = results[0]
        try:
            return GeoPoint(lat=float(first["LATITUDE"]), lon=float(first["LONGITUDE"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"OneMap search result missing/invalid LATITUDE/LONGITUDE: {first}"
            ) from exc


if __name__ == "__main__":
    # Manual smoke test:
    #   ONEMAP_EMAIL=you@example.com ONEMAP_PASSWORD=yourpassword \
    #     python -m motopark_bot.onemap [postal_code]
    # Confirms the field-name guesses above (access_token, results,
    # LATITUDE/LONGITUDE) actually match what OneMap returns for real.
    import asyncio
    import os
    import sys

    email = os.environ.get("ONEMAP_EMAIL")
    password = os.environ.get("ONEMAP_PASSWORD")
    if not email or not password:
        print("Set ONEMAP_EMAIL and ONEMAP_PASSWORD to run this smoke test.", file=sys.stderr)
        raise SystemExit(1)
    postal_code = sys.argv[1] if len(sys.argv) > 1 else "238801"  # ION Orchard

    async def main() -> None:
        client = OneMapClient(email, password)
        point = await client.geocode_postal_code(postal_code)
        if point is None:
            print(
                f"No result for postal code {postal_code!r} — either it's invalid, "
                "or OneMap's response shape doesn't match what this code expects."
            )
        else:
            print(f"{postal_code} -> lat={point.lat}, lon={point.lon}")

    asyncio.run(main())

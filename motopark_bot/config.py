"""Environment-driven configuration.

All secrets/config come from environment variables so the same code runs
locally (via a .env file loaded with python-dotenv) and on Railway (via
Railway's own env var UI).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in (see README.md)."
        )
    return value


class Settings:
    """Lazily-validated settings. Instantiate once at startup."""

    def __init__(self) -> None:
        self.bot_token: str = _require("TELEGRAM_BOT_TOKEN")
        self.lta_account_key: str = _require("LTA_ACCOUNT_KEY")

        # How many nearest carparks to return for /nearest.
        self.nearest_result_count: int = int(os.environ.get("NEAREST_RESULT_COUNT", "5"))

        # How far (km) we're willing to search before saying "nothing nearby".
        self.nearest_max_radius_km: float = float(os.environ.get("NEAREST_MAX_RADIUS_KM", "3.0"))

        # How long to cache the static HDB carpark dataset before re-fetching, in seconds.
        # It barely changes, so a long TTL (default 24h) is fine.
        self.static_data_ttl_seconds: int = int(os.environ.get("STATIC_DATA_TTL_SECONDS", str(24 * 60 * 60)))

        # How long to cache a single LTA live-availability poll, in seconds.
        # LTA refreshes this feed roughly every minute; polling faster just
        # wastes calls, so we cache short-term and re-fetch on demand.
        self.live_data_ttl_seconds: int = int(os.environ.get("LIVE_DATA_TTL_SECONDS", "45"))


_settings: "Settings | None" = None


def get_settings() -> "Settings":
    """Build (and cache) Settings on first use.

    Deliberately NOT constructed at import time: importing config.py (e.g.
    transitively, from a test that only exercises geo.py or matching.py)
    should never blow up just because TELEGRAM_BOT_TOKEN isn't set.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


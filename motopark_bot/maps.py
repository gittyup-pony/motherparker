"""Deep-link helper for "navigate here" buttons under /check and /nearest.

Uses Google Maps' documented universal URL scheme (a plain https:// link,
no API key required): https://developers.google.com/maps/documentation/urls/get-started

Opens the Google Maps app directly if it's installed (Android always;
iOS via universal links), otherwise falls back to the Google Maps website
in the browser - works cross-platform without detecting the user's device
or default maps app. Chosen over Apple Maps / native Telegram location
pins for simplicity; see README for the alternatives considered.
"""
from __future__ import annotations


def maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"

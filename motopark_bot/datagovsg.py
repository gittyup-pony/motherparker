"""Shared fetch helper for data.gov.sg's "poll-download" flow.

Unlike the HDB Carpark Information dataset (a proper datastore_search-able
table), the URA GeoJSON datasets and (per testing during development) the
Carpark Rates CSV are plain file resources: you resolve a dataset ID to a
presigned S3 URL and download the whole file, rather than paging through a
query API. This is data.gov.sg's standard modern flow for any dataset,
tabular or geospatial.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
from dataclasses import dataclass

import httpx

POLL_DOWNLOAD_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"

# Confirmed in production: this endpoint rate-limits (429) rapid successive
# calls. A single startup can easily make 4+ calls to it back-to-back (two
# datasets for ura_store, one for rates_store, plus - on a failed URA prime
# - two more from the raw-feature diagnostic), which is enough to trip it.
# fetch_download_url() below retries with backoff instead of failing
# immediately on a 429.
_MAX_POLL_DOWNLOAD_RETRIES = 4

# Some of data.gov.sg's older geospatial layers (this one included, going by
# its field names — OBJECTID_1, NO_H_VEHIC, FMEL_UPD_D: classic 10-char-max
# SHP attribute names) were migrated from ArcGIS/SHP sources. Those exports
# often put every real attribute inside a single "Description" property as
# an HTML table, rather than as clean top-level GeoJSON properties. We
# couldn't fetch a live sample to confirm which shape these two datasets
# use (network-restricted dev sandbox), so this parses both: clean
# properties if present, else this HTML-table fallback.
_HTML_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<th[^>]*>\s*(.*?)\s*</th>\s*<td[^>]*>\s*(.*?)\s*</td>\s*</tr>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_description_html_table(html: str) -> dict[str, str]:
    """Parse a "Description" field that's an HTML <table> of attr/value rows.

    Returns {} if it doesn't look like that shape at all.
    """
    result: dict[str, str] = {}
    for key, value in _HTML_ROW_RE.findall(html):
        result[_strip_tags(key)] = _strip_tags(value)
    return result


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """How long to back off before retrying a 429, in seconds.

    Honors a numeric Retry-After header if the server sends one; otherwise
    a simple linear backoff (2s, 4s, 6s, ...).
    """
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(float(header), 0.5)
        except ValueError:
            pass
    return 2.0 * (attempt + 1)


async def fetch_download_url(client: httpx.AsyncClient, dataset_id: str) -> str:
    for attempt in range(_MAX_POLL_DOWNLOAD_RETRIES):
        resp = await client.get(POLL_DOWNLOAD_URL.format(dataset_id=dataset_id), timeout=30.0)
        if resp.status_code == 429:
            if attempt == _MAX_POLL_DOWNLOAD_RETRIES - 1:
                resp.raise_for_status()  # out of retries - surface the real 429
            await asyncio.sleep(_retry_after_seconds(resp, attempt))
            continue
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0 or not payload.get("data", {}).get("url"):
            raise RuntimeError(f"poll-download for {dataset_id} did not return a usable URL: {payload}")
        return payload["data"]["url"]
    raise AssertionError("unreachable")  # the loop above always returns or raises


@dataclass(frozen=True)
class GeoFeature:
    lon: float
    lat: float
    properties: dict[str, str]


def parse_geojson(geojson: dict) -> list[GeoFeature]:
    """Pure parse: a decoded GeoJSON FeatureCollection -> flat GeoFeatures.

    Assumes Point geometries with [lon, lat] coordinates (GeoJSON/RFC 7946
    standard order and CRS — data.gov.sg's GeoJSON exports are WGS84, unlike
    the SVY21-coordinate CSV/datastore exports). Separated from the network
    fetch below so it's directly unit-testable against fixture data.
    """
    features: list[GeoFeature] = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])

        raw_props = feat.get("properties") or {}
        # Prefer clean properties if they already look populated with real
        # attributes (more than just Name/Description placeholders).
        props = {k: str(v) for k, v in raw_props.items() if v not in (None, "")}
        description = raw_props.get("Description") or raw_props.get("description")
        if description and isinstance(description, str):
            html_props = _parse_description_html_table(description)
            # HTML-table values take precedence when present — that's where
            # the real per-feature data lives for this export shape.
            props.update(html_props)

        features.append(GeoFeature(lon=lon, lat=lat, properties=props))
    return features


def parse_csv_text(text: str) -> list[dict[str, str]]:
    """Pure parse: CSV text -> list of row dicts. Separated for testability."""
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


async def fetch_geojson_features(dataset_id: str) -> list[GeoFeature]:
    """Download + parse a data.gov.sg GeoJSON dataset into flat features."""
    geojson = await fetch_raw_geojson(dataset_id)
    return parse_geojson(geojson)


async def fetch_raw_geojson(dataset_id: str) -> dict:
    """Download the decoded GeoJSON as-is, with none of parse_geojson()'s
    Point-only filtering or clean/HTML-table property resolution applied.

    Exists for diagnosing a dataset that parses to zero usable records
    (see ura_data.py's log_raw_feature_sample) — if the geometry type
    isn't "Point", or the top-level shape isn't what's expected,
    fetch_geojson_features() silently returns [] and gives no clue why.
    This gives the ground truth instead.
    """
    async with httpx.AsyncClient() as client:
        url = await fetch_download_url(client, dataset_id)
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.json()


async def fetch_csv_rows(dataset_id: str) -> list[dict[str, str]]:
    """Download + parse a data.gov.sg CSV dataset into a list of row dicts."""
    async with httpx.AsyncClient() as client:
        url = await fetch_download_url(client, dataset_id)
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        text = resp.text
    return parse_csv_text(text)

import json
from pathlib import Path

import httpx
import pytest

from motopark_bot import datagovsg
from motopark_bot.datagovsg import parse_csv_text, parse_geojson

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_geojson_clean_properties():
    data = json.loads((FIXTURES / "sample_ura_capacity_clean.geojson.json").read_text())
    features = parse_geojson(data)
    assert len(features) == 2

    f = features[0]
    assert f.lon == 103.8480
    assert f.lat == 1.3005
    assert f.properties["PP_CODE"] == "SP0001"
    assert f.properties["NO_MCYCLE"] == "40"


def test_parse_geojson_html_description_fallback():
    data = json.loads((FIXTURES / "sample_ura_capacity_html_description.geojson.json").read_text())
    features = parse_geojson(data)
    assert len(features) == 1

    f = features[0]
    # These only exist because the HTML-table-in-Description parser found
    # them - "Name" and "Description" are the only real top-level keys.
    assert f.properties["PP_CODE"] == "SP0001"
    assert f.properties["PARKING_PL"] == "ORCHARD ROAD CARPARK"
    assert f.properties["NO_MCYCLE"] == "40"


def test_parse_geojson_skips_non_point_geometry():
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"X": "1"}},
        ],
    }
    features = parse_geojson(data)
    assert len(features) == 1
    assert features[0].properties["X"] == "1"


def test_parse_geojson_empty_features():
    assert parse_geojson({"type": "FeatureCollection", "features": []}) == []
    assert parse_geojson({}) == []


def test_parse_csv_text_basic():
    text = (FIXTURES / "sample_carpark_rates.csv").read_text()
    rows = parse_csv_text(text)
    assert len(rows) == 3
    assert rows[0]["Carpark"] == "ION Orchard"
    assert rows[0]["Category"] == "Orchard"


# --- fetch_download_url's 429 retry-with-backoff -----------------------
#
# Regression coverage for a real production incident: data.gov.sg's
# poll-download endpoint rate-limited (429) a burst of calls from a single
# bot startup (2 for ura_store's two datasets, plus 2 more from the
# ura_data.log_raw_feature_sample diagnostic firing right after). Without
# a retry, that 429 just failed the whole request instead of the caller
# ever seeing real data.


class _FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, url, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


def test_retry_after_seconds_uses_header_when_present():
    resp = _FakeResponse(429, headers={"Retry-After": "3"})
    assert datagovsg._retry_after_seconds(resp, attempt=0) == 3.0


def test_retry_after_seconds_falls_back_to_linear_backoff_without_header():
    resp = _FakeResponse(429, headers={})
    assert datagovsg._retry_after_seconds(resp, attempt=2) == 6.0


@pytest.mark.asyncio
async def test_fetch_download_url_retries_on_429_then_succeeds():
    responses = [
        _FakeResponse(429, headers={"Retry-After": "0"}),
        _FakeResponse(200, json_data={"code": 0, "data": {"url": "https://example.com/file.geojson"}}),
    ]
    client = _FakeClient(responses)

    url = await datagovsg.fetch_download_url(client, "d_test")

    assert url == "https://example.com/file.geojson"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_fetch_download_url_raises_after_exhausting_retries():
    responses = [_FakeResponse(429, headers={"Retry-After": "0"}) for _ in range(datagovsg._MAX_POLL_DOWNLOAD_RETRIES)]
    client = _FakeClient(responses)

    with pytest.raises(httpx.HTTPStatusError):
        await datagovsg.fetch_download_url(client, "d_test")

    assert client.calls == datagovsg._MAX_POLL_DOWNLOAD_RETRIES


@pytest.mark.asyncio
async def test_fetch_download_url_raises_runtime_error_on_bad_payload():
    client = _FakeClient([_FakeResponse(200, json_data={"code": 1, "data": {}})])

    with pytest.raises(RuntimeError):
        await datagovsg.fetch_download_url(client, "d_test")

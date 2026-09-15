"""Tests for onemap.py: postal-code detection and OneMapClient's geocoding.

No real network/credentials available here (see onemap.py's module
docstring) - OneMapClient is exercised against a fake httpx.AsyncClient
stand-in, following the same fake-response pattern test_datagovsg.py uses
for datagovsg.fetch_download_url's 429-retry tests.
"""
import httpx
import pytest

from motopark_bot import onemap
from motopark_bot.onemap import GeoPoint, OneMapClient, is_postal_code


def test_is_postal_code_valid():
    assert is_postal_code("238801")
    assert is_postal_code("  238801  ")  # surrounding whitespace stripped


def test_is_postal_code_rejects_wrong_shape():
    assert not is_postal_code("23880")  # 5 digits
    assert not is_postal_code("2388011")  # 7 digits
    assert not is_postal_code("23880a")  # non-digit
    assert not is_postal_code("jurong point")  # a name query, not a code
    assert not is_postal_code("")


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient - tracks calls so tests can confirm
    the token gets cached/reused instead of re-fetched on every call."""

    def __init__(self, auth_response, search_responses):
        self.auth_response = auth_response
        self.search_responses = list(search_responses)
        self.auth_calls = 0
        self.search_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, timeout=None):
        assert url == onemap.AUTH_URL
        self.auth_calls += 1
        return self.auth_response

    async def get(self, url, params=None, headers=None, timeout=None):
        assert url == onemap.SEARCH_URL
        assert headers.get("Authorization") == "Bearer fake-token"
        self.search_calls += 1
        return self.search_responses.pop(0)


def _patch_client(monkeypatch, fake_client: _FakeAsyncClient) -> None:
    monkeypatch.setattr(onemap.httpx, "AsyncClient", lambda *a, **kw: fake_client)


@pytest.mark.asyncio
async def test_geocode_postal_code_success(monkeypatch):
    fake = _FakeAsyncClient(
        auth_response=_FakeResponse({"access_token": "fake-token"}),
        search_responses=[
            _FakeResponse(
                {"results": [{"LATITUDE": "1.3039", "LONGITUDE": "103.8358", "ADDRESS": "ION ORCHARD"}]}
            )
        ],
    )
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "hunter2")
    point = await client.geocode_postal_code("238801")

    assert point == GeoPoint(lat=1.3039, lon=103.8358)
    assert fake.auth_calls == 1


@pytest.mark.asyncio
async def test_geocode_postal_code_no_results_returns_none(monkeypatch):
    fake = _FakeAsyncClient(
        auth_response=_FakeResponse({"access_token": "fake-token"}),
        search_responses=[_FakeResponse({"results": []})],
    )
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "hunter2")
    point = await client.geocode_postal_code("999999")

    assert point is None


@pytest.mark.asyncio
async def test_geocode_postal_code_malformed_result_raises(monkeypatch):
    fake = _FakeAsyncClient(
        auth_response=_FakeResponse({"access_token": "fake-token"}),
        search_responses=[_FakeResponse({"results": [{"ADDRESS": "NO COORDS HERE"}]})],
    )
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "hunter2")
    with pytest.raises(RuntimeError):
        await client.geocode_postal_code("238801")


@pytest.mark.asyncio
async def test_login_without_access_token_raises(monkeypatch):
    fake = _FakeAsyncClient(auth_response=_FakeResponse({"error": "bad credentials"}), search_responses=[])
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "wrong-password")
    with pytest.raises(RuntimeError):
        await client.geocode_postal_code("238801")


@pytest.mark.asyncio
async def test_token_is_cached_across_calls(monkeypatch):
    fake = _FakeAsyncClient(
        auth_response=_FakeResponse({"access_token": "fake-token"}),
        search_responses=[
            _FakeResponse({"results": [{"LATITUDE": "1.30", "LONGITUDE": "103.80"}]}),
            _FakeResponse({"results": [{"LATITUDE": "1.31", "LONGITUDE": "103.81"}]}),
        ],
    )
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "hunter2")
    await client.geocode_postal_code("238801")
    await client.geocode_postal_code("119613")

    # Two searches, but only one login - the cached token was reused.
    assert fake.search_calls == 2
    assert fake.auth_calls == 1


@pytest.mark.asyncio
async def test_token_refetched_once_expired(monkeypatch):
    fake = _FakeAsyncClient(
        auth_response=_FakeResponse({"access_token": "fake-token"}),
        search_responses=[
            _FakeResponse({"results": [{"LATITUDE": "1.30", "LONGITUDE": "103.80"}]}),
            _FakeResponse({"results": [{"LATITUDE": "1.31", "LONGITUDE": "103.81"}]}),
        ],
    )
    _patch_client(monkeypatch, fake)

    client = OneMapClient("user@example.com", "hunter2")
    await client.geocode_postal_code("238801")

    # Force the cached token to look expired without waiting ~71 hours.
    client._token_expires_at = 0.0

    await client.geocode_postal_code("119613")

    assert fake.search_calls == 2
    assert fake.auth_calls == 2

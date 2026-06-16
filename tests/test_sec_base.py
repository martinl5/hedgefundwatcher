"""Tests for SECBaseClient request retry/backoff behaviour."""

from unittest.mock import MagicMock, patch

import requests

from sec_base import SECBaseClient


def _resp(status_code: int, headers: dict | None = None) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.headers = headers or {}
    return r


def test_request_returns_response_on_success():
    client = SECBaseClient()
    ok = _resp(200)
    with patch.object(client.session, "get", return_value=ok) as get:
        result = client.request("http://example/x")
    assert result is ok
    get.assert_called_once()


def test_request_retries_on_429_then_succeeds():
    client = SECBaseClient()
    ok = _resp(200)
    with patch.object(
        client.session, "get", side_effect=[_resp(429, {"Retry-After": "1"}), ok]
    ) as get:
        result = client.request("http://example/x")
    assert result is ok
    assert get.call_count == 2


def test_request_returns_last_response_when_5xx_exhausted():
    client = SECBaseClient()
    with patch.object(client.session, "get", return_value=_resp(503)) as get:
        result = client.request("http://example/x")
    assert result is not None
    assert result.status_code == 503
    assert get.call_count == 4  # initial attempt + MAX_RETRIES


def test_request_returns_none_on_persistent_connection_error():
    client = SECBaseClient()
    with patch.object(
        client.session, "get", side_effect=requests.ConnectionError("down")
    ) as get:
        result = client.request("http://example/x")
    assert result is None
    assert get.call_count == 4


def test_request_recovers_after_transient_connection_error():
    client = SECBaseClient()
    ok = _resp(200)
    with patch.object(
        client.session, "get", side_effect=[requests.Timeout("slow"), ok]
    ):
        result = client.request("http://example/x")
    assert result is ok

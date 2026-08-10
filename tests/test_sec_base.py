"""Tests for SECBaseClient request retry/backoff behaviour."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from sec_base import FALLBACK_USER_AGENT, SECBaseClient


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


# ─────────────────────────── User-Agent ──────────────────────────


def test_user_agent_read_from_environment(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "MyWatcher/2.0 (me@example.com)")
    client = SECBaseClient()
    assert client.session.headers["User-Agent"] == "MyWatcher/2.0 (me@example.com)"


def test_user_agent_falls_back_and_warns_when_unset(monkeypatch, caplog):
    # SEC serves 403 to clients without a real contact address, so an unset
    # SEC_USER_AGENT needs to be loud rather than silently placeholder.
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with caplog.at_level("WARNING"):
        client = SECBaseClient()
    assert client.session.headers["User-Agent"] == FALLBACK_USER_AGENT
    assert any("SEC_USER_AGENT" in r.message for r in caplog.records)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_user_agent_env_falls_back(monkeypatch, value):
    monkeypatch.setenv("SEC_USER_AGENT", value)
    client = SECBaseClient()
    assert client.session.headers["User-Agent"] == FALLBACK_USER_AGENT

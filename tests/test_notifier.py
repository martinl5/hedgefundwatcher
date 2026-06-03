"""Tests for TelegramNotifier message formatting and sending."""

from unittest.mock import MagicMock, patch

import requests

from models import Holding, HoldingsChange
from notifier import TelegramNotifier, format_holding_summary

TOKEN = "1234567890:ABCDefghijklmnopqrstuvwxyzABCDEFGHI"
CHAT_ID = "-100123456789"


# ─────────────────── send_message ────────────────────────────────


def test_send_message_returns_true_on_success():
    notifier = TelegramNotifier(TOKEN, CHAT_ID)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = notifier.send_message("Hello")
    assert result is True
    mock_post.assert_called_once()


def test_send_message_returns_false_on_http_error():
    notifier = TelegramNotifier(TOKEN, CHAT_ID)
    with patch("requests.post", side_effect=requests.RequestException("timeout")):
        result = notifier.send_message("Hello")
    assert result is False


def test_send_message_returns_false_without_credentials():
    notifier = TelegramNotifier("", "")
    result = notifier.send_message("Hello")
    assert result is False


# ─────────────────── send_filing_alert ───────────────────────────


def _alert_with_changes(**kwargs):
    defaults = dict(
        new_positions=[],
        increased_positions=[],
        decreased_positions=[],
        removed_positions=[],
    )
    defaults.update(kwargs)
    return HoldingsChange(**defaults)


def _capture_alert(changes: HoldingsChange) -> str:
    """Run send_filing_alert and return the text that would be sent to Telegram."""
    sent: list = []

    def fake_send(text, parse_mode="Markdown"):
        sent.append(text)
        return True

    notifier = TelegramNotifier(TOKEN, CHAT_ID)
    notifier.send_message = fake_send  # type: ignore[method-assign]
    notifier.send_filing_alert("Test Fund", "2024-01-01", changes)
    return sent[0] if sent else ""


def test_alert_no_changes_sends_nothing():
    sent: list = []
    notifier = TelegramNotifier(TOKEN, CHAT_ID)
    notifier.send_message = lambda t, **kw: sent.append(t) or True  # type: ignore[method-assign]
    notifier.send_filing_alert("Fund", "2024-01-01", HoldingsChange())
    assert sent == []


def test_alert_new_position_appears_in_message():
    h = Holding("AAPL", "Apple Inc", 1000, 185_000)
    changes = _alert_with_changes(new_positions=[h])
    msg = _capture_alert(changes)
    assert "Apple Inc" in msg
    assert "NEW" in msg


def test_alert_increased_position_shows_percentage():
    old_h = Holding("AAPL", "Apple Inc", 100, 10_000)
    new_h = Holding("AAPL", "Apple Inc", 150, 15_000)
    changes = _alert_with_changes(increased_positions=[(old_h, new_h)])
    msg = _capture_alert(changes)
    assert "INCREASED" in msg
    assert "50.0%" in msg


def test_alert_decreased_position_shows_percentage():
    old_h = Holding("MSFT", "Microsoft", 200, 20_000)
    new_h = Holding("MSFT", "Microsoft", 80, 8_000)
    changes = _alert_with_changes(decreased_positions=[(old_h, new_h)])
    msg = _capture_alert(changes)
    assert "DECREASED" in msg
    assert "60.0%" in msg


def test_alert_removed_position_appears_in_message():
    h = Holding("TSLA", "Tesla Inc", 500, 100_000)
    changes = _alert_with_changes(removed_positions=[h])
    msg = _capture_alert(changes)
    assert "Tesla Inc" in msg
    assert "REMOVED" in msg


def test_alert_limits_new_positions_to_10():
    holdings = [Holding(f"T{i}", f"Company {i}", i * 10, i * 1000) for i in range(20)]
    changes = _alert_with_changes(new_positions=holdings)
    msg = _capture_alert(changes)
    assert "10 more" in msg


def test_alert_includes_fund_name_and_date():
    h = Holding("AAPL", "Apple", 100, 10_000)
    changes = _alert_with_changes(new_positions=[h])
    msg = _capture_alert(changes)
    assert "Test Fund" in msg
    assert "2024-01-01" in msg


# ─────────────────── format_holding_summary ──────────────────────


def test_format_holding_summary_empty():
    assert format_holding_summary([]) == "None"


def test_format_holding_summary_large_value():
    holdings = [Holding("AAPL", "Apple", 100, 5_000_000)]
    result = format_holding_summary(holdings)
    assert "5.0M" in result
    assert "Apple" in result


def test_format_holding_summary_small_value():
    holdings = [Holding("XYZ", "Tiny Corp", 10, 500)]
    result = format_holding_summary(holdings)
    assert "$500" in result


def test_format_holding_summary_overflow_count():
    holdings = [Holding(f"T{i}", f"Co {i}", i, i * 100) for i in range(1, 8)]
    result = format_holding_summary(holdings, max_items=5)
    assert "+2 more" in result

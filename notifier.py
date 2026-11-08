"""Telegram notification client."""

import logging
from datetime import datetime

import requests

from models import Holding, HoldingsChange, trade_value

logger = logging.getLogger(__name__)


def _format_change_line(old_h: Holding, new_h: Holding) -> str:
    """Render a position change, leading with shares — the quantity actually traded.

    Market value is shown as context only; it moves with the share price, so it
    is not the figure the percentage is computed from.
    """
    pct_str = ""
    if old_h.shares > 0:
        pct = ((new_h.shares - old_h.shares) / old_h.shares) * 100
        pct_str = f" ({pct:+.1f}%)"
    return (
        f"• {new_h.display_name}: {old_h.shares:,} → {new_h.shares:,} shares{pct_str}"
        f"  |  ${old_h.value:,.0f} → ${new_h.value:,.0f}"
        f"  |  ≈${trade_value(old_h, new_h):,.0f} traded"
    )


class TelegramNotifier:
    """Sends alerts via Telegram Bot API."""

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to the configured chat; returns True on success."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram credentials not configured")
            return False

        url = f"{self.base_url}/sendMessage"
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}

        try:
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False

    def send_filing_alert(self, fund_name: str, filing_date: str, changes: HoldingsChange) -> bool:
        """Send an alert about 13F filing changes; no-ops if there are no changes."""
        if not changes.has_changes():
            return True

        parts: list[str] = [
            f"📊 *13F Filing Alert: {fund_name}*",
            f"📅 Filing Date: {filing_date}",
            "",
        ]

        if changes.new_positions:
            parts.append("🆕 *NEW POSITIONS:*")
            for h in changes.new_positions[:10]:
                value_str = f"${h.value:,.0f}" if h.value else "$0"
                shares_str = f"{h.shares:,}" if h.shares else "0"
                parts.append(f"• {h.display_name}: {shares_str} shares = {value_str}")
            if len(changes.new_positions) > 10:
                parts.append(f"  ... and {len(changes.new_positions) - 10} more")
            parts.append("")

        if changes.increased_positions:
            parts.append("📈 *INCREASED POSITIONS (>20% of shares):*")
            for old_h, new_h in changes.increased_positions[:10]:
                parts.append(_format_change_line(old_h, new_h))
            if len(changes.increased_positions) > 10:
                parts.append(f"  ... and {len(changes.increased_positions) - 10} more")
            parts.append("")

        if changes.decreased_positions:
            parts.append("📉 *DECREASED POSITIONS (>20% of shares):*")
            for old_h, new_h in changes.decreased_positions[:10]:
                parts.append(_format_change_line(old_h, new_h))
            if len(changes.decreased_positions) > 10:
                parts.append(f"  ... and {len(changes.decreased_positions) - 10} more")
            parts.append("")

        if changes.removed_positions:
            parts.append("❌ *REMOVED POSITIONS:*")
            for h in changes.removed_positions[:10]:
                value_str = f"${h.value:,.0f}" if h.value else "$0"
                parts.append(f"• {h.display_name}: {value_str}")
            if len(changes.removed_positions) > 10:
                parts.append(f"  ... and {len(changes.removed_positions) - 10} more")
            parts.append("")

        parts.extend(["---", f"🕐 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
        return self.send_message("\n".join(parts))

    def send_test_message(self) -> bool:
        """Send a test message to verify Telegram configuration."""
        message = (
            "🧪 *HedgeFundWatcher Test*\n\n"
            "✅ Your Telegram bot is configured correctly!\n"
            "You'll receive 13F filing alerts here."
        )
        return self.send_message(message)


def format_holding_summary(holdings: list[Holding], max_items: int = 5) -> str:
    """Format holdings as a brief summary string."""
    if not holdings:
        return "None"

    sorted_holdings = sorted(holdings, key=lambda h: h.value, reverse=True)
    items = []
    for h in sorted_holdings[:max_items]:
        value_str = f"${h.value / 1_000_000:.1f}M" if h.value >= 1_000_000 else f"${h.value:,.0f}"
        items.append(f"{h.name} ({value_str})")

    result = ", ".join(items)
    if len(sorted_holdings) > max_items:
        result += f" +{len(sorted_holdings) - max_items} more"
    return result

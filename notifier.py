"""Telegram notification client"""

import requests
from typing import Optional, List
from datetime import datetime
from models import Filing, Holding, HoldingsChange


class TelegramNotifier:
    """Sends alerts via Telegram Bot API"""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to the configured chat"""
        if not self.token or not self.chat_id:
            print("Telegram credentials not configured")
            return False
        
        url = f"{self.base_url}/sendMessage"
        
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        try:
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")
            return False
    
    def send_filing_alert(self, fund_name: str, filing_date: str, changes: HoldingsChange) -> bool:
        """Send an alert about filing changes"""
        if not changes.has_changes():
            return True  # No changes, nothing to send
        
        message_parts = []
        
        # Header
        message_parts.append(f"📊 *13F Filing Alert: {fund_name}*")
        message_parts.append(f"📅 Filing Date: {filing_date}")
        message_parts.append("")
        
        # New positions
        if changes.new_positions:
            message_parts.append("🆕 *NEW POSITIONS:*")
            for h in changes.new_positions[:10]:  # Limit to 10
                value_str = f"${h.value:,.0f}" if h.value else "$0"
                shares_str = f"{h.shares:,}" if h.shares else "0"
                message_parts.append(f"• {h.name} ({h.ticker}): {shares_str} shares = {value_str}")
            if len(changes.new_positions) > 10:
                message_parts.append(f"  ... and {len(changes.new_positions) - 10} more")
            message_parts.append("")
        
        # Increased positions
        if changes.increased_positions:
            message_parts.append("📈 *INCREASED POSITIONS (>20%):*")
            for old_h, new_h in changes.increased_positions[:10]:
                pct = ((new_h.value - old_h.value) / old_h.value) * 100 if old_h.value > 0 else 0
                message_parts.append(f"• {new_h.name}: ${old_h.value:,.0f} → ${new_h.value:,.0f} (+{pct:.1f}%)")
            if len(changes.increased_positions) > 10:
                message_parts.append(f"  ... and {len(changes.increased_positions) - 10} more")
            message_parts.append("")
        
        # Decreased positions
        if changes.decreased_positions:
            message_parts.append("📉 *DECREASED POSITIONS (>20%):*")
            for old_h, new_h in changes.decreased_positions[:10]:
                pct = ((old_h.value - new_h.value) / old_h.value) * 100 if old_h.value > 0 else 0
                message_parts.append(f"• {new_h.name}: ${old_h.value:,.0f} → ${new_h.value:,.0f} (-{pct:.1f}%)")
            if len(changes.decreased_positions) > 10:
                message_parts.append(f"  ... and {len(changes.decreased_positions) - 10} more")
            message_parts.append("")
        
        # Removed positions
        if changes.removed_positions:
            message_parts.append("❌ *REMOVED POSITIONS:*")
            for h in changes.removed_positions[:10]:
                value_str = f"${h.value:,.0f}" if h.value else "$0"
                message_parts.append(f"• {h.name} ({h.ticker}): {value_str}")
            if len(changes.removed_positions) > 10:
                message_parts.append(f"  ... and {len(changes.removed_positions) - 10} more")
            message_parts.append("")
        
        # Footer
        message_parts.append("---")
        message_parts.append(f"🕐 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        message = "\n".join(message_parts)
        
        return self.send_message(message)
    
    def send_test_message(self) -> bool:
        """Send a test message"""
        message = "🧪 *HedgeFundWatcher Test*\n\n" \
                  "✅ Your Telegram bot is configured correctly!\n" \
                  "You'll receive 13F filing alerts here."
        
        return self.send_message(message)


def format_holding_summary(holdings: List[Holding], max_items: int = 5) -> str:
    """Format holdings as a brief summary string"""
    if not holdings:
        return "None"
    
    # Sort by value descending
    sorted_holdings = sorted(holdings, key=lambda h: h.value, reverse=True)
    
    items = []
    for h in sorted_holdings[:max_items]:
        value_str = f"${h.value/1000000:.1f}M" if h.value >= 1000000 else f"${h.value:,.0f}"
        items.append(f"{h.name} ({value_str})")
    
    result = ", ".join(items)
    if len(sorted_holdings) > max_items:
        result += f" +{len(sorted_holdings) - max_items} more"
    
    return result
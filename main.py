"""HedgeFundWatcher - Main entry point and CLI"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta
from typing import List, Optional

from models import Config, TrackedFund, FilingCache, State, HoldingsChange, Filing
from sec_client import SECClient, HEDGE_FUND_CIKS, compare_filings
from sec_filings import SECFilingsClient
from notifier import TelegramNotifier


DATA_DIR = "data"


def ensure_data_dir():
    """Ensure data directory exists"""
    os.makedirs(DATA_DIR, exist_ok=True)


class OutputHandler:
    """Handle output to both console and optionally Telegram"""
    
    def __init__(self, notifier=None):
        self.notifier = notifier
        self.lines = []
    
    def add(self, line=""):
        print(line)
        self.lines.append(line)
    
    def send_to_telegram(self, title=""):
        """Send accumulated output to Telegram"""
        if self.notifier and self.lines:
            # Telegram has 4096 char limit, split if needed
            message = "\n".join(self.lines)
            
            # Split into chunks if too long
            if len(message) > 4000:
                # Send title first
                if title:
                    self.notifier.send_message(f"📊 *{title}*")
                
                # Send in chunks
                chunk_size = 3800
                for i in range(0, len(message), chunk_size):
                    chunk = message[i:i+chunk_size]
                    self.notifier.send_message(chunk)
            else:
                if title:
                    message = f"📊 *{title}*\n\n{message}"
                self.notifier.send_message(message)


def setup_config(args) -> Config:
    """Setup or load configuration"""
    config = Config()
    config.data_dir = DATA_DIR
    config.load()
    
    # Update from args if provided
    if args.telegram_token:
        config.telegram_token = args.telegram_token
    if args.telegram_chat_id:
        config.telegram_chat_id = args.telegram_chat_id
    
    if config.telegram_token or config.telegram_chat_id:
        config.save()
    
    return config


def run_scan(config: Config, cache: FilingCache, notifier: Optional[TelegramNotifier] = None):
    """Run the filing scan for all tracked funds"""
    client = SECClient()
    results = []
    
    print(f"Scanning {len(config.tracked_funds)} tracked funds...")
    
    for fund in config.tracked_funds:
        try:
            print(f"  Checking {fund.name} (CIK: {fund.cik})...")
            
            # Get latest 13F filing
            filing_info = client.get_latest_13f_filing(fund.cik)
            
            if not filing_info:
                print(f"    No 13F filing found")
                continue
            
            filing_date = filing_info.get("filing_date", "")
            
            # Check if we already processed this filing
            if fund.last_filing_date == filing_date:
                print(f"    No new filing (last: {filing_date})")
                continue
            
            # Get filing content (with fallback to folder search)
            url = client.get_filing_url(filing_info)
            folder_url = client.get_filing_folder_url(filing_info)
            content = client.get_filing_content(url, folder_url)
            if not content:
                print(f"    Could not fetch filing content")
                continue
            
            # Parse the filing
            parsed_filing = client.parse_13f_filing(content, url)
            if not parsed_filing or not parsed_filing.holdings:
                print(f"    Could not parse holdings")
                continue
            
            # Set fund info
            parsed_filing.cik = fund.cik
            parsed_filing.fund_name = fund.name
            parsed_filing.filing_date = filing_date
            
            # Compare with previous filing
            old_filing = cache.get_last_filing(fund.cik)
            
            if old_filing:
                changes = HoldingsChange(
                    new_positions=[],
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[]
                )
                
                new_pos, inc_pos, dec_pos, rem_pos = compare_filings(old_filing, parsed_filing)
                changes.new_positions = new_pos
                changes.increased_positions = inc_pos
                changes.decreased_positions = dec_pos
                changes.removed_positions = rem_pos
            else:
                # First time - show all holdings as new
                changes = HoldingsChange(
                    new_positions=parsed_filing.holdings[:20],  # Top 20
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[]
                )
            
            # Update cache
            cache.update_filing(parsed_filing)
            
            # Update fund's last filing date
            fund.last_filing_date = filing_date
            config.save()
            
            # Send notification
            if notifier:
                print(f"    Sending alert...")
                notifier.send_filing_alert(fund.name, filing_date, changes)
            
            results.append({
                "fund": fund.name,
                "filing_date": filing_date,
                "changes": changes
            })
            
            print(f"    ✓ Processed (filing date: {filing_date})")
            
        except Exception as e:
            print(f"    ✗ Error: {e}")
            continue
    
    return results


def add_fund(config: Config, name: str, cik: str):
    """Add a new fund to track"""
    # Normalize CIK (remove leading zeros for lookup, keep for API)
    cik = cik.lstrip("0")
    
    fund = TrackedFund(cik=cik, name=name)
    config.add_fund(fund)
    print(f"✓ Added {name} (CIK: {cik}) to tracking list")


def list_funds(config: Config):
    """List all tracked funds"""
    if not config.tracked_funds:
        print("No funds currently tracked.")
        print("Use: python main.py add <name> <CIK>")
        return
    
    print(f"Tracked funds ({len(config.tracked_funds)}):")
    for i, fund in enumerate(config.tracked_funds, 1):
        print(f"  {i}. {fund.name} (CIK: {fund.cik}) - Last filing: {fund.last_filing_date or 'Never'}")


def list_known_funds(args):
    """List known hedge fund CIKs"""
    print("Known hedge fund CIKs (add any using: python main.py add <name> <CIK>):")
    for name, cik in sorted(HEDGE_FUND_CIKS.items()):
        print(f"  • {name}: {cik}")


def show_report(config: Config, notifier=None):
    """Show detailed report of all fund changes - compares latest to previous filing"""
    out = OutputHandler(notifier)
    client = SECClient()
    
    out.add("📊 *13F FILING CHANGES REPORT*")
    out.add()
    
    for fund in config.tracked_funds:
        try:
            out.add(f"*{fund.name}*")
            out.add(f"CIK: {fund.cik}")
            
            # Get latest filings (latest and previous)
            all_filings = client.get_13f_filings(fund.cik)
            
            if not all_filings:
                out.add("❌ No 13F filings found")
                out.add()
                continue
            
            latest_filing = all_filings[0]
            prev_filing = all_filings[1] if len(all_filings) > 1 else None
            
            latest_date = latest_filing.get("filing_date", "Unknown")
            prev_date = prev_filing.get("filing_date", "N/A") if prev_filing else "N/A"
            
            out.add(f"📅 Latest: {latest_date} | Previous: {prev_date}")
            
            # Get latest filing content
            url = client.get_filing_url(latest_filing)
            folder_url = client.get_filing_folder_url(latest_filing)
            content = client.get_filing_content(url, folder_url)
            
            if not content:
                out.add("❌ Could not fetch latest filing")
                out.add()
                continue
            
            latest_parsed = client.parse_13f_filing(content, url)
            
            if not latest_parsed or not latest_parsed.holdings:
                out.add("❌ Could not parse latest filing")
                out.add()
                continue
            
            # Get previous filing content
            prev_parsed = None
            if prev_filing:
                prev_url = client.get_filing_url(prev_filing)
                prev_folder = client.get_filing_folder_url(prev_filing)
                prev_content = client.get_filing_content(prev_url, prev_folder)
                if prev_content:
                    prev_parsed = client.parse_13f_filing(prev_content, prev_url)
            
            # Compare and show changes
            if prev_parsed and prev_parsed.holdings:
                changes = HoldingsChange(
                    new_positions=[],
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[]
                )
                new_pos, inc_pos, dec_pos, rem_pos = compare_filings(prev_parsed, latest_parsed)
                changes.new_positions = new_pos
                changes.increased_positions = inc_pos
                changes.decreased_positions = dec_pos
                changes.removed_positions = rem_pos
            else:
                changes = HoldingsChange(
                    new_positions=latest_parsed.holdings[:20],
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[]
                )
                out.add("(First filing - showing top 20)")
            
            # Print changes
            if changes.new_positions:
                out.add(f"\n🆕 *NEW ({len(changes.new_positions)}):*")
                for h in sorted(changes.new_positions, key=lambda x: x.value, reverse=True)[:8]:
                    out.add(f"• {h.name}: ${h.value/1000000:.1f}M")
            
            if changes.increased_positions:
                out.add(f"\n📈 *INCREASED ({len(changes.increased_positions)}):*")
                for old_h, new_h in sorted(changes.increased_positions, key=lambda x: x[1].value - x[0].value, reverse=True)[:5]:
                    pct = ((new_h.value - old_h.value) / old_h.value) * 100 if old_h.value > 0 else 0
                    out.add(f"• {new_h.name}: ${old_h.value/1000000:.1f}M → ${new_h.value/1000000:.1f}M (+{pct:.0f}%)")
            
            if changes.decreased_positions:
                out.add(f"\n📉 *DECREASED ({len(changes.decreased_positions)}):*")
                for old_h, new_h in sorted(changes.decreased_positions, key=lambda x: x[0].value - x[1].value, reverse=True)[:5]:
                    pct = ((old_h.value - new_h.value) / old_h.value) * 100 if old_h.value > 0 else 0
                    out.add(f"• {new_h.name}: ${old_h.value/1000000:.1f}M → ${new_h.value/1000000:.1f}M (-{pct:.0f}%)")
            
            if changes.removed_positions:
                out.add(f"\n❌ *REMOVED ({len(changes.removed_positions)}):*")
                for h in sorted(changes.removed_positions, key=lambda x: x.value, reverse=True)[:5]:
                    out.add(f"• {h.name}: ${h.value/1000000:.1f}M")
            
            if not any([changes.new_positions, changes.increased_positions, changes.decreased_positions, changes.removed_positions]):
                out.add("\n✓ No significant changes")
            
            out.add()
            out.add("-" * 40)
            out.add()
            
        except Exception as e:
            out.add(f"❌ Error: {e}")
            out.add()
            continue
    
    # Send to Telegram if notifier provided
    if notifier:
        out.send_to_telegram("13F Changes Report")
    
    return out.lines


def compare_funds(config: Config, notifier=None):
    """Compare holdings across all tracked funds - find overlap and common positions"""
    out = OutputHandler(notifier)
    client = SECClient()
    all_holdings = {}  # {fund_name: {ticker/name: value}}
    
    out.add("🔍 *FUND COMPARISON*")
    out.add()
    
    # Fetch latest holdings for each fund
    for fund in config.tracked_funds:
        try:
            out.add(f"Loading {fund.name}...")
            
            # Get latest filing
            filing_info = client.get_latest_13f_filing(fund.cik)
            if not filing_info:
                out.add(f"  ❌ No 13F filing found")
                continue
            
            url = client.get_filing_url(filing_info)
            folder_url = client.get_filing_folder_url(filing_info)
            content = client.get_filing_content(url, folder_url)
            
            if not content:
                out.add(f"  ❌ Could not fetch filing")
                continue
            
            parsed = client.parse_13f_filing(content, url)
            if not parsed or not parsed.holdings:
                out.add(f"  ❌ Could not parse holdings")
                continue
            
            # Store holdings by name (normalized)
            holdings_dict = {}
            for h in parsed.holdings:
                name_key = h.name.upper().strip()
                holdings_dict[name_key] = {
                    'ticker': h.ticker,
                    'name': h.name,
                    'value': h.value,
                    'shares': h.shares
                }
            
            all_holdings[fund.name] = holdings_dict
            out.add(f"  ✓ {len(holdings_dict)} holdings")
            
        except Exception as e:
            out.add(f"  ❌ Error: {e}")
            continue
    
    out.add()
    
    if len(all_holdings) < 2:
        out.add("Need at least 2 funds to compare.")
        if notifier:
            out.send_to_telegram("Fund Comparison")
        return out.lines
    
    # Find overlap
    all_stock_names = set()
    for holdings in all_holdings.values():
        all_stock_names.update(holdings.keys())
    
    stock_funds = {}
    for stock_name in all_stock_names:
        funds_holding = []
        for fund_name, holdings in all_holdings.items():
            if stock_name in holdings:
                funds_holding.append(fund_name)
        
        if len(funds_holding) > 1:
            stock_funds[stock_name] = funds_holding
    
    sorted_stocks = sorted(stock_funds.items(), key=lambda x: len(x[1]), reverse=True)
    
    out.add("*" + "="*40 + "*")
    out.add("*STOCKS HELD BY MULTIPLE FUNDS*")
    out.add("*" + "="*40 + "*")
    out.add()
    
    by_count = {}
    for stock, funds in sorted_stocks:
        count = len(funds)
        if count not in by_count:
            by_count[count] = []
        by_count[count].append((stock, funds))
    
    for count in sorted(by_count.keys(), reverse=True):
        out.add(f"\n*Held by {count} funds ({len(by_count[count])} stocks):*")
        
        sorted_by_value = []
        for stock, funds in by_count[count]:
            try:
                total_value = sum(all_holdings[f][stock]['value'] for f in funds)
                sorted_by_value.append((stock, funds, total_value))
            except:
                continue
        
        sorted_by_value.sort(key=lambda x: x[2], reverse=True)
        
        for stock, funds, total_value in sorted_by_value[:8]:
            ticker = all_holdings[funds[0]][stock]['ticker']
            out.add(f"\n📈 {stock} ({ticker}) - ${total_value/1000000:.1f}M")
            for f in funds:
                val = all_holdings[f][stock]['value']
                out.add(f"   {f[:12]}: ${val/1000000:.1f}M")
        
        if len(by_count[count]) > 8:
            out.add(f"\n   ... and {len(by_count[count]) - 8} more")
    
    out.add()
    out.add("*" + "="*40 + "*")
    out.add("*TOP CONSENSUS PICKS*")
    out.add("*" + "="*40 + "*")
    
    # Top by value across all funds
    aggregate = {}
    for holdings in all_holdings.values():
        for name, data in holdings.items():
            if name not in aggregate:
                aggregate[name] = {'ticker': data['ticker'], 'value': 0}
            aggregate[name]['value'] += data['value']
    
    top_aggregated = sorted(aggregate.items(), key=lambda x: x[1]['value'], reverse=True)[:10]
    for i, (name, data) in enumerate(top_aggregated, 1):
        out.add(f"{i}. {name} ({data['ticker']}): ${data['value']/1000000:.1f}M")
    
    # Send to Telegram
    if notifier:
        out.send_to_telegram("Fund Comparison")
    
    return out.lines


def search_13d(config, args, notifier=None):
    """Search 13D filings by ticker"""
    out = OutputHandler(notifier)
    client = SECFilingsClient()
    
    ticker = args.ticker.upper() if args.ticker else None
    days = args.days
    
    out.add("🔍 *13D FILING SEARCH*")
    out.add(f"Last {days} days")
    out.add()
    
    out.add("📝 *Note: 13D search requires SEC API access.*")
    out.add("Showing recent 13D filings from tracked funds:")
    out.add()
    
    # Show 13D for tracked funds instead
    sec_client = SECClient()
    
    for fund in config.tracked_funds:
        out.add(f"Checking {fund.name}...")
        try:
            filings = client.get_13d_filings(fund.cik, days)
            if filings:
                out.add(f"  ✓ Found {len(filings)} 13D/13G filings")
                for f in filings[:5]:
                    out.add(f"    • {f['form']} - {f['filing_date']}")
            else:
                out.add(f"  - No 13D in last {days} days")
        except Exception as e:
            out.add(f"  Error: {e}")
    
    out.add()
    out.add("💡 *To search by company ticker, use:*")
    out.add("   https://www.sec.gov/edgar/searchedgar/companysearch")
    
    if notifier:
        out.send_to_telegram("13D Search")
    
    return out.lines


def search_insider(config, args, notifier=None):
    """Search Form 4 insider filings for S&P 500 companies"""
    from sp500 import SP500_COMPANIES
    import time
    
    out = OutputHandler(notifier)
    client = SECFilingsClient()
    days = args.days
    
    out.add("🔍 *INSIDER BUYING (FORM 4) SEARCH*")
    out.add(f"Checking S&P 500 companies - Last {days} days")
    out.add()
    
    all_filings = []
    
    # Search top 50 companies (to avoid rate limiting)
    companies = list(SP500_COMPANIES.items())[:50]
    
    for ticker, info in companies:
        cik = info["cik"]
        name = info["name"]
        
        try:
            filings = client.get_form4_filings(cik, days)
            if filings:
                for f in filings:
                    all_filings.append({
                        "ticker": ticker,
                        "name": name,
                        "filing_date": f["filing_date"],
                        "form": f["form"],
                        "cik": cik,
                        "accession": f.get("accession_number", ""),
                        "doc": f.get("primary_document", "")
                    })
        except Exception as e:
            pass
    
    # If no filings found at all
    if not all_filings:
        out.add("❌ No Form 4 filings found in last {days} days")
        return out.lines
    
    # Sort by date (most recent first)
    all_filings.sort(key=lambda x: x["filing_date"], reverse=True)
    
    # Group by ticker
    by_ticker = {}
    for f in all_filings:
        ticker = f["ticker"]
        if ticker not in by_ticker:
            by_ticker[ticker] = []
        by_ticker[ticker].append(f)
    
    # Send to Telegram - grouped by company
    if notifier and all_filings:
        notifier.send_message(f"🔔 *FORM 4 ALERT: {len(by_ticker)} companies, {len(all_filings)} total filings*")
        
        # For each company, get detailed info
        for ticker, filings_list in list(by_ticker.items())[:10]:
            name = filings_list[0]["name"]
            dates = [f["filing_date"] for f in filings_list]
            
            # Build message with details
            msg = f"📈 *{ticker}* ({name})\n"
            msg += f"   📅 {', '.join(set(dates))}\n"
            msg += f"   📊 {len(filings_list)} filing(s)\n"
            
            # Try to get more details from the filings
            try:
                # Get details for up to 3 filings per company to show multiple insiders
                for i, filing in enumerate(filings_list[:3]):
                    if filing.get("accession") and filing.get("doc"):
                        details = client.get_filing_details(
                            filing["cik"], 
                            filing["accession"], 
                            filing["doc"]
                        )
                        if details:
                            # Add separator between multiple insiders
                            if i > 0:
                                msg += "   ─────────────────\n"
                            
                            if details.get("owner_name"):
                                msg += f"   👤 *{details['owner_name']}*\n"
                            if details.get("title"):
                                msg += f"      📋 {details['title']}\n"
                            if details.get("transaction"):
                                emoji = "🟢" if details['transaction'] == "BUY" else "🔴" if details['transaction'] == "SELL" else "🔵"
                                msg += f"   {emoji} *{details['transaction']}*\n"
                            if details.get("transaction_shares"):
                                msg += f"      🔢 {details['transaction_shares']:,} shares\n"
                            if details.get("shares"):
                                msg += f"      📊 Now owns: {details['shares']:,}\n"
                            if details.get("price_per_share"):
                                msg += f"      💵 ${details['price_per_share']:.2f}/share\n"
                            if details.get("value"):
                                msg += f"      💰 Total: ${details['value']:,.0f}\n"
                            if details.get("position"):
                                msg += f"      📄 {details['position']}\n"
            except Exception as e:
                pass
            
            notifier.send_message(msg)
        
        if len(by_ticker) > 10:
            remaining = len(by_ticker) - 10
            notifier.send_message(f"...and {remaining} more companies")
    
    # Console output
    out.add(f"✅ Found *{len(all_filings)}* Form 4 filings across *{len(by_ticker)}* companies!")
    out.add()
    
    for ticker, filings_list in list(by_ticker.items())[:15]:
        name = filings_list[0]["name"]
        dates = ", ".join(set([f["filing_date"] for f in filings_list]))
        out.add(f"📈 {ticker} ({name}) - {len(filings_list)} filing(s) - {dates}")
    
    return out.lines


def test_telegram(config: Config):
    """Test Telegram notification"""
    if not config.telegram_token or not config.telegram_chat_id:
        print("❌ Telegram not configured. Set with --token and --chat-id flags")
        return
    
    notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    
    if notifier.send_test_message():
        print("✓ Test message sent successfully!")
    else:
        print("❌ Failed to send test message")


def run_scheduler(config: Config):
    """Run the weekly scheduler loop"""
    import schedule
    import time
    
    print("HedgeFundWatcher Scheduler Started")
    print("Runs: Every Sunday at 9:00 AM")
    print(f"Tracked funds: {len(config.tracked_funds)}")
    print("Press Ctrl+C to stop\n")
    
    # Initial run
    cache = FilingCache(DATA_DIR)
    notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
    run_scan(config, cache, notifier)
    
    # Schedule weekly
    schedule.every().sunday.at("09:00").do(run_scan, config=config, cache=cache, notifier=notifier)
    
    # Also run every hour for testing (remove in production)
    # schedule.every().hour.do(run_scan, config=config, cache=cache, notifier=notifier)
    
    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(
        description="HedgeFundWatcher - Track SEC 13F filings and get Telegram alerts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py run                                    # Run scan now
  python main.py add "Renaissance Technologies" 1037029  # Add a fund
  python main.py list                                   # List tracked funds
  python main.py known                                  # List known hedge funds
  python main.py test-telegram                          # Test Telegram config
  python main.py scheduler                              # Run weekly scheduler
        """
    )
    
    # Configuration flags
    parser.add_argument("--token", dest="telegram_token", help="Telegram bot token")
    parser.add_argument("--chat-id", dest="telegram_chat_id", help="Telegram chat ID")
    
    # Commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # run command
    subparsers.add_parser("run", help="Run a filing scan now")
    
    # add command
    add_parser = subparsers.add_parser("add", help="Add a fund to track")
    add_parser.add_argument("name", help="Fund name")
    add_parser.add_argument("cik", help="CIK number (without leading zeros)")
    
    # list command
    subparsers.add_parser("list", help="List all tracked funds")
    
    # known command
    subparsers.add_parser("known", help="List known hedge fund CIKs")
    
    # test-telegram command
    subparsers.add_parser("test-telegram", help="Send a test Telegram message")
    
    # scheduler command
    subparsers.add_parser("scheduler", help="Run the weekly scheduler")
    
    # report command
    subparsers.add_parser("report", help="Show detailed changes report for all funds")
    
    # compare command
    subparsers.add_parser("compare", help="Compare holdings across all tracked funds")
    
    # full-report command - sends everything to Telegram
    subparsers.add_parser("full-report", help="Generate full report and send to Telegram")
    
    # 13d command - track 13D filings for a ticker
    d13_parser = subparsers.add_parser("13d", help="Search 13D filings by ticker")
    d13_parser.add_argument("ticker", nargs="?", help="Ticker symbol to search (optional)")
    d13_parser.add_argument("--days", type=int, default=90, help="Days to look back (default: 90)")
    
    # insider command - track insider buying
    insider_parser = subparsers.add_parser("insider", help="Search insider (Form 4) filings by ticker")
    insider_parser.add_argument("ticker", nargs="?", help="Ticker symbol to search (optional)")
    insider_parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    
    args = parser.parse_args()
    
    # Setup
    ensure_data_dir()
    config = setup_config(args)
    
    # Route to command
    if args.command == "run":
        cache = FilingCache(DATA_DIR)
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
        
        if not config.tracked_funds:
            print("No funds tracked. Add some first:")
            print("  python main.py add <name> <CIK>")
            print("\nOr use known funds:")
            print("  python main.py known")
            return
        
        results = run_scan(config, cache, notifier)
        
        if not results:
            print("\nNo new filings found.")
        else:
            print(f"\n✓ Processed {len(results)} funds with new filings")
    
    elif args.command == "add":
        add_fund(config, args.name, args.cik)
    
    elif args.command == "list":
        list_funds(config)
    
    elif args.command == "known":
        list_known_funds(args)
    
    elif args.command == "test-telegram":
        test_telegram(config)
    
    elif args.command == "scheduler":
        if not config.telegram_token or not config.telegram_chat_id:
            print("❌ Telegram not configured")
            print("Set with: python main.py --token <TOKEN> --chat-id <CHAT_ID>")
            return
        run_scheduler(config)
    
    elif args.command == "report":
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
        show_report(config, notifier)
    
    elif args.command == "compare":
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
        compare_funds(config, notifier)
    
    elif args.command == "full-report":
        if not config.telegram_token or not config.telegram_chat_id:
            print("❌ Telegram not configured")
            print("Set with: python main.py --token <TOKEN> --chat-id <CHAT_ID>")
            return
        
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
        
        # Send header
        notifier.send_message("📊 *Generating FULL REPORT...*")
        
        # Run report
        show_report(config, notifier)
        
        # Run comparison  
        compare_funds(config, notifier)
        
        notifier.send_message("✅ Full report complete!")
    
    elif args.command == "13d":
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
        search_13d(config, args, notifier)
    
    elif args.command == "insider":
        notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id) if config.telegram_token and config.telegram_chat_id else None
        search_insider(config, args, notifier)
    
    else:
        # Default: show help
        parser.print_help()


if __name__ == "__main__":
    main()
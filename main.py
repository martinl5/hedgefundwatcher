"""HedgeFundWatcher — CLI entry point."""

import argparse
import logging
import os
import re
import sys

from models import Config, Filing, FilingCache, HoldingsChange, TrackedFund
from notifier import TelegramNotifier
from sec_client import HEDGE_FUND_CIKS, SECClient, compare_filings
from sec_filings import SECFilingsClient

DATA_DIR = "data"

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger for CLI use."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.root.setLevel(level)
    logging.root.handlers = [handler]

    # More detailed format for WARNING and above from library loggers
    warn_handler = logging.StreamHandler(sys.stderr)
    warn_handler.setLevel(logging.WARNING)
    warn_handler.setFormatter(
        logging.Formatter("%(levelname)s [%(name)s] %(message)s")
    )
    logging.root.addHandler(warn_handler)


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _validate_cik(cik: str) -> str:
    """Normalize and validate a CIK string; raises ValueError on bad input."""
    cik = cik.lstrip("0") or "0"
    if not re.match(r"^\d{1,10}$", cik):
        raise ValueError(f"Invalid CIK {cik!r}: must be 1–10 digits")
    return cik


class OutputHandler:
    """Accumulates report lines for console display and optional Telegram delivery."""

    def __init__(self, notifier: TelegramNotifier | None = None) -> None:
        self.notifier = notifier
        self.lines: list[str] = []

    def add(self, line: str = "") -> None:
        print(line)
        self.lines.append(line)

    def send_to_telegram(self, title: str = "") -> None:
        if not self.notifier or not self.lines:
            return
        message = "\n".join(self.lines)
        if len(message) > 4000:
            if title:
                self.notifier.send_message(f"📊 *{title}*")
            for i in range(0, len(message), 3800):
                self.notifier.send_message(message[i : i + 3800])
        else:
            if title:
                message = f"📊 *{title}*\n\n{message}"
            self.notifier.send_message(message)


def setup_config(args: argparse.Namespace) -> Config:
    """Load config and resolve Telegram credentials.

    Credential precedence: ``--token``/``--chat-id`` flags, then the
    ``TELEGRAM_TOKEN``/``TELEGRAM_CHAT_ID`` environment variables, then any
    value previously stored in config.json. Secrets are never written back
    to disk — the config file holds only the tracked-fund list.
    """
    config = Config()
    config.data_dir = DATA_DIR
    config.load()

    config.telegram_token = (
        args.telegram_token or os.environ.get("TELEGRAM_TOKEN") or config.telegram_token
    )
    config.telegram_chat_id = (
        args.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID") or config.telegram_chat_id
    )

    return config


def make_notifier(config: Config) -> TelegramNotifier | None:
    """Return a configured TelegramNotifier, or None if credentials are missing."""
    if config.telegram_token and config.telegram_chat_id:
        return TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    return None


def run_scan(
    config: Config,
    cache: FilingCache,
    notifier: TelegramNotifier | None = None,
) -> list[dict]:
    """Run the 13F filing scan for all tracked funds."""
    client = SECClient()
    results: list[dict] = []

    logger.info("Scanning %d tracked funds...", len(config.tracked_funds))

    for fund in config.tracked_funds:
        try:
            logger.info("  Checking %s (CIK: %s)...", fund.name, fund.cik)

            filing_info = client.get_latest_13f_filing(fund.cik)
            if not filing_info:
                logger.info("    No 13F filing found")
                continue

            filing_date = filing_info.get("filing_date", "")

            if fund.last_filing_date == filing_date:
                logger.info("    No new filing (last: %s)", filing_date)
                continue

            url = client.get_filing_url(filing_info)
            folder_url = client.get_filing_folder_url(filing_info)
            content = client.get_filing_content(url, folder_url)
            if not content:
                logger.info("    Could not fetch filing content")
                continue

            parsed_filing = client.parse_13f_filing(content, url)
            if not parsed_filing or not parsed_filing.holdings:
                logger.info("    Could not parse holdings")
                continue

            parsed_filing.cik = fund.cik
            parsed_filing.fund_name = fund.name
            parsed_filing.filing_date = filing_date

            old_filing = cache.get_last_filing(fund.cik)

            if old_filing:
                new_pos, inc_pos, dec_pos, rem_pos = compare_filings(old_filing, parsed_filing)
                changes = HoldingsChange(
                    new_positions=new_pos,
                    increased_positions=inc_pos,
                    decreased_positions=dec_pos,
                    removed_positions=rem_pos,
                )
            else:
                changes = HoldingsChange(
                    new_positions=parsed_filing.holdings[:20],
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[],
                )

            # Deliver the alert *before* recording the filing as seen, so a
            # failed Telegram send leaves the filing to be retried next run
            # rather than silently swallowed.
            if notifier:
                logger.info("    Sending alert...")
                if not notifier.send_filing_alert(fund.name, filing_date, changes):
                    logger.warning("    Alert delivery failed — will retry %s next run", fund.name)
                    continue

            cache.update_filing(parsed_filing)
            fund.last_filing_date = filing_date
            config.save()

            results.append({"fund": fund.name, "filing_date": filing_date, "changes": changes})
            logger.info("    ✓ Processed (filing date: %s)", filing_date)

        except Exception as e:
            logger.error("    ✗ Error processing %s: %s", fund.name, e)
            continue

    return results


def add_fund(config: Config, name: str, cik: str) -> None:
    """Add a new fund to track."""
    try:
        cik = _validate_cik(cik)
    except ValueError as e:
        print(f"Error: {e}")
        return
    fund = TrackedFund(cik=cik, name=name)
    config.add_fund(fund)
    print(f"✓ Added {name} (CIK: {cik}) to tracking list")


def list_funds(config: Config) -> None:
    """List all tracked funds."""
    if not config.tracked_funds:
        print("No funds currently tracked.")
        print("Use: python main.py add <name> <CIK>")
        return

    print(f"Tracked funds ({len(config.tracked_funds)}):")
    for i, fund in enumerate(config.tracked_funds, 1):
        print(
            f"  {i}. {fund.name} (CIK: {fund.cik})"
            f" - Last filing: {fund.last_filing_date or 'Never'}"
        )


def list_known_funds(args: argparse.Namespace) -> None:
    """List known hedge fund CIKs."""
    print("Known hedge fund CIKs (add any using: python main.py add <name> <CIK>):")
    for name, cik in sorted(HEDGE_FUND_CIKS.items()):
        print(f"  • {name}: {cik}")


def show_report(config: Config, notifier: TelegramNotifier | None = None) -> list[str]:
    """Show detailed report of filing changes — compares latest to previous filing."""
    out = OutputHandler(notifier)
    client = SECClient()

    out.add("📊 *13F FILING CHANGES REPORT*")
    out.add()

    for fund in config.tracked_funds:
        try:
            out.add(f"*{fund.name}*")
            out.add(f"CIK: {fund.cik}")

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

            prev_parsed: Filing | None = None
            if prev_filing:
                prev_url = client.get_filing_url(prev_filing)
                prev_folder = client.get_filing_folder_url(prev_filing)
                prev_content = client.get_filing_content(prev_url, prev_folder)
                if prev_content:
                    prev_parsed = client.parse_13f_filing(prev_content, prev_url)

            if prev_parsed and prev_parsed.holdings:
                new_pos, inc_pos, dec_pos, rem_pos = compare_filings(prev_parsed, latest_parsed)
                changes = HoldingsChange(
                    new_positions=new_pos,
                    increased_positions=inc_pos,
                    decreased_positions=dec_pos,
                    removed_positions=rem_pos,
                )
            else:
                changes = HoldingsChange(
                    new_positions=latest_parsed.holdings[:20],
                    increased_positions=[],
                    decreased_positions=[],
                    removed_positions=[],
                )
                out.add("(First filing — showing top 20)")

            if changes.new_positions:
                out.add(f"\n🆕 *NEW ({len(changes.new_positions)}):*")
                for h in sorted(changes.new_positions, key=lambda x: x.value, reverse=True)[:8]:
                    out.add(f"• {h.name}: ${h.value / 1_000_000:.1f}M")

            if changes.increased_positions:
                out.add(f"\n📈 *INCREASED ({len(changes.increased_positions)}):*")
                for old_h, new_h in sorted(
                    changes.increased_positions,
                    key=lambda x: x[1].value - x[0].value,
                    reverse=True,
                )[:5]:
                    pct = ((new_h.value - old_h.value) / old_h.value) * 100 if old_h.value else 0
                    out.add(
                        f"• {new_h.name}: ${old_h.value / 1_000_000:.1f}M"
                        f" → ${new_h.value / 1_000_000:.1f}M (+{pct:.0f}%)"
                    )

            if changes.decreased_positions:
                out.add(f"\n📉 *DECREASED ({len(changes.decreased_positions)}):*")
                for old_h, new_h in sorted(
                    changes.decreased_positions,
                    key=lambda x: x[0].value - x[1].value,
                    reverse=True,
                )[:5]:
                    pct = ((old_h.value - new_h.value) / old_h.value) * 100 if old_h.value else 0
                    out.add(
                        f"• {new_h.name}: ${old_h.value / 1_000_000:.1f}M"
                        f" → ${new_h.value / 1_000_000:.1f}M (-{pct:.0f}%)"
                    )

            if changes.removed_positions:
                out.add(f"\n❌ *REMOVED ({len(changes.removed_positions)}):*")
                for h in sorted(changes.removed_positions, key=lambda x: x.value, reverse=True)[
                    :5
                ]:
                    out.add(f"• {h.name}: ${h.value / 1_000_000:.1f}M")

            if not changes.has_changes():
                out.add("\n✓ No significant changes")

            out.add()
            out.add("-" * 40)
            out.add()

        except Exception as e:
            logger.error("Error processing fund %s: %s", fund.name, e)
            out.add(f"❌ Error: {e}")
            out.add()
            continue

    if notifier:
        out.send_to_telegram("13F Changes Report")

    return out.lines


def compare_funds(
    config: Config, notifier: TelegramNotifier | None = None
) -> list[str]:
    """Compare holdings across all tracked funds — find overlap and common positions."""
    out = OutputHandler(notifier)
    client = SECClient()
    all_holdings: dict[str, dict] = {}

    out.add("🔍 *FUND COMPARISON*")
    out.add()

    for fund in config.tracked_funds:
        try:
            out.add(f"Loading {fund.name}...")

            filing_info = client.get_latest_13f_filing(fund.cik)
            if not filing_info:
                out.add("  ❌ No 13F filing found")
                continue

            url = client.get_filing_url(filing_info)
            folder_url = client.get_filing_folder_url(filing_info)
            content = client.get_filing_content(url, folder_url)
            if not content:
                out.add("  ❌ Could not fetch filing")
                continue

            parsed = client.parse_13f_filing(content, url)
            if not parsed or not parsed.holdings:
                out.add("  ❌ Could not parse holdings")
                continue

            holdings_dict = {
                h.name.upper().strip(): {
                    "cusip": h.cusip,
                    "name": h.name,
                    "value": h.value,
                    "shares": h.shares,
                }
                for h in parsed.holdings
            }
            all_holdings[fund.name] = holdings_dict
            out.add(f"  ✓ {len(holdings_dict)} holdings")

        except Exception as e:
            logger.error("Error loading holdings for %s: %s", fund.name, e)
            out.add(f"  ❌ Error: {e}")
            continue

    out.add()

    if len(all_holdings) < 2:
        out.add("Need at least 2 funds to compare.")
        if notifier:
            out.send_to_telegram("Fund Comparison")
        return out.lines

    # Find stocks held by more than one fund
    all_stock_names = set().union(*all_holdings.values())
    stock_funds: dict[str, list[str]] = {}
    for stock_name in all_stock_names:
        holders = [fn for fn, h in all_holdings.items() if stock_name in h]
        if len(holders) > 1:
            stock_funds[stock_name] = holders

    sorted_stocks = sorted(stock_funds.items(), key=lambda x: len(x[1]), reverse=True)

    out.add("*" + "=" * 40 + "*")
    out.add("*STOCKS HELD BY MULTIPLE FUNDS*")
    out.add("*" + "=" * 40 + "*")
    out.add()

    by_count: dict[int, list] = {}
    for stock, funds in sorted_stocks:
        count = len(funds)
        by_count.setdefault(count, []).append((stock, funds))

    for count in sorted(by_count.keys(), reverse=True):
        out.add(f"\n*Held by {count} funds ({len(by_count[count])} stocks):*")

        sorted_by_value = []
        for stock, funds in by_count[count]:
            try:
                total_value = sum(all_holdings[f][stock]["value"] for f in funds)
                sorted_by_value.append((stock, funds, total_value))
            except (KeyError, TypeError):
                continue

        sorted_by_value.sort(key=lambda x: x[2], reverse=True)

        for stock, funds, total_value in sorted_by_value[:8]:
            cusip = all_holdings[funds[0]][stock]["cusip"]
            out.add(f"\n📈 {stock} (CUSIP {cusip}) - ${total_value / 1_000_000:.1f}M")
            for fn in funds:
                val = all_holdings[fn][stock]["value"]
                out.add(f"   {fn[:12]}: ${val / 1_000_000:.1f}M")

        if len(by_count[count]) > 8:
            out.add(f"\n   ... and {len(by_count[count]) - 8} more")

    out.add()
    out.add("*" + "=" * 40 + "*")
    out.add("*TOP CONSENSUS PICKS*")
    out.add("*" + "=" * 40 + "*")

    aggregate: dict[str, dict] = {}
    for holdings in all_holdings.values():
        for name, data in holdings.items():
            if name not in aggregate:
                aggregate[name] = {"cusip": data["cusip"], "value": 0}
            aggregate[name]["value"] += data["value"]

    top_aggregated = sorted(aggregate.items(), key=lambda x: x[1]["value"], reverse=True)[:10]
    for i, (name, data) in enumerate(top_aggregated, 1):
        out.add(f"{i}. {name} (CUSIP {data['cusip']}): ${data['value'] / 1_000_000:.1f}M")

    if notifier:
        out.send_to_telegram("Fund Comparison")

    return out.lines


def search_13d(
    config: Config,
    args: argparse.Namespace,
    notifier: TelegramNotifier | None = None,
) -> list[str]:
    """Search 13D/13G filings for tracked funds."""
    out = OutputHandler(notifier)
    client = SECFilingsClient()

    days = args.days

    out.add("🔍 *13D FILING SEARCH*")
    out.add(f"Last {days} days")
    out.add()
    out.add("📝 *Note: 13D search requires SEC API access.*")
    out.add("Showing recent 13D filings from tracked funds:")
    out.add()

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
            logger.error("Error fetching 13D for %s: %s", fund.name, e)
            out.add(f"  Error: {e}")

    out.add()
    out.add("💡 *To search by company ticker, use:*")
    out.add("   https://www.sec.gov/edgar/searchedgar/companysearch")

    if notifier:
        out.send_to_telegram("13D Search")

    return out.lines


def search_insider(
    config: Config,
    args: argparse.Namespace,
    notifier: TelegramNotifier | None = None,
) -> list[str]:
    """Search Form 4 insider filings for S&P 500 companies."""
    from sp500 import SP500_COMPANIES

    out = OutputHandler(notifier)
    client = SECFilingsClient()
    days = args.days

    out.add("🔍 *INSIDER BUYING (FORM 4) SEARCH*")
    out.add(f"Checking S&P 500 companies - Last {days} days")
    out.add()

    all_filings: list[dict] = []

    # Limit the number of companies scanned to avoid long, rate-limited runs.
    limit = getattr(args, "limit", 50)
    companies = list(SP500_COMPANIES.items())[:limit]

    for ticker, info in companies:
        cik = info["cik"]
        name = info["name"]
        try:
            filings = client.get_form4_filings(cik, days)
            for f in filings:
                all_filings.append({
                    "ticker": ticker,
                    "name": name,
                    "filing_date": f["filing_date"],
                    "form": f["form"],
                    "cik": cik,
                    "accession": f.get("accession_number", ""),
                    "doc": f.get("primary_document", ""),
                })
        except Exception as e:
            logger.debug("Skipping %s (%s): %s", ticker, cik, e)

    if not all_filings:
        out.add(f"❌ No Form 4 filings found in last {days} days")
        return out.lines

    all_filings.sort(key=lambda x: x["filing_date"], reverse=True)

    by_ticker: dict[str, list[dict]] = {}
    for f in all_filings:
        by_ticker.setdefault(f["ticker"], []).append(f)

    if notifier:
        notifier.send_message(
            f"🔔 *FORM 4 ALERT: {len(by_ticker)} companies, {len(all_filings)} total filings*"
        )

        for ticker, filings_list in list(by_ticker.items())[:10]:
            name = filings_list[0]["name"]
            dates = ", ".join(set(f["filing_date"] for f in filings_list))
            msg = f"📈 *{ticker}* ({name})\n"
            msg += f"   📅 {dates}\n"
            msg += f"   📊 {len(filings_list)} filing(s)\n"

            try:
                for i, filing in enumerate(filings_list[:3]):
                    if filing.get("accession") and filing.get("doc"):
                        details = client.get_filing_details(
                            filing["cik"], filing["accession"], filing["doc"]
                        )
                        if details:
                            if i > 0:
                                msg += "   ─────────────────\n"
                            if details.get("owner_name"):
                                msg += f"   👤 *{details['owner_name']}*\n"
                            if details.get("title"):
                                msg += f"      📋 {details['title']}\n"
                            if details.get("transaction"):
                                emoji = (
                                    "🟢" if details["transaction"] == "BUY"
                                    else "🔴" if details["transaction"] == "SELL"
                                    else "🔵"
                                )
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
                logger.debug("Could not enrich filing details for %s: %s", ticker, e)

            notifier.send_message(msg)

        if len(by_ticker) > 10:
            notifier.send_message(f"...and {len(by_ticker) - 10} more companies")

    out.add(f"✅ Found *{len(all_filings)}* Form 4 filings across *{len(by_ticker)}* companies!")
    out.add()

    for ticker, filings_list in list(by_ticker.items())[:15]:
        name = filings_list[0]["name"]
        dates = ", ".join(set(f["filing_date"] for f in filings_list))
        out.add(f"📈 {ticker} ({name}) - {len(filings_list)} filing(s) - {dates}")

    return out.lines


def test_telegram(config: Config) -> None:
    """Test Telegram notification."""
    if not config.telegram_token or not config.telegram_chat_id:
        print("❌ Telegram not configured. Set with --token and --chat-id flags")
        return

    notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    if notifier.send_test_message():
        print("✓ Test message sent successfully!")
    else:
        print("❌ Failed to send test message")


def main() -> None:
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
  python main.py insider --days 7                        # Recent insider (Form 4) buys

Telegram credentials are read from the TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
environment variables (or the --token/--chat-id flags). For unattended
quarterly runs, schedule `python main.py run` with cron or GitHub Actions.
        """,
    )

    parser.add_argument("--token", dest="telegram_token", help="Telegram bot token")
    parser.add_argument("--chat-id", dest="telegram_chat_id", help="Telegram chat ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("run", help="Run a filing scan now")

    add_parser = subparsers.add_parser("add", help="Add a fund to track")
    add_parser.add_argument("name", help="Fund name")
    add_parser.add_argument("cik", help="CIK number")

    subparsers.add_parser("list", help="List all tracked funds")
    subparsers.add_parser("known", help="List known hedge fund CIKs")
    subparsers.add_parser("test-telegram", help="Send a test Telegram message")
    subparsers.add_parser("report", help="Show detailed changes report for all funds")
    subparsers.add_parser("compare", help="Compare holdings across all tracked funds")
    subparsers.add_parser("full-report", help="Generate full report and send to Telegram")

    d13_parser = subparsers.add_parser("13d", help="Search 13D/13G filings from tracked funds")
    d13_parser.add_argument("--days", type=int, default=90, help="Days to look back (default: 90)")

    insider_parser = subparsers.add_parser("insider", help="Search insider (Form 4) filings")
    insider_parser.add_argument(
        "--days", type=int, default=30, help="Days to look back (default: 30)"
    )
    insider_parser.add_argument(
        "--limit", type=int, default=50, help="Max S&P 500 companies to scan (default: 50)"
    )

    args = parser.parse_args()
    setup_logging(verbose=getattr(args, "verbose", False))

    ensure_data_dir()
    config = setup_config(args)

    if args.command == "run":
        cache = FilingCache(DATA_DIR)
        notifier = make_notifier(config)

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

    elif args.command == "report":
        show_report(config, make_notifier(config))

    elif args.command == "compare":
        compare_funds(config, make_notifier(config))

    elif args.command == "full-report":
        notifier = make_notifier(config)
        if notifier is None:
            print("❌ Telegram not configured")
            print("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID (or use --token/--chat-id)")
            return
        notifier.send_message("📊 *Generating FULL REPORT...*")
        show_report(config, notifier)
        compare_funds(config, notifier)
        notifier.send_message("✅ Full report complete!")

    elif args.command == "13d":
        search_13d(config, args, make_notifier(config))

    elif args.command == "insider":
        search_insider(config, args, make_notifier(config))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

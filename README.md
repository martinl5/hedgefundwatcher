# HedgeFundWatcher

Track institutional investor filings (13F, 13D, Form 4 insider) via SEC EDGAR with Telegram alerts.

## Features

- **13F Holdings** - Quarterly portfolio positions from institutional investors
- **13D Filings** - Activist investor moves (>5% ownership)
- **Form 4 Insider** - Insider buying/selling with full details (who, how much, position)
- **Fund Comparison** - Find overlapping holdings across multiple funds
- **Change Detection** - Alert on new positions, increases, decreases

## Quick Start

### 1. Setup Telegram

1. Message **@BotFather** on Telegram → `/newbot` → follow instructions
2. Copy your bot token
3. Start a chat with your bot and send a message
4. Get your chat ID: https://api.telegram.org/bot<TOKEN>/getUpdates

### 2. Configure

Provide Telegram credentials via environment variables (recommended — they are
never written to disk):

```bash
export TELEGRAM_TOKEN="YOUR_BOT_TOKEN"
export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
```

Or pass them per-command with `--token` / `--chat-id`. The local
`data/config.json` only stores the tracked-fund list, not secrets.

### 3. Add Funds to Track

```bash
python main.py add "Michael Burry (Scion)" 1649339
python main.py add "Bridgewater Associates" 1350694
python main.py add "Pershing Square" 1336528
python main.py add "Berkshire Hathaway" 1067983
python main.py add "D.E. Shaw" 1009207
```

### 4. Run Commands

```bash
# Test Telegram
python main.py test-telegram

# Check for new 13F filings
python main.py run

# Get detailed changes report (sends to Telegram)
python main.py report

# Compare holdings across funds (sends to Telegram)
python main.py compare

# Full report - everything (sends to Telegram)
python main.py full-report

# Search 13D filings
python main.py 13d --days 90

# Search insider buying (S&P 500 companies)
python main.py insider --days 7
```

## All Commands

| Command | Description |
|---------|-------------|
| `run` | Scan for new 13F filings |
| `report` | Detailed changes report → Telegram |
| `compare` | Fund comparison → Telegram |
| `full-report` | Everything → Telegram |
| `13d` | Search 13D filings from tracked funds |
| `insider` | Search Form 4 insider filings (S&P 500) |
| `add "Name" CIK` | Add fund to track |
| `list` | Show tracked funds |
| `known` | List known hedge fund CIKs |
| `test-telegram` | Test Telegram setup |

## Insider Search (Form 4)

Searches top 50 S&P 500 companies for recent insider transactions:

```bash
python main.py insider --days 7
```

### Example Output

```
📈 *GOOGL* (Alphabet Inc.)
   📅 2026-03-31, 2026-03-27
   📊 8 filing(s)
   👤 *WALKER JOHN KENT*
      📋 President, Global Affairs, CLO
   🔴 *SELL*
      📊 Now owns: 58,124
      💵 $273.91/share
      📄 Class C Capital Stock
   ─────────────────
   👤 *ARNOLD FRANCES*
   🔴 *SELL*
      📊 Now owns: 18,316
      💵 $275.19/share
      📄 Class C Capital Stock
```

Each alert shows:
- 👤 Insider name and title
- 🟢/🔴 Transaction type (BUY/SELL/TRANSFER)
- 📊 Shares owned after transaction
- 💵 Price per share
- 💰 Total transaction value
- 📄 Security type (Common Stock, options, etc.)

## Currently Tracked Funds

| Fund | CIK | Last Filing |
|------|-----|--------------|
| Michael Burry (Scion) | 1649339 | 2025-11-03 |
| Bridgewater Associates | 1350694 | 2026-02-13 |
| Pershing Square | 1336528 | 2026-02-17 |
| Berkshire Hathaway | 1067983 | 2026-02-17 |
| D.E. Shaw | 1009207 | 2026-02-17 |

## Scheduled Runs (Quarterly)

13F-HR filings are due ~45 days after each quarter ends, so new filings appear
in **mid-February, mid-May, mid-August, and mid-November**. There's no value in
running year-round.

The repo ships a GitHub Actions workflow (`.github/workflows/quarterly-scan.yml`)
that runs `main.py run` daily during those four months and stays idle otherwise.
Runs are idempotent — each filing is alerted only once (tracked via
`last_filing_date`) — so the daily cadence never produces duplicate alerts.

To enable it, add two repository secrets:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

You can also trigger it manually from the Actions tab (`workflow_dispatch`).

### Running it yourself (cron)

To run on your own machine instead, schedule the `run` command, e.g. weekdays
at 9:30 AM:

```bash
30 9 * * 1-5 cd /path/to/hedgefundwatcher && \
  TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 main.py run
```

## How It Works

| Filing Type | What It Shows | When |
|-------------|---------------|------|
| **13F-HR** | Quarterly holdings (>$100M) | ~45 days after quarter |
| **13D/G** | Beneficial ownership (>5%) | When ownership crosses threshold |
| **Form 4** | Insider transactions | Within 3 business days |

## Files

```
hedge_fund_watcher/
├── main.py              # CLI entry point
├── sec_base.py          # Shared SEC client (rate limiting + retry/backoff)
├── sec_client.py        # SEC EDGAR API (13F)
├── sec_filings.py       # SEC API (13D, Form 4)
├── sp500.py             # S&P 500 companies with CIKs
├── notifier.py          # Telegram notifications
├── models.py            # Data models
├── requirements.txt     # Python dependencies
├── data/                # Config, cache, state
└── README.md            # This file
```

## Limitations

- SEC's public API doesn't allow ticker-based search (requires authentication)
- Many hedge funds (Renaissance, Citadel, Two Sigma) don't file 13F publicly
- 13F has ~45 day delay - shows holdings from ~2 months ago
- Some Form 4 filings don't include transaction shares (options exercises)

## Add More Funds

Find CIKs at: https://www.sec.gov/cgi-bin/browse-edgar

Example:
```bash
python main.py add "Fund Name" CIK
```
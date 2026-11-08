# HedgeFundWatcher

Track institutional investor filings (13F, 13D, Form 4 insider) via SEC EDGAR with Telegram alerts.

## Features

- **13F Holdings** - Quarterly portfolio positions from institutional investors
- **13D Filings** - Activist investor moves (>5% ownership)
- **Form 4 Insider** - Insider buying/selling with full details (who, how much, position)
- **Fund Comparison** - Find overlapping holdings across multiple funds
- **Change Detection** - Alert on new positions, increases, decreases
- **Big Bets Only** - Moves under $100M are ignored; everything is ranked largest-first
- **Top Bets** - One ranked list of the quarter's biggest moves across all tracked funds

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

Also set a contact address for SEC. Their fair-access policy requires every
request to identify itself, and they serve HTTP 403 to clients that don't:

```bash
export SEC_USER_AGENT="HedgeFundWatcher/1.0 (you@example.com)"
```

### 3. Add Funds to Track

```bash
python main.py add "Bridgewater Associates" 1350694
python main.py add "Pershing Square" 1336528
python main.py add "Berkshire Hathaway" 1067983
```

Quant shops and market makers (Renaissance, Two Sigma, Citadel, Susquehanna,
etc.) are deliberately not tracked: they hold thousands of names and turn them
over constantly, so their 13Fs show churn and hedges rather than bets.

### What counts as a bet

A move is reported only if it is at least **$100M** (`BIG_BET_MIN` in
`sec_client.py`):

- **New / exited positions** — sized by position value
- **Adds / trims** — sized by the value of shares actually traded (share change × quarter-end price)

Every list is sorted by that size, largest first.
```

### 4. Run Commands

```bash
# Test Telegram
python main.py test-telegram

# Check for new 13F filings
python main.py run

# Get detailed changes report (sends to Telegram)
python main.py report

# Biggest moves this quarter across all funds (sends to Telegram)
python main.py top-bets

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
| `top-bets` | This quarter's biggest moves (≥ $100M), ranked → Telegram |
| `compare` | Fund comparison → Telegram |
| `full-report` | Everything → Telegram |
| `13d` | Search 13D filings from tracked funds |
| `insider` | Search Form 4 insider filings (S&P 500) |
| `add "Name" CIK` | Add fund to track (confirms the CIK against EDGAR; `--force` skips) |
| `sync-funds` | Reconcile tracked funds with `data/config.json.example` |
| `verify-funds` | Check every tracked CIK against EDGAR (`--known` checks the built-in list) |
| `list` | Show tracked funds |
| `known` | List known hedge fund CIKs |
| `test-telegram` | Test Telegram setup |

### Which funds are tracked

`data/config.json.example` is checked into git and is the source of truth for
**which** funds are tracked. `data/config.json` is local state — it holds each
fund's `last_filing_date` and is gitignored.

To change the list, edit `config.json.example` and run `python main.py
sync-funds`. Funds are added or removed to match, and any fund present in both
keeps its `last_filing_date`, so syncing never re-alerts a filing you have
already seen. The scheduled workflow runs this on every scan.

### Verifying CIKs

A mistyped CIK is usually still a *valid* CIK belonging to some other
institutional manager, so the tool would happily report confident, plausible
holdings for the wrong firm. `verify-funds` resolves each CIK against EDGAR and
prints the registered name so mismatches are visible:

```
✓    1067983  Berkshire Hathaway     → BERKSHIRE HATHAWAY INC     (last 13F: 2026-05-15)
✗    1146184  Citadel Advisors       → CITADEL SECURITIES LLC     (last 13F: 2026-05-15)
             ⚠️  name mismatch — this CIK may be the wrong entity
```

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

See `data/config.json.example` for the source of truth.

| Fund | CIK |
|------|-----|
| Berkshire Hathaway | 1067983 |
| Bridgewater Associates | 1350694 |
| Pershing Square | 1336528 |

## Scheduled Runs (Quarterly)

13F-HR filings are due ~45 days after each quarter ends, so new filings appear
in **mid-February, mid-May, mid-August, and mid-November**. There's no value in
running year-round.

The repo ships a GitHub Actions workflow (`.github/workflows/quarterly-scan.yml`)
that runs `main.py run` daily during those four months and stays idle otherwise.
Runs are idempotent — each filing is alerted only once (tracked via
`last_filing_date`) — so the daily cadence never produces duplicate alerts.

To enable it, add these repository secrets:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SEC_USER_AGENT` — your contact address, e.g. `HedgeFundWatcher/1.0 (you@example.com)`

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

### What counts as a change

Position increases and decreases are measured on **share count**, not dollar
value. A 13F's `value` field is the position's market value at quarter end, so
a fund that never traded would otherwise show up as buying whenever its stocks
rallied — and a real sale would be hidden behind a rising price. Alerts fire
when share count moves more than 20%.

Two related details:

- A security is usually listed across several `infoTable` rows (one per
  investment-discretion bucket or sub-manager). Those rows are summed into a
  single position.
- Puts and calls are tracked separately from the underlying common stock, even
  though they share a CUSIP — a new put is a bearish bet, not a bigger stake.
- Stock splits change the share count without any trade, so a near-integer
  share ratio paired with roughly unchanged value is treated as a split and
  not reported.

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
- 13F has ~45 day delay - shows holdings from ~2 months ago
- 13F covers long US equity positions only — no shorts, cash, bonds or foreign listings
- A fund that deregisters stops filing entirely (Scion did so in Nov 2025, and its
  final 13F was 2025-11-03)
- Some Form 4 filings don't include transaction shares (options exercises)

## Add More Funds

Find CIKs at: https://www.sec.gov/cgi-bin/browse-edgar

Example:
```bash
python main.py add "Fund Name" CIK
```
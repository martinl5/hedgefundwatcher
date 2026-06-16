# SEC 13F Filing Alert System - Specification

## Project Overview

- **Project Name**: HedgeFundWatcher
- **Type**: Python CLI application with scheduled execution
- **Core Functionality**: Monitor SEC 13F filings for tracked hedge funds and send alerts via Telegram
- **Target Users**: Individual investors who want to follow institutional money

## Functionality Specification

### Core Features

1. **SEC EDGAR Integration**
   - Fetch 13F filings from SEC EDGAR database
   - Parse XML/HTML filings to extract holdings
   - Track multiple hedge funds by CIK number
   - Handle rate limiting and API throttling

2. **Hedge Fund Tracking**
   - Configurable list of hedge funds to monitor
   - Store last checked filing date to avoid duplicates
   - Support adding/removing funds via config file

3. **Change Detection**
   - Detect new positions (holdings not in previous filing)
   - Detect increased positions (>20% increase)
   - Detect decreased positions (>20% decrease)
   - Detect sold positions (holdings removed)

4. **Telegram Notifications**
   - Send formatted messages with:
     - Fund name
     - Filing date
     - New positions with details
     - Significant changes
   - Include company ticker, shares, and value
   - Group changes by type (new/increased/decreased/removed)

5. **Scheduled Execution (Quarterly)**
   - 13F-HR filings are due ~45 days after each quarter ends, so runs only
     matter in mid-Feb, mid-May, mid-Aug, and mid-Nov
   - Runs via GitHub Actions (`quarterly-scan.yml`): daily during those four
     months, idle otherwise
   - Idempotent — each filing is alerted once (tracked via `last_filing_date`)
   - Support manual trigger via the CLI (`main.py run`) or `workflow_dispatch`

### User Interactions

- Run manually: `python main.py run`
- Add hedge fund: `python main.py add <name> <CIK>`
- List tracked funds: `python main.py list`
- Test Telegram: `python main.py test-telegram`

### Data Handling

- **Storage**: Local JSON files for:
  - `config.json` - Tracked funds and their last seen filing date
  - `filings_cache.json` - Last known holdings per fund (for change detection)
- **Secrets**: Telegram credentials come from the `TELEGRAM_TOKEN` /
  `TELEGRAM_CHAT_ID` environment variables and are never written to disk
- **No external database required**

### Edge Cases

- Handle funds with no new filings
- Handle API timeouts with retry logic
- Handle malformed filings gracefully
- Avoid duplicate alerts for same filing

## Technical Architecture

```
hedgefundwatcher/
├── main.py              # Entry point & CLI
├── sec_base.py          # Shared SEC client: rate limiting + retry/backoff
├── sec_client.py        # SEC EDGAR API client (13F) + parsing
├── sec_filings.py       # SEC API client (13D/13G, Form 4) + parsing
├── sp500.py             # S&P 500 CIK lookup (for insider search)
├── notifier.py          # Telegram notification logic
├── models.py            # Data models + config/cache persistence
├── requirements.txt     # Python dependencies
└── .github/workflows/quarterly-scan.yml  # Scheduled quarterly run
```

## Acceptance Criteria

1. ✅ Can fetch 13F filings from SEC EDGAR for specified CIK
2. ✅ Parses holdings (ticker, shares, value)
3. ✅ Detects new/increased/decreased/removed positions
4. ✅ Sends formatted Telegram alert with changes
5. ✅ Quarterly scheduled run (GitHub Actions) runs automatically
6. ✅ Manual run command works
7. ✅ Configurable hedge fund list
8. ✅ Handles API errors gracefully
9. ✅ No duplicate alerts for same filing
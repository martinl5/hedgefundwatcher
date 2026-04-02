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

5. **Weekly Scheduler**
   - Run on configurable schedule (default: every Sunday at 9am)
   - Track execution state (last run timestamp)
   - Support manual trigger via CLI

### User Interactions

- Run manually: `python main.py run`
- Add hedge fund: `python main.py add <name> <CIK>`
- List tracked funds: `python main.py list`
- Test Telegram: `python main.py test-telegram`

### Data Handling

- **Storage**: Local JSON files for:
  - `config.json` - Telegram token, chat ID, tracked funds
  - `filings_cache.json` - Last known holdings per fund
  - `state.json` - Last run timestamp, settings
- **No external database required**

### Edge Cases

- Handle funds with no new filings
- Handle API timeouts with retry logic
- Handle malformed filings gracefully
- Avoid duplicate alerts for same filing

## Technical Architecture

```
hedge_fund_watcher/
├── main.py              # Entry point & CLI
├── config.py            # Configuration management
├── sec_client.py        # SEC EDGAR API client
├── parser.py            # 13F filing parser
├── notifier.py          # Telegram notification logic
├── scheduler.py         # Weekly execution logic
├── models.py            # Data models
├── requirements.txt     # Python dependencies
└── .env.example         # Environment template
```

## Acceptance Criteria

1. ✅ Can fetch 13F filings from SEC EDGAR for specified CIK
2. ✅ Parses holdings (ticker, shares, value)
3. ✅ Detects new/increased/decreased/removed positions
4. ✅ Sends formatted Telegram alert with changes
5. ✅ Weekly scheduler runs automatically
6. ✅ Manual run command works
7. ✅ Configurable hedge fund list
8. ✅ Handles API errors gracefully
9. ✅ No duplicate alerts for same filing
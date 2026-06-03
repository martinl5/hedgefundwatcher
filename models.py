"""Data models for HedgeFundWatcher."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Holding:
    """Represents a single position in a 13F filing."""

    ticker: str
    name: str
    shares: int
    value: float  # in dollars

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "shares": self.shares,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Holding":
        return cls(
            ticker=data.get("ticker", ""),
            name=data.get("name", ""),
            shares=int(data.get("shares", 0)),
            value=float(data.get("value", 0)),
        )


@dataclass
class Filing:
    """Represents a 13F filing."""

    cik: str
    fund_name: str
    filing_date: str
    holdings: list[Holding]

    def to_dict(self) -> dict:
        return {
            "cik": self.cik,
            "fund_name": self.fund_name,
            "filing_date": self.filing_date,
            "holdings": [h.to_dict() for h in self.holdings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Filing":
        return cls(
            cik=data.get("cik", ""),
            fund_name=data.get("fund_name", ""),
            filing_date=data.get("filing_date", ""),
            holdings=[Holding.from_dict(h) for h in data.get("holdings", [])],
        )


@dataclass
class HoldingsChange:
    """Represents changes between two filings."""

    new_positions: list[Holding] = field(default_factory=list)
    increased_positions: list[tuple] = field(default_factory=list)  # (old, new)
    decreased_positions: list[tuple] = field(default_factory=list)  # (old, new)
    removed_positions: list[Holding] = field(default_factory=list)

    def has_changes(self) -> bool:
        return any([
            self.new_positions,
            self.increased_positions,
            self.decreased_positions,
            self.removed_positions,
        ])


@dataclass
class TrackedFund:
    """A hedge fund being tracked."""

    cik: str
    name: str
    last_filing_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "cik": self.cik,
            "name": self.name,
            "last_filing_date": self.last_filing_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedFund":
        return cls(
            cik=data.get("cik", ""),
            name=data.get("name", ""),
            last_filing_date=data.get("last_filing_date"),
        )


class Config:
    """Application configuration."""

    def __init__(self) -> None:
        self.telegram_token: str = ""
        self.telegram_chat_id: str = ""
        self.tracked_funds: list[TrackedFund] = []
        self.data_dir = "data"

    def load(self) -> "Config":
        """Load config from JSON file."""
        config_path = os.path.join(self.data_dir, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                data = json.load(f)
                self.telegram_token = data.get("telegram_token", "")
                self.telegram_chat_id = data.get("telegram_chat_id", "")
                self.tracked_funds = [
                    TrackedFund.from_dict(fd) for fd in data.get("tracked_funds", [])
                ]
        return self

    def save(self) -> None:
        """Persist config to JSON file."""
        os.makedirs(self.data_dir, exist_ok=True)
        config_path = os.path.join(self.data_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(
                {
                    "telegram_token": self.telegram_token,
                    "telegram_chat_id": self.telegram_chat_id,
                    "tracked_funds": [fd.to_dict() for fd in self.tracked_funds],
                },
                f,
                indent=2,
            )

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: list[str] = []

        if self.telegram_token and not re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", self.telegram_token):
            errors.append(
                "telegram_token looks malformed (expected '<bot_id>:<secret>' format)"
            )

        if self.telegram_chat_id and not re.match(r"^-?\d+$", self.telegram_chat_id):
            errors.append("telegram_chat_id must be a numeric string")

        for fund in self.tracked_funds:
            if not re.match(r"^\d{1,10}$", fund.cik):
                errors.append(f"Fund '{fund.name}' has invalid CIK: {fund.cik!r}")

        return errors

    def add_fund(self, fund: TrackedFund) -> None:
        """Add a fund to tracking if not already present."""
        if not any(f.cik == fund.cik for f in self.tracked_funds):
            self.tracked_funds.append(fund)
            self.save()

    def remove_fund(self, cik: str) -> bool:
        """Remove a fund from tracking; returns True if it was present."""
        original_len = len(self.tracked_funds)
        self.tracked_funds = [f for f in self.tracked_funds if f.cik != cik]
        if len(self.tracked_funds) != original_len:
            self.save()
            return True
        return False


class FilingCache:
    """Cache of last known holdings per fund."""

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        self.cache: dict[str, Filing] = {}
        self.load()

    def load(self) -> None:
        """Load cache from JSON file."""
        cache_path = os.path.join(self.data_dir, "filings_cache.json")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                data = json.load(f)
                self.cache = {k: Filing.from_dict(v) for k, v in data.items()}

    def save(self) -> None:
        """Persist cache to JSON file."""
        os.makedirs(self.data_dir, exist_ok=True)
        cache_path = os.path.join(self.data_dir, "filings_cache.json")
        with open(cache_path, "w") as f:
            json.dump({k: v.to_dict() for k, v in self.cache.items()}, f, indent=2)

    def get_last_filing(self, cik: str) -> Filing | None:
        """Return the last cached filing for a fund, or None."""
        return self.cache.get(cik)

    def update_filing(self, filing: Filing) -> None:
        """Update the cached filing for a fund and persist."""
        self.cache[filing.cik] = filing
        self.save()


class State:
    """Application state."""

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        self.last_run: str | None = None
        self.load()

    def load(self) -> None:
        """Load state from JSON file."""
        state_path = os.path.join(self.data_dir, "state.json")
        if os.path.exists(state_path):
            with open(state_path) as f:
                data = json.load(f)
                self.last_run = data.get("last_run")

    def save(self) -> None:
        """Persist state to JSON file."""
        os.makedirs(self.data_dir, exist_ok=True)
        state_path = os.path.join(self.data_dir, "state.json")
        with open(state_path, "w") as f:
            json.dump({"last_run": self.last_run}, f, indent=2)

    def update_last_run(self) -> None:
        """Update last run timestamp."""
        self.last_run = datetime.now().isoformat()
        self.save()

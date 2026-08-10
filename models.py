"""Data models for HedgeFundWatcher."""

import contextlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk holdings format changes in a way that makes older
# cached filings unsafe to diff against freshly parsed ones.
CACHE_SCHEMA_VERSION = 2


def _write_json_atomic(path: str, payload: Any) -> None:
    """Serialise ``payload`` to ``path`` atomically.

    Writes to a temporary file in the same directory, fsyncs it, then renames
    over the target, so a crash mid-write leaves the previous file intact
    rather than a truncated one. That matters because the quarterly workflow
    restores ``data/`` from an Actions cache — a corrupt file would otherwise
    be cached and break every subsequent run.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def read_json(path: str) -> Any:
    """Read JSON from ``path``; return None if it is missing or unreadable.

    State files live in a directory that survives between runs, so a truncated
    or hand-edited file should degrade to "no state" rather than crash the CLI.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Ignoring unreadable state file %s: %s", path, e)
        return None


@dataclass
class Holding:
    """Represents a single position in a 13F filing.

    13F information tables identify securities by CUSIP, not ticker, so
    ``cusip`` holds the 6-character CUSIP prefix used to match positions
    across filings. CUSIP alone is not an identity, though: a put and the
    underlying common stock share one, and share counts cannot be compared
    with principal amounts — hence ``put_call`` and ``amount_type``.
    """

    cusip: str
    name: str
    shares: int
    value: float  # in dollars
    put_call: str = ""  # "Put" / "Call"; empty for common stock
    amount_type: str = "SH"  # SH (share count) or PRN (principal amount)

    @property
    def key(self) -> tuple[str, str, str]:
        """The identity used to match this position across filings."""
        return (self.cusip, self.put_call.upper(), self.amount_type.upper())

    @property
    def display_name(self) -> str:
        """Issuer name, tagged with the derivative type when there is one."""
        return f"{self.name} ({self.put_call.upper()})" if self.put_call else self.name

    def to_dict(self) -> dict:
        return {
            "cusip": self.cusip,
            "name": self.name,
            "shares": self.shares,
            "value": self.value,
            "put_call": self.put_call,
            "amount_type": self.amount_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Holding":
        # Accept the legacy "ticker" key from caches written before the rename.
        return cls(
            cusip=data.get("cusip", data.get("ticker", "")),
            name=data.get("name", ""),
            shares=int(data.get("shares", 0)),
            value=float(data.get("value", 0)),
            put_call=data.get("put_call", ""),
            amount_type=data.get("amount_type", "SH"),
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

    @property
    def _config_path(self) -> str:
        return os.path.join(self.data_dir, "config.json")

    def load(self) -> "Config":
        """Load config from JSON file."""
        data = read_json(self._config_path)
        if isinstance(data, dict):
            self.telegram_token = data.get("telegram_token", "")
            self.telegram_chat_id = data.get("telegram_chat_id", "")
            self.tracked_funds = [
                TrackedFund.from_dict(fd) for fd in data.get("tracked_funds", [])
            ]
        return self

    def save(self) -> None:
        """Persist config to JSON file.

        Only the tracked-fund list is written. Telegram credentials are
        intentionally never persisted — supply them via the TELEGRAM_TOKEN /
        TELEGRAM_CHAT_ID environment variables (or the --token/--chat-id flags).
        """
        _write_json_atomic(
            self._config_path,
            {"tracked_funds": [fd.to_dict() for fd in self.tracked_funds]},
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

    @property
    def _cache_path(self) -> str:
        return os.path.join(self.data_dir, "filings_cache.json")

    def load(self) -> None:
        """Load cache from JSON file, discarding it if the schema is outdated."""
        data = read_json(self._cache_path)
        if not isinstance(data, dict):
            return

        version = data.get("schema_version")
        if version != CACHE_SCHEMA_VERSION:
            # v1 caches were written before duplicate infoTable rows were summed
            # and before puts were separated from common stock, so diffing against
            # them would manufacture exactly the false alerts this schema fixes.
            logger.warning(
                "Filings cache is schema v%s (expected v%s); discarding it and taking a "
                "fresh baseline. The next scan reports current holdings rather than changes.",
                version or 1,
                CACHE_SCHEMA_VERSION,
            )
            return

        filings = data.get("filings", {})
        self.cache = {k: Filing.from_dict(v) for k, v in filings.items()}

    def save(self) -> None:
        """Persist cache to JSON file."""
        _write_json_atomic(
            self._cache_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "filings": {k: v.to_dict() for k, v in self.cache.items()},
            },
        )

    def get_last_filing(self, cik: str) -> Filing | None:
        """Return the last cached filing for a fund, or None."""
        return self.cache.get(cik)

    def update_filing(self, filing: Filing) -> None:
        """Update the cached filing for a fund and persist."""
        self.cache[filing.cik] = filing
        self.save()

"""Data models for HedgeFundWatcher"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime
import json


@dataclass
class Holding:
    """Represents a single position in a 13F filing"""
    ticker: str
    name: str
    shares: int
    value: float  # in dollars
    
    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "shares": self.shares,
            "value": self.value
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Holding":
        return cls(
            ticker=data.get("ticker", ""),
            name=data.get("name", ""),
            shares=int(data.get("shares", 0)),
            value=float(data.get("value", 0))
        )


@dataclass
class Filing:
    """Represents a 13F filing"""
    cik: str
    fund_name: str
    filing_date: str
    holdings: List[Holding]
    
    def to_dict(self) -> dict:
        return {
            "cik": self.cik,
            "fund_name": self.fund_name,
            "filing_date": self.filing_date,
            "holdings": [h.to_dict() for h in self.holdings]
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Filing":
        return cls(
            cik=data.get("cik", ""),
            fund_name=data.get("fund_name", ""),
            filing_date=data.get("filing_date", ""),
            holdings=[Holding.from_dict(h) for h in data.get("holdings", [])]
        )


@dataclass
class HoldingsChange:
    """Represents changes between two filings"""
    new_positions: List[Holding] = field(default_factory=list)
    increased_positions: List[tuple] = field(default_factory=list)  # (old, new)
    decreased_positions: List[tuple] = field(default_factory=list)  # (old, new)
    removed_positions: List[Holding] = field(default_factory=list)
    
    def has_changes(self) -> bool:
        return any([
            self.new_positions,
            self.increased_positions,
            self.decreased_positions,
            self.removed_positions
        ])


@dataclass
class TrackedFund:
    """A hedge fund being tracked"""
    cik: str
    name: str
    last_filing_date: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "cik": self.cik,
            "name": self.name,
            "last_filing_date": self.last_filing_date
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TrackedFund":
        return cls(
            cik=data.get("cik", ""),
            name=data.get("name", ""),
            last_filing_date=data.get("last_filing_date")
        )


class Config:
    """Application configuration"""
    
    def __init__(self):
        self.telegram_token: str = ""
        self.telegram_chat_id: str = ""
        self.tracked_funds: List[TrackedFund] = []
        self.data_dir = "data"
    
    def load(self) -> "Config":
        """Load config from JSON file"""
        import os
        config_path = os.path.join(self.data_dir, "config.json")
        
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                data = json.load(f)
                self.telegram_token = data.get("telegram_token", "")
                self.telegram_chat_id = data.get("telegram_chat_id", "")
                self.tracked_funds = [TrackedFund.from_dict(f) for f in data.get("tracked_funds", [])]
        
        return self
    
    def save(self) -> None:
        """Save config to JSON file"""
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        config_path = os.path.join(self.data_dir, "config.json")
        
        with open(config_path, "w") as f:
            json.dump({
                "telegram_token": self.telegram_token,
                "telegram_chat_id": self.telegram_chat_id,
                "tracked_funds": [f.to_dict() for f in self.tracked_funds]
            }, f, indent=2)
    
    def add_fund(self, fund: TrackedFund) -> None:
        """Add a fund to tracking"""
        if not any(f.cik == fund.cik for f in self.tracked_funds):
            self.tracked_funds.append(fund)
            self.save()
    
    def remove_fund(self, cik: str) -> bool:
        """Remove a fund from tracking"""
        original_len = len(self.tracked_funds)
        self.tracked_funds = [f for f in self.tracked_funds if f.cik != cik]
        if len(self.tracked_funds) != original_len:
            self.save()
            return True
        return False


class FilingCache:
    """Cache of last known holdings per fund"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.cache: Dict[str, Filing] = {}
        self.load()
    
    def load(self) -> None:
        """Load cache from JSON file"""
        import os
        cache_path = os.path.join(self.data_dir, "filings_cache.json")
        
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                data = json.load(f)
                self.cache = {k: Filing.from_dict(v) for k, v in data.items()}
    
    def save(self) -> None:
        """Save cache to JSON file"""
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        cache_path = os.path.join(self.data_dir, "filings_cache.json")
        
        with open(cache_path, "w") as f:
            json.dump({k: v.to_dict() for k, v in self.cache.items()}, f, indent=2)
    
    def get_last_filing(self, cik: str) -> Optional[Filing]:
        """Get the last known filing for a fund"""
        return self.cache.get(cik)
    
    def update_filing(self, filing: Filing) -> None:
        """Update the cached filing for a fund"""
        self.cache[filing.cik] = filing
        self.save()


class State:
    """Application state"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.last_run: Optional[str] = None
        self.load()
    
    def load(self) -> None:
        """Load state from JSON file"""
        import os
        state_path = os.path.join(self.data_dir, "state.json")
        
        if os.path.exists(state_path):
            with open(state_path, "r") as f:
                data = json.load(f)
                self.last_run = data.get("last_run")
    
    def save(self) -> None:
        """Save state to JSON file"""
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        state_path = os.path.join(self.data_dir, "state.json")
        
        with open(state_path, "w") as f:
            json.dump({
                "last_run": self.last_run
            }, f, indent=2)
    
    def update_last_run(self) -> None:
        """Update last run timestamp"""
        self.last_run = datetime.now().isoformat()
        self.save()
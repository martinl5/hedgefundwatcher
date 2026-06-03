"""Tests for data models."""

import tempfile

from models import Config, Filing, FilingCache, Holding, HoldingsChange, TrackedFund

# ──────────────────────────── Holding ────────────────────────────


def test_holding_round_trip():
    h = Holding(ticker="AAPL", name="Apple Inc", shares=1000, value=175000.0)
    assert Holding.from_dict(h.to_dict()) == h


def test_holding_from_dict_defaults():
    h = Holding.from_dict({})
    assert h.ticker == ""
    assert h.shares == 0
    assert h.value == 0.0


# ──────────────────────────── Filing ─────────────────────────────


def test_filing_round_trip():
    holdings = [Holding("AAPL", "Apple", 100, 17000.0), Holding("MSFT", "Microsoft", 50, 9000.0)]
    f = Filing(cik="123", fund_name="Test Fund", filing_date="2024-01-01", holdings=holdings)
    assert Filing.from_dict(f.to_dict()) == f


def test_filing_from_dict_empty_holdings():
    f = Filing.from_dict({"cik": "1", "fund_name": "X", "filing_date": "2024-01-01"})
    assert f.holdings == []


# ─────────────────────── HoldingsChange ──────────────────────────


def test_holdings_change_has_changes_false():
    hc = HoldingsChange()
    assert not hc.has_changes()


def test_holdings_change_has_changes_true():
    h = Holding("AAPL", "Apple", 100, 17000.0)
    hc = HoldingsChange(new_positions=[h])
    assert hc.has_changes()


# ──────────────────────────── Config ─────────────────────────────


def test_config_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config()
        cfg.data_dir = tmpdir
        cfg.telegram_token = "1234567890:ABCDefghijklmnopqrstuvwxyzABCDEFGHI"
        cfg.telegram_chat_id = "-100123456"
        fund = TrackedFund(cik="1649339", name="Scion Capital")
        cfg.tracked_funds = [fund]
        cfg.save()

        cfg2 = Config()
        cfg2.data_dir = tmpdir
        cfg2.load()

        assert cfg2.telegram_token == cfg.telegram_token
        assert cfg2.telegram_chat_id == cfg.telegram_chat_id
        assert len(cfg2.tracked_funds) == 1
        assert cfg2.tracked_funds[0].cik == "1649339"


def test_config_load_missing_file_is_noop():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config()
        cfg.data_dir = tmpdir
        cfg.load()
        assert cfg.tracked_funds == []


def test_config_add_fund_deduplicates():
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    fund = TrackedFund(cik="123", name="Fund A")
    cfg.add_fund(fund)
    cfg.add_fund(fund)
    assert len(cfg.tracked_funds) == 1


def test_config_remove_fund():
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    cfg.tracked_funds = [TrackedFund(cik="1", name="A"), TrackedFund(cik="2", name="B")]
    removed = cfg.remove_fund("1")
    assert removed
    assert len(cfg.tracked_funds) == 1
    assert not cfg.remove_fund("999")


def test_config_validate_valid():
    cfg = Config()
    cfg.telegram_token = "1234567890:ABCDefghijklmnopqrstuvwxyzABCDEFGHI"
    cfg.telegram_chat_id = "-100123456"
    cfg.tracked_funds = [TrackedFund(cik="1649339", name="Fund")]
    assert cfg.validate() == []


def test_config_validate_bad_token():
    cfg = Config()
    cfg.telegram_token = "not-a-valid-token"
    errors = cfg.validate()
    assert any("telegram_token" in e for e in errors)


def test_config_validate_bad_chat_id():
    cfg = Config()
    cfg.telegram_chat_id = "not-numeric"
    errors = cfg.validate()
    assert any("telegram_chat_id" in e for e in errors)


def test_config_validate_bad_cik():
    cfg = Config()
    cfg.tracked_funds = [TrackedFund(cik="ABC", name="Bad Fund")]
    errors = cfg.validate()
    assert any("Bad Fund" in e for e in errors)


# ─────────────────────── FilingCache ─────────────────────────────


def test_filing_cache_update_and_retrieve():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = FilingCache(tmpdir)
        filing = Filing(
            cik="123",
            fund_name="Test",
            filing_date="2024-01-01",
            holdings=[Holding("AAPL", "Apple", 100, 17000.0)],
        )
        cache.update_filing(filing)
        retrieved = cache.get_last_filing("123")
        assert retrieved is not None
        assert retrieved.fund_name == "Test"
        assert len(retrieved.holdings) == 1


def test_filing_cache_get_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = FilingCache(tmpdir)
        assert cache.get_last_filing("nonexistent") is None


def test_filing_cache_persists_across_instances():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache1 = FilingCache(tmpdir)
        filing = Filing(cik="42", fund_name="Pershing", filing_date="2024-06-01", holdings=[])
        cache1.update_filing(filing)

        cache2 = FilingCache(tmpdir)
        assert cache2.get_last_filing("42") is not None

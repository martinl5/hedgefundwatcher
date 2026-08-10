"""Tests for fund-list management: add verification, sync, and CIK verification."""

import json
import os
import tempfile
from unittest.mock import patch

from main import _names_match, add_fund, sync_funds, verify_funds
from models import Config, TrackedFund

# ─────────────────────── name matching ───────────────────────────


def test_names_match_ignores_legal_suffixes():
    assert _names_match("Point72 Asset Management", "POINT72 ASSET MANAGEMENT, L.P.")
    assert _names_match("Berkshire Hathaway", "BERKSHIRE HATHAWAY INC")


def test_names_match_allows_nickname_subset():
    # Our labels are shorter than EDGAR's registered names.
    assert _names_match("Citadel Advisors", "CITADEL ADVISORS LLC")
    assert _names_match("Two Sigma", "TWO SIGMA INVESTMENTS, LP")


def test_names_match_strips_parenthetical_asides():
    assert _names_match("Michael Burry (Scion)", "SCION ASSET MANAGEMENT, LLC")


def test_names_match_rejects_different_entity():
    # The real trap: Citadel Securities is the market maker, not the hedge fund.
    assert not _names_match("Citadel Advisors", "CITADEL SECURITIES LLC")
    assert not _names_match("Renaissance Technologies", "APPALOOSA MANAGEMENT LP")


def test_names_match_handles_empty():
    assert not _names_match("", "CITADEL ADVISORS LLC")
    assert not _names_match("Citadel", "")


# ─────────────────────────── add_fund ────────────────────────────


def _config(tmpdir: str) -> Config:
    cfg = Config()
    cfg.data_dir = tmpdir
    return cfg


def test_add_fund_refuses_unresolvable_cik(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _config(tmpdir)
        with patch("main.SECClient") as client_cls:
            client_cls.return_value.get_fund_name.return_value = None
            add_fund(cfg, "Bogus Fund", "9999999")
        assert cfg.tracked_funds == []
        assert "could not be resolved" in capsys.readouterr().out


def test_add_fund_force_skips_lookup():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _config(tmpdir)
        with patch("main.SECClient") as client_cls:
            add_fund(cfg, "Offline Fund", "1234567", force=True)
            client_cls.return_value.get_fund_name.assert_not_called()
        assert len(cfg.tracked_funds) == 1


def test_add_fund_reports_registered_name(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _config(tmpdir)
        with patch("main.SECClient") as client_cls:
            client_cls.return_value.get_fund_name.return_value = "CITADEL ADVISORS LLC"
            add_fund(cfg, "Citadel Advisors", "1423053")
        out = capsys.readouterr().out
        assert "CITADEL ADVISORS LLC" in out
        assert "⚠️" not in out
        assert len(cfg.tracked_funds) == 1


def test_add_fund_warns_on_name_mismatch_but_adds(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _config(tmpdir)
        with patch("main.SECClient") as client_cls:
            client_cls.return_value.get_fund_name.return_value = "CITADEL SECURITIES LLC"
            add_fund(cfg, "Citadel Advisors", "1146184")
        assert "⚠️" in capsys.readouterr().out
        assert len(cfg.tracked_funds) == 1


# ─────────────────────────── sync_funds ──────────────────────────


def _write_fund_list(path: str, funds: list[tuple[str, str]]) -> None:
    with open(path, "w") as f:
        json.dump(
            {"tracked_funds": [{"cik": c, "name": n, "last_filing_date": None} for c, n in funds]},
            f,
        )


def test_sync_adds_missing_funds():
    with tempfile.TemporaryDirectory() as tmpdir:
        example = os.path.join(tmpdir, "example.json")
        _write_fund_list(example, [("1", "Alpha"), ("2", "Beta")])
        cfg = _config(tmpdir)
        assert sync_funds(cfg, example)
        assert {f.cik for f in cfg.tracked_funds} == {"1", "2"}


def test_sync_removes_funds_absent_from_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        example = os.path.join(tmpdir, "example.json")
        _write_fund_list(example, [("1", "Alpha")])
        cfg = _config(tmpdir)
        cfg.tracked_funds = [TrackedFund("1", "Alpha"), TrackedFund("99", "Retired Fund")]
        sync_funds(cfg, example)
        assert {f.cik for f in cfg.tracked_funds} == {"1"}


def test_sync_preserves_state_of_existing_funds():
    # The alert-once guarantee depends on last_filing_date surviving a sync.
    with tempfile.TemporaryDirectory() as tmpdir:
        example = os.path.join(tmpdir, "example.json")
        _write_fund_list(example, [("1", "Alpha"), ("2", "Beta")])
        cfg = _config(tmpdir)
        cfg.tracked_funds = [TrackedFund("1", "Alpha", last_filing_date="2026-05-15")]
        sync_funds(cfg, example)
        alpha = next(f for f in cfg.tracked_funds if f.cik == "1")
        assert alpha.last_filing_date == "2026-05-15"
        beta = next(f for f in cfg.tracked_funds if f.cik == "2")
        assert beta.last_filing_date is None


def test_sync_persists_to_disk():
    with tempfile.TemporaryDirectory() as tmpdir:
        example = os.path.join(tmpdir, "example.json")
        _write_fund_list(example, [("1", "Alpha")])
        sync_funds(_config(tmpdir), example)
        reloaded = _config(tmpdir)
        reloaded.load()
        assert [f.cik for f in reloaded.tracked_funds] == ["1"]


def test_sync_missing_example_file_fails_cleanly():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = _config(tmpdir)
        assert sync_funds(cfg, os.path.join(tmpdir, "nope.json")) is False


# ────────────────────────── verify_funds ─────────────────────────


def test_verify_passes_when_names_match():
    cfg = Config()
    cfg.tracked_funds = [TrackedFund("1423053", "Citadel Advisors")]
    with patch("main.SECClient") as client_cls:
        client = client_cls.return_value
        client.get_fund_name.return_value = "CITADEL ADVISORS LLC"
        client.get_latest_13f_filing.return_value = {"filing_date": "2026-05-15"}
        assert verify_funds(cfg) is True


def test_verify_fails_on_wrong_entity(capsys):
    cfg = Config()
    cfg.tracked_funds = [TrackedFund("1146184", "Citadel Advisors")]
    with patch("main.SECClient") as client_cls:
        client = client_cls.return_value
        client.get_fund_name.return_value = "CITADEL SECURITIES LLC"
        client.get_latest_13f_filing.return_value = {"filing_date": "2026-05-15"}
        assert verify_funds(cfg) is False
    assert "name mismatch" in capsys.readouterr().out


def test_verify_fails_on_unknown_cik(capsys):
    cfg = Config()
    cfg.tracked_funds = [TrackedFund("9999999", "Ghost Fund")]
    with patch("main.SECClient") as client_cls:
        client_cls.return_value.get_fund_name.return_value = None
        assert verify_funds(cfg) is False
    assert "not found on EDGAR" in capsys.readouterr().out


def test_verify_reports_fund_that_never_filed(capsys):
    cfg = Config()
    cfg.tracked_funds = [TrackedFund("1", "Alpha")]
    with patch("main.SECClient") as client_cls:
        client = client_cls.return_value
        client.get_fund_name.return_value = "ALPHA"
        client.get_latest_13f_filing.return_value = None
        assert verify_funds(cfg) is True
    assert "never" in capsys.readouterr().out


def test_verify_empty_list_passes():
    assert verify_funds(Config()) is True

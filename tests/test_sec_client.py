"""Tests for SECClient — parsing and comparison logic."""

import os
from unittest.mock import patch

from models import Filing, Holding
from sec_client import SECClient, compare_filings

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return f.read()


# ─────────────────────── parse_13f_filing ────────────────────────


def test_parse_13f_filing_returns_filing():
    client = SECClient()
    xml = _read_fixture("13f_sample.xml")
    result = client.parse_13f_filing(xml, "http://test/infotable.xml")
    assert isinstance(result, Filing)


def test_parse_13f_filing_holdings_count():
    client = SECClient()
    xml = _read_fixture("13f_sample.xml")
    result = client.parse_13f_filing(xml, "http://test/infotable.xml")
    # 3 infoTable entries: Apple, Microsoft, Nvidia (all have nameOfIssuer)
    assert len(result.holdings) == 3


def test_parse_13f_filing_first_holding_values():
    client = SECClient()
    xml = _read_fixture("13f_sample.xml")
    result = client.parse_13f_filing(xml, "http://test/infotable.xml")
    apple = next(h for h in result.holdings if h.name == "APPLE INC")
    assert apple.value == 5_000_000
    assert apple.shares == 25_000
    assert apple.cusip == "037833"  # first 6 chars of CUSIP


def test_parse_13f_filing_bad_numbers_default_to_zero():
    client = SECClient()
    xml = _read_fixture("13f_sample.xml")
    result = client.parse_13f_filing(xml, "http://test/infotable.xml")
    nvidia = next(h for h in result.holdings if h.name == "NVIDIA CORP")
    assert nvidia.value == 0
    assert nvidia.shares == 0


def test_parse_13f_filing_invalid_xml_returns_none():
    client = SECClient()
    result = client.parse_13f_filing("this is not xml", "http://test")
    assert result is None


def test_parse_13f_filing_empty_xml_returns_empty_filing():
    client = SECClient()
    xml = '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"/>'
    result = client.parse_13f_filing(xml, "http://test")
    assert result is not None
    assert result.holdings == []


# ─────────────────────── compare_filings ─────────────────────────


def _make_filing(holdings: list) -> Filing:
    return Filing(cik="1", fund_name="Test", filing_date="2024-01-01", holdings=holdings)


def test_compare_filings_detects_new_position():
    old = _make_filing([Holding("AAPL", "Apple", 100, 10_000)])
    new = _make_filing([
        Holding("AAPL", "Apple", 100, 10_000),
        Holding("MSFT", "Microsoft", 50, 5_000),
    ])
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert len(new_pos) == 1
    assert new_pos[0].cusip == "MSFT"
    assert inc == []
    assert dec == []
    assert rem == []


def test_compare_filings_detects_removed_position():
    old = _make_filing([
        Holding("AAPL", "Apple", 100, 10_000),
        Holding("MSFT", "Microsoft", 50, 5_000),
    ])
    new = _make_filing([Holding("AAPL", "Apple", 100, 10_000)])
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert len(rem) == 1
    assert rem[0].cusip == "MSFT"


def test_compare_filings_detects_increased_position():
    old = _make_filing([Holding("AAPL", "Apple", 100, 10_000)])
    new = _make_filing([Holding("AAPL", "Apple", 150, 15_000)])
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert len(inc) == 1
    assert inc[0][0].cusip == "AAPL"
    assert inc[0][1].value == 15_000


def test_compare_filings_detects_decreased_position():
    old = _make_filing([Holding("AAPL", "Apple", 100, 10_000)])
    new = _make_filing([Holding("AAPL", "Apple", 50, 5_000)])
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert len(dec) == 1


def test_compare_filings_ignores_small_changes():
    old = _make_filing([Holding("AAPL", "Apple", 100, 10_000)])
    new = _make_filing([Holding("AAPL", "Apple", 110, 11_000)])  # +10%, below 20% threshold
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert new_pos == []
    assert inc == []
    assert dec == []
    assert rem == []


def test_compare_filings_empty_filings():
    old = _make_filing([])
    new = _make_filing([])
    new_pos, inc, dec, rem = compare_filings(old, new)
    assert new_pos == inc == dec == rem == []


# ─────────────────────── get_company_submissions ──────────────────


def test_get_company_submissions_network_error_returns_none():
    import requests as _requests
    client = SECClient()
    with patch.object(
        client.session, "get", side_effect=_requests.ConnectionError("network down")
    ):
        result = client.get_company_submissions("1649339")
    assert result is None


def test_get_13f_filings_empty_when_no_submissions():
    client = SECClient()
    with patch.object(client, "get_company_submissions", return_value=None):
        assert client.get_13f_filings("1649339") == []


def test_get_13f_filings_filters_form_type():
    client = SECClient()
    submissions = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "13F-NT", "13F-HR", "DEF 14A"],
                "filingDate": ["2024-05-01", "2024-02-01", "2023-11-01", "2024-03-01"],
                "accessionNumber": ["0001-01", "0001-02", "0001-03", "0001-04"],
                "primaryDocument": ["primary.xml", "primary.xml", "primary.xml", "proxy.htm"],
            }
        }
    }
    with patch.object(client, "get_company_submissions", return_value=submissions):
        filings = client.get_13f_filings("1649339")
    assert len(filings) == 2
    assert all(f["form"] == "13F-HR" for f in filings)


def test_get_fund_name_returns_name():
    client = SECClient()
    with patch.object(
        client, "get_company_submissions", return_value={"name": "Scion Asset Management"}
    ):
        assert client.get_fund_name("1649339") == "Scion Asset Management"


def test_get_fund_name_returns_none_on_failure():
    client = SECClient()
    with patch.object(client, "get_company_submissions", return_value=None):
        assert client.get_fund_name("1649339") is None

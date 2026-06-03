"""Tests for SECFilingsClient."""

import os
from datetime import datetime, timedelta
from unittest.mock import patch
from xml.etree import ElementTree as ET

from sec_filings import SECFilingsClient

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return f.read()


def _make_submissions(form_list, date_list=None):
    n = len(form_list)
    if date_list is None:
        # Default: all dates within the last 7 days
        today = datetime.now()
        date_list = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]
    return {
        "filings": {
            "recent": {
                "form": form_list,
                "filingDate": date_list,
                "accessionNumber": [f"0001-{i:02d}" for i in range(n)],
                "primaryDocument": [f"doc{i}.xml" for i in range(n)],
            }
        }
    }


# ─────────────────────── _filings_by_form ────────────────────────


def test_filings_by_form_filters_correctly():
    client = SECFilingsClient()
    subs = _make_submissions(["4", "4", "SC 13G", "4", "DEF 14A"])
    with patch.object(client, "get_company_submissions", return_value=subs):
        result = client.get_form4_filings("123", days_back=30)
    assert len(result) == 3
    assert all(f["form"] == "4" for f in result)


def test_filings_by_form_date_cutoff():
    client = SECFilingsClient()
    recent = datetime.now().strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    subs = _make_submissions(["4", "4"], date_list=[recent, old])
    with patch.object(client, "get_company_submissions", return_value=subs):
        result = client.get_form4_filings("123", days_back=30)
    assert len(result) == 1
    assert result[0]["filing_date"] == recent


def test_filings_by_form_bad_date_skipped():
    client = SECFilingsClient()
    subs = _make_submissions(["4", "4"], date_list=["not-a-date", datetime.now().strftime("%Y-%m-%d")])
    with patch.object(client, "get_company_submissions", return_value=subs):
        result = client.get_form4_filings("123", days_back=30)
    assert len(result) == 1


def test_get_form4_filings_empty_when_no_submissions():
    client = SECFilingsClient()
    with patch.object(client, "get_company_submissions", return_value=None):
        assert client.get_form4_filings("123") == []


# ─────────────────────── get_13d_filings ─────────────────────────


def test_get_13d_filings_matches_13d_and_13g():
    client = SECFilingsClient()
    subs = _make_submissions(["SC 13D", "SC 13G", "13D", "13G/A", "4"])
    with patch.object(client, "get_company_submissions", return_value=subs):
        result = client.get_13d_filings("123", days_back=30)
    # "SC 13D" and "SC 13G" contain "13D"/"13G", as do "13D", "13G/A"
    assert len(result) == 4
    assert all("13D" in f["form"] or "13G" in f["form"] for f in result)


# ─────────────────── _extract_form4_details ──────────────────────


def test_extract_form4_details_buy_transaction():
    client = SECFilingsClient()
    # sharesOwnedFollowingTransaction takes priority over transactionShares;
    # when it is present, transaction_shares is not populated.
    xml = """
    <ownershipDocument>
      <rptOwnerName>JOHN DOE</rptOwnerName>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      <sharesOwnedFollowingTransaction><value>100000</value></sharesOwnedFollowingTransaction>
      <transactionPricePerShare><value>50.00</value></transactionPricePerShare>
      <securityTitle><value>Common Stock</value></securityTitle>
      <officerTitle>CEO</officerTitle>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
    </ownershipDocument>
    """
    root = ET.fromstring(xml)
    details = client._extract_form4_details(root)
    assert details is not None
    assert details["owner_name"] == "JOHN DOE"
    assert details["transaction"] == "BUY"
    assert details["shares"] == 100_000
    assert details["price_per_share"] == 50.0
    assert details["position"] == "Common Stock"
    assert details["title"] == "CEO"
    assert details["relationship"] == "Officer"


def test_extract_form4_details_transaction_shares_fallback():
    client = SECFilingsClient()
    # When sharesOwnedFollowingTransaction is absent, transactionShares is used
    xml = """
    <ownershipDocument>
      <rptOwnerName>JANE DOE</rptOwnerName>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      <transactionShares><value>5000</value></transactionShares>
      <transactionPricePerShare><value>50.00</value></transactionPricePerShare>
    </ownershipDocument>
    """
    root = ET.fromstring(xml)
    details = client._extract_form4_details(root)
    assert details["transaction_shares"] == 5_000
    assert details["value"] == 250_000


def test_extract_form4_details_sell_transaction():
    client = SECFilingsClient()
    xml = """
    <ownershipDocument>
      <rptOwnerName>JANE SMITH</rptOwnerName>
      <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      <sharesOwnedFollowingTransaction><value>500000</value></sharesOwnedFollowingTransaction>
    </ownershipDocument>
    """
    root = ET.fromstring(xml)
    details = client._extract_form4_details(root)
    assert details["transaction"] == "SELL"
    assert details["shares"] == 500_000


def test_extract_form4_details_director_relationship():
    client = SECFilingsClient()
    xml = """
    <ownershipDocument>
      <rptOwnerName>BOARD MEMBER</rptOwnerName>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
      <isTenPercentOwner>1</isTenPercentOwner>
    </ownershipDocument>
    """
    root = ET.fromstring(xml)
    details = client._extract_form4_details(root)
    assert "Director" in details["relationship"]
    assert "10% Owner" in details["relationship"]


def test_extract_form4_details_returns_none_for_empty():
    client = SECFilingsClient()
    root = ET.fromstring("<ownershipDocument/>")
    details = client._extract_form4_details(root)
    assert details is None


def test_extract_form4_details_bad_share_number_skipped():
    client = SECFilingsClient()
    xml = """
    <ownershipDocument>
      <rptOwnerName>TEST USER</rptOwnerName>
      <sharesOwnedFollowingTransaction><value>not_a_number</value></sharesOwnedFollowingTransaction>
    </ownershipDocument>
    """
    root = ET.fromstring(xml)
    details = client._extract_form4_details(root)
    assert "shares" not in details


# ──────────────────── search stubs ───────────────────────────────


def test_search_stubs_return_empty():
    client = SECFilingsClient()
    assert client.search_13d_by_ticker("AAPL") == []
    assert client.search_form4_by_ticker("AAPL") == []

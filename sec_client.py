"""SEC EDGAR client for fetching 13F filings."""

import logging
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from models import Filing, Holding
from sec_base import REQUEST_TIMEOUT, SECBaseClient

logger = logging.getLogger(__name__)


class SECClient(SECBaseClient):
    """Client for interacting with SEC EDGAR — 13F filings."""

    def get_13f_filings(self, cik: str) -> list[dict]:
        """Return all 13F-HR filings for a CIK, most recent first."""
        recent = self._get_recent_filings_data(cik)
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])

        return [
            {
                "form": form,
                "filing_date": dates[i],
                "accession_number": accession_numbers[i],
                "primary_document": primary_documents[i],
                "cik": cik,
            }
            for i, form in enumerate(forms)
            if form == "13F-HR"
        ]

    def get_latest_13f_filing(self, cik: str) -> dict | None:
        """Return the most recent 13F-HR filing for a CIK."""
        filings = self.get_13f_filings(cik)
        return filings[0] if filings else None

    def get_filing_url(self, filing: dict) -> str:
        """Return the expected URL for a filing's holdings XML."""
        cik = self._pad_cik(filing["cik"])
        accession = filing["accession_number"].replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/infotable.xml"

    def get_filing_folder_url(self, filing: dict) -> str:
        """Return the URL for the filing's folder index."""
        cik = self._pad_cik(filing["cik"])
        accession = filing["accession_number"].replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

    def find_holdings_xml_in_folder(self, folder_url: str) -> str | None:
        """Find the holdings XML file URL by scraping a filing folder page."""
        self._rate_limit()
        try:
            response = self.session.get(folder_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            for link in soup.find_all("a"):
                href = link.get("href", "")
                if (
                    ".xml" in href
                    and "xbrl" not in href.lower()
                    and "index" not in href.lower()
                ):
                    return href if href.startswith("http") else f"https://www.sec.gov{href}"
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Network error finding XML in folder %s: %s", folder_url, e)
            return None
        except Exception as e:
            logger.error("Error finding XML in folder %s: %s", folder_url, e)
            return None

    def get_filing_content(
        self, url: str, fallback_folder_url: str | None = None
    ) -> str | None:
        """Fetch filing XML content, falling back to folder search if needed."""
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200 and len(response.text) > 100:
                return response.text
            if fallback_folder_url:
                xml_url = self.find_holdings_xml_in_folder(fallback_folder_url)
                if xml_url:
                    return self.get_filing_content(xml_url)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Network error fetching filing content from %s: %s", url, e)
            return None
        except Exception as e:
            logger.error("Error fetching filing content from %s: %s", url, e)
            return None

    def parse_13f_filing(self, content: str, url: str) -> Filing | None:
        """Parse 13F-HR filing XML into a Filing object."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            logger.error("XML parse error for filing at %s: %s", url, e)
            return None

        ns = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
        holdings: list[Holding] = []

        for info_table in root.findall(".//ns:infoTable", ns):
            name_el = info_table.find("ns:nameOfIssuer", ns)
            cusip_el = info_table.find("ns:cusip", ns)
            value_el = info_table.find("ns:value", ns)
            shares_el = info_table.find("ns:shrsOrPrnAmt/ns:sshPrnamt", ns)

            if name_el is None or not name_el.text:
                continue

            name = name_el.text
            cusip = (
                cusip_el.text[:6]
                if (cusip_el is not None and cusip_el.text)
                else "UNKNOWN"
            )

            try:
                value = (
                    int(value_el.text)
                    if (value_el is not None and value_el.text)
                    else 0
                )
            except (ValueError, TypeError):
                value = 0

            try:
                shares = (
                    int(shares_el.text)
                    if (shares_el is not None and shares_el.text)
                    else 0
                )
            except (ValueError, TypeError):
                shares = 0

            holdings.append(Holding(ticker=cusip, name=name, shares=shares, value=value))

        return Filing(cik="", fund_name="", filing_date="", holdings=holdings)

    def get_fund_name(self, cik: str) -> str | None:
        """Return the fund/company name for a CIK."""
        submissions = self.get_company_submissions(cik)
        return submissions.get("name", "") if submissions else None


def compare_filings(
    old_filing: Filing,
    new_filing: Filing,
) -> tuple[list[Holding], list[tuple], list[tuple], list[Holding]]:
    """Compare two filings; return (new, increased, decreased, removed) positions."""
    old_holdings = {h.ticker: h for h in old_filing.holdings}
    new_holdings = {h.ticker: h for h in new_filing.holdings}

    new_positions: list[Holding] = []
    increased_positions: list[tuple] = []
    decreased_positions: list[tuple] = []
    removed_positions: list[Holding] = []

    for ticker, new_h in new_holdings.items():
        if ticker not in old_holdings:
            new_positions.append(new_h)
        else:
            old_h = old_holdings[ticker]
            if new_h.value > old_h.value * 1.2:
                increased_positions.append((old_h, new_h))
            elif new_h.value < old_h.value * 0.8:
                decreased_positions.append((old_h, new_h))

    for ticker, old_h in old_holdings.items():
        if ticker not in new_holdings:
            removed_positions.append(old_h)

    return new_positions, increased_positions, decreased_positions, removed_positions


# Popular hedge fund CIKs that actually file 13F
HEDGE_FUND_CIKS = {
    "Michael Burry (Scion)": "0001649339",
    "Cathie Wood (ARK Invest)": "0001618652",
    "Bill Ackman (Pershing Square)": "0001336528",
    "David Tepper (Appaloosa)": "0001022315",
    "Soros Fund Management": "0001069355",
    "Leon Black (Apollo)": "0001508217",
    "Daniel Loeb (Third Point)": "0001066299",
    "John Paulson": "0001393535",
    "Renaissance Technologies": "0001037029",
    "Bridgewater Associates": "0001350694",
    "Two Sigma Investments": "0001078013",
    "Point72 Asset Management": "0001552567",
    "Citadel Advisors": "0001146184",
    "D.E. Shaw": "0001009299",
}

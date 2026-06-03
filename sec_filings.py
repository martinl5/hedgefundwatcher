"""SEC filings client for 13D/13G and Form 4 insider filings."""

import logging
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from sec_base import REQUEST_TIMEOUT, SECBaseClient

logger = logging.getLogger(__name__)


class SECFilingsClient(SECBaseClient):
    """Client for 13D/13G beneficial ownership and Form 4 insider filings."""

    # ==================== 13D FILINGS ====================

    def get_13d_filings(self, cik: str, days_back: int = 90) -> list[dict]:
        """Return 13D/13G filings for a CIK within the last `days_back` days."""
        recent = self._get_recent_filings_data(cik)
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        cutoff = datetime.now() - timedelta(days=days_back)

        filings = []
        for i, form in enumerate(forms):
            if "13D" not in form and "13G" not in form:
                continue
            filing_date = dates[i]
            try:
                if datetime.strptime(filing_date, "%Y-%m-%d") >= cutoff:
                    filings.append({
                        "form": form,
                        "filing_date": filing_date,
                        "accession_number": accession_numbers[i],
                        "primary_document": primary_documents[i],
                        "cik": cik,
                    })
            except ValueError:
                logger.warning("Unparseable filing date %r for CIK %s", filing_date, cik)

        return filings

    def get_latest_13d_filings(self, cik: str, limit: int = 10) -> list[dict]:
        """Return the `limit` most recent 13D/13G filings for a CIK."""
        recent = self._get_recent_filings_data(cik)
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])

        filings = []
        for i, form in enumerate(forms):
            if "13D" in form or "13G" in form:
                filings.append({
                    "form": form,
                    "filing_date": dates[i],
                    "accession_number": accession_numbers[i],
                    "primary_document": primary_documents[i],
                    "cik": cik,
                })
                if len(filings) >= limit:
                    break
        return filings

    # ==================== FORM 4 (INSIDER) ====================

    def get_form4_filings(self, cik: str, days_back: int = 30) -> list[dict]:
        """Return Form 4 insider filings for a CIK within the last `days_back` days."""
        return self._filings_by_form(cik, "4", days_back)

    def get_company_insider_filings(self, cik: str, days_back: int = 30) -> list[dict]:
        """Alias for get_form4_filings."""
        return self._filings_by_form(cik, "4", days_back)

    def _filings_by_form(self, cik: str, form_type: str, days_back: int) -> list[dict]:
        """Return recent filings matching `form_type` within `days_back` days."""
        recent = self._get_recent_filings_data(cik)
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        cutoff = datetime.now() - timedelta(days=days_back)

        filings = []
        for i, form in enumerate(forms):
            if form != form_type:
                continue
            filing_date = dates[i]
            try:
                if datetime.strptime(filing_date, "%Y-%m-%d") >= cutoff:
                    filings.append({
                        "form": form,
                        "filing_date": filing_date,
                        "accession_number": accession_numbers[i],
                        "primary_document": primary_documents[i],
                        "cik": cik,
                    })
            except ValueError:
                logger.warning("Unparseable filing date %r for CIK %s", filing_date, cik)
        return filings

    # ==================== FORM 4 DETAIL PARSING ====================

    def get_filing_details(self, cik: str, accession: str, primary_doc: str) -> dict | None:
        """Fetch and parse a Form 4 filing XML; returns a details dict or None."""
        self._rate_limit()

        accession_clean = accession.replace("-", "")
        filer_cik_long = accession_clean[:10]
        filer_cik_short = str(int(filer_cik_long))

        folder_urls = [
            f"https://www.sec.gov/Archives/edgar/data/{filer_cik_long}/{accession_clean}/",
        ]
        if filer_cik_short != filer_cik_long:
            folder_urls.append(
                f"https://www.sec.gov/Archives/edgar/data/{filer_cik_short}/{accession_clean}/"
            )
        if cik:
            folder_urls.append(
                f"https://www.sec.gov/Archives/edgar/data/{cik.zfill(10)}/{accession_clean}/"
            )

        self.session.headers.update({"Accept": "text/html"})
        xml_url: str | None = None

        for folder_url in folder_urls:
            try:
                response = self.session.get(folder_url, timeout=REQUEST_TIMEOUT)
            except Exception as e:
                logger.debug("Could not reach folder %s: %s", folder_url, e)
                continue

            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.content, "html.parser")
            for link in soup.find_all("a"):
                href = link.get("href", "")
                if not isinstance(href, str):
                    continue
                text = link.get_text(strip=True).lower()
                if (
                    "form4" in text
                    or (text.endswith(".xml") and "xbrl" not in href and "index" not in href)
                ) and "index" not in href.lower():
                    xml_url = "https://www.sec.gov" + href
                    break
            if xml_url:
                break

        if not xml_url:
            return None

        self.session.headers.update({"Accept": "application/xml"})
        try:
            xml_response = self.session.get(xml_url, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            logger.error("Failed to fetch Form 4 XML at %s: %s", xml_url, e)
            return None

        if xml_response.status_code != 200:
            return None

        try:
            root = ET.fromstring(xml_response.text)
        except ET.ParseError as e:
            logger.error("XML parse error in Form 4 at %s: %s", xml_url, e)
            return None

        return self._extract_form4_details(root)

    def _extract_form4_details(self, root: ET.Element) -> dict | None:
        """Extract structured data from a parsed Form 4 XML root."""

        def _find_value(element: ET.Element, tag_path: str) -> str | None:
            found = element.find(tag_path)
            if found is not None and found.text:
                return found.text.strip()
            found = element.find(f"{tag_path}//value")
            if found is not None and found.text:
                return found.text.strip()
            return None

        details: dict = {}

        for path in (".//rptOwnerName", ".//reportingOwnerName", ".//ownerName"):
            elem = root.find(path)
            if elem is not None and elem.text:
                details["owner_name"] = elem.text.strip()
                break

        for path in (".//transactionAcquiredDisposedCode", ".//transactionCode"):
            elem = root.find(path)
            if elem is None:
                continue
            val = _find_value(elem, "value") or (elem.text.strip() if elem.text else None)
            if val:
                details["transaction"] = {"A": "BUY", "D": "SELL", "M": "TRANSFER"}.get(val, val)
                break

        for path in (".//sharesOwnedFollowingTransaction", ".//sharesOwnedAfterTransaction"):
            elem = root.find(path)
            if elem is None:
                continue
            val = _find_value(elem, "value")
            if val:
                try:
                    details["shares"] = int(val.replace(",", ""))
                    break
                except (ValueError, TypeError):
                    pass

        if "shares" not in details:
            for path in (".//transactionShares", ".//sharesAcquiredDisposed"):
                elem = root.find(path)
                if elem is None:
                    continue
                val = _find_value(elem, "value")
                if val:
                    try:
                        details["transaction_shares"] = int(val.replace(",", ""))
                        break
                    except (ValueError, TypeError):
                        pass

        for path in (".//transactionPricePerShare", ".//pricePerShare"):
            elem = root.find(path)
            if elem is None:
                continue
            val = _find_value(elem, "value")
            if val:
                try:
                    details["price_per_share"] = float(val.replace(",", ""))
                    if "transaction_shares" in details:
                        details["value"] = int(
                            details["transaction_shares"] * details["price_per_share"]
                        )
                    break
                except (ValueError, TypeError):
                    pass

        for path in (".//securityTitle", ".//titleOfSecurity"):
            elem = root.find(path)
            if elem is None:
                continue
            val = _find_value(elem, "value")
            if val:
                details["position"] = val
                break

        for path in (".//officerTitle", ".//directorTitle", ".//title"):
            elem = root.find(path)
            if elem is not None and elem.text:
                details["title"] = elem.text.strip()
                break

        relationships = []
        for path, label in (
            (".//isDirector", "Director"),
            (".//isOfficer", "Officer"),
            (".//isTenPercentOwner", "10% Owner"),
        ):
            elem = root.find(path)
            if elem is not None and elem.text and elem.text.strip() == "1":
                relationships.append(label)
        if relationships:
            details["relationship"] = ", ".join(relationships)

        return details if details else None

    # ==================== SEARCH STUBS ====================

    def search_13d_by_ticker(self, ticker: str, days_back: int = 90) -> list[dict]:
        """Stub: 13D search by ticker requires SEC API access not yet implemented."""
        logger.info(
            "13D ticker search requires authentication (ticker=%s) — returning empty list", ticker
        )
        return []

    def search_form4_by_ticker(self, ticker: str, days_back: int = 30) -> list[dict]:
        """Stub: Form 4 search by ticker requires SEC API access not yet implemented."""
        logger.info(
            "Form 4 ticker search requires authentication (ticker=%s) — returning empty list",
            ticker,
        )
        return []


# Common tickers to track (for demo)
POPULAR_TICKERS = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corp",
    "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.",
    "NVDA": "NVIDIA Corp",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    "BRK.B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase",
    "V": "Visa Inc.",
}

"""SEC EDGAR client for fetching 13F filings."""

import logging
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from models import Filing, Holding
from sec_base import SECBaseClient

logger = logging.getLogger(__name__)

# A position must move by more than this fraction of its share count to be
# reported as an add or a trim.
CHANGE_THRESHOLD = 0.2
# Positions smaller than this (in dollars, either side) are ignored when
# classifying changes — rounding noise on token stakes isn't worth an alert.
MIN_POSITION_VALUE = 100_000


def _to_int(element: ET.Element | None) -> int:
    """Return an element's text as an int, or 0 when absent or non-numeric."""
    if element is None or not element.text:
        return 0
    try:
        return int(element.text.strip())
    except (ValueError, TypeError):
        return 0


def _text(element: ET.Element | None, default: str = "") -> str:
    """Return an element's stripped text, or ``default`` when absent."""
    if element is None or not element.text:
        return default
    return element.text.strip()


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
        response = self.request(folder_url)
        if response is None or response.status_code != 200:
            return None
        try:
            soup = BeautifulSoup(response.content, "html.parser")
            for link in soup.find_all("a"):
                href = link.get("href", "")
                if not isinstance(href, str):
                    continue
                if (
                    ".xml" in href
                    and "xbrl" not in href.lower()
                    and "index" not in href.lower()
                ):
                    return href if href.startswith("http") else f"https://www.sec.gov{href}"
            return None
        except Exception as e:
            logger.error("Error finding XML in folder %s: %s", folder_url, e)
            return None

    def get_filing_content(
        self, url: str, fallback_folder_url: str | None = None
    ) -> str | None:
        """Fetch filing XML content, falling back to folder search if needed."""
        response = self.request(url)
        if response is not None and response.status_code == 200 and len(response.text) > 100:
            return response.text
        if fallback_folder_url:
            xml_url = self.find_holdings_xml_in_folder(fallback_folder_url)
            if xml_url:
                return self.get_filing_content(xml_url)
        return None

    def parse_13f_filing(self, content: str, url: str) -> Filing | None:
        """Parse 13F-HR filing XML into a Filing object."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            logger.error("XML parse error for filing at %s: %s", url, e)
            return None

        ns = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}

        # A 13F lists a security once per investment-discretion bucket and
        # sub-manager, so one position routinely spans several infoTable rows.
        # Sum them into a single holding instead of letting later rows shadow
        # earlier ones, which would understate the position.
        aggregated: dict[tuple[str, str, str], Holding] = {}

        for info_table in root.findall(".//ns:infoTable", ns):
            name_el = info_table.find("ns:nameOfIssuer", ns)
            if name_el is None or not name_el.text:
                continue

            cusip = _text(info_table.find("ns:cusip", ns))[:6] or "UNKNOWN"
            holding = Holding(
                cusip=cusip,
                name=name_el.text,
                shares=_to_int(info_table.find("ns:shrsOrPrnAmt/ns:sshPrnamt", ns)),
                value=_to_int(info_table.find("ns:value", ns)),
                put_call=_text(info_table.find("ns:putCall", ns)),
                amount_type=_text(info_table.find("ns:shrsOrPrnAmt/ns:sshPrnamtType", ns), "SH"),
            )

            existing = aggregated.get(holding.key)
            if existing is None:
                aggregated[holding.key] = holding
            else:
                existing.shares += holding.shares
                existing.value += holding.value

        return Filing(cik="", fund_name="", filing_date="", holdings=list(aggregated.values()))

    def get_fund_name(self, cik: str) -> str | None:
        """Return the fund/company name for a CIK."""
        submissions = self.get_company_submissions(cik)
        return submissions.get("name", "") if submissions else None


def _looks_like_split(old: Holding, new: Holding) -> bool:
    """Heuristic: did a share-count change come from a split rather than a trade?

    A forward or reverse split multiplies the share count by a near-integer
    factor while leaving the position's market value essentially untouched.
    A genuine doubling of a position moves value roughly in step with shares,
    and that is what separates the two cases. Only near-integer factors are
    recognised, so unusual ratios (a 3-for-2, say) still read as trades.
    """
    if old.shares <= 0 or new.shares <= 0 or old.value <= 0 or new.value <= 0:
        return False

    share_ratio = new.shares / old.shares
    # Invert reverse splits so both directions are checked against the same band.
    factor = share_ratio if share_ratio >= 1 else 1 / share_ratio
    nearest = round(factor)
    if nearest < 2 or abs(factor - nearest) / nearest > 0.05:
        return False

    value_ratio = new.value / old.value
    return 0.75 <= value_ratio <= 1.35


def compare_filings(
    old_filing: Filing,
    new_filing: Filing,
) -> tuple[list[Holding], list[tuple], list[tuple], list[Holding]]:
    """Compare two filings; return (new, increased, decreased, removed) positions.

    Increases and decreases are measured on **share count**, not market value.
    A 13F's ``value`` is the position's market value at quarter end, so diffing
    it would report a fund that never traded as having bought whenever the
    stock rallied — and would hide a real trim behind a rising price. Share
    counts move only when the fund actually trades (or the issuer splits, which
    ``_looks_like_split`` filters out).
    """
    old_holdings = {h.key: h for h in old_filing.holdings}
    new_holdings = {h.key: h for h in new_filing.holdings}

    new_positions: list[Holding] = []
    increased_positions: list[tuple] = []
    decreased_positions: list[tuple] = []
    removed_positions: list[Holding] = []

    for key, new_h in new_holdings.items():
        old_h = old_holdings.get(key)
        if old_h is None:
            new_positions.append(new_h)
            continue

        if max(old_h.value, new_h.value) < MIN_POSITION_VALUE:
            continue

        if _looks_like_split(old_h, new_h):
            logger.debug(
                "Treating %s share change (%d → %d) as a split, not a trade",
                new_h.display_name, old_h.shares, new_h.shares,
            )
            continue

        if new_h.shares > old_h.shares * (1 + CHANGE_THRESHOLD):
            increased_positions.append((old_h, new_h))
        elif new_h.shares < old_h.shares * (1 - CHANGE_THRESHOLD):
            decreased_positions.append((old_h, new_h))

    for key, old_h in old_holdings.items():
        if key not in new_holdings:
            removed_positions.append(old_h)

    return new_positions, increased_positions, decreased_positions, removed_positions


# Suggestions offered by the `known` command. Every entry below was confirmed
# against EDGAR's submissions API on 2026-08-10 — the name in the comment is the
# registered name that CIK resolves to.
#
# Keep it that way. An earlier version of this list had ten wrong CIKs, and the
# failure mode is silent: a wrong CIK usually still resolves to *some*
# institutional manager filing on the same quarterly schedule, so the tool
# reports confident, plausible holdings for the wrong firm and nothing looks
# broken. "Two Sigma" pointed at Great Lakes Advisors, an unrelated active 13F
# filer, and "Point72" pointed at a private individual.
#
# Re-check with: python main.py verify-funds --known
HEDGE_FUND_CIKS = {
    "Berkshire Hathaway": "0001067983",  # BERKSHIRE HATHAWAY INC
    "Bridgewater Associates": "0001350694",  # Bridgewater Associates, LP
    "Citadel Advisors": "0001423053",  # CITADEL ADVISORS LLC
    "D.E. Shaw": "0001009207",  # D. E. Shaw & Co., Inc.
    "Pershing Square": "0001336528",  # Pershing Square Capital Management, L.P.
    "Point72 Asset Management": "0001603466",  # Point72 Asset Management, L.P.
    "Renaissance Technologies": "0001037389",  # RENAISSANCE TECHNOLOGIES LLC
    "Susquehanna International Group": "0001446194",  # SUSQUEHANNA INTERNATIONAL GROUP, LLP
    "Two Sigma Investments": "0001179392",  # TWO SIGMA INVESTMENTS, LP
}

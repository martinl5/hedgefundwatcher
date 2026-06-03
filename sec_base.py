"""Shared SEC EDGAR client base: rate limiting, CIK padding, and submissions fetch."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15  # seconds — prevents hanging on slow SEC responses


class SECBaseClient:
    """Base class shared by SECClient and SECFilingsClient."""

    BASE_URL = "https://data.sec.gov"
    # SEC public API rate limit is 10 requests/second; 0.1s keeps us safely under it.
    DELAY_BETWEEN_REQUESTS = 0.1

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HedgeFundWatcher/1.0 (contact@example.com)",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
        self.last_request_time: float = 0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.DELAY_BETWEEN_REQUESTS:
            time.sleep(self.DELAY_BETWEEN_REQUESTS - elapsed)
        self.last_request_time = time.time()

    def _pad_cik(self, cik: str) -> str:
        return cik.zfill(10)

    def get_company_submissions(self, cik: str) -> dict | None:
        """Fetch the SEC submissions JSON for a CIK."""
        self._rate_limit()
        cik_padded = self._pad_cik(cik)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error("Failed to fetch submissions for CIK %s: %s", cik, e)
            return None

    def _get_recent_filings_data(self, cik: str) -> dict | None:
        """Return the 'recent' filings dict from a company's submissions, or None."""
        submissions = self.get_company_submissions(cik)
        if not submissions:
            return None
        recent = submissions.get("filings", {}).get("recent", {})
        return recent if recent else None

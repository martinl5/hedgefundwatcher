"""Shared SEC EDGAR client base: rate limiting, CIK padding, and submissions fetch."""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15  # seconds — prevents hanging on slow SEC responses
MAX_RETRIES = 3  # transient SEC errors (429/5xx/timeouts) are common; retry before giving up
BACKOFF_BASE = 2.0  # seconds; doubled each retry (2s, 4s, 8s)
FALLBACK_USER_AGENT = "HedgeFundWatcher/1.0 (contact@example.com)"


def _user_agent() -> str:
    """Return the User-Agent to send to SEC, warning if it isn't personalised.

    SEC's fair-access policy requires requests to declare a real contact
    address and serves HTTP 403 to clients that don't, so the placeholder
    below is a last resort rather than a working default.
    """
    configured = os.environ.get("SEC_USER_AGENT", "").strip()
    if configured:
        return configured
    logger.warning(
        "SEC_USER_AGENT is not set — falling back to a placeholder contact address. "
        "SEC may reject these requests with HTTP 403; set SEC_USER_AGENT to something "
        "like 'HedgeFundWatcher/1.0 (you@example.com)'."
    )
    return FALLBACK_USER_AGENT


class SECBaseClient:
    """Base class shared by SECClient and SECFilingsClient."""

    BASE_URL = "https://data.sec.gov"
    # SEC public API rate limit is 10 requests/second; 0.1s keeps us safely under it.
    DELAY_BETWEEN_REQUESTS = 0.1

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": _user_agent(),
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

    def request(self, url: str) -> requests.Response | None:
        """GET a URL with rate limiting and retry/backoff on transient errors.

        Retries on connection errors, timeouts, HTTP 429 and 5xx responses
        (honouring ``Retry-After`` when present). Returns the final response,
        or None if every attempt failed to reach the server.
        """
        for attempt in range(MAX_RETRIES + 1):
            self._rate_limit()
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    logger.error("Request to %s failed after %d retries: %s", url, MAX_RETRIES, e)
                    return None
                self._sleep_before_retry(attempt, None)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRIES:
                    logger.error("Request to %s gave HTTP %s after %d retries",
                                 url, response.status_code, MAX_RETRIES)
                    return response
                self._sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue

            return response
        return None

    @staticmethod
    def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        delay = BACKOFF_BASE * (2 ** attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        logger.warning("Retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
        time.sleep(delay)

    def get_company_submissions(self, cik: str) -> dict | None:
        """Fetch the SEC submissions JSON for a CIK."""
        cik_padded = self._pad_cik(cik)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        response = self.request(url)
        if response is None:
            return None
        try:
            response.raise_for_status()
            data: dict = response.json()
            return data
        except (requests.RequestException, ValueError) as e:
            logger.error("Failed to fetch submissions for CIK %s: %s", cik, e)
            return None

    def _get_recent_filings_data(self, cik: str) -> dict | None:
        """Return the 'recent' filings dict from a company's submissions, or None."""
        submissions = self.get_company_submissions(cik)
        if not submissions:
            return None
        recent = submissions.get("filings", {}).get("recent", {})
        return recent if recent else None

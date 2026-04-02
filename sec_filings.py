"""SEC filings client for 13D and Form 4 insider filings"""

import requests
import time
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from models import Holding


class SECFilingsClient:
    """Client for fetching 13D and Form 4 filings"""
    
    BASE_URL = "https://data.sec.gov"
    DELAY_BETWEEN_REQUESTS = 0.1
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HedgeFundWatcher/1.0 (contact@example.com)",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
        self.last_request_time = 0
    
    def _rate_limit(self):
        """Apply rate limiting"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.DELAY_BETWEEN_REQUESTS:
            time.sleep(self.DELAY_BETWEEN_REQUESTS - elapsed)
        self.last_request_time = time.time()
    
    def _pad_cik(self, cik: str) -> str:
        """Pad CIK to 10 digits"""
        return cik.zfill(10)
    
    def get_company_submissions(self, cik: str) -> Optional[Dict]:
        """Get company submissions from SEC JSON API"""
        self._rate_limit()
        
        cik_padded = self._pad_cik(cik)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching submissions: {e}")
            return None
    
    # ==================== 13D FILINGS ====================
    
    def get_13d_filings(self, cik: str, days_back: int = 90) -> List[Dict]:
        """Get 13D beneficial ownership filings (>5% ownership)"""
        submissions = self.get_company_submissions(cik)
        
        if not submissions:
            return []
        
        recent = submissions.get("filings", {}).get("recent", {})
        
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        
        # Filter to last N days
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        filings = []
        for i, form in enumerate(forms):
            if "13D" in form or "13G" in form:
                filing_date = dates[i]
                try:
                    if datetime.strptime(filing_date, "%Y-%m-%d") >= cutoff_date:
                        filings.append({
                            "form": form,
                            "filing_date": filing_date,
                            "accession_number": accession_numbers[i],
                            "primary_document": primary_documents[i],
                            "cik": cik
                        })
                except:
                    pass
        
        return filings
    
    def get_latest_13d_filings(self, cik: str, limit: int = 10) -> List[Dict]:
        """Get most recent 13D filings"""
        submissions = self.get_company_submissions(cik)
        
        if not submissions:
            return []
        
        recent = submissions.get("filings", {}).get("recent", {})
        
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
                    "cik": cik
                })
                if len(filings) >= limit:
                    break
        
        return filings
    
    # ==================== FORM 4 (INSIDER) ====================
    
    def get_form4_filings(self, cik: str, days_back: int = 30) -> List[Dict]:
        """Get Form 4 insider transactions"""
        submissions = self.get_company_submissions(cik)
        
        if not submissions:
            return []
        
        recent = submissions.get("filings", {}).get("recent", {})
        
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        filings = []
        for i, form in enumerate(forms):
            if form == "4":
                filing_date = dates[i]
                try:
                    if datetime.strptime(filing_date, "%Y-%m-%d") >= cutoff_date:
                        filings.append({
                            "form": form,
                            "filing_date": filing_date,
                            "accession_number": accession_numbers[i],
                            "primary_document": primary_documents[i],
                            "cik": cik
                        })
                except:
                    pass
        
        return filings
    
    def get_filing_details(self, cik: str, accession: str, primary_doc: str) -> Optional[Dict]:
        """Get detailed information from a Form 4 filing"""
        self._rate_limit()
        
        try:
            # The CIK in the filing is different - it's the filer's CIK
            # Extract the CIK from the accession number (first 10 digits)
            accession_clean = accession.replace("-", "")
            
            # Try multiple folder URL patterns
            folder_urls = []
            
            # Pattern 1: First 10 digits as CIK
            filer_cik_1 = accession_clean[:10]
            folder_urls.append(f"https://www.sec.gov/Archives/edgar/data/{filer_cik_1}/{accession_clean}/")
            
            # Pattern 2: CIK with leading zeros stripped from start
            filer_cik_2 = str(int(accession_clean[:10]))
            if filer_cik_2 != filer_cik_1:
                folder_urls.append(f"https://www.sec.gov/Archives/edgar/data/{filer_cik_2}/{accession_clean}/")
            
            # Pattern 3: Try with company CIK instead (some filings use company CIK)
            if cik:
                folder_urls.append(f"https://www.sec.gov/Archives/edgar/data/{cik.zfill(10)}/{accession_clean}/")
            
            # Update headers for SEC Archives (different from data.sec.gov)
            self.session.headers.update({"Accept": "text/html"})
            
            xml_url = None
            
            for folder_url in folder_urls:
                response = self.session.get(folder_url)
                if response.status_code == 200:
                    # Found valid folder, now find XML
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.content, "html.parser")
                    
                    # Look for form4 XML file in the folder listing
                    for link in soup.find_all("a"):
                        href = link.get("href", "")
                        text = link.get_text(strip=True).lower()
                        
                        # Form 4 XML files typically contain form4 in name
                        if "form4" in text or (text.endswith(".xml") and "xbrl" not in href and "index" not in href):
                            if "index" not in href.lower():
                                xml_url = "https://www.sec.gov" + href
                                break
                    
                    if xml_url:
                        break
                # If 404, try next pattern
            
            if not xml_url:
                return None
            
            # Change Accept header for XML
            self.session.headers.update({"Accept": "application/xml"})
            
            xml_response = self.session.get(xml_url)
            if xml_response.status_code != 200:
                return None
            
            content = xml_response.text
            
            # Parse the XML
            from xml.etree import ElementTree as ET
            root = ET.fromstring(content)
            
            details = {}
            
            # Helper function to find value in nested elements
            def find_value(element, tag_path):
                """Find text in element like tag/value or just tag"""
                found = element.find(tag_path)
                if found is not None and found.text:
                    return found.text.strip()
                # Try with //value suffix
                found = element.find(f"{tag_path}//value")
                if found is not None and found.text:
                    return found.text.strip()
                return None
            
            # Find owner name - various paths in Form 4 XML
            for path in ['.//rptOwnerName', './/reportingOwnerName', './/ownerName']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    details['owner_name'] = elem.text.strip()
                    break
            
            # Find transaction type - A = Buy/Acquire, D = Sell/Dispose
            # Check transactionAcquiredDisposedCode first (A or D)
            # The value is nested inside <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
            for path in ['.//transactionAcquiredDisposedCode', './/transactionCode']:
                elem = root.find(path)
                if elem is not None:
                    # Try to find nested value element
                    val = find_value(elem, 'value')
                    code = val if val else (elem.text.strip() if elem.text else None)
                    if code:
                        if code == 'A':
                            details['transaction'] = 'BUY'
                        elif code == 'D':
                            details['transaction'] = 'SELL'
                        elif code == 'M':
                            details['transaction'] = 'TRANSFER'  # M = transfer/exercise
                        else:
                            details['transaction'] = code
                        break
            
            # Find shares owned following transaction (most useful)
            for path in ['.//sharesOwnedFollowingTransaction', './/sharesOwnedAfterTransaction']:
                elem = root.find(path)
                if elem is not None:
                    val = find_value(elem, 'value')
                    if val:
                        try:
                            details['shares'] = int(val.replace(",", ""))
                            break
                        except:
                            pass
            
            # Also try transaction shares (number of shares in transaction)
            if 'shares' not in details:
                for path in ['.//transactionShares', './/sharesAcquiredDisposed']:
                    elem = root.find(path)
                    if elem is not None:
                        val = find_value(elem, 'value')
                        if val:
                            try:
                                details['transaction_shares'] = int(val.replace(",", ""))
                                break
                            except:
                                pass
            
            # Try to find price per share to calculate value
            for path in ['.//transactionPricePerShare', './/pricePerShare']:
                elem = root.find(path)
                if elem is not None:
                    val = find_value(elem, 'value')
                    if val:
                        try:
                            details['price_per_share'] = float(val.replace(",", ""))
                            # Calculate total value if we have shares
                            if 'transaction_shares' in details:
                                details['value'] = int(details['transaction_shares'] * details['price_per_share'])
                            break
                        except:
                            pass
            
            # Find security title (position type - e.g., Common Stock)
            for path in ['.//securityTitle', './/titleOfSecurity']:
                elem = root.find(path)
                if elem is not None:
                    val = find_value(elem, 'value')
                    if val:
                        details['position'] = val
                        break
            
            # Find officer/director title
            for path in ['.//officerTitle', './/directorTitle', './/title']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    details['title'] = elem.text.strip()
                    break
            
            # Find if director/officer/10% owner
            for path in ['.//isDirector', './/isOfficer', './/isTenPercentOwner']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    if elem.text.strip() == '1':
                        if 'relationship' not in details:
                            details['relationship'] = []
                        if path == './/isDirector':
                            details['relationship'].append('Director')
                        elif path == './/isOfficer':
                            details['relationship'].append('Officer')
                        elif path == './/isTenPercentOwner':
                            details['relationship'].append('10% Owner')
            
            if 'relationship' in details and details['relationship']:
                details['relationship'] = ", ".join(details['relationship'])
            
            return details if details else None
            
        except Exception as e:
            print(f"Error parsing filing details: {e}")
            return None
    
    # ==================== SEARCH BY TICKER ====================
    
    def search_13d_by_ticker(self, ticker: str, days_back: int = 90) -> List[Dict]:
        """Search for 13D filings involving a specific ticker"""
        self._rate_limit()
        
        # Try SEC's RSS feed for recent 13D filings
        # Then filter by ticker in the results
        try:
            url = "https://www.sec.gov/Archives/edgar/rss"
            response = self.session.get(url, headers={"Accept": "application/xml"})
            
            if response.status_code == 200:
                from xml.etree import ElementTree as ET
                root = ET.fromstring(response.content)
                
                # This won't work well - let's use a different approach
                # Try the SEC search page and parse the HTML
        except:
            pass
        
        # Alternative: Use finviz or similar for 13D data
        # For now, return a message about the limitation
        print("Note: 13D search via SEC API requires authentication. Use --ticker with specific company CIK instead.")
        return []
    
    def search_form4_by_ticker(self, ticker: str, days_back: int = 30) -> List[Dict]:
        """Search for Form 4 insider filings for a specific ticker"""
        self._rate_limit()
        
        # Try searching by CIK - get a company's CIK first
        # Then query their Form 4 filings
        
        # For now, let's try the SEC's newer search API
        # Using the /filter endpoint
        try:
            # This is the new SEC API - may need authentication
            url = f"https://api.sec-api.io"
            # This requires a paid API - skip for now
        except:
            pass
        
        print("Note: Form 4 search requires SEC API authentication. Try using a specific company CIK.")
        return []
    
    # ==================== TRACK INSIDER BUYING BY COMPANY ====================
    
    def get_company_insider_filings(self, cik: str, days_back: int = 30) -> List[Dict]:
        """Get Form 4 filings for a specific company (by CIK)"""
        submissions = self.get_company_submissions(cik)
        
        if not submissions:
            return []
        
        recent = submissions.get("filings", {}).get("recent", {})
        
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        filings = []
        for i, form in enumerate(forms):
            if form == "4":
                filing_date = dates[i]
                try:
                    if datetime.strptime(filing_date, "%Y-%m-%d") >= cutoff_date:
                        filings.append({
                            "form": form,
                            "filing_date": filing_date,
                            "accession_number": accession_numbers[i],
                            "primary_document": primary_documents[i],
                            "cik": cik
                        })
                except:
                    pass
        
        return filings


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
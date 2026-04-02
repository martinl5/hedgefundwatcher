"""SEC EDGAR client for fetching 13F filings - using SEC JSON API"""

import requests
import time
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from bs4 import BeautifulSoup
from models import Filing, Holding


class SECClient:
    """Client for interacting with SEC EDGAR using the JSON API"""
    
    BASE_URL = "https://data.sec.gov"
    
    # SEC rate limit - max 10 requests per second
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
            print(f"Error fetching submissions for {cik}: {e}")
            return None
    
    def get_13f_filings(self, cik: str) -> List[Dict]:
        """Get all 13F-HR filings for a CIK"""
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
            if form == "13F-HR":
                filings.append({
                    "form": form,
                    "filing_date": dates[i],
                    "accession_number": accession_numbers[i],
                    "primary_document": primary_documents[i],
                    "cik": cik
                })
        
        return filings
    
    def get_latest_13f_filing(self, cik: str) -> Optional[Dict]:
        """Get the most recent 13F-HR filing for a CIK"""
        filings = self.get_13f_filings(cik)
        return filings[0] if filings else None
    
    def get_filing_url(self, filing: Dict) -> str:
        """Get the URL for a filing's holdings XML"""
        cik = self._pad_cik(filing["cik"])
        accession = filing["accession_number"].replace("-", "")
        
        # Try infotable.xml first (most common)
        # If that fails, we'll need to check folder for actual XML
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/infotable.xml"
    
    def get_filing_folder_url(self, filing: Dict) -> str:
        """Get the URL for the filing's folder"""
        cik = self._pad_cik(filing["cik"])
        accession = filing["accession_number"].replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

    def find_holdings_xml_in_folder(self, folder_url: str) -> Optional[str]:
        """Find the XML file containing holdings in a filing folder"""
        self._rate_limit()

        try:
            response = self.session.get(folder_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "html.parser")

            # Look for XML files that might contain holdings
            for link in soup.find_all("a"):
                href = link.get("href", "")

                # Check if it's an XML file (not xbrl, not index)
                if ".xml" in href and "xbrl" not in href.lower() and "index" not in href.lower():
                    if not href.startswith("http"):
                        return f"https://www.sec.gov{href}"
                    return href

            return None

        except Exception as e:
            print(f"Error finding XML in folder: {e}")
            return None
            
        except Exception as e:
            print(f"Error finding XML in folder: {e}")
            return None
    
    def get_filing_content(self, url: str, fallback_folder_url: str = None) -> Optional[str]:
        """Get the content of a filing, with fallback to folder search"""
        self._rate_limit()
        
        try:
            response = self.session.get(url)
            if response.status_code == 200 and len(response.text) > 100:
                return response.text
            
            # If infotable.xml failed, try to find XML in the folder
            if fallback_folder_url:
                xml_url = self.find_holdings_xml_in_folder(fallback_folder_url)
                if xml_url:
                    return self.get_filing_content(xml_url)
            
            return None
        except Exception as e:
            print(f"Error fetching filing content: {e}")
            return None
    
    def parse_13f_filing(self, content: str, url: str) -> Optional[Filing]:
        """Parse 13F-HR filing content from XML"""
        try:
            from xml.etree import ElementTree as ET
            
            root = ET.fromstring(content)
            holdings = []
            
            # Define namespace
            ns = {'ns': 'http://www.sec.gov/edgar/document/thirteenf/informationtable'}
            
            # Find all infoTable elements
            for info_table in root.findall('.//ns:infoTable', ns):
                name_el = info_table.find('ns:nameOfIssuer', ns)
                cusip_el = info_table.find('ns:cusip', ns)
                value_el = info_table.find('ns:value', ns)
                shares_el = info_table.find('ns:shrsOrPrnAmt/ns:sshPrnamt', ns)
                
                if name_el is not None and name_el.text:
                    name = name_el.text
                    
                    # Handle cusip safely
                    if cusip_el is not None and cusip_el.text:
                        cusip = cusip_el.text[:6]
                    else:
                        cusip = "UNKNOWN"
                    
                    # Handle value safely
                    try:
                        value = int(value_el.text) if value_el is not None and value_el.text else 0
                    except:
                        value = 0
                    
                    # Handle shares safely
                    try:
                        shares = int(shares_el.text) if shares_el is not None and shares_el.text else 0
                    except:
                        shares = 0
                    
                    if name:  # Only add if we have a name
                        holdings.append(Holding(
                            ticker=cusip,
                            name=name,
                            shares=shares,
                            value=value
                        ))
            
            return Filing(
                cik="",
                fund_name="",
                filing_date="",
                holdings=holdings
            )
            
        except Exception as e:
            print(f"Error parsing 13F XML: {e}")
            return None
    
    def _parse_information_table(self, table) -> List[Holding]:
        """Parse informationTable from XML"""
        holdings = []
        
        try:
            # Handle both ElementTree elements and BeautifulSoup elements
            if hasattr(table, 'find_all'):
                # BeautifulSoup element
                rows = table.find_all("tr")
            else:
                # ElementTree element
                rows = list(table)
            
            for row in rows:
                if hasattr(row, 'find_all'):
                    cells = row.find_all()
                else:
                    cells = list(row)
                
                if len(cells) >= 5:
                    try:
                        # Try to extract data
                        name = ""
                        cusip = ""
                        shares = 0
                        value = 0
                        
                        for cell in cells:
                            tag = cell.tag.split("}")[-1] if hasattr(cell, 'tag') else ""
                            text = cell.text.strip() if cell.text else ""
                            
                            if "nameOfIssuer" in tag:
                                name = text
                            elif "cusip" in tag:
                                cusip = text[:6]  # First 6 chars of CUSIP
                            elif "sshPrnamt" in tag:
                                try:
                                    shares = int(text.replace(",", ""))
                                except:
                                    pass
                            elif tag == "value":
                                try:
                                    value = int(text.replace(",", ""))
                                except:
                                    pass
                        
                        if name and (cusip or shares > 0):
                            holdings.append(Holding(
                                ticker=cusip if cusip else "UNKNOWN",
                                name=name,
                                shares=shares,
                                value=value
                            ))
                    except:
                        continue
        
        except Exception as e:
            print(f"Error parsing info table: {e}")
        
        return holdings
    
    def _parse_13f_html_table(self, soup) -> List[Holding]:
        """Parse 13F from HTML table"""
        holdings = []
        
        tables = soup.find_all("table")
        
        for table in tables:
            rows = table.find_all("tr")
            
            for row in rows:
                cols = row.find_all(["td", "th"])
                
                # Look for typical 13F structure
                if len(cols) >= 4:
                    try:
                        name = cols[0].get_text(strip=True)
                        
                        # Skip headers
                        if name in ["NAME OF ISSUER", "TITLE OF CLASS", "CUSIP", "SHARES", "VALUE", "TOTAL"]:
                            continue
                        
                        # Try to find ticker (often in second column or in name)
                        ticker = ""
                        if len(cols) > 1:
                            potential_ticker = cols[1].get_text(strip=True)
                            if potential_ticker and len(potential_ticker) <= 6:
                                ticker = potential_ticker
                        
                        # Find numeric columns
                        shares = 0
                        value = 0
                        
                        for col in cols:
                            text = col.get_text(strip=True).replace(",", "").replace("$", "")
                            try:
                                num = int(text)
                                if num > 10000000:  # Likely value
                                    value = num
                                elif num > 1000:  # Likely shares
                                    shares = num
                            except:
                                continue
                        
                        if name and (shares > 0 or value > 0):
                            holdings.append(Holding(
                                ticker=ticker if ticker else "UNKNOWN",
                                name=name,
                                shares=shares,
                                value=value
                            ))
                    except:
                        continue
        
        return holdings
    
    def get_fund_name(self, cik: str) -> Optional[str]:
        """Get company/fund name from CIK"""
        submissions = self.get_company_submissions(cik)
        if submissions:
            return submissions.get("name", "")
        return None


def compare_filings(old_filing: Filing, new_filing: Filing) -> Tuple[List[Holding], List[tuple], List[tuple], List[Holding]]:
    """Compare two filings and identify changes"""
    
    # Create lookup by ticker
    old_holdings = {h.ticker: h for h in old_filing.holdings}
    new_holdings = {h.ticker: h for h in new_filing.holdings}
    
    new_positions = []
    increased_positions = []
    decreased_positions = []
    removed_positions = []
    
    # Find new and increased positions
    for ticker, new_h in new_holdings.items():
        if ticker not in old_holdings:
            new_positions.append(new_h)
        else:
            old_h = old_holdings[ticker]
            # Check for >20% change
            if new_h.value > old_h.value * 1.2:
                increased_positions.append((old_h, new_h))
            elif new_h.value < old_h.value * 0.8:
                decreased_positions.append((old_h, new_h))
    
    # Find removed positions
    for ticker, old_h in old_holdings.items():
        if ticker not in new_holdings:
            removed_positions.append(old_h)
    
    return new_positions, increased_positions, decreased_positions, removed_positions


# Popular hedge fund CIKs that actually file 13F
HEDGE_FUND_CIKS = {
    "Michael Burry (Scion)": "0001649339",  # Files 13F-HR
    "Cathie Wood (ARK Invest)": "0001618652",  # Files 13F-HR
    "Bill Ackman (Pershing Square)": "0001336528",  # Check if files
    "David Tepper (Appaloosa)": "0001022315",  # Check if files
    "Soros Fund Management": "0001069355",  # Check if files
    "Leon Black (Apollo)": "0001508217",  # Check if files
    "Daniel Loeb (Third Point)": "0001066299",  # Check if files
    "John Paulson": "0001393535",  # Check if files
    "Renaissance Technologies": "0001037029",  # May not file publicly
    "Bridgewater Associates": "0001350694",  # May not file publicly
    "Two Sigma Investments": "0001078013",  # May not file publicly
    "Point72 Asset Management": "0001552567",  # May not file publicly
    "Citadel Advisors": "0001146184",  # May not file publicly
    "D.E. Shaw": "0001009299",  # May not file publicly
}
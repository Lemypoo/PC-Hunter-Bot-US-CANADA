"""Shared building blocks for retailer sources."""
import re
from dataclasses import dataclass, asdict
from typing import Optional

# "$929.98", "C $800.00", "CA$550", "US $1,229.98" -> 929.98 etc.
PRICE_RE = re.compile(r'(?:C\s?\$|CA\s?\$|US\s?\$|CAD\s?|USD\s?|\$)\s*(\d[\d,]*(?:\.\d{1,2})?)')
BARE_RE = re.compile(r'^\s*(\d[\d,]*(?:\.\d{1,2})?)\s*$')

# Stores sometimes show a price in the other country's dollars: eBay.ca prints some listings as
# "US $1,229.98", and Amazon.com shows a visitor from Canada "CAD 16,358.58". Such a price is skipped
# rather than filed under the wrong currency.
_USD_RE = re.compile(r"US\s?\$|\bUSD\b")
_CAD_RE = re.compile(r"\bC\s?\$|\bCA\s?\$|\bCAD\b")
_OTHER_RE = re.compile(r"[€£¥₹]|\b(?:EUR|GBP|JPY|AUD|MXN)\b")


def shown_currency(text, region: str) -> str:
    """The currency a price is written in: 'USD', 'CAD', or 'other'. A bare '$' is the store's own."""
    t = str(text or "")
    if _USD_RE.search(t):
        return "USD"
    if _CAD_RE.search(t):
        return "CAD"
    if _OTHER_RE.search(t):
        return "other"
    return "CAD" if region == "CA" else "USD"


def parse_price(text) -> Optional[float]:
    if text is None:
        return None
    s = str(text)
    m = PRICE_RE.search(s) or BARE_RE.match(s)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


@dataclass
class Listing:
    source: str
    source_name: str
    id: str
    title: str
    url: str
    price: Optional[float]
    currency: str = "CAD"
    in_stock: bool = True
    condition: str = ""
    location: str = ""
    seller: str = ""
    was_price: Optional[float] = None
    sponsored: bool = False
    category: str = ""      # store's own category label, checked by the exclude filter too
    region: str = ""        # CA or US; the checker fills it in

    @property
    def key(self) -> str:
        # the same Amazon ASIN on Amazon.ca and Amazon.com is two listings in two currencies
        return f"{self.source}:{self.region}:{self.id}" if self.region else f"{self.source}:{self.id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


class SourceError(Exception):
    """A source could not be read (blocked, layout changed, network)."""


BLOCK_TITLES = (
    "access denied", "just a moment", "robot check", "something went wrong",
    "are you a human", "security verification", "error page", "attention required",
)
# Some blocks come as a near-empty page with no title at all; then the body text is the tell.
# Amazon's robot check keeps the normal "Amazon.ca" title and says "not a robot" instead.
BLOCK_BODY_RE = re.compile(r"access denied|access to this page has been denied|request blocked|forbidden|"
                           r"verify you are human|are you a robot|not a robot|captcha|unusual traffic|"
                           r"enter the characters you see below|type the characters you see", re.I)


class Source:
    key = ""
    name = ""
    regions = ("CA", "US")
    min_gap = 10    # seconds between two requests to this store; raised for stores that block bursts
    interval = 300  # seconds before one item is checked at this store again

    def __init__(self, settings: dict):
        self.settings = settings

    def display_name(self, region: str) -> str:
        return self.name

    @staticmethod
    def currency(region: str) -> str:
        return "CAD" if region == "CA" else "USD"

    def search(self, page, query: str, region: str) -> list:
        raise NotImplementedError

    def _check_blocked(self, page):
        title = (page.title() or "").strip()
        if any(b in title.lower() for b in BLOCK_TITLES):
            raise SourceError(f"blocked (page title: '{title[:60]}')")
        try:
            body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 2000) : ''")
        except Exception:
            return
        if len(body) < 600 and BLOCK_BODY_RE.search(body):
            raise SourceError(f"blocked ({' '.join(body.split())[:80]})")

    def goto(self, page, url: str, wait_for: str = None, timeout: int = 45000, settle: int = 1500):
        page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        self._check_blocked(page)
        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=15000)
            except Exception:
                pass  # zero results is legitimate; the caller decides
        page.wait_for_timeout(settle)
        self._check_blocked(page)

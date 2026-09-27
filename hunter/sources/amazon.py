import re
import threading
import time
from urllib.parse import quote_plus

from .base import Listing, Source, SourceError, parse_price, shown_currency

# One search-result card -> the new-in-box price, plus the "$X (N used & new offers)" line that
# Amazon prints under a result when other sellers (used, open box, third party) undercut it.
# The count can read "13+", and on the Resale page that line is the only price a card has.
JS = r"""() => [...document.querySelectorAll('div[data-component-type="s-search-result"]')].map(c => {
  const h2 = c.querySelector('h2');
  const a = c.querySelector('a.a-link-normal.s-no-outline') || c.querySelector('h2 a') || c.querySelector('a[href*="/dp/"]');
  const p = c.querySelector('.a-price[data-a-color="base"] .a-offscreen') || c.querySelector('.a-price:not([data-a-strike]) .a-offscreen') || c.querySelector('.a-price .a-offscreen');
  const was = c.querySelector('.a-price[data-a-strike="true"] .a-offscreen') || c.querySelector('.a-text-price .a-offscreen');
  const txt = c.innerText || '';
  const ttl = h2 ? h2.innerText.trim() : '';
  const mo = txt.match(/\$\s?([\d,]+(?:\.\d{2})?)\s*\(\s*(\d+)\+?\s+(used & new|used|new)\s+offers?\s*\)/i);
  return {
    id: c.dataset.asin || '',
    title: ttl,
    url: a ? a.href : '',
    price: p ? p.textContent : '',
    was: was ? was.textContent : '',
    offers: mo ? {price: mo[1], count: mo[2], kind: mo[3].toLowerCase()} : null,
    sponsored: /\bSponsored\b/.test(txt),
    unavailable: /Currently unavailable|Temporarily out of stock/i.test(txt),
    used: /\b(Used|Renewed|Refurbished)\b/i.test(ttl),
  };
})"""
RESULTS = 'div[data-component-type="s-search-result"], :text("No results for"), form[action*="validateCaptcha"]'

# What kind of page did Amazon actually hand back? A robot check has the same "Amazon.ca" title as a
# real page, so the title alone cannot tell them apart.
PAGE_STATE_JS = r"""() => ({
  cards: document.querySelectorAll('div[data-component-type="s-search-result"]').length,
  captcha: !!document.querySelector('form[action*="validateCaptcha"], #captchacharacters'),
  noResults: /No results for|did not match any products/i.test(document.body ? document.body.innerText : ''),
})"""

# The Resale page changes slowly, and fetching it every check doubled Amazon traffic. Reuse it for a while.
RESALE_REFRESH = 20 * 60
_resale_cache = {}            # (site, query) -> (fetched_at, raw rows)
_resale_lock = threading.Lock()


class Amazon(Source):
    key = "amazon"
    name = "Amazon"
    regions = ("CA", "US")
    min_gap = 20
    interval = 900    # Amazon has throttled this connection twice; keep it slow

    def display_name(self, region):
        return "Amazon.ca" if region == "CA" else "Amazon.com"

    @staticmethod
    def _clean(r):
        asin = (r.get("id") or "").strip()
        title = re.sub(r"^\s*Sponsored Ad\s*[-–]\s*", "", r.get("title") or "").strip()
        return asin, title

    def _load(self, page, url):
        """Open an Amazon search page and make sure it really is one. Returns the result cards."""
        self.goto(page, url, wait_for=RESULTS)
        state = page.evaluate(PAGE_STATE_JS)
        if state.get("captcha"):
            raise SourceError("blocked (Amazon is showing a robot check)")
        if not state.get("cards"):
            if state.get("noResults"):
                return []
            raise SourceError("Amazon returned a page with no search results (throttled or still loading)")
        return page.evaluate(JS)

    def search(self, page, query, region):
        tld = "ca" if region == "CA" else "com"
        base = f"https://www.amazon.{tld}"
        name, cur = self.display_name(region), self.currency(region)
        want_used = self.settings.get("include_used", True)
        new = {}    # asin -> Listing; the same product can appear twice (sponsored and organic)
        used = {}   # asin -> the one cheapest non-new offer, so an ASIN gets a single used row

        def note_used(asin, title, price, condition, seller):
            best = used.get(asin)
            if best is None or price < best.price:
                used[asin] = Listing(
                    source=self.key, source_name=name, id=f"{asin}:used", title=title,
                    url=f"{base}/gp/offer-listing/{asin}?condition=used", price=price, currency=cur,
                    in_stock=True, condition=condition, seller=seller,
                )
            elif price == best.price and seller == "Amazon Resale":
                best.condition, best.seller = condition, seller   # the more specific label wins

        # 1. the regular search: new-in-box price, plus the cheapest "used & new" offer per result
        for r in self._load(page, f"{base}/s?k={quote_plus(query)}"):
            asin, title = self._clean(r)
            if not asin or not title:
                continue
            price = parse_price(r.get("price"))
            if shown_currency(r.get("price"), region) != cur:
                price = None     # shown in the other country's currency; see base.shown_currency
            if price is not None and (asin not in new or price < new[asin].price):
                new[asin] = Listing(
                    source=self.key, source_name=name, id=asin, title=title, url=f"{base}/dp/{asin}",
                    price=price, currency=cur, in_stock=not r.get("unavailable"),
                    condition="Used" if r.get("used") else "New",
                    was_price=parse_price(r.get("was")), sponsored=bool(r.get("sponsored")),
                )
            off = r.get("offers")
            if want_used and off and "used" in off.get("kind", ""):
                other = parse_price("$" + off["price"])
                if other is not None and (price is None or other < price):
                    note_used(asin, title, other, f"Used or new, cheapest of {off['count']} offers",
                              "Other sellers")

        # 2. Amazon Resale (the old Warehouse Deals): open-box and used stock sold by Amazon itself
        if want_used:
            for r in self._resale_rows(page, base, query):
                asin, title = self._clean(r)
                if not asin or not title:
                    continue
                off = r.get("offers")
                price = parse_price(r.get("price"))
                if price is None and off:
                    price = parse_price("$" + off["price"])
                if price is None:
                    continue
                count = int(off["count"]) if off and str(off.get("count", "")).isdigit() else 1
                note_used(asin, title, price,
                          "Used, Amazon Resale" + (f", {count} offers" if count > 1 else ""),
                          "Amazon Resale")
        return list(new.values()) + list(used.values())

    def _resale_rows(self, page, base, query):
        """Resale results, fetched at most every RESALE_REFRESH seconds per query."""
        key = (base, query.strip().lower())
        with _resale_lock:
            cached = _resale_cache.get(key)
        if cached and time.time() - cached[0] < RESALE_REFRESH:
            return cached[1]
        page.wait_for_timeout(2500)
        try:
            rows = self._load(page, f"{base}/s?k={quote_plus(query)}&i=warehouse-deals")
        except SourceError as e:
            if "blocked" in str(e).lower():
                raise                      # a robot check means Amazon wants us gone; back off
            return cached[1] if cached else []   # a flaky Resale page should not sink the main search
        with _resale_lock:
            _resale_cache[key] = (time.time(), rows)
        return rows

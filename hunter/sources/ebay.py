import re
from urllib.parse import quote_plus

from .base import Listing, Source, parse_price, shown_currency

PARTS_RE = re.compile(r"parts only|for parts|not working|defective|broken", re.I)

JS = r"""() => [...document.querySelectorAll('li.s-card[data-listingid], li.s-item')].map(c => {
  const a = c.querySelector('a.s-card__link, a.s-item__link') || c.querySelector('a[href*="/itm/"]');
  const t = c.querySelector('.s-card__title, .s-item__title');
  const p = c.querySelector('.s-card__price, .s-item__price');
  const sub = c.querySelector('.s-card__subtitle, .SECONDARY_INFO');
  const rows = [...c.querySelectorAll('.s-card__attribute-row, .s-item__detail')].map(r => r.innerText.trim()).filter(Boolean);
  return {
    id: c.dataset.listingid || '',
    title: t ? t.innerText.replace(/Opens in a new window or tab/g, '').replace(/^New Listing/i, '').trim() : '',
    url: a ? a.href : '',
    price: p ? p.innerText : '',
    condition: sub ? sub.innerText.trim() : '',
    rows,
  };
})"""


class Ebay(Source):
    key = "ebay"
    name = "eBay"
    regions = ("CA", "US")
    min_gap = 20      # serves an error page after bursts
    interval = 240

    def display_name(self, region):
        return "eBay.ca" if region == "CA" else "eBay.com"

    def search(self, page, query, region):
        tld = "ca" if region == "CA" else "com"
        # _sop=15: price + shipping, lowest first. LH_BIN=1: Buy It Now only (auction bids mislead).
        url = f"https://www.ebay.{tld}/sch/i.html?_nkw={quote_plus(query)}&_sop=15&LH_BIN=1"
        self.goto(page, url, wait_for="li.s-card, li.s-item")
        rows = page.evaluate(JS)
        out = []
        for r in rows:
            link = r.get("url") or ""
            m = re.search(r"/itm/(\d{6,})", link)
            item_id = r.get("id") or (m.group(1) if m else "")
            title = (r.get("title") or "").strip()
            if not item_id or item_id == "123456" or "/itm/123456" in link or title.lower() == "shop on ebay":
                continue  # eBay's placeholder cards
            price = parse_price(r.get("price"))
            if shown_currency(r.get("price"), region) != self.currency(region):
                continue     # listed in the other country's currency; see base.shown_currency
            if not title or price is None:
                continue
            if PARTS_RE.search(r.get("condition") or ""):
                continue  # "For parts or not working" is not a purchase option for a build
            ship, loc = "", ""
            for row in r.get("rows") or []:
                low = row.lower()
                if not ship and ("shipping" in low or "delivery" in low or "pickup" in low):
                    ship = row
                if not loc and low.startswith("from "):
                    loc = row[5:].strip()
            out.append(Listing(
                source=self.key, source_name=self.display_name(region), id=item_id, title=title,
                url=f"https://www.ebay.{tld}/itm/{item_id}", price=price, currency=self.currency(region),
                in_stock=True, condition=r.get("condition") or "", location=loc, seller=ship,
            ))
        return out

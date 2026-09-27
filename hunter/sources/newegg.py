import hashlib
import re
from urllib.parse import quote_plus

from .base import Listing, Source, parse_price

JS = r"""() => [...document.querySelectorAll('.item-cell')].map(c => {
  const a = c.querySelector('a.item-title');
  const p = c.querySelector('.price-current');
  const s = p ? p.querySelector('strong') : null;
  const sup = p ? p.querySelector('sup') : null;
  const was = c.querySelector('.price-was');
  const promo = c.querySelector('.item-promo');
  const btn = c.querySelector('.item-button-area button');
  const txt = c.innerText || '';
  const m = txt.match(/Sold(?: and shipped)? by[:\s]+([^\n]+)/i);
  return {
    title: a ? a.innerText.trim() : '',
    url: a ? a.href : '',
    price: s ? (s.textContent.trim() + (sup ? sup.textContent.trim() : '')) : (p ? p.textContent : ''),
    was: was ? was.textContent : '',
    promo: promo ? promo.innerText.trim() : '',
    oos: /OUT OF STOCK|Sold Out/i.test(txt),
    btn: btn ? btn.innerText.trim() : '',
    seller: m ? m[1].trim() : '',
    sponsored: /\bSponsored\b/.test(txt),
  };
})"""


class Newegg(Source):
    key = "newegg"
    name = "Newegg"
    regions = ("CA", "US")
    min_gap = 12
    interval = 180

    def display_name(self, region):
        return "Newegg.ca" if region == "CA" else "Newegg.com"

    def search(self, page, query, region):
        tld = "ca" if region == "CA" else "com"
        url = f"https://www.newegg.{tld}/p/pl?d={quote_plus(query)}"
        self.goto(page, url, wait_for=".item-cell")
        rows = page.evaluate(JS)
        out = []
        for r in rows:
            title, link = (r.get("title") or "").strip(), r.get("url") or ""
            price = parse_price(r.get("price"))
            if not title or not link or price is None:
                continue
            m = re.search(r"/p/([A-Za-z0-9-]{6,})", link) or re.search(r"[?&]Item=([A-Za-z0-9-]{6,})", link)
            # tracking parameters change on every load; hashing them would make a "new" listing each time
            item_id = m.group(1) if m else hashlib.sha1(link.split("?")[0].encode()).hexdigest()[:12]
            out.append(Listing(
                source=self.key, source_name=self.display_name(region), id=item_id, title=title,
                url=link.split("?")[0], price=price, currency=self.currency(region),
                in_stock=not r.get("oos") and not re.search(r"notify", r.get("btn") or "", re.I),
                condition="New", seller=r.get("seller") or "",
                was_price=parse_price(r.get("was")), sponsored=bool(r.get("sponsored")),
            ))
        return out

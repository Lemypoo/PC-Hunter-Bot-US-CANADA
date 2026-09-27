import re
from urllib.parse import quote_plus

from .base import Listing, Source, SourceError, parse_price

JS = r"""() => [...document.querySelectorAll('.product-miniature[data-id-product]')].map(c => {
  const a = c.querySelector('.product-title a') || c.querySelector('h2 a');
  const p = c.querySelector('.price');
  const reg = c.querySelector('.regular-price');
  const av = c.querySelector('.available-tag');
  return {
    id: c.dataset.idProduct || '',
    title: a ? a.innerText.trim() : '',
    url: a ? a.href.split('?')[0] : '',
    price: p ? p.innerText : '',
    was: reg ? reg.innerText : '',
    avail: av ? av.innerText.replace(/\s+/g, ' ').trim() : '',
  };
})"""


CATEGORY_RE = re.compile(r"canadacomputers\.com/(?:en|fr)/([a-z0-9-]+)/\d+/")


def category_from_url(url: str) -> str:
    """The store's category slug sits in every product URL, e.g. /en/gaming-laptops/12345/..."""
    m = CATEGORY_RE.search(url or "")
    return m.group(1).replace("-", " ") if m else ""


class CanadaComputers(Source):
    key = "canadacomputers"
    name = "Canada Computers"
    regions = ("CA",)
    min_gap = 45      # blocked the whole connection after a burst
    interval = 1200

    def search(self, page, query, region):
        url = f"https://www.canadacomputers.com/en/search?s={quote_plus(query)}"
        self.goto(page, url, wait_for=".product-miniature")
        rows = page.evaluate(JS)
        if rows and not any((r.get("avail") or "").strip() for r in rows):
            raise SourceError("availability markup missing on search page (layout change?)")
        out = []
        for r in rows:
            title, link = (r.get("title") or "").strip(), r.get("url") or ""
            price = parse_price(r.get("price"))
            if not r.get("id") or not title or not link or price is None:
                continue
            avail = (r.get("avail") or "").lower()
            in_stock = bool(re.search(r"(?<!not )(?<!sold out )available", avail))
            category = category_from_url(link)
            out.append(Listing(
                source=self.key, source_name=self.name, id=str(r["id"]), title=title, url=link,
                price=price, currency="CAD", in_stock=in_stock,
                condition="Open Box" if "open box" in title.lower() else "New",
                seller=r.get("avail") or "", was_price=parse_price(r.get("was")), category=category,
            ))
        return out

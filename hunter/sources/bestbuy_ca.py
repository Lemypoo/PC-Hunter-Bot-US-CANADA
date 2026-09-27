import json
from urllib.parse import quote_plus

from .base import Listing, Source, SourceError

SEARCH_URL = "https://www.bestbuy.ca/api/v2/json/search?query={q}&lang=en-CA&pageSize=48&page=1"
# The availability endpoint insists on a full postal code and a store list. The shipping status it
# returns is national, so any valid store IDs will do; these are Toronto stores.
AVAIL_URL = ("https://www.bestbuy.ca/ecomm-api/availability/products?accept=application%2Fvnd.bestbuy.standardproduct.v1%2Bjson"
             "&accept-language=en-CA&locations=956%7C237%7C200%7C198%7C965&postalCode=M5G2C3&skus=")
FETCH_JS = """async (u) => {
  const r = await fetch(u, {headers: {'accept': 'application/vnd.bestbuy.standardproduct.v1+json', 'accept-language': 'en-CA'}});
  return {status: r.status, text: await r.text()};
}"""
IN_STOCK_STATUSES = {"InStock", "InStockOnlineOnly"}


class BestBuyCA(Source):
    key = "bestbuy_ca"
    name = "Best Buy Canada"
    regions = ("CA",)
    min_gap = 5
    interval = 90     # a plain JSON API, the cheapest store to ask

    def search(self, page, query, region):
        page.goto(SEARCH_URL.format(q=quote_plus(query)), timeout=45000, wait_until="domcontentloaded")
        body = page.evaluate("() => document.body ? document.body.innerText : ''")
        try:
            data = json.loads(body)
        except ValueError:
            self._check_blocked(page)
            raise SourceError("search API did not return JSON (blocked?)")
        found = []
        for p in data.get("products") or []:
            sku = str(p.get("sku") or "")
            name = (p.get("name") or "").strip()
            sale, reg = p.get("salePrice"), p.get("regularPrice")
            price = sale if sale not in (None, 0) else reg
            if not sku or not name or not price:
                continue
            seller = (p.get("seller") or {}).get("name") or ("Marketplace seller" if p.get("isMarketplace") else "Best Buy")
            found.append((sku, name, float(price), reg, sale, seller, p.get("productUrl"), p.get("categoryName") or ""))
        if not found:
            return []
        stock = self._availability(page, [f[0] for f in found])
        out = []
        for sku, name, price, reg, sale, seller, purl, category in found:
            out.append(Listing(
                source=self.key, source_name=self.name, id=sku, title=name,
                url="https://www.bestbuy.ca" + (purl or f"/en-ca/product/{sku}"),
                price=price, currency="CAD", in_stock=stock.get(sku, False),
                condition="Open Box" if "open box" in name.lower() else "New", seller=seller,
                was_price=float(reg) if reg and sale and reg > sale else None, category=category,
            ))
        return out

    @staticmethod
    def _availability(page, skus):
        """sku -> in stock for shipping, from Best Buy's availability API (same-origin fetch)."""
        result = {}
        for i in range(0, len(skus), 40):
            chunk = skus[i:i + 40]
            res = page.evaluate(FETCH_JS, AVAIL_URL + "%7C".join(chunk))
            if res.get("status") != 200:
                raise SourceError(f"availability API returned HTTP {res.get('status')}")
            try:
                data = json.loads((res.get("text") or "").lstrip("﻿"))
            except ValueError:
                raise SourceError("availability API did not return JSON")
            for a in data.get("availabilities") or []:
                sh = a.get("shipping") or {}
                qty = sh.get("quantityRemaining")
                result[str(a.get("sku"))] = (sh.get("status") in IN_STOCK_STATUSES
                                             and bool(sh.get("purchasable", True))
                                             and (qty is None or qty > 0))
        return result

"""Item Hunter's test suite. Run it with:  python tests/test_hunter.py

No test touches the network: the stores and the browser are faked. Every check is one line of
plain English, so a failure says what behaviour broke rather than which assertion tripped.
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hunter import notify
import hunter.scheduler as schedmod
import hunter.sources.amazon as amazon_mod
import hunter.store as storemod
from hunter.matching import matches, tokens_from_query
from hunter.scheduler import Scheduler
from hunter.sources.amazon import Amazon
from hunter.sources.base import BLOCK_BODY_RE, Listing, SourceError
from hunter.sources.facebook import Facebook
from hunter.store import Store, suggest_excludes

notify.toast = lambda *a, **k: None
sent = []
ok = True
paths = []


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def fresh(name):
    p = os.path.join(tempfile.gettempdir(), f"ihtest_{name}.json")
    for f in (p, p + ".tmp", p + ".bak"):
        if os.path.exists(f):
            os.remove(f)
    paths.append(p)
    return p


def L(src, i, price, stock=True, title="AMD Ryzen 9 9950X3D CPU", **kw):
    return Listing(source=src, source_name=src, id=i, title=title, url="u", price=price,
                   in_stock=stock, **kw)


print("\n--- matching ---")
cpu = {"must": tokens_from_query("9950X3D"), "exclude": suggest_excludes("9950X3D")}
gpu = {"must": tokens_from_query("rtx 5090"), "exclude": suggest_excludes("rtx 5090")}
for it, title, price, extra, exp in [
    (cpu, "AMD Ryzen 9 9950X3D 16-Core/32Thread 4nm ZEN 5 CPU", 929.98, "", True),
    (cpu, "AMD Ryzen 9 9950X3D CPU + ASUS ROG STRIX X870E-H Motherboard", 2449.98, "", False),
    (cpu, "Mother Board Fit for JGINYUE B650M supports 9950X3D", 450.92, "", False),
    (cpu, "AMD Ryzen 9 9950X3D2 Dual Edition", 1229.98, "", False),
    (cpu, "AMD Ryzen 9 9950 X3D Processor", 900, "", True),
    (cpu, "Refurbished Dell Alienware Desktop 9950X3D", 3000, "Everyday Computing", False),
    (gpu, "ISO Nvidia RTX 5090", 123, "", False),
    (gpu, "(No GPU) MSI GeForce RTX 5090 Gaming Trio shroud", 240, "", False),
    (gpu, "ROG Strix SCAR 18 RTX 5090", 5999, "Gaming Laptops", False),
    (gpu, "Gigabyte Rtx 5090 Oc", 2700, "Parts Only", False),
    (gpu, "ASUS ROG Astral GeForce RTX 5090 OC", 3999, "Graphics Cards Brand New", True),
    (gpu, "NVIDIA GeForce RTX5090 32GB", 3999, "", True),        # written without the space
    (gpu, "NVIDIA GeForce RTX5080 16GB", 1999, "", False),
    (gpu, "PC i9 14900k/Asus Z790 Formula/2Tb NVMe G5 /48 RAM/1200W/ready for RTX 5090", 2598, "", False),
    (gpu, "Corsair HX1500i 1500W PSU ready for RTX 5090", 300, "", False),
    (gpu, "MSI GeForce RTX 5090 32G VENTUS 3X OC Graphics Card", 2699, "", True),
    (gpu, "HAVN CS9463 GPU Vertical Standing Kit White Sumitomo Electric RTX5090", 124, "", False),
    (gpu, "darkFlash FLOATRON F1 Micro-ATX PC case, RTX 5090 up to 425mm, 360mm radiator support", 404, "", False),
    (gpu, "Gigabyte AORUS GeForce RTX 5090 STEALTH ICE 32G Graphics Card - 32GB GDDR7, 512bit", 3799, "", True),
    (gpu, "ZOTAC GAMING GeForce RTX 5090 AMP Extreme INFINITY 32GB GDDR7", 3999, "Standard shipping", True),
]:
    check(f"{title[:46]!r:<48} -> {exp}", matches(it, title, price, extra)[0] == exp)


print("\n--- Amazon: which page came back ---")
class AmazonPage:
    def __init__(self, pages):
        self.pages, self.cur, self.urls = list(pages), None, []

    def goto(self, url, **k):
        self.urls.append(url)
        self.cur = self.pages.pop(0)

    def title(self):
        return "Amazon.ca"

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, js, arg=None):
        state, cards = self.cur
        if "captcha:" in js:
            return state
        if "document.body" in js:
            return "Amazon.ca results"
        return cards


def card(asin, price, sponsored=False, offers=None, title="AMD Ryzen 9 9950X3D 16-Core Processor"):
    return {"id": asin, "title": title, "url": "", "price": price, "was": "", "offers": offers,
            "sponsored": sponsored, "unavailable": False, "used": False}


OKST = lambda n: {"cards": n, "captcha": False, "noResults": False}
amazon_mod._resale_cache.clear()

try:
    Amazon({"include_used": False}).search(AmazonPage([({"cards": 0, "captcha": True, "noResults": False}, [])]), "q", "CA")
    check("a robot check is reported as a block", False)
except SourceError as e:
    check("a robot check is reported as a block", "blocked" in str(e).lower())
try:
    Amazon({"include_used": False}).search(AmazonPage([({"cards": 0, "captcha": False, "noResults": False}, [])]), "q", "CA")
    check("a page with no result cards is an error, not an empty market", False)
except SourceError as e:
    check("a page with no result cards is an error, not an empty market", "no search results" in str(e))
check("a real 'No results for' page is quietly empty",
      Amazon({"include_used": False}).search(AmazonPage([({"cards": 0, "captcha": False, "noResults": True}, [])]), "zz", "CA") == [])
out = Amazon({"include_used": False}).search(
    AmazonPage([(OKST(2), [card("B01", "$949.99", sponsored=True), card("B01", "$929.98")])]), "q", "CA")
check("one ASIN listed twice on a page becomes one row at the cheaper price",
      len(out) == 1 and out[0].price == 929.98)
check("Amazon's robot-check wording is recognised",
      bool(BLOCK_BODY_RE.search("Enter the characters you see below. Sorry, we just need to make sure you're not a robot.")))

amazon_mod._resale_cache.clear()
p1 = AmazonPage([(OKST(1), [card("B01", "$929.98")]),
                 (OKST(1), [card("B02", "$700.00", offers={"price": "700.00", "count": "1", "kind": "used"})])])
Amazon({"include_used": True}).search(p1, "9950x3d", "CA")
p2 = AmazonPage([(OKST(1), [card("B01", "$929.98")])])
out2 = Amazon({"include_used": True}).search(p2, "9950X3D", "CA")
check("the Resale page is fetched once then reused, halving Amazon traffic",
      len(p1.urls) == 2 and len(p2.urls) == 1 and any(l.id == "B02:used" for l in out2))
amazon_mod._resale_cache.clear()
out = Amazon({"include_used": True}).search(
    AmazonPage([(OKST(1), [card("B01", "$929.98")]), ({"cards": 0, "captcha": False, "noResults": False}, [])]), "x1", "CA")
check("a half-loaded Resale page keeps the main results", [l.id for l in out] == ["B01"])
amazon_mod._resale_cache.clear()
try:
    Amazon({"include_used": True}).search(
        AmazonPage([(OKST(1), [card("B01", "$929.98")]), ({"cards": 0, "captcha": True, "noResults": False}, [])]), "x2", "CA")
    check("a robot check on the Resale page still backs Amazon off", False)
except SourceError as e:
    check("a robot check on the Resale page still backs Amazon off", "blocked" in str(e).lower())


print("\n--- other stores ---")
class FBPage:
    url = "https://www.facebook.com/marketplace/vancouver/search/?query=x"

    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, js, arg=None):
        return [{"href": "/marketplace/item/111/", "spans": ["CA$550", "Just listed", "Ryzen 7 9800X3D", "Nanaimo, BC"]}]


fb = {l.id: l for l in Facebook({"fb_city": "vancouver"}).search(FBPage(), "9800x3d", "CA")}
check("Facebook's 'Just listed' badge is not mistaken for the title", fb["111"].title == "Ryzen 7 9800X3D")


class FBSearch:
    """Facebook as it behaved in September 2026: nothing for "9950X3D", listings for "cpu 9950X3D"."""
    def __init__(self):
        self.urls, self.url = [], ""

    def goto(self, url, **k):
        self.urls.append(url)
        self.url = url

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, js, arg=None):
        if "map" not in js:
            return 'No listings found for "9950X3D" within 65 kilometres. Try a new search.'
        if "query=cpu+9950X3D" in self.url:
            return [{"href": "/marketplace/item/333/", "spans": ["CA$660", "AMD Ryzen 9 9950X3D 16-Core Processor", "Richmond, BC"]}]
        return []


fbp = FBSearch()
got = Facebook({"fb_city": "vancouver"}).search(fbp, "9950X3D", "CA")
check("Facebook's empty answer for a bare model number is retried as 'cpu 9950X3D', which finds it",
      [l.price for l in got] == [660] and len(fbp.urls) == 2 and "query=cpu+9950X3D" in fbp.urls[1])
fbp2 = FBSearch()
Facebook({"fb_city": "vancouver"}).search(fbp2, "9950X3D", "CA")
check("...and the wording that works is used straight away next time", fbp2.urls == [fbp.urls[1]])
fbp3 = FBSearch()
check("a search that is not a PC part is not retried",
      Facebook({"fb_city": "vancouver"}).search(fbp3, "desk lamp", "CA") == [] and len(fbp3.urls) == 1)
src = open(os.path.join(os.path.dirname(__file__), "..", "hunter", "sources", "newegg.py"), encoding="utf-8").read()
check("Newegg's fallback id ignores tracking parameters",
      'link.split("?")[0].encode()' in src
      and hashlib.sha1(b"https://n.ca/p") .hexdigest()[:12] == hashlib.sha1(b"https://n.ca/p").hexdigest()[:12])


print("\n--- ingest: events, alerts, churn ---")
notify.toast = lambda t, m, u=None, wait=False: sent.append(t)
store = Store(fresh("ingest"))
sc = Scheduler(store)
item = store.add_item("9950X3D")


class AZ:
    key = "amazon"


class EB:
    key = "ebay"


S = {"notify": True}
sc.ingest(item, AZ, [L("amazon", "A", 929.98), L("amazon", "B", 999.0, False)], S)
check("the first read of a store is a baseline: no feed entries, no alerts", store.events == [] and sent == [])
sc.ingest(item, AZ, [L("amazon", "A", 899.0), L("amazon", "B", 999.0, False)], S)
check("a price drop raises one feed entry and one alert",
      [e["type"] for e in store.events] == ["drop"] and len(sent) == 1)
sc.ingest(item, AZ, [L("amazon", "A", 899.0), L("amazon", "B", 1100.0, True)], S)
check("a restock far above the best price stays out of the feed", len(store.events) == 1)
recs = store.listings[item["id"]]
sc.ingest(item, EB, [L("ebay", "E", 10.0), L("ebay", "F", 850.0)], S)
check("a placeholder price is flagged and the real one is not",
      recs["ebay:CA:E"]["suspect"] and not recs["ebay:CA:F"]["suspect"])
check("the best price ignores the placeholder", sc._best_price(recs, set()) == 850.0)
for _ in range(5):
    sc.ingest(item, AZ, [], S)
check("a store answering with nothing keeps the listings it had",
      recs["amazon:CA:A"]["active"] and recs["amazon:CA:A"]["misses"] == 0)
recs["amazon:CA:A"]["last_seen"] -= 7 * 3600
sc.ingest(item, AZ, [], S)
check("...but a listing unseen for six hours expires", not recs["amazon:CA:A"]["active"])
recs["amazon:CA:A"].update(active=True, last_seen=time.time(), misses=0)
for _ in range(5):
    sc.ingest(item, AZ, [L("amazon", "B", 1100.0)], S)
check("a listing missing for a few minutes is not declared gone (result-order churn)", recs["amazon:CA:A"]["active"])
recs["amazon:CA:A"]["last_seen"] -= 3600
sc.ingest(item, AZ, [L("amazon", "B", 1100.0)], S)
check("...it is declared gone after half an hour", not recs["amazon:CA:A"]["active"])
store.clear_events()
sc.ingest(item, AZ, [L("amazon", "A", 700.0), L("amazon", "A", 750.0)], S)
check("the same listing twice in one read gives one price, not a drop and a rise",
      recs["amazon:CA:A"]["price"] == 700.0 and len([e for e in store.events if e["key"] == "amazon:CA:A"]) == 1)
sent.clear()
store.clear_events()
sc.ingest(item, EB, [L("ebay", f"N{i}", 600 + i) for i in range(8)], S)
check("eight new cheap listings give eight feed entries but at most three alerts",
      sum(e["type"] == "new" for e in store.events) == 8 and len(sent) == 3)

st_r = Store(fresh("restock"))
sc_r = Scheduler(st_r)
it_r = st_r.add_item("9950X3D")
sc_r.ingest(it_r, AZ, [L("amazon", "A", 929.98, False)], {"notify": False})
sc_r.ingest(it_r, AZ, [L("amazon", "A", 929.98, True)], {"notify": False})
check("a listing coming back in stock raises a restock entry", [e["type"] for e in st_r.events] == ["restock"])

st_f = Store(fresh("floor"))
sc_f = Scheduler(st_f)
it_f = st_f.add_item("9950X3D")
prices = [929.98, 999.99, 1890, 2406, 2682, 2748, 2838, 4390, 4432, 4479]
sc_f.ingest(it_f, AZ, [L("amazon", f"A{i}", p) for i, p in enumerate(prices)], {"notify": False})
sc_f.ingest(it_f, EB, [L("ebay", "E1", 800.0), L("ebay", "E2", 10.0)], {"notify": False})
rf = st_f.listings[it_f["id"]]
check("a real bargain survives a listing page full of overpriced resellers", not rf["ebay:CA:E1"]["suspect"])
check("a one-dollar placeholder does not", rf["ebay:CA:E2"]["suspect"])



print("\n--- Canada and the US ---")
st_m = Store(fresh("move"))
st_m.update_settings({"region": "CA"})
sc_m = Scheduler(st_m)
it_m = st_m.add_item("9950X3D")
CAN = {"notify": True, "region": "CA"}
USA = {"notify": True, "region": "US"}
sc_m.ingest(it_m, AZ, [L("amazon", "A", 929.98)], CAN)
sc_m.ingest(it_m, AZ, [L("amazon", "A", 899.00)], CAN)
check("the Facebook city starts as Canada's default", st_m.settings["fb_city"] == "vancouver")
saved = st_m.update_settings({"region": "US"})
check("switching to the US brings the US default Facebook city along", saved["fb_city"] == "nyc")
st_m.clear_events()
sent.clear()
sc_m.ingest(it_m, AZ, [L("amazon", "A", 649.0, currency="USD"), L("amazon", "B", 699.0, currency="USD")], USA)
check("the first Amazon.com read after switching is a baseline, not a flood of alerts", st_m.events == [] and sent == [])
recs_m = st_m.listings[it_m["id"]]
check("the Canadian and US copies of one product are kept apart, each in its own currency",
      recs_m["amazon:CA:A"]["currency"] == "CAD" and recs_m["amazon:US:A"]["currency"] == "USD")
import app as appmod
appmod.store, appmod.sched = st_m, sc_m
card_m = next(i for i in appmod.build_state()["items"] if i["id"] == it_m["id"])
check("the page shows only the current country's listings", {r["region"] for r in card_m["listings"]} == {"US"})
st_m.update_settings({"fb_city": "chicago"})
check("a city the user typed stays when the country changes", st_m.update_settings({"region": "CA"})["fb_city"] == "chicago")

real_detect = storemod.detect_country
storemod.detect_country = lambda: "US"
st_us = Store(fresh("pc_in_us"))
check("the first start on a computer set to the US picks the US and New York",
      st_us.settings["region"] == "US" and st_us.settings["fb_city"] == "nyc")
storemod.detect_country = lambda: "CA"
check("...and on one set to Canada, Canada", Store(fresh("pc_in_ca")).settings["region"] == "CA")
appmod.detect_country = lambda: "DE"
appmod.store = Store(fresh("pc_in_de"))
check("a computer set to another country is pointed to the international edition",
      (appmod.build_state()["other_edition"] or {}).get("url") == "https://github.com/Lemypoo/PC-Hunter-Bot-International")
appmod.store.update_settings({"region": "CA"})
check("...until a country is picked in Settings", appmod.build_state()["other_edition"] is None)
appmod.detect_country = real_detect
storemod.detect_country = real_detect
appmod.store = st_m

legacy_path = fresh("legacy")
legacy = {
    "settings": {"region": "CA", "fb_city": "vancouver", "push": {"enabled": True, "topic": "t", "server": "https://ntfy.sh"}},
    "items": [{"id": "i1", "query": "9950X3D", "name": "9950X3D", "must": ["9950x3d"], "exclude": [],
               "hidden": ["amazon:B0H"], "seen_sources": {"amazon": True}, "sources": None, "paused": False}],
    "listings": {"i1": {
        "amazon:B01": {"key": "amazon:B01", "source": "amazon", "source_name": "Amazon.ca", "id": "B01",
                       "title": "AMD Ryzen 9 9950X3D", "url": "u", "price": 929.98, "currency": "CAD",
                       "in_stock": True, "active": True, "last_seen": time.time(), "history": [], "lowest": 929.98},
        "amazon:B0H": {"key": "amazon:B0H", "source": "amazon", "source_name": "Amazon.ca", "id": "B0H",
                       "title": "AMD Ryzen 9 9950X3D", "url": "u", "price": 950.0, "currency": "CAD",
                       "in_stock": True, "active": True, "last_seen": time.time(), "history": [], "lowest": 950.0}}},
    "events": [{"key": "amazon:B01", "item_id": "i1", "type": "drop", "price": 929.98}],
}
json.dump(legacy, open(legacy_path, "w", encoding="utf-8"))
moved = Store(legacy_path)
lr = moved.listings["i1"]
check("a state file from before this change is filed under its country, with the new keys",
      set(lr) == {"amazon:CA:B01", "amazon:CA:B0H"})
check("hidden listings stay hidden, feed entries still point at them, and settings are untouched",
      moved.items[0]["hidden"] == ["amazon:CA:B0H"] and moved.events[0]["key"] == "amazon:CA:B01"
      and moved.settings["push"]["topic"] == "t" and moved.settings["fb_city"] == "vancouver")
sc_l = Scheduler(moved)
sent.clear()
moved.clear_events()
sc_l.ingest(moved.items[0], AZ, [L("amazon", "B01", 929.98), L("amazon", "B0H", 950.0)], CAN)
check("the first check after the update raises no 'new listing' alerts", moved.events == [] and sent == [])


class RowsPage:
    def __init__(self, rows):
        self.rows = rows

    def goto(self, *a, **k):
        pass

    def title(self):
        return "eBay"

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, js, arg=None):
        return "x" * 700 if "map" not in js else self.rows


from hunter.sources.ebay import Ebay
rows = [{"id": "1", "title": "MSI RTX 5090 Ventus", "url": "", "price": "C $3,411.37", "condition": "New", "rows": []},
        {"id": "2", "title": "ASUS RTX 5090 TUF", "url": "", "price": "US $2,229.98", "condition": "New", "rows": []}]
got = Ebay({}).search(RowsPage(rows), "rtx 5090", "CA")
check("eBay.ca: a listing shown in US dollars is skipped instead of being read as Canadian dollars",
      [(l.id, l.price, l.currency) for l in got] == [("1", 3411.37, "CAD")])
cards = [card("B01", "CAD 16,358.58"), card("B02", "$1,999.99")]
got = Amazon({"include_used": False}).search(AmazonPage([(OKST(2), cards)]), "rtx 5090", "US")
check("Amazon.com in US mode skips prices it shows in Canadian dollars and keeps the US ones",
      [(l.id, l.price, l.currency) for l in got] == [("B02", 1999.99, "USD")])


class WrongCity(FBPage):
    url = "https://www.facebook.com/marketplace/category/search/?query=x"


try:
    Facebook({"fb_city": "notarealcity"}).search(WrongCity(), "rtx 5090", "US")
    check("a city Facebook does not know is reported, not silently swapped for another", False)
except SourceError as e:
    check("a city Facebook does not know is reported, not silently swapped for another",
          "does not recognise" in str(e) and "blocked" not in str(e))


print("\n--- the store file ---")
st = Store(fresh("store"))
st.add_item("rtx 4090")
for q in ("RTX 4090", "  rtx   4090 ", "Rtx-4090"):
    try:
        st.add_item(q)
        check(f"a duplicate of {q!r} is refused", False)
    except ValueError as e:
        check(f"a duplicate of {q!r} is refused", "Already hunting" in str(e))
other = st.add_item("rtx 5090")
try:
    st.update_item(other["id"], {"query": "RTX 4090", "name": "renamed"})
    check("editing a card into a duplicate is refused", False)
except ValueError:
    check("editing a card into a duplicate is refused, leaving the card untouched",
          st.get_item(other["id"])["name"] == "rtx 5090")
check("the checking speed only accepts known values",
      st.update_settings({"speed": "fast"})["speed"] == "fast"
      and st.update_settings({"speed": "ludicrous"})["speed"] == "fast")
check("the number of stores checked at once is held between 1 and 4",
      st.update_settings({"workers": 9})["workers"] == 4 and st.update_settings({"workers": 0})["workers"] == 1)

itp = st.add_item("9800X3D")
recs = st.listings[itp["id"]]
old = time.time() - 20 * 86400
recs["x:old"] = {"key": "x:old", "source": "amazon", "active": False, "last_seen": old, "price": 1}
recs["x:gone_recently"] = {"key": "x:gone_recently", "source": "amazon", "active": False, "last_seen": time.time(), "price": 1}
recs["x:live"] = {"key": "x:live", "source": "amazon", "active": True, "last_seen": old, "price": 1}
st.get_item(itp["id"])["hidden"] = ["x:old", "x:live"]
n = st.prune()
check("listings gone a fortnight are forgotten, recent and live ones kept",
      n == 1 and "x:old" not in recs and "x:gone_recently" in recs and "x:live" in recs)
check("a hide-mark for a forgotten listing is cleaned up", st.get_item(itp["id"])["hidden"] == ["x:live"])

st.save()
check("saving keeps the previous version as a backup", os.path.exists(st.path + ".bak"))
open(st.path, "wb").write(b"\x00" * 4000)          # exactly how the power cut corrupted it
recovered = Store(st.path)
check("a state file destroyed by a power cut is recovered from the backup",
      [i["name"] for i in recovered.items] == [i["name"] for i in st.items])
check("...and the wrecked file is kept aside rather than deleted",
      any(f.startswith(os.path.basename(st.path) + ".corrupt-") for f in os.listdir(os.path.dirname(st.path))))
src = open(os.path.join(os.path.dirname(__file__), "..", "hunter", "store.py"), encoding="utf-8").read()
check("the state file is flushed to disk before it replaces the old one", "os.fsync(f.fileno())" in src)

real_replace = os.replace
tries = {"n": 0}


def locked_twice(a, b):
    tries["n"] += 1
    if tries["n"] <= 2:
        raise PermissionError(13, "file in use")
    return real_replace(a, b)


storemod.os.replace = locked_twice
real_sleep, storemod.time.sleep = time.sleep, lambda s: None
st.save()
storemod.os.replace, storemod.time.sleep = real_replace, real_sleep
check("saving retries when Windows briefly locks the file", tries["n"] >= 3)


print("\n--- checking: back-off and honesty ---")
class FakePage:
    def close(self):
        pass


class FakeBrowser:
    def __init__(self, channel="msedge", region="CA", state_file=""):
        self.channel, self.region = channel, region
        self._ctx = object()
        self.restarts = 0

    @staticmethod
    def state_file_for(name):
        return ""

    def start(self):
        self._ctx = object()

    def page(self):
        return FakePage()

    def restart(self):
        self.restarts += 1

    def stop(self):
        pass


def source(key, behaviour, gap=0, every=60):
    class Src:
        min_gap = gap
        interval = every

        def __init__(self, settings):
            pass

        def display_name(self, r):
            return key

        def search(self, page, q, r):
            return behaviour(q)
    Src.key = key
    return Src


def boom(q):
    raise TimeoutError("Page.goto: Timeout 45000ms exceeded")


real_uniform = schedmod.random.uniform
schedmod.random.uniform = lambda a, b: 0    # skip run_cycle's polite pause between stores
st2 = Store(fresh("backoff"))
sc2 = Scheduler(st2)
sc2.browser = FakeBrowser()
it2 = st2.add_item("9950X3D")
hits = []
schedmod.sources_for = lambda s: [source("amazon", lambda q: hits.append(q) or boom(q))(s)]
sc2.run_cycle(None)
check("one failure alone does not back a store off", "amazon" not in sc2._backoff and len(hits) == 1)
sc2.run_cycle(None)
check("a second failure in a row does (the old code retried forever)", sc2._backoff.get("amazon", 0) > time.time() + 100)
sc2.run_cycle(None)
check("a backed-off store is not contacted", len(hits) == 2)
check("the error says when the store will be tried again",
      "Trying this store again at" in sc2.status_snapshot()["errors"]["amazon"]["msg"])
check("a card is not marked checked when every store failed", st2.get_item(it2["id"])["last_checked"] is None)
sc2._backoff.clear()
schedmod.sources_for = lambda s: [source("amazon", lambda q: [])(s)]
sc2.run_cycle(None)
check("a success clears the failure count, the pause and the error",
      "amazon" not in sc2._fails and "amazon" not in sc2._backoff and "amazon" not in sc2.status_snapshot()["errors"])
check("...and marks the card checked", st2.get_item(it2["id"])["last_checked"] is not None)
sc2._set_error("ebay", "blocked", "x")
sc2.run_cycle(None)
check("an error from a store no longer switched on disappears", "ebay" not in sc2.status_snapshot()["errors"])


print("\n--- checking: several stores at once ---")
st3 = Store(fresh("parallel"))
for q in ("RTX 5090", "9950X3D", "RTX 5080"):
    st3.add_item(q)
sc3 = Scheduler(st3)
spans = []
spans_lock = threading.Lock()


def timed(key):
    def run(q):
        start = time.time()
        time.sleep(0.25)          # a store takes a moment to answer
        with spans_lock:
            spans.append((key, q, start, time.time()))
        return []
    return run


schedmod.random.uniform = real_uniform     # the cadence jitter needs to be real again
schedmod.Browser = FakeBrowser
schedmod.sources_for = lambda s: [source("bestbuy_ca", timed("bestbuy_ca"), gap=0, every=1)(s),
                                  source("amazon", timed("amazon"), gap=0, every=30)(s)]
st3.update_settings({"workers": 2, "speed": "normal"})
sc3.start()
time.sleep(4)
sc3.stop()
with spans_lock:
    runs = list(spans)
by_store = {}
for key, q, a, b in runs:
    by_store.setdefault(key, []).append((a, b))
overlap_same = any(a1 < b2 and a2 < b1 for key, times in by_store.items()
                   for i, (a1, b1) in enumerate(times) for (a2, b2) in times[i + 1:])
amz = by_store.get("amazon", [])
bby = by_store.get("bestbuy_ca", [])
overlap_diff = any(a1 < b2 and a2 < b1 for (a1, b1) in amz for (a2, b2) in bby)
check("both stores were checked", len(amz) >= 1 and len(bby) >= 3)
check("two stores really are checked at the same time", overlap_diff)
check("but one store is never asked twice at the same moment", not overlap_same)
fast_per_item = {}
for key, q, a, b in runs:
    if key == "bestbuy_ca":
        fast_per_item.setdefault(q, []).append(a)
check("the quick store swept every item in four seconds", len(fast_per_item) == 3)
check("the slow store did not run away with itself", len(amz) <= 3)
gaps = [round(t2 - t1, 2) for times in fast_per_item.values() for t1, t2 in zip(sorted(times), sorted(times)[1:])]
check("an item is not re-checked at one store faster than that store's own clock",
      all(g >= 0.85 for g in gaps) if gaps else True)

st3.update_settings({"speed": "relaxed"})
check("the speed setting scales a store's clock",
      abs(schedmod.SPEED["relaxed"] - 2.0) < 1e-9 and schedmod.SPEED["fast"] < schedmod.SPEED["normal"])

sc4 = Scheduler(st3)
sc4._due[(st3.items[0]["id"], "amazon")] = time.time() + 9999
sc4.request(st3.items[0]["id"])
check("pressing Check now makes an item due immediately",
      sc4._due[(st3.items[0]["id"], "amazon")] == 0.0)
sc4._due[("ghost-item", "amazon")] = 0.0
schedmod.sources_for = lambda s: [source("amazon", lambda q: [])(s)]
sc4._claim()
check("jobs for a removed item are forgotten", ("ghost-item", "amazon") not in sc4._due)
st3.items[0]["paused"] = True
picked = [sc4._claim() for _ in range(3)]
check("a paused item is never picked up", all(j is None or j[0]["id"] != st3.items[0]["id"] for j in picked))


print("\n--- the web app ---")
st5 = Store(fresh("web"))
sc5 = Scheduler(st5)
appmod.store, appmod.sched = st5, sc5
st5.add_item("RTX 5090")
stop = threading.Event()


def churn():
    i = 0
    while not stop.is_set():
        i += 1
        sc5._set_error(f"k{i % 40}", "x" * 40, "RTX 5090")
        sc5._clear_error(f"k{(i + 20) % 40}")
        with sc5._lock:
            sc5._due[(f"i{i % 30}", "amazon")] = time.time()
            sc5._busy[f"w{i % 3}"] = "checking"
            sc5._busy.pop(f"w{(i + 1) % 3}", None)


schedmod.print = lambda *a, **k: None
t = threading.Thread(target=churn, daemon=True)
t.start()
client = appmod.app.test_client()
bad = sum(1 for _ in range(400) if client.get("/api/state").status_code != 200)
stop.set()
t.join()
check("the page's state request never fails while the checkers write status", bad == 0)
r = client.post("/api/items", json={"query": "rtx 5090"})
check("adding a duplicate answers 409 with a readable message",
      r.status_code == 409 and "Already hunting" in r.get_json()["error"])
first = client.post("/api/items", json={"query": "9950X3D"}).get_json()
check("editing into a duplicate answers 409, not a server error",
      client.patch(f"/api/items/{first['id']}", json={"query": "RTX 5090"}).status_code == 409)
state = client.get("/api/state").get_json()
check("the page is told how often each store is checked",
      state["cadence"]["bestbuy_ca"] < state["cadence"]["amazon"])
for it in st5.items:
    sc5._due[(it["id"], "amazon")] = time.time() + 900
client.patch("/api/settings", json={"speed": "fast"})
check("an ordinary settings change leaves the stores' clocks alone",
      all(v > time.time() for (i, k), v in sc5._due.items() if k == "amazon" and not i.startswith("i")))
client.patch("/api/settings", json={"region": "US"})
check("switching country makes every store due at once, instead of after the old country's clocks",
      all(v == 0.0 for (i, k), v in sc5._due.items() if k == "amazon" and not i.startswith("i")))

for p in paths:
    for f in (p, p + ".tmp", p + ".bak"):
        if os.path.exists(f):
            os.remove(f)
for f in os.listdir(tempfile.gettempdir()):
    if f.startswith("ihtest_") and ".corrupt-" in f:
        os.remove(os.path.join(tempfile.gettempdir(), f))
print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)

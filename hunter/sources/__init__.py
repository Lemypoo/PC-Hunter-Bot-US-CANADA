from .amazon import Amazon
from .bestbuy_ca import BestBuyCA
from .canadacomputers import CanadaComputers
from .ebay import Ebay
from .facebook import Facebook
from .newegg import Newegg

ALL = [Amazon, Newegg, BestBuyCA, CanadaComputers, Ebay, Facebook]
REGISTRY = {s.key: s for s in ALL}


def describe(settings=None):
    """Static description of every source for the UI."""
    region = (settings or {}).get("region", "CA")
    return [{"key": s.key, "name": s(settings or {}).display_name(region), "regions": list(s.regions)}
            for s in ALL]


def sources_for(settings):
    region = settings.get("region", "CA")
    enabled = settings.get("sources") or {}
    return [S(settings) for S in ALL if region in S.regions and enabled.get(S.key, True)]


def active_keys(settings, item=None) -> set:
    """Store keys that count for an item right now: switched on, in this region, and on the
    item's own list if it has one. Listings from any other store are kept but not shown."""
    keys = {s.key for s in sources_for(settings)}
    if item and item.get("sources"):
        keys &= set(item["sources"])
    return keys


def is_used_offer(rec) -> bool:
    """A row that stands for the used and open-box offers on a product rather than new stock."""
    return str(rec.get("id") or "").endswith(":used")


def counts_now(settings, rec, keys) -> bool:
    """Should this stored listing show on the card and count towards the best price?
    Listings found under the other country stay stored but out of sight."""
    if rec.get("source") not in keys:
        return False
    if rec.get("region") and rec["region"] != settings.get("region", "CA"):
        return False
    return settings.get("include_used", True) or not is_used_offer(rec)

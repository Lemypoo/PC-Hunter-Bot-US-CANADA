"""JSON-file persistence for items, listings, events and settings."""
import copy
import json
import os
import re
import threading
import time
import uuid

from .matching import matches, tokens_from_query

FILTER_FIELDS = ("query", "must", "exclude", "min_price", "max_price", "sources")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")

# Facebook city used when the user has not typed one: checked by loading each (September 2026)
FACEBOOK_CITY = {"CA": "vancouver", "US": "nyc"}
INTERNATIONAL_URL = "https://github.com/Lemypoo/PC-Hunter-Bot-International"

DEFAULT_SETTINGS = {
    "region": "CA",             # CA or US; the first start takes it from the computer's region setting
    "region_chosen": False,     # set once the user picks a country in Settings
    "poll_minutes": 15,
    "fb_city": "vancouver",     # slug used in facebook.com/marketplace/<city>/search
    "sources": {
        "amazon": True, "newegg": True, "ebay": True,
        "bestbuy_ca": True, "canadacomputers": True, "facebook": True,
    },
    "notify": True,
    "include_used": True,       # Amazon used and Resale (Warehouse) offers, alongside new stock
    "speed": "normal",          # scales every store's own cadence: fast | normal | relaxed
    "workers": 2,               # stores checked at the same time; each one costs a browser
    "browser_channel": "msedge",
    # optional alert channels; the same rules decide what is worth an alert on every channel
    "email": {"enabled": False, "to": "", "smtp_host": "smtp.gmail.com", "smtp_port": 587,
              "username": "", "password": "", "security": "starttls"},
    "push": {"enabled": False, "topic": "", "server": "https://ntfy.sh"},
}
NESTED_SETTINGS = ("sources", "email", "push")

# Phrases that usually mean "prebuilt / bundle / junk", not the bare part. "+" matches
# the literal " + " that bundle listings use ("CPU + Motherboard + RAM").
DEFAULT_EXCLUDES = [
    "gaming pc", "gaming desktop", "gaming computer", "gaming system", "gaming rig", "desktop pc",
    "desktop computer", "prebuilt", "bundle", "combo", "laptop", "notebook", "for parts",
    "not working", "parts only", "as is", "defective", "broken", "box only", "empty box",
    "all in one", "everyday computing", "+",
    # marketplace posts that are not sales: wanted ads and trade offers
    "iso", "wtb", "wanted", "looking for", "trade", "trading", "swap",
]

# Extra excludes chosen from what the query looks like, so a CPU search ignores motherboards
# that list "supports 9950X3D", and a GPU search ignores CPUs.
_CPU_RE = re.compile(r"\b\d{4,5}x3d\b|\b[579]\d{3}x\b|\bryzen\b|\bcore i[3579]\b|\bi[3579]-?\d{4,5}\w*\b|\bcore ultra\b|\bthreadripper\b", re.I)
_GPU_RE = re.compile(r"\brtx\b|\bgtx\b|\bradeon\b|\brx ?\d{4}\b|\bgeforce\b|\barc [ab]\d{3}\b|\b[4-9]0[5-9]0\b", re.I)
_RAM_RE = re.compile(r"\bddr[45]\b|\bram\b|\bmemory kit\b", re.I)
_CATEGORY_EXCLUDES = {
    "cpu": ["motherboard", "mother board", "mobo", "graphics card", "rtx", "geforce"],
    "gpu": ["motherboard", "mother board", "cpu", "processor", "ryzen", "intel core", "ddr5", "ssd",
            "no gpu", "water block", "waterblock", "backplate", "bracket", "riser", "cable", "adapter",
            "egpu", "mobile", "qhd", "oled", "240hz", "300hz",
            # a PC or power supply "ready for RTX 5090" is not the card
            "ready for", "for rtx", "psu", "power supply", "i5", "i7", "i9", "z790", "z890", "x870", "x870e",
            "b650", "b850", "nvme",
            # mounts, cases and coolers that list the cards they fit
            "vertical", "mount", "holder", "stand", "standing", "case", "chassis", "atx", "radiator"],
    "ram": ["motherboard", "cpu", "processor", "ryzen", "rtx", "gpu"],
}


def part_category(query: str):
    """'cpu', 'gpu' or 'ram' when the search looks like one, else None."""
    q = query or ""
    if _CPU_RE.search(q):
        return "cpu"
    if _GPU_RE.search(q):
        return "gpu"
    if _RAM_RE.search(q):
        return "ram"
    return None


def suggest_excludes(query: str) -> list:
    cat = part_category(query)
    return list(DEFAULT_EXCLUDES) + (_CATEGORY_EXCLUDES[cat] if cat else [])

MAX_EVENTS = 300
MAX_HISTORY = 400
MIN_POLL_MINUTES = 5     # faster than this and the stores start refusing us
PRUNE_AFTER = 14 * 86400


def now_ts() -> float:
    return time.time()


class Store:
    def __init__(self, path: str = STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.state = {
            "settings": copy.deepcopy(DEFAULT_SETTINGS),
            "items": [],
            "listings": {},   # item_id -> {listing_key -> record}
            "events": [],
        }
        if not self.load():
            # first start: a computer set to the United States starts there
            if detect_country() == "US":
                self.state["settings"]["region"] = "US"
                self.state["settings"]["fb_city"] = FACEBOOK_CITY["US"]

    # ---------- persistence ----------
    def load(self) -> bool:
        """Read the state file (or its backup). False when there is none: a first start."""
        data = self._read(self.path)
        if data is None and os.path.exists(self.path + ".bak"):
            data = self._read(self.path + ".bak")
            if data is not None:
                print(f"[store] recovered items, history and alert settings from {self.path}.bak", flush=True)
        if data is None:
            return False
        with self.lock:
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            for k, v in (data.get("settings") or {}).items():
                if k in NESTED_SETTINGS and isinstance(v, dict):
                    known = settings[k]
                    settings[k].update({kk: vv for kk, vv in v.items() if k == "sources" or kk in known})
                else:
                    settings[k] = v
            settings["poll_minutes"] = _clamp_poll(settings.get("poll_minutes"))
            self.state["settings"] = settings
            self.state["items"] = data.get("items") or []
            self.state["listings"] = data.get("listings") or {}
            self.state["events"] = data.get("events") or []
            moved = self._tag_regions(settings.get("region"))
            if moved:
                print(f"[store] filed {moved} saved listing(s) under country {settings.get('region')}", flush=True)
        return True

    def _tag_regions(self, region) -> int:
        """Listings saved before the app kept Canada and the US apart carry no country. They were found
        under the country setting in the same file, so file them there, with the keys this version uses;
        otherwise every one of them would come back as a "new listing" alert. Caller holds the lock."""
        if region not in ("CA", "US"):
            return 0
        renamed = {}
        for recs in self.state["listings"].values():
            for old in [k for k, r in recs.items() if not r.get("region")]:
                rec = recs.pop(old)
                rec["region"] = region
                new = f"{rec.get('source')}:{region}:{rec.get('id')}"
                rec["key"] = new
                recs[new] = rec
                renamed[old] = new
        for it in self.state["items"]:
            seen = it.get("seen_sources") or {}
            if any("@" not in k for k in seen):
                it["seen_sources"] = {(k if "@" in k else f"{k}@{region}"): v for k, v in seen.items()}
        if renamed:
            for it in self.state["items"]:
                it["hidden"] = [renamed.get(k, k) for k in it.get("hidden") or []]
            for ev in self.state["events"]:
                ev["key"] = renamed.get(ev.get("key"), ev.get("key"))
        return len(renamed)

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())   # without this a power cut leaves a file of zero bytes behind
            if os.path.exists(self.path):
                self._replace(self.path, self.path + ".bak")   # last good copy, in case this one dies
            self._replace(tmp, self.path)

    @staticmethod
    def _replace(src, dst):
        """os.replace, retried: on Windows an antivirus scan or an open editor can hold a file briefly."""
        for attempt in range(8):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.25)

    def _read(self, path):
        """Parse a state file, or set it aside and return None when it is unreadable."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            bad = path + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                os.replace(path, bad)
            except OSError:
                pass
            print(f"[store] could not read {path} ({e}); moved to {bad}", flush=True)
            return None

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.state)

    # ---------- accessors ----------
    @property
    def settings(self) -> dict:
        return self.state["settings"]

    @property
    def items(self) -> list:
        return self.state["items"]

    @property
    def listings(self) -> dict:
        return self.state["listings"]

    @property
    def events(self) -> list:
        return self.state["events"]

    def update_settings(self, patch: dict) -> dict:
        with self.lock:
            s = self.state["settings"]
            for k, v in patch.items():
                if k == "sources" and isinstance(v, dict):
                    s["sources"].update({kk: bool(vv) for kk, vv in v.items()})
                elif k == "poll_minutes":
                    s[k] = _clamp_poll(v, s.get(k))
                elif k == "region":
                    if v in ("CA", "US"):
                        s["region_chosen"] = True
                        # a city that was only the old country's default follows the country
                        if v != s.get(k) and "fb_city" not in patch \
                                and s.get("fb_city", "") in ("", FACEBOOK_CITY.get(s.get(k), "")):
                            s["fb_city"] = FACEBOOK_CITY[v]
                        s[k] = v
                elif k == "fb_city":
                    s[k] = str(v).strip().lower().replace(" ", "") or FACEBOOK_CITY.get(s.get("region"), "vancouver")
                elif k in ("notify", "include_used"):
                    s[k] = bool(v)
                elif k == "speed":
                    if str(v).lower() in ("fast", "normal", "relaxed"):
                        s[k] = str(v).lower()
                elif k == "workers":
                    try:
                        s[k] = max(1, min(4, int(v)))
                    except (TypeError, ValueError):
                        pass
                elif k in ("email", "push") and isinstance(v, dict):
                    cur = s[k]
                    for kk, vv in v.items():
                        if kk not in DEFAULT_SETTINGS[k]:
                            continue
                        if kk == "enabled":
                            cur[kk] = bool(vv)
                        elif kk == "smtp_port":
                            try:
                                cur[kk] = max(1, min(65535, int(vv)))
                            except (TypeError, ValueError):
                                pass
                        elif kk == "password":
                            if vv:              # blank means "keep the saved one"
                                cur[kk] = str(vv)
                        else:
                            cur[kk] = str(vv or "").strip()
            self.save()
            return self.public_settings()

    def public_settings(self) -> dict:
        """Settings safe to hand to the browser: the SMTP password is masked."""
        with self.lock:
            s = copy.deepcopy(self.state["settings"])
        s["email"]["password_set"] = bool(s["email"].get("password"))
        s["email"]["password"] = ""
        return s

    # ---------- items ----------
    def get_item(self, item_id: str):
        with self.lock:
            for it in self.state["items"]:
                if it["id"] == item_id:
                    return it
        return None

    def add_item(self, query: str, **fields) -> dict:
        query = (query or "").strip()
        if not query:
            raise ValueError("query is required")
        with self.lock:
            twin = next((i for i in self.state["items"] if _same_query(i.get("query"), query)), None)
        if twin:
            # a second copy doubles every request to every store and splits the listings in two
            raise ValueError(f'Already hunting "{twin["name"]}". Edit that card instead.')
        item = {
            "id": uuid.uuid4().hex[:8],
            "query": query,
            "name": (fields.get("name") or query).strip(),
            "must": fields.get("must") or tokens_from_query(query),
            "exclude": fields.get("exclude") if fields.get("exclude") is not None else suggest_excludes(query),
            "min_price": _num_or_none(fields.get("min_price")),
            "max_price": _num_or_none(fields.get("max_price")),
            "target_price": _num_or_none(fields.get("target_price")),
            "sources": _source_list(fields.get("sources")),   # None = every enabled store
            "created": now_ts(),
            "last_checked": None,
            "paused": False,
            "hidden": [],
            "seen_sources": {},
        }
        with self.lock:
            self.state["items"].append(item)
            self.state["listings"][item["id"]] = {}
            self.save()
        return copy.deepcopy(item)

    def update_item(self, item_id: str, patch: dict):
        with self.lock:
            it = self.get_item(item_id)
            if not it:
                return None
            new_query = (patch.get("query") or "").strip()
            if new_query and any(i["id"] != item_id and _same_query(i.get("query"), new_query)
                                 for i in self.state["items"]):
                raise ValueError(f'Another card already hunts "{new_query}".')   # before anything changes
            for k, v in patch.items():
                if k in ("query", "name"):
                    v = (v or "").strip()
                    if v:
                        it[k] = v
                elif k in ("must", "exclude"):
                    if isinstance(v, str):
                        v = [p.strip() for p in v.split(",")]
                    it[k] = [p.strip() for p in (v or []) if p and p.strip()]
                elif k in ("min_price", "max_price", "target_price"):
                    it[k] = _num_or_none(v)
                elif k == "paused":
                    it[k] = bool(v)
                elif k == "sources":
                    it[k] = _source_list(v)
                elif k in ("last_checked", "seen_sources", "hidden"):
                    it[k] = v
            if any(k in patch for k in FILTER_FIELDS):
                self.refilter(item_id)
            self.save()
            return copy.deepcopy(it)

    def refilter_all(self) -> int:
        """Re-check every stored listing against its item's filters (run at startup)."""
        from .sources.canadacomputers import category_from_url
        gone = 0
        with self.lock:
            for it in self.state["items"]:
                for rec in (self.state["listings"].get(it["id"]) or {}).values():
                    if rec.get("source") == "canadacomputers" and not rec.get("category"):
                        rec["category"] = category_from_url(rec.get("url"))
                gone += self.refilter(it["id"])
            if gone:
                self.save()
        return gone

    def refilter(self, item_id: str) -> int:
        """Drop stored listings that no longer pass the item's filters. Returns how many went."""
        with self.lock:
            it = self.get_item(item_id)
            recs = self.state["listings"].get(item_id) or {}
            if not it:
                return 0
            wanted = it.get("sources")
            region = self.state["settings"].get("region")
            # only this country's listings: the price filters are in its currency
            gone = [k for k, r in recs.items()
                    if r.get("region") in (None, "", region)
                    and ((wanted and r.get("source") not in wanted)
                         or not matches(it, r.get("title", ""), r.get("price"),
                                        f"{r.get('category', '')} {r.get('condition', '')}")[0])]
            for k in gone:
                del recs[k]
            return len(gone)

    def prune(self, max_age: float = PRUNE_AFTER) -> int:
        """Forget listings that have been gone for a long time, and hide-marks that point nowhere.
        Without this the state file grows forever and every save and page refresh gets slower."""
        cutoff = now_ts() - max_age
        gone = 0
        with self.lock:
            for it in self.state["items"]:
                recs = self.state["listings"].get(it["id"]) or {}
                stale = [k for k, r in recs.items()
                         if not r.get("active", True) and r.get("last_seen", 0) < cutoff]
                for k in stale:
                    del recs[k]
                gone += len(stale)
                if it.get("hidden"):
                    it["hidden"] = [k for k in it["hidden"] if k in recs]
        return gone

    def remove_item(self, item_id: str) -> bool:
        with self.lock:
            before = len(self.state["items"])
            self.state["items"] = [i for i in self.state["items"] if i["id"] != item_id]
            self.state["listings"].pop(item_id, None)
            self.state["events"] = [e for e in self.state["events"] if e.get("item_id") != item_id]
            self.save()
            return len(self.state["items"]) != before

    def set_hidden(self, item_id: str, key: str, hidden: bool):
        with self.lock:
            it = self.get_item(item_id)
            if not it:
                return
            h = set(it.get("hidden") or [])
            (h.add if hidden else h.discard)(key)
            it["hidden"] = sorted(h)
            self.save()

    # ---------- events ----------
    def add_event(self, ev: dict):
        with self.lock:
            ev = dict(ev)
            ev.setdefault("id", uuid.uuid4().hex[:10])
            ev.setdefault("ts", now_ts())
            self.state["events"].insert(0, ev)
            del self.state["events"][MAX_EVENTS:]

    def clear_events(self):
        with self.lock:
            self.state["events"] = []
            self.save()


def detect_country():
    """This computer's country (two letters) from the operating system's region setting, or None."""
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(16)
            if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, len(buf)) and len(buf.value) == 2:
                return buf.value.upper()
        except Exception:
            pass
    import locale
    for raw in (locale.getlocale()[0], os.environ.get("LC_ALL"), os.environ.get("LANG")):
        tail = (raw or "").split(".")[0].replace("-", "_").split("_")
        if len(tail) >= 2 and len(tail[-1]) == 2 and tail[-1].isalpha():
            return tail[-1].upper()
    return None


def _clamp_poll(v, fallback=None):
    """Check interval in whole minutes, never below MIN_POLL_MINUTES."""
    try:
        return max(MIN_POLL_MINUTES, min(720, int(float(v))))
    except (TypeError, ValueError):
        return fallback if fallback is not None else DEFAULT_SETTINGS["poll_minutes"]


def _same_query(a, b) -> bool:
    """'RTX 4090', 'rtx 4090' and 'RTX  4090 ' are the same hunt."""
    norm = lambda s: " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())
    return bool(norm(a)) and norm(a) == norm(b)


def _source_list(v):
    """A sorted list of store keys, or None meaning "every enabled store"."""
    if not v or not isinstance(v, (list, tuple, set)):
        return None
    keys = sorted({str(x).strip() for x in v if str(x).strip()})
    return keys or None


def _num_or_none(v):
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

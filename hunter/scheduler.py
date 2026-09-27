"""Background checking: every (item, store) pair is a job with its own due time.

Worker threads pull whichever job is due next, so a cheap store like Best Buy is checked often
while Amazon stays slow, and two stores are read at the same time instead of one long pass.
"""
import copy
import random
import threading
import time
import traceback

from . import notify
from .browser import Browser
from .matching import matches
from .sources import active_keys, counts_now, is_used_offer, sources_for
from .store import MAX_HISTORY, now_ts

MISSES_BEFORE_INACTIVE = 3
INACTIVE_AFTER = 30 * 60        # ...and not seen for at least this long, so result-order churn is not "sold out"
STALE_AFTER = 6 * 3600          # a store that keeps answering with nothing: its old listings expire after this
HISTORY_MIN_GAP = 6 * 3600
RETAIL_SOURCES = {"amazon", "newegg", "bestbuy_ca", "canadacomputers"}
SUSPECT_RATIO = 0.30   # a listing under 30% of the typical retail price is a placeholder, not a deal
EVENT_WINDOW = 1.15    # feed only carries listings within 15% of the current best in-stock price
TOAST_WINDOW = 1.05    # a price drop notifies when it lands within 5% of the current best
MAX_TOASTS_PER_PASS = 3
BLOCK_BACKOFF = 30 * 60                      # a store that shows a block page or robot check
FAIL_BACKOFF = [0, 120, 300, 600, 1200, 1800, 3600]   # by consecutive ordinary failures (timeouts, bad pages)
MIN_SOURCE_GAP = 10      # fallback spacing between two requests to one store; each store sets its own
DEFAULT_INTERVAL = 300   # fallback cadence for a store that does not set one
URGENT_GAP = 3           # a freshly added item still waits this long between requests to one store
BROWSER_RESTART_AFTER = 4   # consecutive failures, across stores, before a worker restarts its browser
SPEED = {"fast": 0.6, "normal": 1.0, "relaxed": 2.0}
IDLE_SLEEP = 0.5
SAVE_EVERY = 30          # seconds between writes of the state file while checking
LOG_LINES = 60


class Scheduler:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()          # due times, in-flight stores, rate limits
        self._status_lock = threading.Lock()   # the web server reads status while workers write it
        self._stop = threading.Event()
        self._threads = []
        self.browser = None                    # only the serial run_cycle path uses this
        self._due = {}       # (item id, store key) -> unix time it may be checked again
        self._inflight = set()                 # store keys a worker is busy with right now
        self._backoff = {}   # store key -> unix time before which we do not contact it
        self._fails = {}     # store key -> consecutive failures
        self._fail_streak = 0
        self._last_hit = {}  # store key -> unix time our last request to it finished
        self._busy = {}      # worker name -> what it is doing
        self._last_save = 0.0
        self.status = {
            "running": False, "current": "", "last_run": None, "next_run": None,
            "errors": {}, "browser": None, "cycle": 0, "log": [], "workers": 0,
        }

    # ---------- lifecycle ----------
    def start(self):
        n = self._worker_count()
        for i in range(n):
            t = threading.Thread(target=self._worker, args=(f"w{i + 1}",), daemon=True,
                                 name=f"hunter-worker-{i + 1}")
            t.start()
            self._threads.append(t)
        self._set(workers=n)
        self.log(f"watching with {n} checker{'s' if n != 1 else ''}")

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)

    def _worker_count(self) -> int:
        try:
            return max(1, min(4, int(self.store.settings.get("workers", 2))))
        except (TypeError, ValueError):
            return 2

    def _speed(self) -> float:
        return SPEED.get(str(self.store.settings.get("speed", "normal")).lower(), 1.0)

    # ---------- public ----------
    def request(self, item_id: str = None, urgent: bool = False):
        """Make an item (or everything) due right now; a worker picks it up within half a second."""
        with self.store.lock:
            ids = [item_id] if item_id else [i["id"] for i in self.store.items]
        with self._lock:
            for key in list(self._due):
                if key[0] in ids:
                    self._due[key] = 0.0   # a pair with no due time yet is already due
        if urgent:
            with self._lock:       # a brand new item should not wait behind a store's usual spacing
                for k in list(self._last_hit):
                    self._last_hit[k] = min(self._last_hit[k], time.time() - URGENT_GAP)

    def status_snapshot(self) -> dict:
        with self._status_lock:
            s = copy.deepcopy(self.status)
        with self._lock:
            due = [t for t in self._due.values() if t]
            s["next_run"] = min(due) if due else None
            if self._busy:
                s["running"] = True
                s["current"] = "; ".join(sorted(self._busy.values()))
        return s

    def log(self, msg: str):
        with self._status_lock:
            self.status["log"].insert(0, {"ts": now_ts(), "msg": msg})
            del self.status["log"][LOG_LINES:]
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    def _set(self, **fields):
        with self._status_lock:
            self.status.update(fields)

    def _set_error(self, key, msg, item_name):
        with self._status_lock:
            self.status["errors"][key] = {"msg": msg, "ts": now_ts(), "item": item_name}

    def _clear_error(self, key):
        with self._status_lock:
            self.status["errors"].pop(key, None)

    # ---------- the worker loop ----------
    def _worker(self, name):
        browser = None
        while not self._stop.is_set():
            try:
                job = self._claim()
            except Exception as e:
                self.log(f"checker {name} could not pick up work: {type(e).__name__}: {e}")
                self._stop.wait(2)
                continue
            if job is None:
                self._stop.wait(IDLE_SLEEP)
                continue
            item, src, settings, region = job
            trouble = False
            try:
                browser = self._ensure_worker_browser(browser, settings, name)
                with self._lock:
                    self._busy[name] = f"{item['name']} @ {src.display_name(region)}"
                self._check_one(item, src, settings, region, browser)
            except Exception as e:
                self.log(f"checker {name} hit a problem: {type(e).__name__}: {e}")
                traceback.print_exc()
                browser = None      # build a fresh one rather than reusing a broken handle
                trouble = True
            finally:
                with self._lock:
                    self._busy.pop(name, None)
                self._finish(job)   # hand the store back before doing anything slow
            if trouble:
                self._stop.wait(2)
        if browser:
            browser.stop()

    def _ensure_worker_browser(self, browser, settings, name):
        region = settings.get("region", "CA")
        chan = settings.get("browser_channel", "msedge")
        if browser and (browser.region != region or browser.channel != chan):
            browser.stop()
            browser = None
        if browser is None:
            browser = Browser(channel=chan, region=region,
                              state_file=Browser.state_file_for(name))
        if browser._ctx is None:
            browser.start()
            self._set(browser=browser.channel_used)
            self.log(f"checker {name} ready ({browser.channel_used}, region {region})")
        return browser

    def _claim(self):
        """The job that is due soonest and whose store is free to be asked right now."""
        settings = copy.deepcopy(self.store.settings)
        region = settings.get("region", "CA")
        sources = {s.key: s for s in sources_for(settings)}
        with self.store.lock:
            items = copy.deepcopy(self.store.items)
        now = time.time()
        best = None
        with self._lock:
            self._drop_unknown_jobs(items, sources)
            for item in items:
                if item.get("paused"):
                    continue
                wanted = item.get("sources")
                for key, src in sources.items():
                    if wanted and key not in wanted:
                        continue
                    if key in self._inflight or self._backoff.get(key, 0) > now:
                        continue
                    if self._last_hit.get(key, 0) + getattr(src, "min_gap", MIN_SOURCE_GAP) > now:
                        continue
                    due = self._due.get((item["id"], key), 0.0)
                    if due > now:
                        continue
                    if best is None or due < best[0]:
                        best = (due, item, src)
            if best is None:
                return None
            _, item, src = best
            self._inflight.add(src.key)          # one request per store at a time, across all workers
            self._last_hit[src.key] = now
        return item, src, settings, region

    def _drop_unknown_jobs(self, items, sources):
        """Forget due times for items or stores that no longer exist. Caller holds the lock."""
        live = {i["id"] for i in items}
        for key in [k for k in self._due if k[0] not in live or (k[1] != "*" and k[1] not in sources)]:
            del self._due[key]

    def _finish(self, job):
        item, src, settings, _ = job
        interval = getattr(src, "interval", DEFAULT_INTERVAL) * self._speed() * random.uniform(0.9, 1.1)
        with self._lock:
            self._inflight.discard(src.key)
            self._last_hit[src.key] = time.time()   # spacing counts from when the request ended
            self._due[(item["id"], src.key)] = time.time() + interval
        if time.time() - self._last_save > SAVE_EVERY:
            self._last_save = time.time()
            try:
                pruned = self.store.prune()
                if pruned:
                    self.log(f"forgot {pruned} listing(s) gone for more than two weeks")
                self.store.save()
            except Exception as e:
                self.log(f"could not save the state file: {type(e).__name__}: {e}")

    # ---------- one (item, store) check ----------
    def _check_one(self, item, src, settings, region, browser):
        label = f"{item['name']} @ {src.display_name(region)}"
        t0 = time.time()
        page = None
        try:
            page = browser.page()
            listings = src.search(page, item["query"], region)
            kept, new, drops = self.ingest(item, src, listings, settings)
            self._on_success(src)
            self.store.update_item(item["id"], {"last_checked": now_ts()})
            self.log(f"{label}: {len(listings)} results, {kept} match, {new} new, "
                     f"{drops} drops ({time.time() - t0:.1f}s)")
            return True
        except Exception as e:
            self._on_failure(src, item, label, e, browser)
            return False
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            with self._status_lock:
                self.status["last_run"] = now_ts()
                self.status["cycle"] += 1

    def _on_success(self, src):
        with self._lock:
            self._fails.pop(src.key, None)
            self._backoff.pop(src.key, None)
            self._fail_streak = 0
        self._clear_error(src.key)

    def _on_failure(self, src, item, label, e, browser=None):
        first = str(e).splitlines()[0][:160] if str(e) else ""
        msg = f"{type(e).__name__}: {first}" if first else type(e).__name__
        with self._lock:
            n = self._fails.get(src.key, 0) + 1
            self._fails[src.key] = n
            delay = FAIL_BACKOFF[min(n - 1, len(FAIL_BACKOFF) - 1)]
            if "blocked" in first.lower():
                delay = max(delay, BLOCK_BACKOFF)
            if delay:
                until = time.time() + delay
                self._backoff[src.key] = until
                msg += f". Trying this store again at {time.strftime('%H:%M', time.localtime(until))}"
            self._fail_streak += 1
            streak = self._fail_streak
        self._set_error(src.key, msg, item["name"])
        self.log(f"{label}: ERROR {msg}")
        closed = any(s in str(e) for s in ("has been closed", "Connection closed", "Target closed",
                                           "Browser closed", "browser has disconnected"))
        if browser and (closed or streak >= BROWSER_RESTART_AFTER):
            with self._lock:
                self._fail_streak = 0
            try:
                browser.restart()
                self.log("browser restarted after repeated failures")
            except Exception as e2:
                self.log(f"browser restart failed: {e2}")

    # ---------- serial pass (used by tests and by anything wanting a blocking check) ----------
    def run_cycle(self, item_ids, skip_gap=False, skip_recent=0):
        settings = copy.deepcopy(self.store.settings)
        region = settings.get("region", "CA")
        items = [i for i in self.store.snapshot()["items"]
                 if (item_ids is None and not i.get("paused")) or (item_ids and i["id"] in item_ids)]
        if skip_recent:
            items = [i for i in items if not i.get("last_checked") or time.time() - i["last_checked"] > skip_recent]
        if not items:
            return
        sources = sources_for(settings)
        self._drop_stale_errors({s.key for s in sources})
        if self.browser is None:
            self.browser = Browser(channel=settings.get("browser_channel", "msedge"), region=region)
        if self.browser._ctx is None:
            self.browser.start()
            self._set(browser=self.browser.channel_used)
        self._set(running=True)
        try:
            for item in items:
                wanted = item.get("sources")
                for src in [s for s in sources if not wanted or s.key in wanted]:
                    if self._backoff.get(src.key, 0) > time.time():
                        continue
                    gap = getattr(src, "min_gap", MIN_SOURCE_GAP)
                    if skip_gap:
                        gap = min(gap, URGENT_GAP)
                    wait = self._last_hit.get(src.key, 0) + gap - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    self._check_one(item, src, settings, region, self.browser)
                    self._last_hit[src.key] = time.time()
                    time.sleep(random.uniform(1.0, 2.5))
            self.store.save()
        finally:
            self._set(running=False, current="")

    def _drop_stale_errors(self, source_keys):
        """A store switched off in Settings should not keep its old error chip forever."""
        with self._status_lock:
            for key in list(self.status["errors"]):
                if key not in source_keys and key not in ("email", "push"):
                    del self.status["errors"][key]

    # ---------- diffing ----------
    def ingest(self, item, src, listings, settings):
        """Merge fresh listings into the store; return (kept, new, drops)."""
        item_id = item["id"]
        ts = now_ts()
        notes = []
        region = settings.get("region", "CA")
        # "has this store been read for this item before" is asked per country: the first Amazon.com
        # read after switching from Canada is a baseline, not a page full of new listings
        seen_key = f"{src.key}@{region}"
        # the same listing can appear twice on one results page (sponsored and organic);
        # keep the cheaper copy so one pass never reports a drop and a rise for the same thing
        unique = {}
        for L in listings:
            if L.price is None or not L.id or not L.title:
                continue
            if not L.region:
                L.region = region
            if L.key not in unique or L.price < unique[L.key].price:
                unique[L.key] = L
        listings = list(unique.values())

        with self.store.lock:
            live = self.store.get_item(item_id)
            if live is None:          # deleted while we were scraping
                return 0, 0, 0
            recs = self.store.listings.setdefault(item_id, {})
            mine = [r for r in recs.values() if r.get("source") == src.key and r.get("active", True)]

            if not listings:
                # A store that suddenly answers with nothing at all, when it had listings a moment ago,
                # is far more often a half-loaded or throttled page than a sold-out market. Keep what we
                # know; only listings unseen for a long time expire.
                for rec in mine:
                    if ts - rec.get("last_seen", ts) >= STALE_AFTER:
                        rec["active"] = False
                live.setdefault("seen_sources", {})[seen_key] = True
                return 0, 0, 0

            hidden = set(live.get("hidden") or [])
            keys = active_keys(settings, live) | {src.key}
            counts = lambda rec: counts_now(settings, rec, keys)
            seen_before = bool((live.get("seen_sources") or {}).get(seen_key))
            best_before = self._best_price(recs, hidden, counts)
            seen_now = set()
            kept = new = drops = restocks = 0

            matched = []
            for L in listings:
                if matches(live, L.title, L.price, f"{L.category} {L.condition}")[0]:
                    matched.append(L)
                elif L.key in recs:
                    del recs[L.key]   # stored earlier; a filter or fresher store data now rules it out
            # reference for the sanity floor: what this part costs new at a real store
            retail = [r["price"] for k, r in recs.items()
                      if r.get("source") in RETAIL_SOURCES and counts(r) and r.get("active", True)
                      and r.get("in_stock", True) and r.get("price") and k not in hidden
                      and not is_used_offer(r)]
            if src.key in RETAIL_SOURCES:
                retail += [L.price for L in matched if L.in_stock and not is_used_offer({"id": L.id})]
            floor = self._floor(retail)

            for L in matched:
                kept += 1
                key = L.key
                seen_now.add(key)
                rec = recs.get(key)
                fresh = L.to_dict()
                fresh["suspect"] = bool(floor is not None and L.price < floor)
                restarted = False
                if rec is not None and rec.get("currency") != L.currency:
                    # shown in another currency now: comparing the two would report a drop that never
                    # happened, so start its record over quietly
                    del recs[key]
                    rec, restarted = None, True
                if rec is None:
                    rec = {**fresh, "first_seen": ts, "last_seen": ts, "prev_price": None,
                           "lowest": L.price, "history": [[ts, L.price]], "misses": 0,
                           "active": True, "changed_at": None}
                    recs[key] = rec
                    if seen_before and not restarted and not rec["suspect"] and L.in_stock:
                        new += 1
                        self._raise(live, rec, "new", None, best_before, notes)
                    continue

                old_price = rec.get("price")
                was_in_stock = rec.get("in_stock", True)
                rec.update({k: v for k, v in fresh.items() if k != "price"})
                rec["last_seen"] = ts
                rec["misses"] = 0
                rec["active"] = True
                hist = rec.get("history") or []
                dropped = False
                if old_price is None or abs(L.price - old_price) >= max(1.0, 0.005 * old_price):
                    rec["prev_price"] = old_price
                    rec["price"] = L.price
                    rec["changed_at"] = ts
                    hist.append([ts, L.price])
                    if old_price is not None and L.price < old_price and not rec["suspect"] and L.in_stock:
                        drops += 1
                        dropped = True
                        self._raise(live, rec, "drop", old_price, best_before, notes)
                else:
                    rec["price"] = L.price
                    if not hist or ts - hist[-1][0] > HISTORY_MIN_GAP:
                        hist.append([ts, L.price])
                rec["history"] = hist[-MAX_HISTORY:]
                rec["lowest"] = min(rec.get("lowest") or L.price, L.price)
                if L.in_stock and not was_in_stock and not dropped and not rec["suspect"]:
                    restocks += 1
                    self._raise(live, rec, "restock", None, best_before, notes)

            # listings from this source that are missing from this read
            for key, rec in recs.items():
                if rec.get("source") == src.key and key not in seen_now and rec.get("active", True):
                    rec["misses"] = rec.get("misses", 0) + 1
                    if rec["misses"] >= MISSES_BEFORE_INACTIVE and ts - rec.get("last_seen", ts) >= INACTIVE_AFTER:
                        rec["active"] = False
            # the sanity floor moved, so re-judge this country's stored listings against it
            for rec in recs.values():
                if rec.get("price") and rec.get("region") in (None, "", region):
                    rec["suspect"] = bool(floor is not None and rec["price"] < floor)

            live.setdefault("seen_sources", {})[seen_key] = True

        if notes:
            # cheapest first, and never more than a few per store per pass
            picked = sorted(notes, key=lambda e: e["price"])[:MAX_TOASTS_PER_PASS]
            threading.Thread(target=self.deliver, args=(settings, picked), daemon=True).start()
        return kept, new + restocks, drops

    def deliver(self, settings, events):
        """Send the picked events through every enabled channel; failures show as error chips."""
        if settings.get("notify", True):
            for ev in events:
                title, body = notify.format_event(ev)
                notify.toast(title, body, ev.get("url"))
        push = settings.get("push") or {}
        if push.get("enabled"):
            for ev in events:
                title, body = notify.format_event(ev)
                try:
                    notify.send_push(push, title, body, ev.get("url"))
                    self._clear_error("push")
                except Exception as e:
                    self._channel_error("push", "phone push failed", e, ev)
        email = settings.get("email") or {}
        if email.get("enabled"):
            subject, body = notify.format_digest(events)
            try:
                notify.send_email(email, subject, body)
                self._clear_error("email")
            except Exception as e:
                self._channel_error("email", "email failed", e, events[0])

    def _channel_error(self, key, what, e, ev):
        msg = f"{what}: {type(e).__name__}: {str(e)[:120]}"
        self._set_error(key, msg, ev["item_name"])
        self.log(msg)

    def _raise(self, item, rec, kind, prev_price, best_before, notes):
        """Put an event in the feed if it is near the best price; queue a toast if it beats it."""
        ev = self._event(item, rec, kind, prev_price)
        target = item.get("target_price")
        hits_target = target not in (None, "") and ev["price"] <= float(target)
        if not hits_target and best_before is not None and ev["price"] > best_before * EVENT_WINDOW:
            return   # a target hit always goes through; everything else must be near the best price
        self.store.add_event(ev)
        if self._worth_a_toast(item, ev, best_before):
            notes.append(ev)

    @staticmethod
    def _floor(retail):
        """Below this a price is a placeholder, not a deal. The reference is the cheapest retail
        price, ignoring one outlier when there are enough of them, because search results carry
        both accessories priced far below the part and resellers priced far above it."""
        if not retail:
            return None
        prices = sorted(retail)
        return prices[1 if len(prices) >= 4 else 0] * SUSPECT_RATIO

    @staticmethod
    def _best_price(recs, hidden, counts=None):
        prices = [r["price"] for k, r in recs.items()
                  if r.get("active", True) and r.get("in_stock", True) and not r.get("suspect")
                  and k not in hidden and r.get("price") and (counts is None or counts(r))]
        return min(prices) if prices else None

    @staticmethod
    def _event(item, rec, kind, prev_price):
        return {
            "item_id": item["id"], "item_name": item["name"], "type": kind, "key": rec["key"],
            "source": rec["source"], "source_name": rec["source_name"], "title": rec["title"],
            "url": rec["url"], "price": rec["price"], "prev_price": prev_price,
            "currency": rec.get("currency"), "location": rec.get("location", ""),
            "condition": rec.get("condition", ""), "in_stock": rec.get("in_stock", True),
        }

    @staticmethod
    def _worth_a_toast(item, ev, best_before):
        price = ev["price"]
        if not ev.get("in_stock", True):
            return False
        target = item.get("target_price")
        if target not in (None, "") and price <= float(target):
            return True
        if best_before is None:
            return True          # nothing else is in stock, so any in-stock listing is news
        if price <= best_before:
            return True
        return ev["type"] == "drop" and price <= best_before * TOAST_WINDOW

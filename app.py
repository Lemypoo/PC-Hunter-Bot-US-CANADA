"""Item Hunter: local PC-part price watcher. Run: python app.py  (or double-click start.bat)"""
import os
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, request, send_from_directory

from hunter import notify
from hunter.scheduler import Scheduler
from hunter.sources import ALL as ALL_SOURCES, active_keys, counts_now, describe
from hunter.store import DEFAULT_EXCLUDES, FACEBOOK_CITY, INTERNATIONAL_URL, Store, detect_country, suggest_excludes

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = int(os.environ.get("ITEM_HUNTER_PORT", "8787"))
DEFAULT_ITEMS = ["RTX 5090", "9950X3D"]

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
store = Store()
sched = Scheduler(store)


def cadence(settings):
    """How often each store is swept, in seconds, after the speed setting is applied."""
    from hunter.scheduler import DEFAULT_INTERVAL, SPEED
    mult = SPEED.get(str(settings.get("speed", "normal")).lower(), 1.0)
    return {S.key: round(getattr(S, "interval", DEFAULT_INTERVAL) * mult) for S in ALL_SOURCES}


def build_state():
    snap = store.snapshot()
    items = []
    for it in snap["items"]:
        recs = snap["listings"].get(it["id"], {})
        hidden = set(it.get("hidden") or [])
        keys = active_keys(snap["settings"], it)   # stores switched off disappear from the card
        rows = []
        for key, rec in recs.items():
            if not rec.get("in_stock", True) or not counts_now(snap["settings"], rec, keys):
                continue
            r = dict(rec)
            r["hidden"] = key in hidden
            r["history"] = (rec.get("history") or [])[-40:]
            rows.append(r)
        rows.sort(key=lambda r: (not r.get("active", True), bool(r.get("suspect")),
                                 r["price"] if r.get("price") is not None else 1e12))
        visible = [r for r in rows if r.get("active", True) and not r["hidden"]]
        priced = [r for r in visible if not r.get("suspect")]
        best = min(priced, key=lambda r: r["price"]) if priced else None
        items.append({**it, "listings": rows, "best": best,
                      "counts": {"visible": len(visible), "hidden": len(hidden), "total": len(rows)}})
    pc = detect_country()
    return {
        # a computer set to another country is pointed at the international edition, until a country is picked
        "other_edition": ({"url": INTERNATIONAL_URL}
                          if pc and pc not in ("CA", "US") and not snap["settings"].get("region_chosen") else None),
        "fb_defaults": FACEBOOK_CITY,
        "settings": store.public_settings(),
        "items": items,
        "events": snap["events"][:100],
        "status": sched.status_snapshot(),   # a copy: the scheduler thread keeps writing the original
        "sources": describe(snap["settings"]),
        "cadence": cadence(snap["settings"]),
        "default_excludes": DEFAULT_EXCLUDES,
        "now": time.time(),
    }


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/privacy")
def privacy():
    return send_from_directory(STATIC, "privacy.html")


@app.get("/terms")
def terms():
    return send_from_directory(STATIC, "terms.html")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(STATIC, "favicon.svg", mimetype="image/svg+xml")


@app.get("/api/state")
def api_state():
    return jsonify(build_state())


@app.post("/api/items")
def api_add_item():
    body = request.get_json(silent=True) or {}
    try:
        item = store.add_item(body.get("query", ""), name=body.get("name"), must=body.get("must"),
                              exclude=body.get("exclude"), min_price=body.get("min_price"),
                              max_price=body.get("max_price"), target_price=body.get("target_price"),
                              sources=body.get("sources"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 409 if "Already hunting" in str(e) else 400
    sched.request(item["id"], urgent=True)
    return jsonify(item), 201


@app.patch("/api/items/<item_id>")
def api_update_item(item_id):
    body = request.get_json(silent=True) or {}
    try:
        it = store.update_item(item_id, body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    if not it:
        return jsonify({"error": "not found"}), 404
    if any(k in body for k in ("query", "must", "exclude", "min_price", "max_price", "sources")):
        sched.request(item_id)
    return jsonify(it)


@app.delete("/api/items/<item_id>")
def api_delete_item(item_id):
    return jsonify({"ok": store.remove_item(item_id)})


@app.post("/api/items/<item_id>/check")
def api_check_item(item_id):
    if not store.get_item(item_id):
        return jsonify({"error": "not found"}), 404
    sched.request(item_id)
    return jsonify({"ok": True})


@app.post("/api/items/<item_id>/hide")
def api_hide(item_id):
    body = request.get_json(silent=True) or {}
    if not body.get("key"):
        return jsonify({"error": "key required"}), 400
    store.set_hidden(item_id, body["key"], bool(body.get("hidden", True)))
    return jsonify({"ok": True})


@app.post("/api/check")
def api_check_all():
    sched.request(None)
    return jsonify({"ok": True})


@app.patch("/api/settings")
def api_settings():
    body = request.get_json(silent=True) or {}
    before = (store.settings.get("region"), store.settings.get("fb_city"))
    saved = store.update_settings(body)
    if (saved.get("region"), saved.get("fb_city")) != before:
        sched.request(None)   # the other country or city: its stores should not wait out the old clocks
    return jsonify(saved)


@app.post("/api/events/clear")
def api_clear_events():
    store.clear_events()
    return jsonify({"ok": True})


def _merged(channel, body):
    """Saved channel settings with whatever the form currently holds on top (blank password keeps the saved one)."""
    cfg = dict(store.settings.get(channel) or {})
    for k, v in (body or {}).items():
        if k in cfg and not (k == "password" and v in ("", None)):
            cfg[k] = v
    return cfg


def _explain_email_error(e) -> str:
    """Turn the usual SMTP failures into a sentence that says what to change."""
    raw = f"{type(e).__name__}: {e}"
    text = str(e)
    if "535" in text or "5.7.8" in text or "Username and Password not accepted" in text or "BadCredentials" in text:
        return ("Gmail rejected the username and password. Use a 16-character app password "
                "(Google account > Security > 2-Step Verification > App passwords), not your normal "
                f"password, and make sure the username is the full address. Server said: {raw}")
    if "5.7.0" in text and ("Authentication Required" in text or "authentication" in text.lower()):
        return f"The server wants a login: fill in SMTP username and password. Server said: {raw}"
    if "getaddrinfo" in text or "Name or service not known" in text or "11001" in text:
        return f"SMTP host not found; for Gmail it is smtp.gmail.com. Server said: {raw}"
    if "timed out" in text.lower() or "10060" in text:
        return f"Could not reach the SMTP host on that port (firewall or wrong port?). Server said: {raw}"
    if "recipient" in text or "SMTP host" in text:
        return text
    return raw


@app.post("/api/test-email")
def api_test_email():
    cfg = _merged("email", request.get_json(silent=True))
    subject, body = notify.format_digest([{
        "type": "drop", "item_name": "9950X3D", "source_name": "Amazon.ca", "prev_price": 929.98, "price": 899.0,
        "currency": "CAD", "title": "AMD Ryzen 9 9950X3D 16-Core Processor (this is a test)", "url": f"http://127.0.0.1:{PORT}/"}])
    try:
        notify.send_email(cfg, subject, body)
        print(f"[test-email] sent to {cfg.get('to')}", flush=True)
        return jsonify({"ok": True, "to": cfg.get("to")})
    except Exception as e:
        print(f"[test-email] failed: {type(e).__name__}: {e}", flush=True)
        return jsonify({"ok": False, "error": _explain_email_error(e)})


@app.post("/api/test-push")
def api_test_push():
    cfg = _merged("push", request.get_json(silent=True))
    try:
        notify.send_push(cfg, "Item Hunter test", "Phone alerts are working. Tap to open the app.", f"http://127.0.0.1:{PORT}/")
        print(f"[test-push] sent to topic {cfg.get('topic')}", flush=True)
        return jsonify({"ok": True, "topic": cfg.get("topic")})
    except Exception as e:
        print(f"[test-push] failed: {type(e).__name__}: {e}", flush=True)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@app.post("/api/test-notify")
def api_test_notify():
    ok, err = notify.toast("Item Hunter", "Notifications are working. Click me to open the app.",
                           f"http://127.0.0.1:{PORT}/", wait=True)
    return jsonify({"ok": ok, "error": err})


def already_running(url: str) -> bool:
    """True when another Item Hunter instance already answers on our port."""
    import urllib.request
    try:
        with urllib.request.urlopen(url + "api/state", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if already_running(url):
        print(f"Item Hunter is already running at {url}; opening it.", flush=True)
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    print(f"Item Hunter running at {url}  (Ctrl+C to stop)", flush=True)
    if not store.items:
        for query in DEFAULT_ITEMS:   # a fresh install starts with two example parts
            store.add_item(query)
    dropped = store.refilter_all()
    if dropped:
        print(f"[startup] removed {dropped} stored listing(s) the filters no longer accept", flush=True)
    pruned = store.prune()
    if pruned:
        store.save()
        print(f"[startup] forgot {pruned} listing(s) gone for more than two weeks", flush=True)
    sched.start()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

"""Alert channels: Windows toast, email over SMTP, and phone push through ntfy."""
import html
import json
import smtplib
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage

# Registered AppUserModelID of Windows PowerShell; lets an unpackaged script show toasts.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


# ---------------- shared wording ----------------
def format_event(ev: dict) -> tuple:
    """(title, body) for one feed event, used by every channel."""
    cur = ev.get("currency") or ""
    where = f" ({ev['location']})" if ev.get("location") else ""
    listing = (ev.get("title") or "")[:90]
    if ev["type"] == "drop":
        title = f"{ev['item_name']}: price drop on {ev['source_name']}"
        body = f"${ev['prev_price']:,.2f} -> ${ev['price']:,.2f} {cur}{where} | {listing}"
    elif ev["type"] == "restock":
        title = f"{ev['item_name']}: back in stock on {ev['source_name']}"
        body = f"${ev['price']:,.2f} {cur}{where} | {listing}"
    else:
        title = f"{ev['item_name']}: new listing on {ev['source_name']}"
        body = f"${ev['price']:,.2f} {cur}{where} | {listing}"
    return title, body


def format_digest(events: list) -> tuple:
    """(subject, body) for one email covering every event of a pass."""
    if len(events) == 1:
        subject = format_event(events[0])[0]
    else:
        names = ", ".join(sorted({e["item_name"] for e in events}))
        subject = f"Item Hunter: {len(events)} alerts for {names}"
    parts = []
    for ev in events:
        title, body = format_event(ev)
        parts.append(f"{title}\n{body}\n{ev.get('url') or ''}")
    return subject, "\n\n".join(parts) + "\n"


# ---------------- Windows toast ----------------
def _script(title: str, message: str, url: str = None) -> str:
    x = lambda s: html.escape(str(s), quote=True)
    launch = f' activationType="protocol" launch="{x(url)}"' if url else ""
    xml = (
        f'<toast{launch}><visual><binding template="ToastGeneric">'
        f'<text>{x(title)}</text><text>{x(message)}</text>'
        f'</binding></visual><audio src="ms-winsoundevent:Notification.Default"/></toast>'
    )
    return "\n".join([
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null",
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
        "$doc.LoadXml(@'",
        xml,
        "'@)",
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $doc",
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)",
    ])


def toast(title: str, message: str, url: str = None, wait: bool = False):
    """Fire-and-forget by default. Returns (ok, stderr) when wait=True."""
    if sys.platform != "win32":
        print(f"[notify] {title}: {message} {url or ''}")
        return True, ""

    def _run():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _script(title, message, url)],
                capture_output=True, text=True, timeout=25, creationflags=flags,
            )
            return r.returncode == 0, (r.stderr or "").strip()
        except Exception as e:
            return False, str(e)

    if wait:
        return _run()
    threading.Thread(target=_run, daemon=True).start()
    return True, ""


# ---------------- email ----------------
def send_email(cfg: dict, subject: str, body: str):
    """Send one plain-text email with the SMTP details in cfg. Raises on any failure."""
    to = (cfg.get("to") or "").strip()
    host = (cfg.get("smtp_host") or "").strip()
    user = (cfg.get("username") or "").strip()
    password = cfg.get("password") or ""
    sender = (cfg.get("from") or user or to).strip()
    try:
        port = int(cfg.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    if not to:
        raise ValueError("no recipient address")
    if not host:
        raise ValueError("no SMTP host")
    security = (cfg.get("security") or ("ssl" if port == 465 else "starttls")).lower()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context()) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=25) as smtp:
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)


# ---------------- phone push (ntfy) ----------------
def _ntfy_publish(server: str, payload: dict):
    server = (server or "https://ntfy.sh").strip().rstrip("/") or "https://ntfy.sh"
    req = urllib.request.Request(
        server + "/", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status >= 300:
                raise RuntimeError(f"ntfy answered HTTP {r.status}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error") or ""
        except Exception:
            pass
        raise RuntimeError(f"ntfy answered HTTP {e.code}{': ' + detail if detail else ''}") from None


def send_push(cfg: dict, title: str, message: str, url: str = None, priority: int = 4):
    """Publish one notification to an ntfy topic. Raises on any failure."""
    topic = (cfg.get("topic") or "").strip().strip("/")
    if not topic:
        raise ValueError("no ntfy topic")
    payload = {"topic": topic, "title": title, "message": message, "priority": int(priority)}
    if url:
        payload["click"] = url
    _ntfy_publish(cfg.get("server"), payload)

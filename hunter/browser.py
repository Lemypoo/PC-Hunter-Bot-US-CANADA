"""One headless browser (your installed Edge/Chrome) shared by all sources."""
import os

from playwright.sync_api import sync_playwright

UA_EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/{v}.0.0.0 Safari/537.36 Edg/{v}.0.0.0")
UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/{v}.0.0.0 Safari/537.36")
SKIP_RESOURCES = {"image", "font", "media"}
PAGES_BEFORE_RESTART = 400   # restarting too often turns every visit into a brand-new, cookie-less session
SAVE_STATE_EVERY = 20        # pages between saves of the browser's cookies

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "data", "browser_state.json")


class Browser:
    @staticmethod
    def state_file_for(name: str) -> str:
        """Each checker keeps its own cookie jar; sharing one file would have them overwrite each other."""
        return os.path.join(BASE_DIR, "data", f"browser_state_{name}.json")

    def __init__(self, channel: str = "msedge", region: str = "CA", state_file: str = STATE_FILE):
        self.channel = channel or "msedge"
        self.region = region
        self.state_file = state_file
        self.channel_used = None
        self._pw = None
        self._browser = None
        self._ctx = None
        self.pages_opened = 0

    def start(self):
        self.stop()
        self._pw = sync_playwright().start()
        last_err = None
        tried = []
        for ch in [self.channel, "msedge", "chrome", None]:
            if ch in tried:
                continue
            tried.append(ch)
            try:
                kw = dict(headless=True, args=["--disable-blink-features=AutomationControlled"])
                if ch:
                    kw["channel"] = ch
                self._browser = self._pw.chromium.launch(**kw)
                self.channel_used = ch or "chromium"
                break
            except Exception as e:
                last_err = e
        if not self._browser:
            self._pw.stop()
            self._pw = None
            raise RuntimeError(f"Could not launch Edge, Chrome or Chromium: {last_err}")
        major = (self._browser.version or "130").split(".")[0]
        ua = (UA_EDGE if self.channel_used == "msedge" else UA_CHROME).format(v=major)
        options = dict(user_agent=ua, viewport={"width": 1366, "height": 900},
                       locale="en-CA" if self.region == "CA" else "en-US")
        self._ctx = None
        if self.state_file and os.path.exists(self.state_file):
            try:   # carry cookies over, so stores see the same returning visitor rather than a new one
                self._ctx = self._browser.new_context(storage_state=self.state_file, **options)
            except Exception:
                self._ctx = None   # an unreadable state file is not worth failing over
        if self._ctx is None:
            self._ctx = self._browser.new_context(**options)
        self._ctx.set_default_timeout(30000)
        self._ctx.route("**/*", self._route)
        self.pages_opened = 0

    @staticmethod
    def _route(route):
        if route.request.resource_type in SKIP_RESOURCES:
            route.abort()
        else:
            route.continue_()

    def page(self):
        if self._ctx is None or self.pages_opened >= PAGES_BEFORE_RESTART:
            self.start()
        self.pages_opened += 1
        if self.pages_opened % SAVE_STATE_EVERY == 0:
            self.save_state()
        return self._ctx.new_page()

    def save_state(self):
        if not self._ctx or not self.state_file:
            return
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp = self.state_file + ".tmp"
            self._ctx.storage_state(path=tmp)
            os.replace(tmp, self.state_file)
        except Exception:
            pass   # losing a cookie snapshot only costs a slightly colder next start

    def restart(self):
        self.start()

    def stop(self):
        self.save_state()
        for closer in (lambda: self._ctx and self._ctx.close(),
                       lambda: self._browser and self._browser.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._ctx = self._browser = self._pw = None

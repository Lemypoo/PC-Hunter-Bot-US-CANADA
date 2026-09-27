"""Decide whether a search result is really the part the user asked for."""
import re


def normalize(s: str) -> str:
    s = (s or "").lower().replace("™", " ").replace("®", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _compact(s: str) -> str:
    # "ryzen 9 9950 x3d" -> "ryzen 9 9950x3d" so split model numbers still match
    return re.sub(r"(?<=\d) (?=[a-z])", "", s)


def _split(s: str) -> str:
    # "rtx5090" -> "rtx 5090" so a title written without the space still has both words
    return re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)


def _word_re(tok: str, plural: bool = False):
    # whole-word match; exclude phrases also accept a plural "s" ("laptop" catches "Gaming Laptops")
    tail = r"s?(?![a-z0-9])" if plural else r"(?![a-z0-9])"
    return re.compile(r"(?<![a-z0-9])" + re.escape(tok) + tail)


def tokens_from_query(query: str) -> list:
    return [t for t in normalize(query).split(" ") if t]


def matches(item: dict, title: str, price, extra: str = "") -> tuple:
    """Return (ok, reason). Must-have words are checked in the title; excludes also in `extra`
    (for example the store's category label, so "Gaming Laptops" is caught by "laptop")."""
    t = normalize(title)
    tc = _compact(t)
    ts = _split(t)
    te = normalize(extra)
    tce = _compact(te)
    for tok in item.get("must") or []:
        tok_n = normalize(tok)
        if not tok_n:
            continue
        candidates = {tok_n, tok_n.replace(" ", "")}
        if not any(_word_re(c).search(t) or _word_re(c).search(tc) or _word_re(c).search(ts)
                   for c in candidates):
            return False, f"missing '{tok}'"
    raw_title = " " + " ".join((title or "").lower().split()) + " "
    for ph in item.get("exclude") or []:
        ph_n = normalize(ph)
        if not ph_n:
            # symbol-only phrase such as "+": match it literally as its own word ("CPU + Motherboard")
            raw = " ".join((ph or "").lower().split())
            if raw and f" {raw} " in raw_title:
                return False, f"excluded '{ph}'"
            continue
        for body, comp in ((t, tc), (te, tce)):
            if body and (_word_re(ph_n, True).search(body) or _word_re(ph_n.replace(" ", ""), True).search(comp)):
                return False, f"excluded '{ph}'"
    if price is not None:
        lo = item.get("min_price")
        hi = item.get("max_price")
        if lo not in (None, "") and price < float(lo):
            return False, "below min price"
        if hi not in (None, "") and price > float(hi):
            return False, "above max price"
    return True, ""

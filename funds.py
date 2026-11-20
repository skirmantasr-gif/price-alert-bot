"""📦 Listed funds (e.g. Destiny Tech100 DXYZ): price vs the fund's own net asset value (NAV) per share
(owner 2026-11-20 after Saba Capital's activist stake: "4. do").

A watchlist stock counts as a fund when its own SEC filings include fund forms (N-PORT, N-CSR, N-2). The NAV per
share comes from the newest prospectus supplement / shareholder report that states it ("our net asset value as of
September 30, 2026, is $38.32 per share"). Cached for CACHE_SECONDS. NAV of private holdings is the fund's own
estimate and is weeks old: the line says as of when.
"""
import html
import logging
import os
import re
import time
import urllib.request
from datetime import datetime

log = logging.getLogger("bot")
ON = os.environ.get("FUND_NAV", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
FUND_FORMS = ("NPORT-P", "N-CSR", "N-CSRS", "N-2", "N-2/A", "N-2ASR", "N-CEN")
NAV_FORMS = ("424B3", "424B5", "497", "N-CSR", "N-CSRS")
MAX_DOCS = 4
CACHE_SECONDS = 12 * 3600
NAV_RE = re.compile(r"net asset value(?:\s*\(\W?NAV\W?\))?(?:\s+per\s+(?:common\s+)?share)?(?:\s+of\s+(?:our|the)\s+"
                    r"common\s+stock)?,?\s+as\s+of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4}),?\s+(?:is|was)\s+"
                    r"\$\s?(\d[\d,]*\.\d+)", re.I)
NAV_RE2 = re.compile(r"\bNAV\s+per\s+share\s+(?:as\s+of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4}),?\s+)?(?:of|was|is)\s+"
                     r"\$\s?(\d[\d,]*\.\d+)", re.I)
_cache = {}


def _text(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"price-alert-bot {SEC_CONTACT}"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read(3_000_000).decode("utf-8", "ignore")
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def find_nav(text):
    """(nav, 'YYYY-MM-DD' or '') from filing text, or None."""
    for rx in (NAV_RE, NAV_RE2):
        m = rx.search(text or "")
        if m:
            try:
                nav = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
            asof = ""
            if m.group(1):
                try:
                    asof = datetime.strptime(" ".join(m.group(1).split()), "%B %d, %Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
            if nav > 0:
                return nav, asof
    return None


def nav(t, filings_of=None, text_of=_text):
    """(nav, asof) for a fund on the watchlist, else None. Never raises."""
    if not ON or not SEC_CONTACT:
        return None
    hit = _cache.get(t)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    out = None
    try:
        if filings_of is None:
            import news
            filings_of = news.sec_filings
        fl = filings_of(t) or []
        if any(str(f.get("form", "")).upper() in FUND_FORMS for f in fl):
            docs = [f for f in fl if str(f.get("form", "")).upper() in NAV_FORMS and f.get("doc")][:MAX_DOCS]
            for f in docs:     # newest first
                try:
                    out = find_nav(text_of(f["link"]))
                except Exception:
                    continue
                if out:
                    break
    except Exception as e:
        log.info("Fund NAV unavailable (%s)", type(e).__name__)
        return hit[1] if hit else None
    _cache[t] = (time.time(), out)
    return out


def line(t, price, nav_of=nav):
    """'📦 Below its holdings value: …' for a fund, else ''."""
    if not price:
        return ""
    n = nav_of(t)
    if not n:
        return ""
    v, asof = n
    gap = price / v - 1
    when = datetime.strptime(asof, "%Y-%m-%d").strftime("%b %d") if asof else "latest report"
    word = "Below its holdings value" if gap < 0 else "Above its holdings value"
    return (f"📦 {word}: price ${price:,.2f} vs NAV <b>${v:,.2f}</b> per share ({when}, the fund's own estimate) → "
            f"<b>{abs(gap) * 100:.0f}% {'discount' if gap < 0 else 'premium'}</b>")

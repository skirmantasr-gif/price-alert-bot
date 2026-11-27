"""⏳ Data freshness watchdog (owner 2026-11-27, "1."): CBOE's free options files for small caps froze on Nov 23
and the bot kept saving the same numbers for four days without saying anything — no error, just old data.

Sources report what they see from deep inside the code (no state needed there):
    fresh.ok("CBOE options", sym)                     data was current
    fresh.bad("CBOE options", sym, "2026-11-23 15:31") data was old
    fresh.beat("News feeds")                          something fresh arrived (for sources judged by silence)
    fresh.bad("SEC filings", sym, "HTTPError")        a fetch failed (ok() after a success)
FreshWatch.check(state) merges that into state["_fresh"] and sends ONE ⚠️ when a source goes stale and ✅ when it
recovers; line(state) is the ☀️ daily-check line. Rules per source in RULES:
    min_bad   distinct stale symbols in the last WINDOW_H hours before it counts as stale
    ratio     ... and at least this share of all symbols checked in that window (Yahoo: thin stocks can go quiet)
    silent_h  for beat sources: stale when nothing fresh for this many hours on a weekday
"""
import html
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("price-alert-bot")

ON = os.environ.get("FRESH_WATCH", "true").lower() != "false"
WINDOW_H = 24
CHECK_SECONDS = 300
RULES = {
    "CBOE options": {"min_bad": 3, "ratio": 0.0, "fix": "📼 snapshots, 📈 market odds and 🎲 options posts pause for "
                                                        "those stocks (old numbers are thrown away)"},
    "Yahoo prices": {"min_bad": 3, "ratio": 0.5, "fix": "price alerts may be late or missing"},
    "News feeds": {"silent_h": 24, "fix": "news / FDA headlines may be missing"},
    # SEC filings come at random times, so judge the fetches, not the news (owner 2026-11-27 "3.")
    "SEC filings": {"min_bad": 3, "ratio": 0.5, "silent_ok_h": 24, "word": "failed lookups", "head": "FAILING",
                    "fix": "8-K / Form 4 / 13D alerts and 🤖 filing summaries may be missing"},
}

_lock = threading.Lock()
_pending = []          # (source, kind, symbol, detail, unix time)


def _now():
    return time.time()


def ok(source, sym=""):
    with _lock:
        _pending.append((source, "ok", sym, "", _now()))
        del _pending[:-5000]


def bad(source, sym, detail=""):
    with _lock:
        _pending.append((source, "bad", sym, str(detail)[:40], _now()))
        del _pending[:-5000]


def beat(source):
    with _lock:
        _pending.append((source, "beat", "", "", _now()))
        del _pending[:-5000]


def merge(state, now=None):
    """Move pending reports into state['_fresh'] and drop anything older than the window."""
    now = now or _now()
    with _lock:
        items, _pending[:] = list(_pending), []
    fr = state.setdefault("_fresh", {})
    for src, kind, sym, det, ts in items:
        r = fr.setdefault(src, {"ok": {}, "bad": {}, "beat": None, "alerted": False})
        if kind == "beat":
            r["beat"] = max(ts, r.get("beat") or 0)
        elif kind == "ok":
            r["ok"][sym] = ts
            r["last_ok"] = max(ts, r.get("last_ok") or 0)
            r["bad"].pop(sym, None)           # fresh again
        else:
            r["bad"][sym] = [ts, det]
    cut = now - WINDOW_H * 3600
    for r in fr.values():
        r["ok"] = {k: v for k, v in r["ok"].items() if v >= cut}
        r["bad"] = {k: v for k, v in r["bad"].items() if v[0] >= cut}
    return bool(items)


def _weekday_daytime(now):
    try:
        import extras
        lt = extras.local_now()
    except Exception:
        lt = datetime.fromtimestamp(now)
    return lt.weekday() < 5 and lt.hour >= 12


def stale(state, src, now=None):
    """'' when fine, else a short reason."""
    now = now or _now()
    rule, r = RULES.get(src, {}), (state.get("_fresh") or {}).get(src)
    if not r:
        return ""
    if "silent_h" in rule:
        b = r.get("beat")
        if b and now - b > rule["silent_h"] * 3600 and _weekday_daytime(now):
            return f"nothing new for {int((now - b) // 3600)} h"
        return ""
    lo = r.get("last_ok")
    if "silent_ok_h" in rule and lo and now - lo > rule["silent_ok_h"] * 3600 and _weekday_daytime(now):
        return f"no successful fetch for {int((now - lo) // 3600)} h"
    n_bad = len(r["bad"])
    total = n_bad + len(r["ok"])
    if n_bad >= rule.get("min_bad", 3) and total and n_bad / total >= rule.get("ratio", 0):
        egs = sorted(r["bad"].items(), key=lambda kv: kv[1][0], reverse=True)
        names = ", ".join(f"{html.escape(k)}" + (f" ({html.escape(v[1])})" if v[1] else "") for k, v in egs[:3])
        more = f" +{n_bad - 3} more" if n_bad > 3 else ""
        return f"{rule.get('word', 'old data')} for {n_bad} of {total} stocks: {names}{more}"
    return ""


class FreshWatch:
    def __init__(self, send, now=None):
        self.send = send
        self.now = now or _now
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        n = self.now()
        if n - self.last < CHECK_SECONDS:
            return False
        self.last = n
        changed = merge(state, n)
        for src, rule in RULES.items():
            r = (state.get("_fresh") or {}).get(src)
            if not r:
                continue
            why = stale(state, src, n)
            if why and not r.get("alerted"):
                r["alerted"] = True
                changed = True
                log.info("Freshness: source went stale")
                self.send(f"⏳⚠️ <b>{html.escape(src)}: {rule.get('head', 'serving OLD data')}</b> — {why}\n"
                          f"Effect: {rule['fix']}. Nothing to do unless it lasts days; the ☀️ daily check repeats it.",
                          True)
            elif not why and r.get("alerted"):
                r["alerted"] = False
                changed = True
                log.info("Freshness: source recovered")
                self.send(f"⏳✅ <b>{html.escape(src)}</b>: fresh data again.", True)
        return changed


def line(state):
    """☀️ daily check line."""
    merge(state)
    srcs = [s for s in RULES if (state.get("_fresh") or {}).get(s)]
    if not srcs:
        return ""
    bad_ = [(s, stale(state, s)) for s in srcs]
    bad_ = [(s, w) for s, w in bad_ if w]
    if not bad_:
        return "⏳ Data fresh: " + ", ".join(srcs)
    return "⏳⚠️ <b>Old data</b>: " + " · ".join(f"{html.escape(s)} — {w}" for s, w in bad_)

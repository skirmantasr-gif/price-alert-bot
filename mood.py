"""🌡️ Market mood: is the market in a risk-on, neutral or defensive phase?

Idea from the open-source AI-Trader project (market_intel.py), cut down to what matters for this watchlist
(small caps, biotech, growth): four ETFs, 20 trading days.

    QQQ          big growth stocks             ±3%   -> +1 / -1
    QQQ − XLP    growth vs defensive staples   ±2 pts
    IWM          small caps                    ±3%
    XBI          biotech (moves more)          ±4%

score = sum of the four (−4…+4): ≥ +2 risk-on, ≤ −2 defensive, otherwise neutral.
Used by: the ☀️ daily check (one line), the 🧪 practice statements, and the 🌡️ mood-filtered practice
portfolio (skips buys while the mood is defensive) so the 🧪 results show whether the filter actually helps.
"""
import logging
import time

log = logging.getLogger("bot")

DAYS = 20
REFRESH = 3600
RULES = [  # (key, label, threshold)
    ("qqq", "big tech (QQQ)", 3.0),
    ("spread", "growth vs staples (QQQ−XLP)", 2.0),
    ("iwm", "small caps (IWM)", 3.0),
    ("xbi", "biotech (XBI)", 4.0),
]
SYMBOLS = ("QQQ", "XLP", "IWM", "XBI")
LABEL = {"risk-on": "🟢 risk-on", "neutral": "⚪ neutral", "defensive": "🔴 defensive"}


def ret(closes, days=DAYS):
    """% change over the last `days` trading days (None if not enough data)."""
    c = [x for x in closes or [] if x]
    if len(c) <= days:
        return None
    return (c[-1] / c[-1 - days] - 1) * 100


def compute(closes):
    """closes: {"QQQ": [daily closes, oldest first], ...} -> mood dict, or None if QQQ data is missing."""
    r = {s: ret(closes.get(s)) for s in SYMBOLS}
    if r["QQQ"] is None:
        return None
    vals = {"qqq": r["QQQ"], "iwm": r["IWM"], "xbi": r["XBI"],
            "spread": r["QQQ"] - r["XLP"] if r["XLP"] is not None else None}
    score, parts = 0, []
    for key, label, th in RULES:
        v = vals[key]
        if v is None:
            continue
        s = 1 if v >= th else -1 if v <= -th else 0
        score += s
        parts.append({"k": key, "label": label, "v": round(v, 2), "s": s})
    status = "risk-on" if score >= 2 else "defensive" if score <= -2 else "neutral"
    return {"status": status, "score": score, "parts": parts}


def line(m):
    """One Telegram line, e.g. '🌡️ Market mood: 🔴 defensive (−3) · big tech (QQQ) −4.1% · …'."""
    if not m:
        return "🌡️ Market mood: no data"
    bits = []
    for p in m["parts"]:
        unit = " pts" if p["k"] == "spread" else "%"
        bits.append(f"{p['label']} {p['v']:+.1f}{unit}")
    return (f"🌡️ Market mood: <b>{LABEL[m['status']]}</b> ({m['score']:+d}) · " + " · ".join(bits)
            + f" <i>({DAYS} trading days)</i>")


def _closes(get_json, sym):
    r = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d")
    q = r["chart"]["result"][0]["indicators"]["quote"][0]
    return [c for c in q.get("close") or [] if c]


def get(state, get_json, now=None):
    """Current mood, refreshed at most once an hour and saved in state["_mood"]; old value kept on errors."""
    now = now or time.time()
    st = state.setdefault("_mood", {})
    if st.get("m") and now - st.get("t", 0) < REFRESH:
        return st["m"]
    if now - st.get("tried", 0) < 600:  # failed recently: don't hammer Yahoo
        return st.get("m")
    st["tried"] = now
    closes = {}
    for s in SYMBOLS:
        try:
            closes[s] = _closes(get_json, s)
        except Exception as e:
            log.warning("Market mood: %s prices failed (%s)", s, type(e).__name__)
    m = compute(closes)
    if m:
        st.update(m=m, t=now)
    return st.get("m")


def status(state):
    """Last known status ('risk-on' / 'neutral' / 'defensive') or None — no network."""
    return ((state.get("_mood") or {}).get("m") or {}).get("status")

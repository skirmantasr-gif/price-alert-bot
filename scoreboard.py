"""
📊 Scoreboard: does each KIND of alert point to money, compared with simply holding the S&P 500?

Every alert the bot sends that names a stock and a direction is noted here with the price at that moment and the
S&P 500 (SPY) price at that moment. Later the bot fills in what both did:
  r1 / x1  stock move / move minus SPY's move, from the alert to the first close after it
  r5 / x5  the same to the 5th close after the alert
Moves are signed by the alert's direction (an UP alert that falls counts as a loss), so "x5 > 0" means
following the alert beat the S&P over the same days. Kinds without a call of their own (price moves, volume
spikes) use the direction of the move: they test "does the move continue?".

Rules, fixed BEFORE looking at any results (2026-11-15) — do not tune them on the same data they judge:
  * a kind needs MIN_N alerts with a 5-day result before it gets a verdict
  * ✅ possible edge : average x5 >= +1.0 % AND beat the S&P (x5 > 0) in >= 55 % of cases
  * ❌ loses         : average x5 <= -1.0 %
  * ➖ no edge       : everything else (noise; fine to mute)
  * a ✅ only counts once it repeats on the NEXT MIN_N alerts (shown as "confirm"): with ~12 kinds, one or two
    will look good by luck alone.
Results are posted every Sunday (SCORE_TIME, owner's time zone) to the main channel. Same stock + same kind is
counted once per day. Kept for KEEP_DAYS. Logs never contain tickers (the repo is public).
"""

import html
import logging
import os
import time
from datetime import datetime

import extras

log = logging.getLogger("bot")

ON = os.environ.get("SCOREBOARD", "true").lower() == "true"
SCORE_TIME = os.environ.get("SCORE_TIME", "10:05")
KEEP_DAYS = 70
MIN_N = 20
EDGE = 1.0          # % average excess return over 5 closes
HIT = 55            # % of alerts beating the S&P
MAX_EV = 4000
BENCH = "SPY"

NAMES = {
    "news": "📰 News (AI/rule call)", "sec": "📄 SEC filings", "gov": "🏛️ US federal contracts",
    "contract": "🏛️ Contract-win channel", "insider": "👥 Insider cluster buys", "salesignal": "📣 Sale signals",
    "premarket": "🌅 Pre-market movers", "outlook": "🧭 Hourly outlook", "price": "📈 Price-move alerts (±step)",
    "volume": "📊 Unusual volume", "bo_takeover": "🎯 Takeover-type news", "bo_major": "📰 Buyout-list major news",
    "bo_spike": "🎯⚡ Buyout-list unusual moves",
}

STATE = None        # set by attach(); alerts are noted without passing the state around
PRICE = None        # PRICE(ticker) -> latest price or None (set by bot.py)
_spy = [0.0, None]  # (time, price) cache


def attach(state, price_fn):
    global STATE, PRICE
    STATE, PRICE = state, price_fn


def _sb(state):
    return state.setdefault("_sb", {})


def _spy_now():
    if time.time() - _spy[0] < 120 and _spy[1]:
        return _spy[1]
    try:
        p = PRICE(BENCH) if PRICE else None
    except Exception:
        p = None
    if p:
        _spy[:] = [time.time(), p]
    return p


def note(kind, ticker, direction, price=None, state=None, now=None):
    """Remember one alert. direction 'up' or 'down'. Never raises (scoring must not break alerts)."""
    try:
        state = state if state is not None else STATE
        if not ON or state is None or direction not in ("up", "down") or not ticker:
            return False
        now = now or time.time()
        if price is None and PRICE:
            price = PRICE(ticker)
        if not price:
            return False
        sb = _sb(state)
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        key = f"{day}|{kind}|{ticker}"
        seen = sb.setdefault("seen", {})
        if key in seen:
            return False
        seen[key] = 1
        for k in [k for k in seen if not k.startswith(day)]:
            del seen[k]
        ev = sb.setdefault("ev", [])
        ev.append({"t": int(now), "k": kind, "tk": ticker, "d": direction, "p0": round(float(price), 4),
                   "b0": _spy_now()})
        cut = now - KEEP_DAYS * 86400
        ev[:] = [e for e in ev if e["t"] >= cut][-MAX_EV:]
        return True
    except Exception as e:   # pragma: no cover
        log.warning("Scoreboard note failed: %s", type(e).__name__)
        return False


# ---------------------------------------------------------------- evaluation
def _after(closes, ts, tz, close_hm):
    """Closes strictly after the alert moment (same-day close counts if the alert came before it)."""
    dt = datetime.fromtimestamp(ts, tz)
    day = dt.strftime("%Y-%m-%d")
    after_close = (dt.hour, dt.minute) >= close_hm
    return [(d, c) for d, c in closes if d > day or (d == day and not after_close)]


def _bench_on(bclose, d):
    """SPY close on date d, or the last one before it (other exchanges trade on other days)."""
    best = None
    for bd, c in bclose:
        if bd <= d:
            best = c
        else:
            break
    return best


def evaluate(state, closes_fn, now=None):
    """closes_fn(ticker) -> (closes [(date, close)], tz, (close h, m)) — learn._closes. Returns # filled."""
    ev = [e for e in _sb(state).get("ev", []) if "x5" not in e]
    if not ev:
        return 0
    try:
        bclose = closes_fn(BENCH)[0]
    except Exception as e:
        log.warning("Scoreboard: benchmark history failed (%s)", type(e).__name__)
        return 0
    by_t, done = {}, 0
    for e in ev:
        by_t.setdefault(e["tk"], []).append(e)
    for tk, es in by_t.items():
        try:
            closes, tz, close_hm = closes_fn(tk)
        except Exception as ex:
            log.warning("Scoreboard: price history failed (%s)", type(ex).__name__)
            continue
        for e in es:
            later = _after(closes, e["t"], tz, close_hm)
            sign = 1 if e["d"] == "up" else -1
            for n, rk, xk in ((1, "r1", "x1"), (5, "r5", "x5")):
                if xk in e or len(later) < n:
                    continue
                d, c = later[n - 1]
                r = (c / e["p0"] - 1) * 100
                b = _bench_on(bclose, d)
                b0 = e.get("b0")
                if b is None or not b0:
                    continue
                e[rk] = round(sign * r, 2)
                e[xk] = round(sign * (r - (b / b0 - 1) * 100), 2)
                done += 1
    return done


# ---------------------------------------------------------------- verdicts + report
def verdict(xs):
    """xs: excess returns (5 closes) of one kind, in time order. -> (label, detail)."""
    n = len(xs)
    if n < MIN_N:
        return "⏳", f"too few ({n}/{MIN_N})"
    def good(v):
        return sum(v) / len(v) >= EDGE and sum(x > 0 for x in v) / len(v) * 100 >= HIT

    avg = sum(xs) / n
    if n >= 2 * MIN_N and good(xs[:MIN_N]):     # discovery batch was good: does it repeat on the new alerts?
        if good(xs[MIN_N:]):
            return "✅", "edge confirmed on new alerts"
        return "⚠️", "first batch looked good, newer alerts did not repeat it"
    if good(xs):
        return "✅?", f"possible edge — confirm on the next {MIN_N}"
    if avg <= -EDGE:
        return "❌", "loses vs the S&P"
    return "➖", "no edge (noise)"


def rows(state):
    ev = _sb(state).get("ev", [])
    out = []
    for k in sorted({e["k"] for e in ev}, key=lambda k: list(NAMES).index(k) if k in NAMES else 99):
        es = sorted((e for e in ev if e["k"] == k), key=lambda e: e["t"])
        x1 = [e["x1"] for e in es if "x1" in e]
        x5 = [e["x5"] for e in es if "x5" in e]
        lab, why = verdict(x5)
        out.append({"k": k, "n": len(es), "n5": len(x5),
                    "x1": round(sum(x1) / len(x1), 2) if x1 else None,
                    "x5": round(sum(x5) / len(x5), 2) if x5 else None,
                    "hit": round(sum(x > 0 for x in x5) / len(x5) * 100) if x5 else None,
                    "v": lab, "why": why})
    return out


def _pct(v):
    return "  n/a" if v is None else f"{v:+.1f}%"


def message(state, now=None):
    rs = rows(state)
    lines = [f"📊 <b>Scoreboard</b> — does each kind of alert beat simply holding the S&amp;P 500? (last {KEEP_DAYS} days)",
             "<i>vs S&amp;P = the stock's move in the alert's direction minus the S&amp;P's move over the same days.</i>", ""]
    if not rs:
        return "\n".join(lines + ["No alerts recorded yet."])
    for r in rs:
        name = NAMES.get(r["k"], r["k"])
        beat = "  –" if r["hit"] is None else f"{r['hit']:>3}%"
        lines.append(f"{r['v']} <b>{html.escape(name)}</b>: {r['n']} alerts · vs S&amp;P 1 day {_pct(r['x1'])}, "
                     f"5 days {_pct(r['x5'])} · beat it {beat.strip()} — <i>{html.escape(r['why'])}</i>")
    lines += ["", f"<i>Rules fixed in advance: verdict after {MIN_N} results; ✅ needs ≥ +{EDGE:g}% vs S&amp;P on "
                  f"average and beating it ≥ {HIT}% of the time, then the same again on the next {MIN_N}. "
                  "➖/❌ kinds are candidates to mute. Not advice.</i>"]
    return "\n".join(lines)


class Board:
    def __init__(self, closes_fn, send):
        self.closes_fn = closes_fn
        self.send = send
        self.last_eval = 0

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        changed = False
        if time.time() - self.last_eval >= 3 * 3600:
            self.last_eval = time.time()
            changed |= evaluate(state, self.closes_fn) > 0
        loc = extras.local_now()
        week = loc.strftime("%G-%V")
        sb = _sb(state)
        if loc.weekday() == 6 and loc.strftime("%H:%M") >= SCORE_TIME and sb.get("week") != week:
            sb["week"] = week
            evaluate(state, self.closes_fn)
            self.send(message(state), True)
            log.info("Scoreboard sent")
            changed = True
        return changed

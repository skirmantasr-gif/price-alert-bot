"""
🧪 Practice portfolio: pretend €100 that "trades" the bot's own UP calls, to see which alert types make money.

Portfolios (each starts with €100, run side by side on the same alerts):
  strong   💪 "Expected to go UP" calls (high confidence) + 🚨 contract wins
  likely   👍 "Likely UP" or stronger (medium + high), news / SEC / contracts
  outlook  🧭 hourly outlook "May go UP" calls (medium + high)
  + benchmark: the same €100 in the S&P 500 (SPY) from the start date

Rules (same for all):
  buy €10 per signal (max 10 positions incl. waiting orders, one per stock; skipped when cash runs out)
  REAL PRICES ONLY: an order never fills at the alert's own price. It fills at the first regular-session
  5-minute bar that starts at/after the decision (its open) on the stock's own exchange — so an alert outside
  market hours (night, pre-market, after-hours, weekend, holiday) waits for the next opening price. Cash for a
  waiting order is reserved.
  sell after 3 trading days at that day's closing price (a market-on-close order), at -8% (stop) or +15%
  (take profit) judged on regular-session prices only and sold at the next price, or when a DOWN call
  (medium+) comes (next available price; a waiting buy order is cancelled instead)
  fee 0.15% on every buy and sell (typical currency-exchange cost at Trading 212 / Revolut); currency moves
  between EUR and the stock's currency are ignored (returns are the stock's own % move)

Signals come from learn.record() (every up/down call the bot sends, with the price at that moment).
Own channel (PAPER_CHAT_ID): while the US market is open, one message per hour (10:00…16:00 New York, with
sound) listing the pretend trades of that hour — sent only when something was bought or sold; trades made
outside market hours go into the next session's first hourly message. Plus the daily report. Without the
channel only the daily report goes to the main channel. Daily quiet 🧪 report after the New York close on weekdays.
State: `_paper` in the encrypted state.
Simulation only — not advice; real fills, spreads and pre/after-hours prices can differ.
"""

import html
import logging
import os
import time
from datetime import datetime, timedelta

log = logging.getLogger("bot")

ON = os.environ.get("PAPER", "true").lower() == "true"
START_EUR = 100.0
SIZE_EUR = 10.0
MAX_OPEN = 10
FEE = 0.0015
HOLD_DAYS = 3
STOP = -0.08
TAKE = 0.15
CHECK_SECONDS = 1800      # exit checks (stop / take profit / holding time)
REPORT_NY = "16:30"       # daily report time, New York (after the close)
BENCH = "SPY"

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = None

PORTFOLIOS = {
    "strong": ("💪 Strong calls", lambda s: s["d"] == "up" and s["c"] == "high" and s["src"] != "outlook"),
    "likely": ("👍 Likely+ calls", lambda s: s["d"] == "up" and s["c"] in ("medium", "high") and s["src"] != "outlook"),
    "outlook": ("🧭 Hourly outlook", lambda s: s["d"] == "up" and s["c"] in ("medium", "high") and s["src"] == "outlook"),
}
SRC = {"news": "news", "sec": "SEC", "gov": "US contract", "contract": "contract win", "outlook": "outlook"}
CONF = {"high": "Expected UP", "medium": "Likely UP", "low": "Possibly UP"}


# ---------------------------------------------------------------- engine (pure; used live and in backtests)
# Market data comes from data(tk) -> {"bars": [(ts, open, close, day), ...] regular-session 5-minute bars only,
#                                     "open_end": end of the session in progress (or None)}
# Orders never fill at an alert's own price: they fill at the first regular-session bar that starts at or after
# the decision (its open) — outside market hours that is the next session's opening price.
VERSION = 2  # 2 = real-price fills; older saved portfolios (filled at alert prices) are restarted once


def new_state(now):
    return {"v": VERSION, "since": int(now), "pf": {k: _pf() for k in PORTFOLIOS}}


def _pf():
    return {"cash": START_EUR, "open": {}, "pending": {}, "closed": [], "log": []}


def _ny(ts):
    return datetime.fromtimestamp(ts, NY)


def _log(pf, now, **e):
    pf["log"].append({"t": int(now), **e})
    pf["log"] = pf["log"][-80:]


def _first_bar(bars, t):
    for b in bars:
        if b[0] >= t:
            return b
    return None


def _sell(pf, tk, price, at, now, why):
    pos = pf["open"].pop(tk)
    ret = price / pos["p0"] - 1
    eur = pos["eur"] * (1 + ret) * (1 - FEE)
    pf["cash"] += eur
    pf["closed"].append({"tk": tk, "ret": round(ret, 4), "eur": round(eur - SIZE_EUR, 3), "t0": pos["t"],
                         "t": int(at), "why": why, "src": pos["src"]})
    pf["closed"] = pf["closed"][-300:]
    _log(pf, now, a="sell", tk=tk, ret=round(ret, 4), why=why, p=price, at=int(at))


def on_signal(st, sig, now=None):
    """sig: {"tk","src","d","c","p0"} (learn.record format). Places orders in every portfolio; they fill in step()."""
    now = now or time.time()
    for key, (_, rule) in PORTFOLIOS.items():
        pf = st["pf"].setdefault(key, _pf())
        pf.setdefault("pending", {})
        tk = sig["tk"]
        if sig["d"] == "down" and sig["c"] in ("medium", "high"):
            if tk in pf["open"] and "exit" not in pf["open"][tk]:
                pf["open"][tk]["exit"] = {"t": int(now), "why": "DOWN call"}
            elif tk in pf["pending"]:
                pf["pending"].pop(tk)
                pf["cash"] += SIZE_EUR
                _log(pf, now, a="cancel", tk=tk, why="DOWN call before the market opened")
        elif (rule(sig) and tk not in pf["open"] and tk not in pf["pending"]
              and len(pf["open"]) + len(pf["pending"]) < MAX_OPEN and pf["cash"] >= SIZE_EUR - 1e-9):
            pf["cash"] -= SIZE_EUR  # reserved until the order fills
            pf["pending"][tk] = {"t": int(now), "src": sig["src"], "c": sig["c"], "alert_p": sig.get("p0")}
            _log(pf, now, a="order", tk=tk, src=sig["src"], c=sig["c"])


def step(st, now, data):
    """Fill waiting orders and apply exits, using only regular-session prices up to `now`."""
    for pf in st["pf"].values():
        pf.setdefault("pending", {})
        for tk, o in list(pf["pending"].items()):
            d = data(tk)
            bars = [b for b in (d or {}).get("bars", []) if b[0] <= now]
            b = _first_bar(bars, o["t"])
            if b:
                pf["pending"].pop(tk)
                pf["open"][tk] = {"p0": b[1], "eur": SIZE_EUR * (1 - FEE), "t": int(b[0]), "day": b[3],
                                  "src": o["src"], "c": o["c"]}
                _log(pf, now, a="buy", tk=tk, p=b[1], at=int(b[0]), src=o["src"], c=o["c"],
                     late=b[0] - o["t"] > 900)
            elif now - o["t"] > 7 * 86400:
                pf["pending"].pop(tk)
                pf["cash"] += SIZE_EUR
                _log(pf, now, a="cancel", tk=tk, why="no trading for 7 days")
        for tk, pos in list(pf["open"].items()):
            d = data(tk)
            if not d:
                continue
            bars = [b for b in d.get("bars", []) if pos["t"] < b[0] <= now]
            if "exit" in pos:  # decided earlier (DOWN call / stop / target): next available price
                b = _first_bar(bars, pos["exit"]["t"])
                if b:
                    _sell(pf, tk, b[1], b[0], now, pos["exit"]["why"])
                continue
            days, done = [], False
            for i, b in enumerate(bars):
                if b[3] != pos["day"] and (not days or days[-1] != b[3]):
                    days.append(b[3])
                ret = b[2] / pos["p0"] - 1
                if ret <= STOP or ret >= TAKE:
                    why = "stop −8%" if ret <= STOP else "take profit +15%"
                    nxt = bars[i + 1] if i + 1 < len(bars) else None
                    if nxt:
                        _sell(pf, tk, nxt[1], nxt[0], now, why)
                    else:
                        pos["exit"] = {"t": int(b[0]) + 1, "why": why}
                    done = True
                    break
                if i + 1 < len(bars):
                    last_of_day = bars[i + 1][3] != b[3]
                else:  # newest bar: is its session over? (live: session end known; history: no newer bars for 20+ min)
                    end = d.get("open_end")
                    if d.get("live"):
                        last_of_day = end is None or now >= end
                    else:
                        last_of_day = now - b[0] >= 1200
                if len(days) >= HOLD_DAYS and last_of_day:
                    _sell(pf, tk, b[2], b[0] + 300, now, f"{HOLD_DAYS} days held (sold at the close)")
                    done = True
                    break


def _last(data, tk, now):
    d = data(tk) or {}
    bars = [b for b in d.get("bars", []) if b[0] <= now]
    return bars[-1][2] if bars else None


def value(pf, now, data):
    v = pf["cash"] + SIZE_EUR * len(pf.get("pending", {}))
    for tk, pos in pf["open"].items():
        p = _last(data, tk, now) or pos["p0"]
        v += pos["eur"] * p / pos["p0"]
    return v


def _bench(st, now, data):
    """€ value of the S&P 500 benchmark: bought at the first regular-session price after the start."""
    if not st.get("bench_p0"):
        d = data(BENCH) or {}
        b = _first_bar([x for x in d.get("bars", []) if x[0] <= now], st["since"])
        if not b:
            return None
        st["bench_p0"] = b[1]
    p = _last(data, BENCH, now)
    return START_EUR * (1 - FEE) * p / st["bench_p0"] if p else None


def _when(e):
    at = e.get("at")
    return datetime.fromtimestamp(at, NY).strftime(" (%a %H:%M NY)") if at else ""


def _line(e, names):
    tk = html.escape(e["tk"])
    if e["a"] == "order":
        return (f"⏳ BUY order <b>${tk}</b> €{SIZE_EUR:.0f} — {SRC.get(e['src'], e['src'])}, {CONF.get(e['c'], e['c'])}; "
                f"market closed, fills at the next opening price {names}")
    if e["a"] == "buy":
        return (f"🟢 Bought <b>${tk}</b> €{SIZE_EUR:.0f} at {e['p']:,.4g}{_when(e)}"
                f"{' — next opening price' if e.get('late') else ''} ({SRC.get(e['src'], e['src'])}, "
                f"{CONF.get(e['c'], e['c'])}) {names}")
    if e["a"] == "cancel":
        return f"✖️ Order cancelled <b>${tk}</b> ({html.escape(e['why'])}) {names}"
    return (f"{'✅' if e['ret'] > 0 else '❌'} Sold <b>${tk}</b> {e['ret'] * 100:+.1f}% at {e['p']:,.4g}{_when(e)} "
            f"({html.escape(e['why'])}) {names}")


def trade_lines(st, since, short=True):
    """Trades recorded after `since`, merged across portfolios: [(t, text)]."""
    trades = {}
    for key, (name, _) in PORTFOLIOS.items():
        for e in st["pf"][key]["log"]:
            if e["t"] > since:
                k = (e["t"] // 60, e["a"], e["tk"], round(e.get("ret", 0), 3))
                trades.setdefault(k, [e, []])[1].append(name.split()[0] if short else name)
    filled = {e["tk"] for e, _ in trades.values() if e["a"] in ("buy", "cancel")}
    return [(e["t"], _line(e, "".join(n) if short else "· " + " · ".join(n))) for k, (e, n) in sorted(trades.items())
            if not (e["a"] == "order" and e["tk"] in filled)]  # order already filled/cancelled: show only that


def report(st, now, data, title):
    d0 = _ny(st["since"]).strftime("%b %d")
    lines = [f"🧪 <b>{html.escape(title)}</b>", f"<i>Pretend €100 each, started {d0}. Simulation, not advice.</i>"]
    for key, (name, _) in PORTFOLIOS.items():
        pf = st["pf"][key]
        v = value(pf, now, data)
        won = sum(1 for c in pf["closed"] if c["ret"] > 0)
        wait = len(pf.get("pending", {}))
        lines.append(f"{name}: <b>€{v:.2f}</b> ({(v / START_EUR - 1) * 100:+.1f}%) · {len(pf['open'])} open"
                     + (f" · {wait} waiting for the open" if wait else "") + f" · {len(pf['closed'])} closed"
                     + (f" ({won} won)" if pf["closed"] else ""))
    bv = _bench(st, now, data)
    if bv:
        lines.append(f"📊 S&P 500 (SPY): €{bv:.2f} ({(bv / START_EUR - 1) * 100:+.1f}%) — the number to beat")
    tl = trade_lines(st, now - 86400)
    if tl:
        lines.append("<b>Last 24h:</b>")
        lines += [x for _, x in tl[:30]] + ([f"… and {len(tl) - 30} more"] if len(tl) > 30 else [])
    lines.append(f"<i>Rules: €{SIZE_EUR:.0f} per UP call, max {MAX_OPEN} open; orders fill only at real market prices "
                 f"(outside market hours: the next opening price); sell after {HOLD_DAYS} trading days at the close, "
                 f"at −8% / +15%, or on a DOWN call; 0.15% fee each way; currency moves ignored.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- live tracker
_state_ref = None


def record(sig):
    """Called by learn.record for every saved up/down call."""
    if not ON or _state_ref is None:
        return
    try:
        on_signal(_state_ref, sig)
    except Exception as e:
        log.warning("Practice portfolio signal failed: %s", type(e).__name__)


def parse_chart(r):
    """Yahoo chart result (regular session only) -> data dict for the engine."""
    from zoneinfo import ZoneInfo
    meta = r.get("meta") or {}
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    bars = [(t, o, c, datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d"))
            for t, o, c in zip(r.get("timestamp") or [], q.get("open") or [], q.get("close") or []) if o and c]
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    now = time.time()
    open_end = reg.get("end") if reg.get("start", 0) <= now < reg.get("end", 0) else None
    return {"bars": bars, "open_end": open_end, "live": True}


class Tracker:
    def __init__(self, get_json, send, live_trades=False):
        self.get_json = get_json
        self.send = send
        self.live_trades = live_trades  # own channel: hourly trade updates while the market is open
        self.last = 0.0
        self.cache = {}

    @staticmethod
    def enabled():
        return ON and NY is not None

    def attach(self, state):
        global _state_ref
        if state.get("_paper", {}).get("v") != VERSION:
            state["_paper"] = new_state(time.time())
        _state_ref = state["_paper"]

    def data(self, tk):
        hit = self.cache.get(tk)
        if hit and time.time() - hit[0] < 240:
            return hit[1]
        try:
            r = self.get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=5d&interval=5m")
            d = parse_chart(r["chart"]["result"][0])
        except Exception:
            d = None
        self.cache[tk] = (time.time(), d)
        return d

    def check(self, state):
        self.attach(state)
        st = state["_paper"]
        now = time.time()
        changed = False
        busy = any(pf["open"] or pf.get("pending") for pf in st["pf"].values())
        if busy and now - self.last >= (300 if any(pf.get("pending") for pf in st["pf"].values()) else CHECK_SECONDS):
            self.last = now
            step(st, now, self.data)
            changed = True
        et = _ny(now)
        if self.live_trades and et.weekday() < 5 and 10 <= et.hour <= 16:
            slot = et.strftime("%Y-%m-%d %H")
            if st.get("hour_slot") != slot:
                st["hour_slot"] = slot
                changed = True
                step(st, now, self.data)
                new = trade_lines(st, st.get("posted", st["since"]), short=False)
                if new:
                    head = (f"🧪 <b>Practice portfolio · {et.strftime('%H:00')} New York</b> — "
                            f"{len(new)} update{'s' if len(new) > 1 else ''} since the last message")
                    body = [x for _, x in new[:15]] + ([f"… and {len(new) - 15} more"] if len(new) > 15 else [])
                    vals = " · ".join(f"{name.split()[0]} €{value(st['pf'][k], now, self.data):.2f}"
                                      for k, (name, _) in PORTFOLIOS.items())
                    if self.send(head + "\n\n" + "\n".join(body) + "\n\nNow: " + vals, False):
                        st["posted"] = new[-1][0]
        day = et.strftime("%Y-%m-%d")
        if et.weekday() < 5 and et.strftime("%H:%M") >= REPORT_NY and st.get("rep") != day:
            st["rep"] = day
            step(st, now, self.data)
            self.send(report(st, now, self.data, f"Practice portfolio · {et.strftime('%a %b %d')}"), True)
            changed = True
        return changed

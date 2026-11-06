"""
🧪 Practice portfolio: pretend €100 that "trades" the bot's own UP calls, to see which alert types make money.

Portfolios (each starts with €100, run side by side on the same alerts):
  strong   💪 "Expected to go UP" calls (high confidence) + 🚨 contract wins
  likely   👍 "Likely UP" or stronger (medium + high), news / SEC / contracts
  outlook  🧭 hourly outlook "May go UP" calls (medium + high)
  + benchmark: the same €100 in the S&P 500 (SPY) from the start date

Rules (same for all):
  buy €10 per signal (max 10 positions, one per stock; skipped when cash runs out)
  sell after 3 trading days (at/after that day's close), at -8% (stop), at +15% (take profit),
  or when a DOWN call (medium+) comes for a stock it holds
  fee 0.15% on every buy and sell (typical currency-exchange cost at Trading 212 / Revolut); currency moves
  between EUR and the stock's currency are ignored (returns are the stock's own % move)

Signals come from learn.record() (every up/down call the bot sends, with the price at that moment).
Own channel (PAPER_CHAT_ID): a quiet message for every pretend trade + the daily report; without it only the
daily report goes to the main channel. Daily quiet 🧪 report after the New York close on weekdays.
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
def new_state(now):
    return {"since": int(now), "pf": {k: {"cash": START_EUR, "open": {}, "closed": [], "log": []} for k in PORTFOLIOS}}


def _ny(ts):
    return datetime.fromtimestamp(ts, NY)


def _trading_days_after(t0, now):
    """Weekdays after the entry day up to and including today (New York dates; holidays ignored)."""
    d0, d1 = _ny(t0).date(), _ny(now).date()
    n, d = 0, d0 + timedelta(days=1)
    while d <= d1:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def _close(pf, tk, price, now, why):
    pos = pf["open"].pop(tk)
    ret = price / pos["p0"] - 1
    eur = pos["eur"] * (1 + ret) * (1 - FEE)
    pf["cash"] += eur
    pf["closed"].append({"tk": tk, "ret": round(ret, 4), "eur": round(eur - SIZE_EUR, 3), "t0": pos["t"],
                         "t": int(now), "why": why, "src": pos["src"]})
    pf["closed"] = pf["closed"][-300:]
    pf["log"].append({"t": int(now), "a": "sell", "tk": tk, "ret": round(ret, 4), "why": why})
    pf["log"] = pf["log"][-60:]


def on_signal(st, sig, now=None):
    """sig: {"tk","src","d","c","p0"} (learn.record format). Opens / closes positions in every portfolio."""
    now = now or time.time()
    for key, (_, rule) in PORTFOLIOS.items():
        pf = st["pf"].setdefault(key, {"cash": START_EUR, "open": {}, "closed": [], "log": []})
        tk = sig["tk"]
        if sig["d"] == "down" and sig["c"] in ("medium", "high") and tk in pf["open"]:
            _close(pf, tk, sig["p0"], now, "DOWN call")
        elif rule(sig) and tk not in pf["open"] and len(pf["open"]) < MAX_OPEN and pf["cash"] >= SIZE_EUR - 1e-9:
            pf["cash"] -= SIZE_EUR
            pf["open"][tk] = {"p0": sig["p0"], "eur": SIZE_EUR * (1 - FEE), "t": int(now), "src": sig["src"],
                              "c": sig["c"]}
            pf["log"].append({"t": int(now), "a": "buy", "tk": tk, "p": sig["p0"], "src": sig["src"], "c": sig["c"]})
            pf["log"] = pf["log"][-60:]


def step(st, now, price):
    """Exit checks. price(tk, ts) -> latest price at ts or None."""
    for pf in st["pf"].values():
        for tk, pos in list(pf["open"].items()):
            p = price(tk, now)
            if not p:
                continue
            ret = p / pos["p0"] - 1
            days = _trading_days_after(pos["t"], now)
            if ret <= STOP:
                _close(pf, tk, p, now, "stop −8%")
            elif ret >= TAKE:
                _close(pf, tk, p, now, "take profit +15%")
            elif days > HOLD_DAYS or (days == HOLD_DAYS and _ny(now).strftime("%H:%M") >= "16:00"):
                _close(pf, tk, p, now, f"{HOLD_DAYS} days held")


def value(pf, now, price):
    v = pf["cash"]
    for tk, pos in pf["open"].items():
        p = price(tk, now) or pos["p0"]
        v += pos["eur"] * p / pos["p0"]
    return v


def report(st, now, price, title):
    d0 = _ny(st["since"]).strftime("%b %d")
    lines = [f"🧪 <b>{html.escape(title)}</b>", f"<i>Pretend €100 each, started {d0}. Simulation, not advice.</i>"]
    for key, (name, _) in PORTFOLIOS.items():
        pf = st["pf"][key]
        v = value(pf, now, price)
        won = sum(1 for c in pf["closed"] if c["ret"] > 0)
        lines.append(f"{name}: <b>€{v:.2f}</b> ({(v / START_EUR - 1) * 100:+.1f}%) · {len(pf['open'])} open · "
                     f"{len(pf['closed'])} closed" + (f" ({won} won)" if pf["closed"] else ""))
    b0 = st.get("bench_p0")
    bp = price(BENCH, now) if b0 else None
    if b0 and bp:
        bv = START_EUR * (1 - FEE) * bp / b0
        lines.append(f"📊 S&P 500 (SPY): €{bv:.2f} ({(bv / START_EUR - 1) * 100:+.1f}%) — the number to beat")
    day0 = now - 86400
    trades = {}
    for key, (name, _) in PORTFOLIOS.items():
        for e in st["pf"][key]["log"]:
            if e["t"] >= day0:
                k = (e["t"] // 60, e["a"], e["tk"], round(e.get("ret", 0), 3))
                trades.setdefault(k, [e, []])[1].append(name.split()[0])
    if trades:
        lines.append("<b>Last 24h:</b>")
    for k in sorted(trades):
        e, tags = trades[k]
        if e["a"] == "buy":
            lines.append(f"🟢 BUY <b>${html.escape(e['tk'])}</b> €{SIZE_EUR:.0f} at {e['p']:,.4g} "
                         f"({SRC.get(e['src'], e['src'])}, {CONF.get(e['c'], e['c'])}) {''.join(tags)}")
        else:
            lines.append(f"{'✅' if e['ret'] > 0 else '❌'} SELL <b>${html.escape(e['tk'])}</b> {e['ret'] * 100:+.1f}% "
                         f"({html.escape(e['why'])}) {''.join(tags)}")
    if len(lines) > 40:
        lines = lines[:40] + [f"… and {len(lines) - 40} more trades"]
    lines.append(f"<i>Rules: €{SIZE_EUR:.0f} per UP call, max {MAX_OPEN} open; sell after {HOLD_DAYS} trading days, "
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


def trade_lines(st, since):
    """New trades after `since`, merged across portfolios: [(t, text)]."""
    trades = {}
    for key, (name, _) in PORTFOLIOS.items():
        for e in st["pf"][key]["log"]:
            if e["t"] > since:
                k = (e["t"] // 60, e["a"], e["tk"], round(e.get("ret", 0), 3))
                trades.setdefault(k, [e, []])[1].append(name)
    out = []
    for k in sorted(trades):
        e, names = trades[k]
        who = " · ".join(names)
        if e["a"] == "buy":
            out.append((e["t"], f"🧪🟢 Pretend BUY <b>${html.escape(e['tk'])}</b> €{SIZE_EUR:.0f} at {e['p']:,.4g}\n"
                                f"Why: {SRC.get(e['src'], e['src'])} — {CONF.get(e['c'], e['c'])}\nIn: {who}"))
        else:
            out.append((e["t"], f"🧪{'✅' if e['ret'] > 0 else '❌'} Pretend SELL <b>${html.escape(e['tk'])}</b> "
                                f"{e['ret'] * 100:+.1f}%\nWhy: {html.escape(e['why'])}\nIn: {who}"))
    return out


class Tracker:
    def __init__(self, get_json, send, live_trades=False):
        self.get_json = get_json
        self.send = send
        self.live_trades = live_trades  # own channel: post every trade as it happens
        self.last = 0.0
        self.cache = {}

    @staticmethod
    def enabled():
        return ON and NY is not None

    def attach(self, state):
        global _state_ref
        if "_paper" not in state:
            state["_paper"] = new_state(time.time())
        _state_ref = state["_paper"]

    def price(self, tk, now=None):
        hit = self.cache.get(tk)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        try:
            r = self.get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=1d&interval=5m")
            p = float(r["chart"]["result"][0]["meta"]["regularMarketPrice"])
        except Exception:
            p = None
        self.cache[tk] = (time.time(), p)
        return p

    def check(self, state):
        self.attach(state)
        st = state["_paper"]
        now = time.time()
        changed = False
        if not st.get("bench_p0"):
            p = self.price(BENCH)
            if p:
                st["bench_p0"] = p
                changed = True
        if now - self.last >= CHECK_SECONDS and any(pf["open"] for pf in st["pf"].values()):
            self.last = now
            step(st, now, self.price)
            changed = True
        if self.live_trades:
            new = trade_lines(st, st.get("posted", st["since"]))
            for t, text in new[:10]:
                if not self.send(text, True):
                    break
                st["posted"] = t
                changed = True
            if len(new) > 10:
                st["posted"] = new[-1][0]  # flood guard: the daily report lists the rest
        et = _ny(now)
        day = et.strftime("%Y-%m-%d")
        if et.weekday() < 5 and et.strftime("%H:%M") >= REPORT_NY and st.get("rep") != day:
            st["rep"] = day
            step(st, now, self.price)
            self.send(report(st, now, self.price, f"Practice portfolio · {et.strftime('%a %b %d')}"), True)
            changed = True
        return changed

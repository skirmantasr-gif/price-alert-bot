"""
🧪 Practice portfolio: pretend €100 that "trades" the bot's own UP calls, to see which alert types make money.

Portfolios (each starts with €100, run side by side on the same alerts):
  strong   💪 "Expected to go UP" calls (high confidence) + 🚨 contract wins
  likely   👍 "Likely UP" or stronger (medium + high), news / SEC / contracts
  outlook  🧭 hourly outlook "May go UP" calls (medium + high)
  + benchmark: the same €100 in the S&P 500 (SPY) from the start date

Rules (same for all):
  max 10 positions incl. waiting orders, one per stock; skipped when cash runs out
  REAL PRICES ONLY: an order never fills at the alert's own price. It fills at the first regular-session
  5-minute bar that starts at/after the decision (its open) on the stock's own exchange — so an alert outside
  market hours (night, pre-market, after-hours, weekend, holiday) waits for the next opening price. Cash for a
  waiting order is reserved.
  LIVE exits, checked every 5 minutes on regular-session 5-minute prices, sold at the next price (any time,
  even 30 minutes after buying):
    • profit lock (trailing stop): once a stock is up +3% or more, it is sold when it falls 2% from its
      highest price since the buy (e.g. up +6% → sold if it drops back to about +4%)
    • take profit at +15%, stop loss at -8%
    • a DOWN call (medium+) for the stock (a waiting buy order is cancelled instead)
    • no time limit: a stock is kept until one of these happens
  buy €15 on "Expected UP" (high confidence) calls, €10 on the others; no chasing: if the fill price would be
  5%+ above the price at the alert, the order is cancelled (the move already happened)
  spread / slippage: a real buy pays the ask and a sell gets the bid, so every fill is moved against us by an
  estimated half-spread from the stock's price and trading volume (US: 0.05% very liquid … 1% penny / illiquid;
  other currencies 0.3%)
  fee 0.15% on every buy and sell (typical currency-exchange cost at Trading 212 / Revolut); currency moves
  between EUR and the stock's currency are ignored (returns are the stock's own % move)

Report numbers: value vs the S&P 500, and per portfolio (once trades are closed) win rate, average trade,
"vs S&P" = each trade's return minus the S&P 500's return over the same days (did the alert beat the market?),
profit factor (gains ÷ losses), max drawdown, and a warning while there are fewer than 30 closed trades.

Signals come from learn.record() (every up/down call the bot sends, with the price at that moment).
Own channel (PAPER_CHAT_ID): a broker-style account statement right after the US open (~09:36 New York) and
every market hour (10:00…16:00 New York): per portfolio the total value, free cash, money reserved for waiting
orders, money in stocks, and each holding (value, profit/loss, buy price → now, high, sell levels). Every buy /
sell is also posted right away (with sound). Statements are silent. Plus the daily report. Without the
channel only the daily report goes to the main channel. Daily quiet 🧪 report after the New York close on weekdays.
State: `_paper` in the encrypted state.
Simulation only — not advice; real fills, spreads and pre/after-hours prices can differ.
"""

import hashlib
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
SIZE_STRONG_EUR = 15.0    # bigger bet on "Expected UP" (high confidence) calls
CHASE = 0.05              # no chasing: skip the buy if the price is already 5%+ above the alert price
STOP = -0.08
TAKE = 0.15
TRAIL_START = 0.03        # profit lock switches on at +3% ...
TRAIL = 0.02              # ... then sell when the price falls 2% from its high since the buy
CHECK_SECONDS = 300       # live exit checks (every 5 minutes, = one price bar)
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


# Trades bought on an alert that later turned out to be wrong: removed once, money back, not counted.
# (id, sha256(ticker)[:16] — the public repo shows no tickers —, from, to [UTC epoch of buy/order], reason)
VOIDS = [
    ("2026-11-09-a", "f5612b5e835a2784", 1794009600, 1794355200,  # Nov 7 00:00 – Nov 11 00:00 UTC
     "wrong alert: the company was BUYING land, not winning a contract"),
]


def _tkh(tk):
    return hashlib.sha256(tk.encode()).hexdigest()[:16]


def apply_voids(state, now=None):
    """Remove voided trades once (open position or waiting order -> cost back to cash; closed -> dropped) and
    their guesses in the learning log. Returns the number of portfolios changed."""
    now = now or time.time()
    st = state.get("_paper")
    done = st.setdefault("voided", []) if st else []
    n = 0
    for vid, h, t0, t1, why in VOIDS:
        if vid in done:
            continue
        tks = set()
        if st:
            for pf in st["pf"].values():
                tks |= set(pf["open"]) | set(pf.get("pending", {})) | {c["tk"] for c in pf["closed"]}
        tks |= {x.get("tk", "") for x in state.get("_learn", {}).get("p", [])}
        tk = next((t for t in tks if _tkh(t) == h), None)
        if tk is None:
            if st:
                done.append(vid)
            continue
        if st:
            for pf in st["pf"].values():
                hit = False
                pos = pf["open"].get(tk)
                if pos and t0 <= pos["t"] <= t1:
                    pf["open"].pop(tk)
                    pf["cash"] += pos.get("cost", SIZE_EUR)
                    hit = True
                o = pf.get("pending", {}).get(tk)
                if o and t0 <= o.get("t", 0) <= t1:
                    pf["pending"].pop(tk)
                    pf["cash"] += o.get("eur", SIZE_EUR)
                    hit = True
                before = len(pf["closed"])
                pf["closed"] = [c for c in pf["closed"] if not (c["tk"] == tk and t0 <= c["t0"] <= t1)]
                hit |= len(pf["closed"]) != before
                if hit:
                    _log(pf, now, a="void", tk=tk, why=why)
                    n += 1
            done.append(vid)
        lp = state.get("_learn", {}).get("p")
        if lp:
            lp[:] = [x for x in lp if not (x.get("tk") == tk and t0 <= x.get("t", 0) <= t1)]
    return n


def new_state(now):
    return {"v": VERSION, "since": int(now), "pf": {k: _pf() for k in PORTFOLIOS}}


def _pf():
    return {"cash": START_EUR, "open": {}, "pending": {}, "closed": [], "log": []}


def _ny(ts):
    return datetime.fromtimestamp(ts, NY)


def _log(pf, now, **e):
    pf["log"].append({"t": int(now), **e})
    pf["log"] = pf["log"][-80:]


def slip(d, price):
    """Estimated half-spread + slippage for one fill (fraction of the price)."""
    if (d or {}).get("cur", "USD") != "USD":
        return 0.003
    dv = (d or {}).get("dollar_vol") or 0
    if price < 1 or dv < 1e6:
        return 0.01
    if price < 5 or dv < 10e6:
        return 0.004
    if dv < 100e6:
        return 0.0015
    return 0.0005


def _price_at(data, tk, ts):
    d = data(tk) or {}
    bars = [b for b in d.get("bars", []) if b[0] <= ts]
    return bars[-1][2] if bars else None


def _first_bar(bars, t):
    for b in bars:
        if b[0] >= t:
            return b
    return None


def _sell(pf, tk, price, at, now, why, data=None):
    pos = pf["open"].pop(tk)
    if data:
        price *= 1 - slip(data(tk), price)
    ret = price / pos["p0"] - 1
    vs = None
    if data and tk != BENCH:
        b0, b1 = _price_at(data, BENCH, pos["t"]), _price_at(data, BENCH, at)
        if b0 and b1:
            vs = round(ret - (b1 / b0 - 1), 4)
    eur = pos["eur"] * (1 + ret) * (1 - FEE)
    pf["cash"] += eur
    pf["closed"].append({"tk": tk, "ret": round(ret, 4), "eur": round(eur - pos.get("cost", SIZE_EUR), 3), "t0": pos["t"],
                         "t": int(at), "why": why, "src": pos["src"], "vs": vs})
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
                o = pf["pending"].pop(tk)
                pf["cash"] += o.get("eur", SIZE_EUR)
                _log(pf, now, a="cancel", tk=tk, why="DOWN call before the order filled")
        else:
            size = SIZE_STRONG_EUR if sig["c"] == "high" else SIZE_EUR
            if (rule(sig) and tk not in pf["open"] and tk not in pf["pending"]
                    and len(pf["open"]) + len(pf["pending"]) < MAX_OPEN and pf["cash"] >= size - 1e-9):
                pf["cash"] -= size  # reserved until the order fills
                pf["pending"][tk] = {"t": int(now), "src": sig["src"], "c": sig["c"], "alert_p": sig.get("p0"), "eur": size}
                _log(pf, now, a="order", tk=tk, src=sig["src"], c=sig["c"], eur=size)


def step(st, now, data):
    """Fill waiting orders and apply exits, using only regular-session prices up to `now`."""
    for pf in st["pf"].values():
        pf.setdefault("pending", {})
        for tk, o in list(pf["pending"].items()):
            d = data(tk)
            bars = [b for b in (d or {}).get("bars", []) if b[0] <= now]
            b = _first_bar(bars, o["t"])
            size = o.get("eur", SIZE_EUR)
            if b:
                pf["pending"].pop(tk)
                fill = b[1] * (1 + slip(d, b[1]))
                ap = o.get("alert_p")
                if ap and fill > ap * (1 + CHASE):
                    pf["cash"] += size
                    _log(pf, now, a="cancel", tk=tk, at=int(b[0]),
                         why=f"price already {(fill / ap - 1) * 100:+.0f}% above the alert price — not chasing it")
                    continue
                pf["open"][tk] = {"p0": fill, "eur": size * (1 - FEE), "cost": size, "t": int(b[0]), "day": b[3],
                                  "src": o["src"], "c": o["c"]}
                _log(pf, now, a="buy", tk=tk, p=fill, at=int(b[0]), src=o["src"], c=o["c"], eur=size,
                     late=b[0] - o["t"] > 900)
            elif now - o["t"] > 7 * 86400:
                pf["pending"].pop(tk)
                pf["cash"] += size
                _log(pf, now, a="cancel", tk=tk, why="no trading for 7 days")
        for tk, pos in list(pf["open"].items()):
            d = data(tk)
            if not d:
                continue
            bars = [b for b in d.get("bars", []) if pos["t"] < b[0] <= now]
            if "exit" in pos:  # decided earlier (DOWN call / stop / target): next available price
                b = _first_bar(bars, pos["exit"]["t"])
                if b:
                    _sell(pf, tk, b[1], b[0], now, pos["exit"]["why"], data)
                continue
            # live exits: only bars not looked at before (the high since the buy is kept in the position)
            peak = pos.get("hi", pos["p0"])
            for i, b in enumerate(bars):
                if b[0] <= pos.get("seen", 0):
                    continue
                ret = b[2] / pos["p0"] - 1
                peak = max(peak, b[2])
                trail = peak / pos["p0"] - 1 >= TRAIL_START and b[2] <= peak * (1 - TRAIL)
                if ret <= STOP or ret >= TAKE or trail:
                    why = ("stop −8%" if ret <= STOP else "take profit +15%" if ret >= TAKE else
                           f"profit lock: was up {(peak / pos['p0'] - 1) * 100:+.1f}%, fell 2% from the high")
                    nxt = bars[i + 1] if i + 1 < len(bars) else None
                    if nxt:
                        _sell(pf, tk, nxt[1], nxt[0], now, why, data)
                    else:
                        pos.update(hi=peak, seen=b[0], exit={"t": int(b[0]) + 1, "why": why})
                    break
                pos["hi"], pos["seen"] = peak, b[0]


def _peak(data, tk, pos, now):
    d = data(tk) or {}
    return max([pos["p0"], pos.get("hi", 0)] + [b[2] for b in d.get("bars", []) if pos["t"] <= b[0] <= now])


def _age(sec):
    h = sec / 3600
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.0f}h" if h < 48 else f"{h / 24:.0f} days"


def _pct(x):
    return f"{x * 100:+.1f}%" if abs(x) >= 0.0005 else "±0.0%"


def _local(ts, fmt="%a %H:%M"):
    """Owner's time zone (watchlist timezone, e.g. Europe/Vilnius → LT)."""
    try:
        import extras
        return extras.fmt_local(datetime.fromtimestamp(ts, NY), fmt)
    except Exception:
        return datetime.fromtimestamp(ts, NY).strftime(fmt) + " NY"


def _last(data, tk, now):
    d = data(tk) or {}
    bars = [b for b in d.get("bars", []) if b[0] <= now]
    return bars[-1][2] if bars else None


def value(pf, now, data):
    v = pf["cash"] + sum(o.get("eur", SIZE_EUR) for o in pf.get("pending", {}).values())
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
    return f" ({_local(at)})" if at else ""


def _line(e, names):
    tk = html.escape(e["tk"])
    if e["a"] == "order":
        return (f"⏳ BUY order <b>${tk}</b> €{e.get('eur', SIZE_EUR):.0f} — {SRC.get(e['src'], e['src'])}, {CONF.get(e['c'], e['c'])}; "
                f"fills at the next real market price (if the market is closed: the next opening price) {names}")
    if e["a"] == "buy":
        return (f"🟢 Bought <b>${tk}</b> €{e.get('eur', SIZE_EUR):.0f} at {e['p']:,.4g}{_when(e)}"
                f"{' — next opening price' if e.get('late') else ''} ({SRC.get(e['src'], e['src'])}, "
                f"{CONF.get(e['c'], e['c'])}) {names}")
    if e["a"] == "void":
        return f"↩️ Trade removed <b>${tk}</b> ({html.escape(e['why'])}) — money back, not counted {names}"
    if e["a"] == "cancel":
        return f"✖️ Order cancelled <b>${tk}</b> ({html.escape(e['why'])}) {names}"
    return (f"{'✅' if e['ret'] > 0 else '❌'} Sold <b>${tk}</b> {_pct(e['ret'])} at {e['p']:,.4g}{_when(e)} "
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


def stats(pf):
    c = pf["closed"]
    if not c:
        return ""
    wins = [x["ret"] for x in c if x["ret"] > 0]
    loss = [x["ret"] for x in c if x["ret"] <= 0]
    pfac = sum(wins) / -sum(loss) if loss and sum(loss) < 0 else None
    vs = [x["vs"] for x in c if x.get("vs") is not None]
    eq = pf.get("eq") or []
    mdd, peak = 0.0, START_EUR
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out = (f"   {len(wins)}/{len(c)} won · avg trade {sum(x['ret'] for x in c) / len(c) * 100:+.1f}%"
           + (f" · vs S&P {sum(vs) / len(vs) * 100:+.1f}%/trade" if vs else "")
           + (f" · profit factor {pfac:.2f}" if pfac is not None else "")
           + (f" · max drawdown {mdd * 100:.1f}%" if eq else ""))
    if len(c) < 30:
        out += f" · <i>only {len(c)} closed trades — too early to judge (30+ needed)</i>"
    return out


def snapshot(st, now, data):
    """Equity history for max drawdown (one point per hourly check / report)."""
    for pf in st["pf"].values():
        eq = pf.setdefault("eq", [])
        eq.append(round(value(pf, now, data), 3))
        del eq[:-1000]


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
        if stats(pf):
            lines.append(stats(pf))
    bv = _bench(st, now, data)
    if bv:
        lines.append(f"📊 S&P 500 (SPY): €{bv:.2f} ({(bv / START_EUR - 1) * 100:+.1f}%) — the number to beat")
    tl = trade_lines(st, now - 86400)
    if tl:
        lines.append("<b>Last 24h:</b>")
        lines += [x for _, x in tl[:30]] + ([f"… and {len(tl) - 30} more"] if len(tl) > 30 else [])
    lines.append(f"<i>Rules: €{SIZE_STRONG_EUR:.0f} per Expected-UP call, €{SIZE_EUR:.0f} per Likely-UP call, max {MAX_OPEN} open, "
                 f"no buy if the price is already {CHASE * 100:.0f}%+ above the alert; real market prices only (outside market hours: "
                 f"the next opening price) minus an estimated spread; sold live (checked every 5 min) by the profit lock "
                 f"(from +{TRAIL_START * 100:.0f}%, a {TRAIL * 100:.0f}% drop from the high), "
                 f"at −8% / +15%, or on a DOWN call; no time limit; 0.15% fee each way; currency moves ignored.</i>")
    return "\n".join(lines)


def account(st, key, now, data):
    """Broker-style view of one portfolio: total value, cash, money in stocks, each holding."""
    name = PORTFOLIOS[key][0]
    pf = st["pf"][key]
    cash = pf["cash"]
    reserved = sum(o.get("eur", SIZE_EUR) for o in pf.get("pending", {}).values())
    rows, stock_val = [], 0.0
    for tk, pos in sorted(pf["open"].items(), key=lambda x: x[1]["t"]):
        p = _last(data, tk, now) or pos["p0"]
        mv = pos["eur"] * p / pos["p0"]
        stock_val += mv
        pl = mv - pos.get("cost", SIZE_EUR)
        hi = _peak(data, tk, pos, now)
        if "exit" in pos:
            plan = "selling at the next price"
        else:
            sell = [f"stop ≤{pos['p0'] * (1 + STOP):,.4g}", f"target ≥{pos['p0'] * (1 + TAKE):,.4g}"]
            if hi / pos["p0"] - 1 >= TRAIL_START:
                sell.insert(0, f"🔒 profit lock ≤{hi * (1 - TRAIL):,.4g}")
            else:
                sell.append(f"profit lock from ≥{pos['p0'] * (1 + TRAIL_START):,.4g}")
            plan = "sells at: " + " · ".join(sell) + f" · held {_age(now - pos['t'])}"
        rows.append(f"   • <b>${html.escape(tk)}</b> €{mv:.2f} ({pl:+.2f}, {_pct(p / pos['p0'] - 1)}) · "
                    f"bought {pos['p0']:,.4g} ({_local(pos['t'])}) → now {p:,.4g} · high {hi:,.4g}\n"
                    f"     {plan}")
    for tk in sorted(pf.get("pending", {})):
        rows.append(f"   • ⏳ <b>${html.escape(tk)}</b> €{pf['pending'][tk].get('eur', SIZE_EUR):.0f} buy order waiting for the market price")
    total = cash + reserved + stock_val
    realized = sum(c["eur"] for c in pf["closed"])
    invested = sum(p.get("cost", SIZE_EUR) for p in pf["open"].values())
    head = (f"{name}: <b>€{total:.2f}</b> ({_pct(total / START_EUR - 1)})\n"
            f"   💵 Free cash €{cash:.2f}" + (f" · ⏳ reserved €{reserved:.0f}" if reserved else "")
            + (f" · 📈 in stocks €{stock_val:.2f}" if pf["open"] else "") + (f" (invested €{invested:.0f}, {stock_val - invested:+.2f})" if invested else "")
            + (f"\n   ✔️ Closed trades: {len(pf['closed'])}, profit {realized:+.2f} €" if pf["closed"] else ""))
    if not rows:
        return head + "\n   <i>no positions — all in cash</i>"
    return head + "\n" + "\n".join(rows)


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
    vol = {}
    for t, c, v in zip(r.get("timestamp") or [], q.get("close") or [], q.get("volume") or []):
        if c and v:
            day = datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")
            vol[day] = vol.get(day, 0) + c * v
    days = sorted(vol)[:-1] or sorted(vol)  # full days only (today is partial)
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    now = time.time()
    open_end = reg.get("end") if reg.get("start", 0) <= now < reg.get("end", 0) else None
    return {"bars": bars, "open_end": open_end, "live": True, "cur": meta.get("currency") or "USD",
            "dollar_vol": sum(vol[d] for d in days) / len(days) if days else 0}


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
        try:
            apply_voids(state)
        except Exception as e:
            log.warning("Practice portfolio: removing a voided trade failed (%s)", type(e).__name__)

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

    def _trades_now(self, st, now):
        """Every buy / sell / cancel is posted right away (with sound), not only in the hourly statement."""
        new = trade_lines(st, st.get("live_posted", st["since"]), short=False)
        new = [(t, x) for t, x in new if not x.startswith("⏳")]  # orders: shown once they fill or are cancelled
        if not new:
            return False
        vals = " · ".join(f"{PORTFOLIOS[k][0].split()[0]} €{value(st['pf'][k], now, self.data):.2f}" for k in PORTFOLIOS)
        text = "🧪 <b>Practice trade</b>\n" + "\n".join(x for _, x in new[:10]) + f"\n<i>Now: {vals}</i>"
        if self.send(text, False):
            st["live_posted"] = new[-1][0]
        return True

    @staticmethod
    def _groups(st):
        """Portfolios with exactly the same cash and holdings are shown once ("💪 = 👍")."""
        groups = []
        for key in PORTFOLIOS:
            pf = st["pf"][key]
            sig = (round(pf["cash"], 4), tuple(sorted(pf["open"])), tuple(sorted(pf.get("pending", {}))),
                   len(pf["closed"]))
            for g in groups:
                if g[0] == sig:
                    g[1].append(key)
                    break
            else:
                groups.append((sig, [key]))
        return [keys for _, keys in groups]

    def _post(self, st, now, label):
        """Hourly account statement (every market hour), silent; trades were already posted when they happened."""
        new = trade_lines(st, st.get("posted", st["since"]), short=False)
        parts = [f"🧪 <b>Practice account · {label}</b>"]
        for keys in self._groups(st):
            txt = account(st, keys[0], now, self.data)
            if len(keys) > 1:
                same = " = ".join(PORTFOLIOS[k][0] for k in keys)
                txt = txt.replace(PORTFOLIOS[keys[0]][0] + ":", same + " <i>(same trades so far)</i>:", 1)
            parts.append(txt)
        bv = _bench(st, now, self.data)
        if bv:
            parts.append(f"📊 Same €100 in the S&P 500: <b>€{bv:.2f}</b> ({(bv / START_EUR - 1) * 100:+.1f}%) — the number to beat")
        if new:
            tl = [x for _, x in new[:10]] + ([f"… and {len(new) - 10} more"] if len(new) > 10 else [])
            parts.append("<b>Trades since the last statement:</b>\n" + "\n".join(tl))
        else:
            parts.append("<i>No trades since the last statement.</i>")
        text = "\n\n".join(parts)
        if len(text) > 4000:
            text = text[:3990].rsplit("\n", 1)[0] + "\n…"
        if self.send(text, True) and new:
            st["posted"] = new[-1][0]

    def check(self, state):
        self.attach(state)
        st = state["_paper"]
        now = time.time()
        changed = False
        busy = any(pf["open"] or pf.get("pending") for pf in st["pf"].values())
        if busy and now - self.last >= CHECK_SECONDS:
            self.last = now
            step(st, now, self.data)
            changed = True
        if self.live_trades:
            changed |= self._trades_now(st, now)
        et = _ny(now)
        hm = et.strftime("%H:%M")
        if self.live_trades and et.weekday() < 5:
            slot = None
            if "09:36" <= hm < "10:00":
                slot = et.strftime("%Y-%m-%d open")
                label = f"market open (09:30 New York = {_local(et.replace(minute=30, second=0).timestamp(), '%H:%M')})"
            elif 10 <= et.hour <= 16:
                slot, label = et.strftime("%Y-%m-%d %H"), f"{et.strftime('%H:00')} New York = {_local(et.replace(minute=0).timestamp(), '%H:%M')}"
            if slot and st.get("hour_slot") != slot:
                st["hour_slot"] = slot
                changed = True
                step(st, now, self.data)
                snapshot(st, now, self.data)
                self._post(st, now, label)
        day = et.strftime("%Y-%m-%d")
        if et.weekday() < 5 and et.strftime("%H:%M") >= REPORT_NY and st.get("rep") != day:
            st["rep"] = day
            step(st, now, self.data)
            snapshot(st, now, self.data)
            self.send(report(st, now, self.data, f"Practice portfolio · {et.strftime('%a %b %d')}"), True)
            changed = True
        return changed

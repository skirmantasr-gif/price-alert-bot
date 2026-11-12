"""🎲 Options context for watchlist stocks (CBOE delayed quotes, free, ~15 min late).

What is shown and why (owner, 2026-11-12: "price can depend on option due date"):
  - next MONTHLY expiry (3rd Friday) and days left: the big expiries where many contracts close
  - expected move: the at-the-money straddle price for the next expiry (the market's own estimate of the swing)
  - max pain: the price at which option holders would lose the most at that expiry. Popular, but a WEAK
    forecaster; shown as context only
  - call wall / put wall: strikes with the most open contracts above / below the price; on expiry day prices
    often close near big strikes ("pinning": Ni, Pearson & Poteshman 2005 - real but small)
  - put/call open-interest ratio
  - unusual activity: contracts trading far above existing open positions (new bets; can be buys OR sells)
Used in: ☀️ daily check (expiry week), price / volume alerts (≤ 5 days before a monthly expiry), 📊 earnings
patterns (expected move vs past moves), 🧭 hourly outlook (AI input), quiet 🎲 unusual-activity alerts.
GitHub only; every failure is silent for the caller (returns None)."""
import calendar
import html
import json
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

log = logging.getLogger("bot")
ON = os.environ.get("OPTIONS", "true").lower() == "true"
URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{}.json"
CACHE_SECONDS = 1800
NEAR_DAYS = 5                 # alerts get the options line this close to a monthly expiry
UNUSUAL_MIN_VOL = 500         # contracts
UNUSUAL_VOL_OI = 2.0          # today's volume vs existing open interest
UNUSUAL_MIN_USD = 100_000     # premium traded
UNUSUAL_SECONDS = 1800
MAX_BYTES = 30_000_000
_SYM = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")
_cache = {}                   # symbol -> (time, summary or None)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return data


def monthly_expiry(d):
    """Third Friday of d's month, or of the next month if that has passed."""
    for y, m in ((d.year, d.month), (d.year + (d.month == 12), d.month % 12 + 1)):
        fridays = [day for day in range(1, calendar.monthrange(y, m)[1] + 1) if date(y, m, day).weekday() == 4]
        third = date(y, m, fridays[2])
        if third >= d:
            return third
    return None


def parse(chain):
    """CBOE json -> (price, iv30, [(expiry date, 'C'/'P', strike, oi, volume, bid, ask)])."""
    d = chain["data"]
    rows = []
    for o in d.get("options") or []:
        m = _SYM.match(o.get("option") or "")
        if not m:
            continue
        exp = datetime.strptime(m.group(2), "%y%m%d").date()
        rows.append((exp, m.group(3), int(m.group(4)) / 1000, float(o.get("open_interest") or 0),
                     float(o.get("volume") or 0), float(o.get("bid") or 0), float(o.get("ask") or 0)))
    return float(d.get("current_price") or d.get("close") or 0), d.get("iv30"), rows


def max_pain(rows):
    strikes = sorted({k for _, _, k, *_ in rows})
    if not strikes:
        return None

    def pay(p):
        return sum(oi * (max(0.0, p - k) if cp == "C" else max(0.0, k - p)) for _, cp, k, oi, *_ in rows)
    return min(strikes, key=pay)


def summarize(price, iv30, rows, today=None):
    """Numbers for the nearest monthly expiry (+ the nearest expiry for the expected move)."""
    today = today or date.today()
    if not price or not rows:
        return None
    exps = sorted({r[0] for r in rows if r[0] >= today})
    if not exps:
        return None
    mexp = monthly_expiry(today)
    mexp = mexp if mexp in exps else next((e for e in exps if e >= (mexp or today)), exps[0])
    m = [r for r in rows if r[0] == mexp]
    oi_c = sum(r[3] for r in m if r[1] == "C")
    oi_p = sum(r[3] for r in m if r[1] == "P")
    calls = [r for r in m if r[1] == "C" and r[2] > price and r[3] > 0]
    puts = [r for r in m if r[1] == "P" and r[2] < price and r[3] > 0]
    days = max((mexp - today).days, 1)
    move = None
    atm = min({r[2] for r in m}, key=lambda k: abs(k - price)) if m else None
    legs = [r for r in m if r[2] == atm]
    good = len(legs) == 2 and all(r[5] > 0 and (r[6] - r[5]) <= 0.5 * (r[5] + r[6]) / 2 for r in legs)
    if good and price >= 1:      # at-the-money straddle with sane quotes = the market's expected move to expiry
        move = sum((r[5] + r[6]) / 2 for r in legs) / price * 100
    elif iv30 and price >= 2:    # wide / missing quotes: 30-day implied volatility scaled to the days left
        move = float(iv30) * math.sqrt(days / 365)
    return {
        "price": price, "iv30": iv30, "exp": mexp.isoformat(), "days": (mexp - today).days,
        "max_pain": max_pain(m) if oi_c + oi_p > 0 else None,
        "call_wall": max(calls, key=lambda r: r[3])[2] if calls else None,
        "put_wall": max(puts, key=lambda r: r[3])[2] if puts else None,
        "pc_ratio": oi_p / oi_c if oi_c else None, "oi": oi_c + oi_p,
        "move_pct": move,
    }


def unusual(rows, today=None):
    """Contracts trading far above existing open interest today: [(expiry, C/P, strike, volume, oi, usd)]."""
    out = []
    for exp, cp, k, oi, vol, bid, ask in rows:
        if exp < (today or date.today()) or vol < UNUSUAL_MIN_VOL or vol < UNUSUAL_VOL_OI * max(oi, 1):
            continue
        usd = vol * (bid + ask) / 2 * 100
        if usd >= UNUSUAL_MIN_USD:
            out.append((exp.isoformat(), cp, k, int(vol), int(oi), usd))
    return sorted(out, key=lambda x: -x[5])


def get(sym):
    """Cached summary (+ the unusual list) for a US symbol, or None."""
    if not ON:
        return None
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        price, iv30, rows = parse(json.loads(_get(URL.format(urllib.parse.quote(sym)))))
        s = summarize(price, iv30, rows)
        if s is not None:
            s["unusual"] = unusual(rows)
    except Exception as e:
        log.info("Options data unavailable (%s)", type(e).__name__)
        s = None
    _cache[sym] = (time.time() if s is not None else time.time() - CACHE_SECONDS + 300, s)   # retry failures in 5 min
    return s


def _p(x):
    return f"${x:,.2f}" if x < 100 else f"${x:,.0f}"


def _d(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %b %d")


def line(sym, s=None, short=False):
    """One line for alerts / the daily check, or '' without data."""
    s = s if s is not None else get(sym)
    if not s or not s.get("oi"):
        return ""
    bits = []
    if s.get("move_pct"):
        bits.append(f"options expect ±{s['move_pct']:.0f}% by then")
    if s.get("max_pain"):
        bits.append(f"max pain {_p(s['max_pain'])}")
    if s.get("call_wall"):
        bits.append(f"call wall {_p(s['call_wall'])}")
    if s.get("put_wall"):
        bits.append(f"put wall {_p(s['put_wall'])}")
    if s.get("pc_ratio") is not None and not short:
        bits.append(f"put/call {s['pc_ratio']:.1f}")
    head = (f"🎲 Options ({'expiry ' if s['days'] <= NEAR_DAYS else 'monthly expiry '}{_d(s['exp'])}, "
            f"{s['days']} day{'s' * (s['days'] != 1)}): ")
    return head + " · ".join(bits)


def near_expiry(sym):
    """Line for price / volume alerts, only in the days before a monthly expiry."""
    s = get(sym)
    return line(sym, s, short=True) if s and s["days"] <= NEAR_DAYS else ""


def outlook_input(sym):
    """Compact facts for the AI outlook, or None."""
    s = get(sym)
    if not s:
        return None
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()
            if k in ("exp", "days", "max_pain", "call_wall", "put_wall", "pc_ratio", "move_pct", "iv30")}


class UnusualWatch:
    """Quiet 🎲 message when a watchlist stock's options trade far above their open positions (once per contract
    per day). New bets - buyers or sellers - often ahead of news; never a direction by itself."""

    def __init__(self, send, tickers):
        self.send, self.tickers, self.last = send, list(tickers), float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state, market_open=True):
        if not market_open or time.monotonic() - self.last < UNUSUAL_SECONDS:
            return False
        self.last = time.monotonic()
        st = state.setdefault("_opt", {})
        today = date.today().isoformat()
        seen = st.get("seen", {}) if st.get("day") == today else {}
        changed = False
        for t in self.tickers:
            s = get(t)
            new = [u for u in (s or {}).get("unusual", []) if f"{t}:{u[0]}:{u[1]}:{u[2]}" not in seen][:3]
            if not new:
                continue
            for u in new:
                seen[f"{t}:{u[0]}:{u[1]}:{u[2]}"] = 1
            lines = [f"🎲 <b>${html.escape(t)}</b> unusual options activity"]
            for exp, cp, k, vol, oi, usd in new:
                lines.append(f"• {vol:,} {'calls' if cp == 'C' else 'puts'} {_p(k)} {_d(exp)} · {vol / max(oi, 1):.0f}× "
                             f"the open positions · about ${usd / 1e3:,.0f}k traded")
            lines.append("<i>New bets, buyers or sellers - often before news, but no direction by itself.</i>")
            self.send("\n".join(lines), True)
            changed = True
        st.update({"day": today, "seen": seen})
        return changed


DAILY_DAYS = 7


def daily_lines(tickers):
    """☀️ daily check: one compact line per stock in the week before a monthly expiry."""
    out, exp = [], None
    for t in tickers:
        s = get(t)
        if not s or not s.get("oi") or s["days"] > DAILY_DAYS:
            continue
        exp = exp or s["exp"]
        bits = [f"±{s['move_pct']:.0f}%"] if s.get("move_pct") else []
        if s.get("max_pain"):
            bits.append(f"pain {_p(s['max_pain'])}")
        if s.get("put_wall") or s.get("call_wall"):
            bits.append("walls " + "/".join(_p(x) for x in (s.get("put_wall"), s.get("call_wall")) if x))
        out.append(f"${html.escape(t)} {_p(s['price'])}: " + " · ".join(bits))
    if not out:
        return []
    d = (datetime.strptime(exp, "%Y-%m-%d").date() - date.today()).days
    return [f"🎲 <b>Options expiry {_d(exp)}</b> ({d} days; expected move · max pain · put/call walls):"] + \
        [f"• {x}" for x in out]


def market_open_ny():
    """Rough regular-session check (holidays not handled: no data then anyway)."""
    try:
        from zoneinfo import ZoneInfo
        n = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return False
    return n.weekday() < 5 and "09:30" <= n.strftime("%H:%M") < "16:00"

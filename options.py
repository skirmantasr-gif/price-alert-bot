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
patterns (expected move vs past moves), 🧭 hourly outlook (AI input), quiet 🎲 unusual-activity alerts,
🎲 lean: today's unusual call vs put money gives "expect a RISE / FALL until <expiry>", every lean is
scored at that date (owner 2026-11-20); 📼 HistoryWatch: one snapshot per trading day for watchlist stocks with an FDA date ahead (owner 2026-11-20: free
sources have no past option chains, so we keep our own to compare the next PDUFA against).
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
POST_GAP = 3600               # one 🎲 message per stock per hour at most (owner 2026-11-20: ORCL posted 4× in an hour)
LEAN_SHARE = 0.65             # calls (or puts) >= 65% of today's unusual premium = a lean
LEAN_MAX_DAYS = 60            # longer-dated contracts are mostly hedges / long-term bets: left out of the lean
LEAN_MIN_DAYS = 3             # expiring this week = mostly closing / rolling old positions, not new bets
LEAN_MAX_AWAY = 0.20          # strikes more than 20% from the price (crash insurance, lottery calls): left out too
MAX_BYTES = 30_000_000
_SYM = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")
_cache = {}                   # symbol -> (time, summary or None)


def _get(url, timeout=20):
    # CBOE answers 403 without a Referer since 2026-11-20 (seen from the server and the Mac)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)",
                                               "Referer": "https://www.cboe.com/"})
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


def lean(rows, today=None, price=None):
    """Direction of today's unusual premium (owner 2026-11-20: "expect to rise until / expect to fall until").
    rows = unusual() output. {'dir': up/down/mixed, 'until': iso, 'calls', 'puts', 'strike'} or None."""
    today = today or date.today()
    near = [u for u in rows if LEAN_MIN_DAYS <= (datetime.strptime(u[0], "%Y-%m-%d").date() - today).days <= LEAN_MAX_DAYS
            and (not price or abs(u[2] / price - 1) <= LEAN_MAX_AWAY)]
    calls = sum(u[5] for u in near if u[1] == "C")
    puts = sum(u[5] for u in near if u[1] == "P")
    if calls + puts < UNUSUAL_MIN_USD:
        return None
    share = calls / (calls + puts)
    d = "up" if share >= LEAN_SHARE else "down" if share <= 1 - LEAN_SHARE else "mixed"
    side = [u for u in near if u[1] == ("C" if d == "up" else "P" if d == "down" else u[1])]
    by_exp = {}
    for u in side:
        by_exp[u[0]] = by_exp.get(u[0], 0) + u[5]
    until = max(by_exp, key=by_exp.get)
    top = [u for u in side if u[0] == until]
    strike = sum(u[2] * u[5] for u in top) / sum(u[5] for u in top)
    return {"dir": d, "until": until, "calls": calls, "puts": puts, "strike": strike}


def _m(x):
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def lean_line(ln):
    if not ln:
        return ""
    money = f"calls {_m(ln['calls'])} vs puts {_m(ln['puts'])} today"
    if ln["dir"] == "up":
        return (f"📈 <b>Options bets expect a RISE until {_d(ln['until'])}</b> — {money} "
                f"(biggest bets on above ~{_p(ln['strike'])})")
    if ln["dir"] == "down":
        return (f"📉 <b>Options bets expect a FALL until {_d(ln['until'])}</b> — {money} "
                f"(biggest bets on below ~{_p(ln['strike'])})")
    return f"↔️ Options bets are mixed — {money}, no clear direction"


def score_line(leans):
    done = [x for x in leans if x.get("right") is not None]
    if not done:
        return "📒 Past leans: none scored yet (each is checked at its 'until' date)"
    right = sum(1 for x in done if x["right"])
    return f"📒 Past leans: {right} of {len(done)} right (checked at the 'until' date)"


class UnusualWatch:
    """Quiet 🎲 message when a watchlist stock's options trade far above their open positions (once per contract
    per day). New bets - buyers or sellers - often ahead of news; never a direction by itself."""

    def __init__(self, send, tickers, close_on=None, now=None):
        self.send, self.tickers, self.last = send, list(tickers), float("-inf")
        self.close_on, self.now = close_on, now or time.time

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
        last_post = st.setdefault("last_post", {})
        leans = st.setdefault("leans", [])
        changed = self._score(leans, today)
        now = self.now()
        for t in self.tickers:
            if t in last_post and now - last_post[t] < POST_GAP:
                continue          # new contracts wait for the next hourly message (they stay unseen until then)
            s = get(t)
            new = [u for u in (s or {}).get("unusual", []) if f"{t}:{u[0]}:{u[1]}:{u[2]}" not in seen][:5]
            if not new:
                continue
            for u in new:
                seen[f"{t}:{u[0]}:{u[1]}:{u[2]}"] = 1
            lines = [f"🎲 <b>${html.escape(t)}</b> unusual options activity"]
            for exp, cp, k, vol, oi, usd in new:
                lines.append(f"• {vol:,} {'calls' if cp == 'C' else 'puts'} {_p(k)} {_d(exp)} · {vol / max(oi, 1):.0f}× "
                             f"the open positions · about ${usd / 1e3:,.0f}k traded")
            ln = lean(s.get("unusual", []), price=s.get("price"))
            if ln:
                lines.append(lean_line(ln))
                if ln["dir"] in ("up", "down") and s.get("price") and not any(
                        x["t"] == t and x["made"] == today and x["dir"] == ln["dir"] for x in leans):
                    leans.append({"t": t, "dir": ln["dir"], "until": ln["until"], "p0": s["price"], "made": today})
                    del leans[:-300]
                lines.append(score_line(leans))
            lines.append("<i>Options data can't show who bought and who sold, so a lean is a hint, not a forecast. "
                         "Not advice.</i>")
            self.send("\n".join(lines), True)
            last_post[t] = now
            changed = True
        st.update({"day": today, "seen": seen})
        return changed

    def _score(self, leans, today):
        """Lean right = the close on its 'until' day (or the next trading day) moved the expected way."""
        changed = False
        for x in leans:
            if x.get("right") is not None or x["until"] >= today:
                continue
            try:
                got = (self.close_on or _close_on)(x["t"], x["until"])
            except Exception:
                continue
            if got:
                x["close"] = round(got[1], 4)
                x["right"] = got[1] > x["p0"] if x["dir"] == "up" else got[1] < x["p0"]
                changed = True
        return changed


def _close_on(t, day):
    import predict
    return predict.close_on(t, day)


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


# ---------------------------------------------------------------- 📼 option history before FDA dates
HIST_KINDS = {"fda": 30, "earnings": 7}   # _cal event kind -> start recording this many days before
HIST_BEFORE = 30             # longest window (FDA)
HIST_AFTER = 5               # and keep recording this many days after it (the reaction)
HIST_MAX_EVENTS = 80         # oldest records dropped beyond this (state size); 💊 calendar adds every US FDA date
HIST_EXPIRIES = 3


def snapshot(price, iv30, rows, today=None):
    """Compact record of one chain: per nearest expiry [exp, atm strike, straddle % or None, call vol, put vol,
    call OI, put OI]; the 5 biggest open positions; up to 3 unusual trades."""
    today = today or date.today()
    exps = sorted({r[0] for r in rows if r[0] >= today})[:HIST_EXPIRIES]
    out = []
    for e in exps:
        m = [r for r in rows if r[0] == e]
        atm = min({r[2] for r in m}, key=lambda k: abs(k - price))
        legs = [r for r in m if r[2] == atm]
        good = len(legs) == 2 and all(r[5] > 0 and r[6] >= r[5] for r in legs) and price > 0
        st = round(sum((r[5] + r[6]) / 2 for r in legs) / price * 100, 1) if good else None
        out.append([e.isoformat(), atm, st,
                    int(sum(r[4] for r in m if r[1] == "C")), int(sum(r[4] for r in m if r[1] == "P")),
                    int(sum(r[3] for r in m if r[1] == "C")), int(sum(r[3] for r in m if r[1] == "P"))])
    live = [r for r in rows if r[0] >= today]
    top = sorted(live, key=lambda r: -r[3])[:5]
    return {"px": round(price, 4), "iv30": round(float(iv30), 1) if iv30 else None, "exps": out,
            "top": [[r[0].isoformat(), r[1], r[2], int(r[3]), int(r[4])] for r in top],
            "unu": [[u[0], u[1], u[2], u[3], u[4], round(u[5])] for u in unusual(rows, today)[:3]]}


def _ny_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return None


class HistoryWatch:
    """📼 Saves one options snapshot per trading day (near the close: 15:30-20:00 New York) for each watchlist
    stock with an FDA date in the next HIST_BEFORE days, until HIST_AFTER days after it. Silent; state _opth
    {"TICKER:YYYY-MM-DD": {"t", "event", "label", "snaps": {day: snapshot}}} (the event date is kept here because
    _cal drops passed events)."""

    def __init__(self, tickers, fetch=None, now=None):
        self.tickers = set(tickers)
        self.fetch = fetch or (lambda sym: parse(json.loads(_get(URL.format(urllib.parse.quote(sym))))))
        self.now = now or _ny_now
        self.last_day = None

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        n = self.now()
        if n is None or n.weekday() >= 5 or not ("15:30" <= n.strftime("%H:%M") < "20:00"):
            return False
        today = n.date()
        day = today.isoformat()
        if self.last_day == day:
            return False
        self.last_day = day
        h = state.setdefault("_opth", {})
        events = [(t, iso, kind, label) for t, iso, kind, label in (state.get("_cal") or []) if t in self.tickers]
        events += [(e["t"], e["date"], "fda", "FDA decision date (PDUFA)")       # 💊 all US stocks (catalysts.py)
                   for e in ((state.get("_fdacal") or {}).get("ev") or {}).values()]
        for t, iso, kind, label in events:
            if kind in HIST_KINDS:
                left = (datetime.strptime(iso, "%Y-%m-%d").date() - today).days
                if 0 <= left <= HIST_KINDS[kind]:
                    h.setdefault(f"{t}:{iso}", {"t": t, "event": iso, "label": label, "kind": kind, "snaps": {}})
        changed = False
        for key, rec in h.items():
            ev = datetime.strptime(rec["event"], "%Y-%m-%d").date()
            if not (-HIST_BEFORE <= (today - ev).days <= HIST_AFTER) or day in rec["snaps"]:
                continue
            try:
                price, iv30, rows = self.fetch(rec["t"])
                if not price or not rows:
                    continue
                rec["snaps"][day] = snapshot(price, iv30, rows, today)
                changed = True
                log.info("Options history: saved %s (%s days to %s)", rec["t"], (ev - today).days, rec["label"])
            except Exception as e:
                log.info("Options history: %s unavailable (%s)", rec["t"], type(e).__name__)
        if len(h) > HIST_MAX_EVENTS:
            for key in sorted(h, key=lambda k: h[k]["event"])[:len(h) - HIST_MAX_EVENTS]:
                del h[key]
            changed = True
        return changed


def history_lines(rec):
    """Readable table of one record (for Claude / a later report)."""
    out = [f"{rec['t']} · {rec['label']} on {rec['event']}"]
    for day in sorted(rec["snaps"]):
        s = rec["snaps"][day]
        e = s["exps"][0] if s["exps"] else None
        bit = (f"next exp {e[0]} straddle {'±%.0f%%' % e[2] if e[2] is not None else '?'} · vol C/P {e[3]}/{e[4]}"
               f" · OI C/P {e[5]}/{e[6]}") if e else "no expiries"
        out.append(f"{day} ${s['px']:.2f} iv30 {s['iv30']} · {bit}")
    return out

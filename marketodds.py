"""📈 Market-implied FDA approval odds, scored next to Claude's (owner 2026-11-22: "7 do").

Why: on SLS and CAPR the market's pricing beat Claude's own odds. To learn whose numbers deserve trust, every
FDA date (watchlist + 💊 calendar stocks under CAP_MAX, since 2026-11-23) gets a market estimate made from the 📼 options snapshots (options.HistoryWatch, state _opth)
and is scored like Claude's forecasts once the outcome is known.

Model (rough, said so in every post): two outcomes and no drift.
  m = at-the-money straddle for the first expiry on/after the FDA date, as a fraction of the price
      (the market's expected size of the move, either way)
  d = drop if rejected: the stock falls to max(cash per share, FLOOR × price), d kept within [D_MIN, D_MAX]
  no drift: p·u = (1−p)·d and E|move| = p·u + (1−p)·d = 2(1−p)·d  →  p = 1 − m / (2d)
Check on CAPR Nov 22 2026: price $7.88, Nov 27 straddle 111%, cash ≈ $2/share → d ≈ 0.72 → p ≈ 23% (CRL came).
Only snapshots taken BEFORE the outcome / a missed-date notice count (SVRA's only snapshot came after the FDA said
it would miss the date → no market estimate).

Outcome: approved at / near the date = YES; CRL / rejection = NO; the FDA missing the date = NO (same rule as
Claude's "approved at/near PDUFA" calls). Sources: catalysts decided/missed marks, or a linked Claude forecast in
predictions.json ("fda": "CAPR:2026-11-22" with "outcome"). State _mkt {"T:date": {...}}. Posts go to the
trading-bot channel next to 🎯 forecast scoring.
"""
import html
import json
import logging
import os
import statistics
import time
import urllib.request
from datetime import datetime, timedelta

import extras

log = logging.getLogger("bot")
ON = os.environ.get("MARKET_ODDS", "true").lower() == "true"
CHECK_SECONDS = 3600
FLOOR = 0.25          # rejected stocks rarely trade below a quarter of the pre-decision price unless cash says so
D_MIN, D_MAX = 0.30, 0.85
P_MIN, P_MAX = 0.05, 0.95
CAP_MAX = 10e9        # above this the drug is only part of the company's value: the two-outcome model doesn't fit
RESOLVE_AFTER_DAYS = 14   # no decision / miss seen 14 days after the date → left open (shown as "no result")


def implied(move, price, cash_ps=None):
    """move = straddle fraction (1.11 = ±111%) → {"p", "d"} or None."""
    if not move or not price or move <= 0 or price <= 0:
        return None
    floor = max(cash_ps or 0.0, FLOOR * price)
    d = min(max(1 - floor / price, D_MIN), D_MAX)
    p = min(max(1 - move / (2 * d), P_MIN), P_MAX)
    return {"p": round(p, 3), "d": round(d, 3)}


def pick(rec, cutoff=None):
    """Latest usable snapshot before `cutoff` (iso day, exclusive): (day, price, straddle %, expiry) or None."""
    ev = rec["event"]
    for day in sorted(rec.get("snaps") or {}, reverse=True):
        if day > ev or (cutoff and day >= cutoff):
            continue
        s = rec["snaps"][day]
        for exp, _atm, st, *_ in s.get("exps") or []:
            if exp >= ev and st:
                return day, s["px"], st, exp
    return None


_cash_cache = {}


def cash_per_share(t):
    """Cash ÷ shares from the latest SEC XBRL facts, or None."""
    if t in _cash_cache:
        return _cash_cache[t]
    out = None
    try:
        import news
        contact = news.SEC_CONTACT
        cik = news.sec_cik(t) if contact else None
        if cik:
            cf = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                headers={"User-Agent": f"price-alert-bot {contact}"}), timeout=30).read(20_000_000))
            facts = cf.get("facts") or {}

            def last(ns, k, unit):
                v = sorted((facts.get(ns, {}).get(k, {}).get("units", {}).get(unit) or []), key=lambda x: x["end"])
                return v[-1]["val"] if v else None
            cash = last("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD") or 0
            cash += last("us-gaap", "ShortTermInvestments", "USD") or 0
            sh = last("dei", "EntityCommonStockSharesOutstanding", "shares")
            out = cash / sh if cash and sh else None
    except Exception as e:
        log.info("Market odds: cash per share unavailable (%s)", type(e).__name__)
    _cash_cache[t] = out
    return out


def outcome(state, key, preds):
    """True / False / None (not known yet)."""
    fc = state.get("_fdacal") or {}
    dec = (fc.get("decided") or {}).get(key)
    if dec and isinstance(dec.get("approved"), bool):
        return dec["approved"]
    if key in (fc.get("missed") or {}):
        return False
    for x in preds:
        if x.get("fda") == key and isinstance(x.get("outcome"), bool):
            return x["outcome"]
    return None


def claude_line(preds, key, p):
    """Claude's own odds for the same FDA date next to the market's (owner 2026-11-26: one card, not two places)."""
    mine = next((x for x in preds if x.get("fda") == key and isinstance(x.get("p"), (int, float))), None)
    if not mine:
        return ""
    gap = (mine["p"] - p) * 100
    side = ("Claude is more hopeful than the market" if gap >= 10 else
            "Claude is less hopeful than the market" if gap <= -10 else "about the same as the market")
    return f"🎯 Claude said {mine['p'] * 100:.0f}% on {html.escape(mine.get('made', '?'))} ({gap:+.0f} pts: {side})\n"


def _cutoff(state, key):
    fc = state.get("_fdacal") or {}
    ats = [v["at"] for k in ("decided", "missed", "shock") for kk, v in (fc.get(k) or {}).items() if kk == key]
    return min(ats) if ats else None


def brier(rows, k):
    return statistics.mean((r[k] - (1.0 if r["outcome"] else 0.0)) ** 2 for r in rows) if rows else None


def _cap(t):
    try:
        import context
        return context.market_cap(t)
    except Exception:
        return None


class MarketOdds:
    """Every FDA date in _opth (watchlist AND the other US stocks of the 💊 calendar, owner 2026-11-23: "3. proceed"),
    except companies over CAP_MAX (VRTX-type: a rejection barely moves them, so the straddle says little)."""

    def __init__(self, send, tickers, cash=cash_per_share, preds=None, now=None, cap=_cap):
        self.send, self.tickers, self.cash, self.cap = send, set(tickers), cash, cap
        self.preds = preds or (lambda: __import__("predict").load())
        self.now = now or extras.local_now
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        if time.monotonic() - self.last < CHECK_SECONDS:
            return False
        self.last = time.monotonic()
        today = self.now().date().isoformat()
        mk = state.setdefault("_mkt", {})
        preds = self.preds() or []
        changed = False
        for key, rec in (state.get("_opth") or {}).items():
            if rec.get("kind") != "fda" or key in mk:
                continue
            # estimate on the last day before the date (or once the date passed: from what was saved before)
            ev = datetime.strptime(rec["event"], "%Y-%m-%d").date()
            if today < (ev - timedelta(days=1)).isoformat():
                continue
            cap = self.cap(rec["t"]) if self.cap else None
            if cap and cap > CAP_MAX:
                mk[key] = {"t": rec["t"], "event": rec["event"], "skip": f"market cap ${cap / 1e9:.0f}B"}
                changed = True
                log.info("Market odds: skipped (large cap)")
                continue
            got = pick(rec, _cutoff(state, key))
            if not got:
                continue
            day, px, st, exp = got
            est = implied(st / 100, px, self.cash(rec["t"]))
            if not est:
                continue
            mk[key] = {"t": rec["t"], "event": rec["event"], "made": day, "px": px, "move": st, "exp": exp, **est}
            changed = True
            log.info("Market odds: estimate saved")
            self.send(f"📈 <b>Market-implied odds</b> · <b>${html.escape(rec['t'])}</b> FDA date "
                      f"{ev.strftime('%a %b %d')}: <b>~{est['p'] * 100:.0f}% approval</b>\n"
                      + claude_line(preds, key, est["p"]) +
                      f"From options on {day}: ±{st:.0f}% priced to {exp} at ${px:,.2f}; if rejected, assumed fall "
                      f"≈ −{est['d'] * 100:.0f}% (toward cash per share). Rough model: scored after the decision "
                      f"next to Claude's 🎯 odds.", True)
        rows = []
        for key, r in mk.items():
            if r.get("skip"):
                continue
            if "outcome" not in r:
                o = outcome(state, key, preds)
                if o is None:
                    continue
                r["outcome"] = o
                changed = True
                mine = next((x for x in preds if x.get("fda") == key), None)
                r["claude"] = float(mine["p"]) if mine else None
                vs = (f" · Claude said {r['claude'] * 100:.0f}% (Brier {(r['claude'] - o) ** 2:.2f})"
                      if r["claude"] is not None else "")
                self.send(f"📈 <b>Market odds scored</b> · <b>${html.escape(r['t'])}</b> {r['event']}: "
                          f"{'APPROVED ✅' if o else 'not approved ❌'} · market said {r['p'] * 100:.0f}% "
                          f"(Brier {(r['p'] - o) ** 2:.2f}){vs}\n{self.summary(mk)}", True)
        return changed

    @staticmethod
    def summary(mk):
        done = [r for r in mk.values() if "outcome" in r and not r.get("skip")]
        both = [r for r in done if r.get("claude") is not None]
        line = f"<i>Running: market Brier {brier(done, 'p'):.2f} on {len(done)} FDA date(s)"
        if both:
            line += (f" · same dates: market {brier(both, 'p'):.2f} vs Claude {brier(both, 'claude'):.2f} "
                     f"({len(both)})")
        return line + ". Lower is better; 0.25 = coin flip. Needs 10–20 dates before it means much.</i>"

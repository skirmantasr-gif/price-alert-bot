"""💼 The owner's REAL holdings: 🛑 −15% stop warnings and 🧾 December tax-loss reminders (owner 2026-11-26: "proceed",
"use the previously sent" = the Revolut statement of 2026-11-15).

The holdings live in a PRIVATE file on the server (HOLDINGS_FILE, default holdings.json next to the state file;
never in the public repo, chmod 600):
  {"as_of": "2026-11-15", "source": "Revolut statement",
   "positions": [{"t": "VNDA", "qty": 306.94, "cost_usd": 1996.71}, ...],     # cost_usd optional
   "tax": {"realized_eur": 1856, "exempt_eur": 500, "harvest": ["VNDA", "VRRM", "SLS"]}}
Without the file nothing happens. Nothing is sent anywhere except the owner's Telegram chat.

🛑 Stop (owner's rule: stop at −15% from the top). Once per trading day after the US close: top = the higher of
   the average buy price and the highest close since "as_of"; a holding at or below −15% from its top gets one
   warning. It re-arms when the stock gets back above −10%. The first run sends one summary of holdings that are
   already below the line instead of a burst.
🧾 Tax (Lithuania, 2026 plan): in December, on Mondays and Dec 28–30, one message: realized gains so far, what
   selling the "harvest" positions would realize at today's price (USD → EUR at today's rate), and the gains left
   versus the €500 allowance. The numbers in the file are from a statement date: trades after it aren't known.
"""
import html
import json
import logging
import os
import time
import urllib.request
from datetime import date, datetime

import extras

log = logging.getLogger("bot")
ON = os.environ.get("HOLDINGS", "true").lower() == "true"
CHECK_SECONDS = 1800
STOP = -0.15
REARM = -0.10
TAX_DAYS = (28, 29, 30)       # extra reminders in the last days (Dec 31 is the last trading day; T+1 settlement)
UA = {"User-Agent": "Mozilla/5.0"}


def path():
    st = os.environ.get("STATE_FILE", "state.json")
    return os.environ.get("HOLDINGS_FILE") or os.path.join(os.path.dirname(os.path.abspath(st)), "holdings.json")


def load(p=None):
    try:
        with open(p or path()) as f:
            d = json.load(f)
        pos = [x for x in d.get("positions", []) if x.get("t") and isinstance(x.get("qty"), (int, float))]
        return dict(d, positions=pos) if pos else None
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Holdings file unreadable (%s)", type(e).__name__)
        return None


def _chart(sym, rng="6mo"):
    req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d",
                                 headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["chart"]["result"][0]


def closes(t):
    """[(iso day, close)] for about 6 months (Yahoo daily)."""
    r = _chart(t)
    out = []
    for ts, c in zip(r.get("timestamp") or [], r["indicators"]["quote"][0].get("close") or []):
        if c is not None:
            out.append((datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"), float(c)))
    return out


def eurusd():
    return float(_chart("EURUSD=X", "5d")["meta"]["regularMarketPrice"])


def top_of(pos, rows, as_of):
    """(top price, where it came from) — the average buy price or the highest close since as_of."""
    after = [(d, c) for d, c in rows if d >= as_of]
    best = max(after, key=lambda x: x[1]) if after else None
    buy = pos["cost_usd"] / pos["qty"] if pos.get("cost_usd") and pos["qty"] else None
    if buy and (not best or buy >= best[1]):
        return buy, "your average buy price"
    return (best[1], f"close {best[0]}") if best else (None, "")


def _money(x):
    return f"€{x:,.0f}" if abs(x) >= 10 else f"€{x:,.2f}"


class HoldingsWatch:
    def __init__(self, send, file=None, closes=closes, fx=eurusd, now=None, trading_day=None):
        self.send, self.file, self.closes, self.fx = send, file, closes, fx
        self.now = now or extras.local_now
        self.trading_day = trading_day or _trading_day
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        if time.monotonic() - self.last < CHECK_SECONDS:
            return False
        self.last = time.monotonic()
        h = load(self.file)
        if not h:
            return False
        st = state.setdefault("_hold", {})
        changed = False
        now = self.now()
        try:
            changed |= self._stop(h, st, now)
        except Exception as e:
            log.warning("Holdings stop check failed: %s", type(e).__name__)
        try:
            changed |= self._tax(h, st, now)
        except Exception as e:
            log.warning("Holdings tax reminder failed: %s", type(e).__name__)
        return changed

    # -------- 🛑 −15% from the top
    def _stop(self, h, st, now):
        ny = _ny(now)
        day = ny.strftime("%Y-%m-%d")
        if st.get("stop_day") == day or not self.trading_day(ny.date()) or ny.strftime("%H:%M") < "16:15":
            return False
        rows_out, alerted = [], st.setdefault("alerted", {})
        for p in h["positions"]:
            try:
                rows = self.closes(p["t"])
            except Exception:
                continue
            if not rows:
                continue
            top, src = top_of(p, rows, h.get("as_of", "0000"))
            if not top:
                continue
            last = rows[-1][1]
            dd = last / top - 1
            if dd <= STOP and p["t"] not in alerted:
                rows_out.append((p, last, top, src, dd))
                alerted[p["t"]] = day
            elif dd > REARM and p["t"] in alerted:
                del alerted[p["t"]]
        st["stop_day"] = day
        first = not st.get("init")
        st["init"] = True
        if not rows_out:
            return True
        lines = [f"• <b>${html.escape(p['t'])}</b> ${last:,.2f} · <b>{dd * 100:.0f}%</b> from {src} (${top:,.2f}) · "
                 f"≈ ${p['qty'] * last:,.0f} held" for p, last, top, src, dd in sorted(rows_out, key=lambda r: r[4])]
        head = ("🛑 <b>Your −15% rule</b> · real holdings already below the line (from your "
                f"{html.escape(h.get('source', 'statement'))} of {html.escape(h.get('as_of', '?'))}):" if first else
                "🛑 <b>Your −15% rule</b> · fell 15% from its top today:")
        self.send(head + "\n" + "\n".join(lines) + "\n<i>Your own rule: sell or decide again on purpose. Each stock "
                  "is warned once and again only after it climbs back above −10%. Holdings changed? Tell Claude. "
                  "Not advice.</i>", False)
        return True

    # -------- 🧾 December tax-loss reminder
    def _tax(self, h, st, now):
        tax = h.get("tax") or {}
        if not tax.get("harvest") or now.month != 12 or now.strftime("%H:%M") < "09:00":
            return False
        day = now.strftime("%Y-%m-%d")
        if st.get("tax_day") == day or not (now.weekday() == 0 or now.day in TAX_DAYS or not st.get("tax_day")):
            return False
        rate = self.fx()
        pos = {p["t"]: p for p in h["positions"]}
        realized = float(tax.get("realized_eur", 0))
        allow = float(tax.get("exempt_eur", 500))
        lines, total = [], 0.0
        for t in tax["harvest"]:
            p = pos.get(t)
            if not p or not p.get("cost_usd"):
                continue
            rows = self.closes(t)
            if not rows:
                continue
            pl = (p["qty"] * rows[-1][1] - p["cost_usd"]) / rate
            total += pl
            lines.append(f"• <b>${html.escape(t)}</b> {'loss' if pl < 0 else 'gain'} {_money(pl)} at ${rows[-1][1]:,.2f} "
                         f"→ gains left {_money(realized + total)}")
        if not lines:
            return False
        st["tax_day"] = day
        left = realized + total
        verdict = (f"✅ under the {_money(allow)} allowance" if left <= allow else
                   f"still {_money(left - allow)} over the {_money(allow)} allowance")
        self.send(f"🧾 <b>Tax-loss check</b> · {now.strftime('%a %b %d')}\n"
                  f"Realized gains this year: {_money(realized)} (from your statement of "
                  f"{html.escape(h.get('as_of', '?'))}; later sales not included)\n"
                  f"Selling these now would realize (today's price, ${rate:.4f} per €):\n" + "\n".join(lines) +
                  f"\nAll of them: {_money(total)} → gains left <b>{_money(left)}</b>, {verdict}\n"
                  "⏰ Last US trading day: Thu Dec 31; sell by Wed Dec 30 to be safe (T+1 settlement).\n"
                  "<i>Plan from Nov 16: sell and buy back only down to the allowance. Check the €500 allowance and "
                  "rebuying with VMI or an advisor first. Not tax advice.</i>", True)
        return True


def _ny(now):
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return now


def _trading_day(d):
    try:
        import catalysts
        return catalysts.trading_day(d)
    except Exception:
        return d.weekday() < 5

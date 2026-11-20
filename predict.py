"""🎯 Claude's forecasts, scored automatically (owner 2026-11-20: "6. do").

The forecasts live in a PRIVATE file on the server (PREDICTIONS_FILE, default predictions.json next to the state
file; never in the public repo): a list of
  {"id": "sls-1117-10", "made": "2026-11-17", "claim": "SLS closes Nov 17 above $10", "p": 0.65,
   "due": "2026-11-17", "rule": {"type": "close_above", "ticker": "SLS", "level": 10}}
rule types: close_above / close_below (Yahoo close on the due date, or the first trading day after it) and manual
(Claude writes "outcome": true/false into the file). The bot reads the file every hour, resolves what it can,
posts each newly resolved forecast once with the running calibration (Brier score: 0 = perfect, 0.25 = a coin
flip) and keeps results in state _pred. Nothing is ever sent elsewhere.
"""
import html
import json
import logging
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import extras

log = logging.getLogger("bot")
ON = os.environ.get("PREDICTIONS", "true").lower() == "true"
CHECK_SECONDS = 3600


def path():
    st = os.environ.get("STATE_FILE", "state.json")
    return os.environ.get("PREDICTIONS_FILE") or os.path.join(os.path.dirname(os.path.abspath(st)), "predictions.json")


def load(p=None):
    try:
        with open(p or path()) as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, dict) and x.get("id") and isinstance(x.get("p"), (int, float))]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("Predictions file unreadable (%s)", type(e).__name__)
        return []


def close_on(ticker, day):
    """(date, close) of the first trading day on/after `day` (New York), or None if not traded yet."""
    a = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) - 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?"
           + urllib.parse.urlencode({"period1": a, "period2": a + 12 * 86400, "interval": "1d"}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read())["chart"]["result"][0]
    off = j["meta"].get("gmtoffset") or -18000
    for ts, c in zip(j.get("timestamp") or [], j["indicators"]["quote"][0]["close"]):
        d = datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m-%d")
        if c is not None and d >= day:
            return d, c
    return None


def resolve(x, now, price=close_on):
    """(outcome bool, note) or None while open."""
    if isinstance(x.get("outcome"), bool):
        return x["outcome"], x.get("note", "")
    r = x.get("rule") or {}
    if r.get("type") not in ("close_above", "close_below") or not x.get("due"):
        return None
    # the due day's close is final after 22:30 Vilnius (16:00 New York + data delay)
    due = datetime.strptime(x["due"], "%Y-%m-%d").date()
    if now.date() < due or (now.date() == due and now.strftime("%H:%M") < "23:30"):
        return None
    got = price(r["ticker"], x["due"])
    if not got:
        return None
    d, c = got
    ok = c > r["level"] if r["type"] == "close_above" else c < r["level"]
    return ok, f"close {d} ${c:,.2f} vs ${r['level']:,.2f}"


def brier(rows):
    return statistics.mean((r["p"] - (1.0 if r["outcome"] else 0.0)) ** 2 for r in rows) if rows else None


def calibration_line(rows):
    b = brier(rows)
    if b is None:
        return ""
    hi = [r for r in rows if r["p"] >= 0.6]
    lo = [r for r in rows if r["p"] <= 0.4]
    bits = [f"📒 Calibration: {len(rows)} resolved · Brier {b:.2f} (0 = perfect, 0.25 = coin flip)"]
    if hi:
        bits.append(f"'likely' (≥60%) calls came true {sum(r['outcome'] for r in hi)}/{len(hi)}")
    if lo:
        bits.append(f"'unlikely' (≤40%) {sum(r['outcome'] for r in lo)}/{len(lo)}")
    return " · ".join(bits)


class PredictionWatch:
    def __init__(self, send, file=None, price=close_on, now=None):
        self.send, self.file, self.price = send, file, price
        self.now = now or extras.local_now
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state):
        if time.monotonic() - self.last < CHECK_SECONDS:
            return False
        self.last = time.monotonic()
        preds = load(self.file)
        if not preds:
            return False
        st = state.setdefault("_pred", {})
        now = self.now()
        changed = False
        new = []
        first = not st.get("_init")
        for x in preds:
            if x["id"] in st:
                continue
            try:
                r = resolve(x, now, self.price)
            except Exception as e:
                log.info("Prediction check failed (%s)", type(e).__name__)
                continue
            if r is None:
                continue
            st[x["id"]] = {"outcome": r[0], "note": r[1], "p": float(x["p"]), "at": now.strftime("%Y-%m-%d"),
                           "posted": False}
            changed = True
            new.append(x)
        rows = [{"p": v["p"], "outcome": v["outcome"]} for k, v in st.items() if not k.startswith("_")]
        if first:   # the forecasts already settled before the bot tracked them: one summary, not a burst
            st["_init"] = True
            changed = True
            open_n = sum(1 for x in preds if x["id"] not in st)
            text = (f"🎯 <b>Forecast tracking started</b>: {len(rows)} of Claude's calls already settled, {open_n} open "
                    f"(each is posted here when it settles)\n" + calibration_line(rows) +
                    "\n<i>Scored to show how far to trust these percentages. Not advice.</i>")
            if self.send(text, True):
                for x in new:
                    st[x["id"]]["posted"] = True
            return changed
        for x in new:
            v = st[x["id"]]
            icon = "✅" if v["outcome"] else "❌"
            text = (f"🎯 <b>Claude's forecast resolved</b> {icon}\n{html.escape(x.get('claim', x['id']))}\n"
                    f"Said <b>{x['p'] * 100:.0f}%</b> on {html.escape(x.get('made', '?'))} → "
                    f"{'it happened' if v['outcome'] else 'it did not happen'}"
                    + (f" ({html.escape(v['note'])})" if v["note"] else "") +
                    f" · this call's score {(x['p'] - (1.0 if v['outcome'] else 0.0)) ** 2:.2f}\n"
                    + calibration_line(rows) +
                    "\n<i>Scored to show how far to trust these percentages. Not advice.</i>")
            if self.send(text, True):
                v["posted"] = True
        return changed

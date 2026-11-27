"""📼 Backup options source (owner 2026-11-27, "5."): when CBOE's free file for a stock is old (options.Stale), ask
the IB Gateway paper account for IBKR's free DELAYED quotes (15 min) of the at-the-money options instead.

- Read-only connection (readonly=True), its own client id (CLIENT_ID), never places or touches orders.
- Runs in a child process (python -I ibopts.py SYMBOL → JSON on stdout) started from one background thread, so a
  slow or stuck Gateway can never hold up the price loop; HistoryWatch picks the result up on its next retry.
- Only real delayed bid/ask count: IBKR's "close" field is the previous day's price, so a leg without a bid/ask
  stays empty and the snapshot's straddle is None (no made-up numbers).
- Off with IBOPTS=false, without ib_async, or when not on the server.
"""
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from datetime import date, datetime

log = logging.getLogger("price-alert-bot")

HOST, PORT = "127.0.0.1", int(os.environ.get("IBKR_PORT", "4002"))
CLIENT_ID = int(os.environ.get("IBOPTS_CLIENT_ID", "78"))
EXPIRIES, STRIKES = 3, 3          # nearest expiries / strikes around the price per expiry
WAIT = 8                          # seconds for delayed quotes to arrive
TIMEOUT = 120
try:
    import ib_async  # noqa: F401
    HAVE_LIB = True
except Exception:
    HAVE_LIB = False
ON = (os.environ.get("IBOPTS", "true").lower() == "true" and HAVE_LIB
      and os.environ.get("HEARTBEAT_ROLE", "server") == "server")


def _ny_today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


class Fallback:
    """request(sym) queues a background fetch; result(sym, day) -> (price, iv30, rows) once it is in, else None."""

    def __init__(self, run=None, today=None):
        self.run = run or _run_child
        self.today = today or _ny_today
        self.q, self.res, self.busy = queue.Queue(), {}, set()
        self.lock = threading.Lock()
        self.thread = None

    def request(self, sym):
        with self.lock:
            if sym in self.busy or (self.res.get(sym) or (None,))[0] == self.today():
                return
            self.busy.add(sym)
        self.q.put(sym)
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._work, daemon=True, name="ibopts")
            self.thread.start()

    def _work(self):
        while True:
            try:
                sym = self.q.get(timeout=5)
            except queue.Empty:
                return
            got = None
            try:
                got = self.run(sym)
            except Exception as e:
                log.info("IBKR options backup: failed (%s)", type(e).__name__)
            with self.lock:
                self.busy.discard(sym)
                if got:
                    self.res[sym] = (self.today(), got)

    def result(self, sym, day):
        r = self.res.get(sym)
        return r[1] if r and r[0] == day else None


def _run_child(sym):
    if not ON:
        return None
    p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), sym], capture_output=True, text=True,
                       timeout=TIMEOUT, cwd=os.path.dirname(os.path.abspath(__file__)))
    line = (p.stdout or "").strip().splitlines()[-1:] or [""]
    return decode(line[0])


def decode(text):
    """Child JSON -> (price, iv30, rows) in options.parse format, or None."""
    try:
        j = json.loads(text)
    except ValueError:
        return None
    if not j.get("px") or not j.get("rows"):
        return None
    rows = [(date.fromisoformat(r[0]), r[1], float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]))
            for r in j["rows"]]
    return float(j["px"]), j.get("iv30"), rows


def _child(sym):
    """Runs in the child process: prints one JSON line."""
    import math
    from ib_async import IB, Option, Stock
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True, timeout=20)
    try:
        ib.reqMarketDataType(3)                      # delayed (free)
        st = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(st)
        bars = ib.reqHistoricalData(st, "", "2 D", "1 min", "TRADES", False)
        if not bars:
            print(json.dumps({"err": "no price"}))
            return
        px = float(bars[-1].close)
        ch = [c for c in ib.reqSecDefOptParams(sym, "", "STK", st.conId) if c.exchange == "SMART"]
        if not ch:
            print(json.dumps({"err": "no chain"}))
            return
        today = _ny_today().strftime("%Y%m%d")
        exps = sorted(e for e in ch[0].expirations if e >= today)[:EXPIRIES]
        ks = sorted(ch[0].strikes, key=lambda k: abs(k - px))[:STRIKES]
        opts = [Option(sym, e, k, r, "SMART", tradingClass=ch[0].tradingClass) for e in exps for k in ks for r in "CP"]
        opts = [o for o in ib.qualifyContracts(*opts) if o and o.conId]
        ts = [(o, ib.reqMktData(o, "", False, False)) for o in opts]
        ib.sleep(WAIT)

        def num(x):
            return float(x) if x is not None and not math.isnan(x) and x > 0 else 0.0
        rows = []
        for o, t in ts:
            e = o.lastTradeDateOrContractMonth
            rows.append([f"{e[:4]}-{e[4:6]}-{e[6:8]}", o.right, o.strike, 0, num(t.volume), num(t.bid), num(t.ask)])
        print(json.dumps({"px": px, "iv30": None, "rows": rows, "src": "ibkr-delayed"}))
    finally:
        ib.disconnect()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    _child(sys.argv[1].upper()[:10])

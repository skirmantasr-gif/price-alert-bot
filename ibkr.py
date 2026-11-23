"""🏦 IBKR paper mirror: the trades of one practice portfolio (default 👥 insider clusters) are also sent as real
orders to an Interactive Brokers PAPER account (IB Gateway on the server, localhost:4002, via ib_async).

Why: the practice portfolios fill at simulated prices (next 5-minute bar open + an estimated spread). The paper
account gets real order handling (queue at the open, real spread, partial fills), so comparing the two shows how
honest the simulation is — before any real money is involved (see paper.money_rule).

How:
  - after every practice step, new "buy" / "sell" events of the mirrored portfolio are queued (only events after
    the mirror was switched on; nothing old is replayed)
  - buy: market order, whole shares for €eur x USD_PER_EUR (default €10 -> $1,000), US stocks only, DAY order in
    regular hours (placed while the market is closed it waits for the open)
  - sell: market order for the whole IBKR position of that stock; an unfilled buy order is cancelled instead
  - fills are posted to the practice channel next to the practice price ("real vs practice" difference)
  - a short account line (net value, positions, open P/L) is added to the practice report
Safety: refuses to trade unless EVERY account on the connection is a paper account (IDs start with "D") and the
port is the paper port; one order is capped at MAX_ORDER_USD. Never handles credentials (IBC holds the login).
Server only: off without ib_async (GitHub fallback) or with IBKR=false. Every failure is logged and skipped.
"""
import asyncio
import html
import logging
import math
import os
import queue
import re
import threading
import time

log = logging.getLogger("bot")

try:
    import ib_async  # noqa: F401
    HAVE_LIB = True
except Exception:  # GitHub fallback / tests without the library
    HAVE_LIB = False

ON = (os.environ.get("IBKR", "true").lower() == "true"
      and os.environ.get("HEARTBEAT_ROLE", "server") == "server")
HOST = "127.0.0.1"
PORT = int(os.environ.get("IBKR_PORT", "4002"))   # 4002 = IB Gateway PAPER; live would be 4001 (refused below)
CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "17"))
MIRROR = os.environ.get("IBKR_MIRROR", "insider")  # practice portfolio key to mirror
USD_PER_EUR = float(os.environ.get("IBKR_USD_PER_EUR", "100"))  # €10 practice trade -> $1,000 order
MAX_ORDER_USD = 5000.0
FILL_WAIT = 45          # seconds to wait for a fill before reporting "waiting for the open"
SYNC_SECONDS = 600      # fills / account line refresh
REQUEST_TIMEOUT = 20    # seconds per IBKR request (ib_async default = wait forever)
JOB_LIMIT = 180         # a job running longer = stuck worker (2026-11-16: hung all day, buys never sent)
LIVE_PORT = 4001
DOWN_WARN = 900         # gateway unreachable this long -> one warning (the daily 05:30 restart takes ~1 min)
HOLD_MAX = 2 * 86400    # held buys/sells older than this are dropped when the gateway comes back
CONN_ERRORS = (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError)


def enabled():
    return ON and HAVE_LIB and PORT != LIVE_PORT


def is_paper(accounts):
    return bool(accounts) and all(str(a).upper().startswith("D") for a in accounts)


def shares_for(eur, price):
    """Whole shares for a practice trade of `eur` at `price` (USD), capped at MAX_ORDER_USD; 0 = too small."""
    if not price or price <= 0:
        return 0
    usd = min(eur * USD_PER_EUR, MAX_ORDER_USD)
    return int(math.floor(usd / price))


def new_events(st, key=MIRROR):
    """Practice buy/sell events of the mirrored portfolio not yet sent (marks them as sent)."""
    ib = st.setdefault("ib", {})
    if "since" not in ib:
        ib["since"] = int(time.time())
    seen = set(ib.get("seen", []))
    pf = st.get("pf", {}).get(key)
    out = []
    for e in (pf or {}).get("log", []):
        if e.get("a") not in ("buy", "sell") or e.get("t", 0) < ib["since"]:
            continue
        k = f"{e['a']}|{e['tk']}|{e.get('at', e['t'])}"
        if k in seen:
            continue
        seen.add(k)
        out.append(dict(e))
    ib["seen"] = sorted(seen)[-200:]
    return out


def fill_line(side, tk, qty, avg, practice_p):
    diff = ""
    if practice_p and avg:
        d = (avg / practice_p - 1) * 100 * (1 if side == "BOT" else -1)  # positive = real fill worse
        diff = f" · practice {practice_p:,.4g} ({'real worse' if d > 0.005 else 'real better' if d < -0.005 else 'same'} {abs(d):.2f}%)"
    verb = "bought" if side == "BOT" else "sold"
    return f"🏦 IBKR paper: {verb} {qty:g} <b>${html.escape(tk)}</b> at {avg:,.4g}{diff}"


def reason(msgs):
    """IBKR's reject text, short and without HTML (e.g. the EU 'no KID' rule for US ETFs)."""
    txt = " ".join(m for m in msgs if m and "Order Canceled" not in m)
    txt = re.sub(r"<[^>]+>", " ", txt)
    if "Key Information Document" in txt or "KID" in txt:
        return "EU retail rule: no KID document (US ETFs can't be bought)"
    return re.sub(r"\s+", " ", txt).strip()[:160]


class Mirror:
    """Runs ib_async in its own thread (own asyncio loop); the bot thread only queues jobs and reads results."""

    def __init__(self, send):
        self.send = send
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.line = None            # account summary for the report
        self.practice = {}          # tk -> last practice price (for the fill comparison)
        self.posted = set()         # execution ids already posted
        self.ib_state = None        # st["ib"]: posted ids are written into it right after posting
        self.last_sync = 0.0
        self.thread = None
        self.job = None             # job the worker is running now
        self.started = 0.0          # when it started
        self.gen = 0                # worker generation; a stuck worker is abandoned by bumping it
        self.down_since = None      # gateway unreachable since (time); None = reachable / unknown
        self.down_warned = False
        self.held = []              # buy/sell jobs that failed because the gateway was down; retried on reconnect
        logging.getLogger("ib_async").setLevel(logging.WARNING)

    # ---- bot thread
    def sync(self, st):
        """Queue new practice trades; called after every practice step. Returns True if state changed."""
        ib = st.setdefault("ib", {})
        with self.lock:
            if not self.posted and ib.get("posted"):
                self.posted = set(ib["posted"])
            ib["posted"] = sorted(self.posted)[-300:]
            self.ib_state = ib
            if self.line:
                ib["line"] = self.line
            if not self.held and ib.get("held"):     # survive a bot restart
                self.held = list(ib["held"])
            ib["held"] = list(self.held)
        self._watchdog()
        evs = new_events(st)
        for e in evs:
            log.info("IBKR mirror: queued %s %s", e["a"], e["tk"])
            self.q.put(e)
        if time.time() - self.last_sync >= SYNC_SECONDS:
            self.last_sync = time.time()
            self.q.put({"a": "sync"})
        if not self.thread or not self.thread.is_alive():
            self._start()
        return bool(evs)

    def _start(self):
        self.thread = threading.Thread(target=self._run, args=(self.gen, self.q), name="ibkr", daemon=True)
        self.thread.start()

    def _watchdog(self, now=None):
        """A job hanging past JOB_LIMIT: leave that worker behind (it can't be killed), start a fresh one with a
        new queue and give the stuck buy/sell another try there. Returns True if it restarted."""
        now = now or time.time()
        with self.lock:
            job, started = self.job, self.started
        if not job or now - started < JOB_LIMIT:
            return False
        a, tk = job.get("a"), job.get("tk", "")
        log.warning("IBKR mirror: %s %s stuck for %ds; starting a new worker", a, tk, int(now - started))
        old = self.q
        self.gen += 1
        self.q = queue.Queue()
        with self.lock:
            self.job = None
        if a in ("buy", "sell") and not job.get("retry"):
            self.q.put(dict(job, retry=True))
        while True:  # carry over what was waiting behind the stuck job
            try:
                self.q.put(old.get_nowait())
            except queue.Empty:
                break
        self.send(f"🏦⚠️ IBKR paper: the connection hung ({html.escape(a or '')}"
                  f"{' <b>$' + html.escape(tk) + '</b>' if tk else ''}) — restarted"
                  f"{', trying again' if a in ('buy', 'sell') and not job.get('retry') else ''}", True)
        self._start()
        return True

    # ---- worker thread
    def _run(self, gen=0, q=None):
        asyncio.set_event_loop(asyncio.new_event_loop())
        q = q or self.q
        while gen == self.gen:
            job = q.get()
            if gen != self.gen:  # abandoned while waiting; the new worker has the queue
                break
            with self.lock:
                self.job, self.started = job, time.time()
            if job.get("a") != "sync":
                log.info("IBKR mirror: %s %s …", job.get("a"), job.get("tk"))
            try:
                self._do(dict(job, gen=gen))
            except CONN_ERRORS as e:
                if not self._gateway_down(job, e):
                    log.warning("IBKR mirror job failed: %s %s", type(e).__name__, str(e)[:120])
                    if job.get("a") in ("buy", "sell"):
                        self.send(f"🏦⚠️ IBKR paper: could not {job['a']} <b>${html.escape(job['tk'])}</b> "
                                  f"({html.escape(type(e).__name__)}) — practice trade still counts", True)
            except Exception as e:
                log.warning("IBKR mirror job failed: %s %s", type(e).__name__, str(e)[:120])
                if job.get("a") in ("buy", "sell"):
                    self.send(f"🏦⚠️ IBKR paper: could not {job['a']} <b>${html.escape(job['tk'])}</b> "
                              f"({html.escape(type(e).__name__)}) — practice trade still counts", True)
            finally:
                with self.lock:
                    if gen == self.gen:
                        self.job = None

    def _gateway_down(self, job, e, now=None):
        """Connection to IB Gateway failed (2026-11-23: stuck at its login screen after the 05:30 restart, 7 buys
        lost). Buys/sells are held and retried on reconnect; one warning after DOWN_WARN. Returns True if handled
        (False = the gateway was reachable for this job, so it is a real per-order failure)."""
        if not isinstance(e, ConnectionRefusedError) and self.down_since is None:
            return False      # a timeout while the gateway was up: a real failure of this order
        now = now or time.time()
        log.info("IBKR mirror: gateway unreachable (%s)%s", type(e).__name__,
                 f", holding {job['a']} {job.get('tk')}" if job.get("a") in ("buy", "sell") else "")
        with self.lock:
            if self.down_since is None:
                self.down_since = now
            if job.get("a") in ("buy", "sell"):
                self.held.append({k: v for k, v in job.items() if k not in ("gen", "retry")})
            if self.ib_state is not None:
                self.ib_state["held"] = list(self.held)
            warn = not self.down_warned and now - self.down_since >= DOWN_WARN
            if warn:
                self.down_warned = True
            n = len(self.held)
        if warn:
            self.send(f"🏦⚠️ <b>IB Gateway not reachable</b> for {int((now - self.down_since) // 60)} min "
                      f"(port {PORT}) — paper mirror paused"
                      f"{f', {n} order(s) held, sent when it is back' if n else ''}. "
                      f"The server restarts the gateway by itself; if this stays, check it.", False)
        return True

    def _back_up(self, now=None):
        """First successful connection after an outage: report it and re-queue held orders (recent ones only)."""
        now = now or time.time()
        with self.lock:
            if self.down_since is None:
                return []
            since, warned = self.down_since, self.down_warned
            keep = [j for j in self.held if now - j.get("t", now) <= HOLD_MAX]
            dropped = len(self.held) - len(keep)
            self.down_since, self.down_warned, self.held = None, False, []
            if self.ib_state is not None:
                self.ib_state["held"] = []
        log.info("IBKR mirror: gateway back after %d min, %d held order(s) re-queued", (now - since) // 60, len(keep))
        if warned or keep or dropped:
            self.send(f"🏦✅ IB Gateway back after {int((now - since) // 60)} min"
                      f"{f' — sending {len(keep)} held order(s)' if keep else ''}"
                      f"{f' ({dropped} older than 2 days dropped)' if dropped else ''}", True)
        return keep

    def _connect(self):
        from ib_async import IB
        ib = IB()
        ib.RequestTimeout = REQUEST_TIMEOUT
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=20)
        for j in self._back_up():
            self.q.put(j)
        accts = ib.managedAccounts()
        if not is_paper(accts):
            ib.disconnect()
            raise RuntimeError("not a paper account — mirror refuses to trade")
        return ib

    def _do(self, job):
        from ib_async import MarketOrder, Stock
        ib = self._connect()
        errors = []
        try:
            ib.errorEvent += lambda *args: errors.append(str(args[2]) if len(args) > 2 else "")
        except Exception:
            pass
        try:
            a = job["a"]
            if a in ("buy", "sell"):
                tk = job["tk"]
                c = Stock(tk, "SMART", "USD")
                if not ib.qualifyContracts(c):
                    self.send(f"🏦 IBKR paper: <b>${html.escape(tk)}</b> not found at IBKR (US) — skipped", True)
                    return
                self.practice[tk] = job.get("p")
                if a == "buy":
                    qty = shares_for(job.get("eur", 10.0), job.get("p"))
                    if qty < 1:
                        self.send(f"🏦 IBKR paper: <b>${html.escape(tk)}</b> price too high for one share — skipped", True)
                        return
                    order = MarketOrder("BUY", qty, tif="DAY")
                else:
                    for t in ib.openTrades():  # buy still waiting -> cancel instead of selling
                        if t.contract.symbol == tk and t.order.action == "BUY":
                            ib.cancelOrder(t.order)
                    ib.sleep(1)
                    qty = sum(p.position for p in ib.positions() if p.contract.symbol == tk)
                    if qty <= 0:
                        self.send(f"🏦 IBKR paper: nothing to sell in <b>${html.escape(tk)}</b> "
                                  f"(buy never filled — cancelled)", True)
                        return
                    order = MarketOrder("SELL", qty, tif="DAY")
                if job.get("gen", self.gen) != self.gen:  # this worker was left behind as stuck; the new one orders
                    log.warning("IBKR mirror: stale worker woke up, not placing %s %s", a, tk)
                    return
                trade = ib.placeOrder(c, order)
                end = time.time() + FILL_WAIT
                while time.time() < end and not trade.isDone():
                    ib.sleep(1)
                if trade.orderStatus.status in ("Cancelled", "ApiCancelled", "Inactive"):
                    msg = reason(errors + [x.message for x in trade.log if x.message])
                    self.send(f"🏦⚠️ IBKR paper: {a} order for <b>${html.escape(tk)}</b> rejected "
                              f"({html.escape(msg or trade.orderStatus.status)})", True)
                elif not trade.fills:
                    self.send(f"🏦 IBKR paper: {a} {qty:g} <b>${html.escape(tk)}</b> placed — fills at the next "
                              f"market open (posted then)", True)
            self._post_fills(ib)
            self._account(ib)
        finally:
            ib.disconnect()

    def _post_fills(self, ib):
        from ib_async import ExecutionFilter
        fills = ib.reqExecutions(ExecutionFilter())
        groups = {}
        for f in fills:
            eid = f.execution.execId
            if eid in self.posted:
                continue
            key = (f.contract.symbol, f.execution.side, f.execution.orderId)
            groups.setdefault(key, []).append(f)
        for (tk, side, _), fs in groups.items():
            qty = sum(f.execution.shares for f in fs)
            avg = sum(f.execution.shares * f.execution.price for f in fs) / qty if qty else 0
            if self.send(fill_line(side, tk, qty, avg, self.practice.get(tk)), False) is not False:
                with self.lock:
                    self.posted.update(f.execution.execId for f in fs)
                    if self.ib_state is not None:   # saved even if the bot restarts before the next sync
                        self.ib_state["posted"] = sorted(self.posted)[-300:]

    def _account(self, ib):
        tags = {x.tag: x.value for x in ib.accountSummary()}
        nl = float(tags.get("NetLiquidation", 0) or 0)
        port = ib.portfolio()
        upl = sum(p.unrealizedPNL or 0 for p in port)
        with self.lock:
            self.line = (f"🏦 IBKR paper mirror ({MIRROR}): net value ${nl:,.0f} · {len(port)} positions · "
                         f"open P/L ${upl:+,.0f}")

"""💰 Companies the stock market values at LESS than their cash minus everything they owe (owner 2026-11-20, "3. do").

Classic target for buyers, liquidators (Tang Capital / Concentra, XOMA), activists and reverse mergers. Built once a
week from SEC XBRL "frames" (one call per balance-sheet item covers every US filer): cash + short-term investments
minus TOTAL liabilities (debt, royalty financing, leases, payables — e.g. Milestone MIST had $145M cash but $169M
liabilities, so it is NOT on the list). Price from Yahoo; banks/insurers/SPACs/funds (SIC 6000-6799) are left out.
Posted quietly to the buyout channel on Fridays at the buyout report time (and once after the first build).
Caveats in the message: the cash is from the last quarterly report and burns, and a cheap stock can stay cheap.
"""
import html
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

import extras

log = logging.getLogger("bot")
ON = os.environ.get("NETCASH_SCREEN", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
FRAMES = "https://data.sec.gov/api/xbrl/frames/"
CASH = "us-gaap/CashAndCashEquivalentsAtCarryingValue/USD"
STI = ("us-gaap/ShortTermInvestments/USD", "us-gaap/MarketableSecuritiesCurrent/USD",
       "us-gaap/AvailableForSaleSecuritiesDebtSecuritiesCurrent/USD")
LIAB = "us-gaap/Liabilities/USD"
SHARES = ("dei/EntityCommonStockSharesOutstanding/shares", "us-gaap/CommonStockSharesOutstanding/shares")
EXCHANGES = {"Nasdaq", "NYSE"}
MIN_NET = 10e6            # cash left after all liabilities
MIN_CAP = 10e6
MAX_RATIO = 1.0           # market cap / (cash - liabilities)
LIST_MAX = 20
WEEKDAY = 4               # Friday
REPORT_TIME = os.environ.get("BUYOUT_REPORT_TIME", "17:00")
REBUILD_DAYS = 6


def quarter(today):
    """Frame name of the latest calendar quarter that ended >= 50 days ago (reports are in by then)."""
    d = today - timedelta(days=50)
    y, q = d.year, (d.month - 1) // 3          # quarters that ENDED by d: the one before d's quarter
    if q == 0:
        y, q = y - 1, 4
    return f"CY{y}Q{q}I", (f"CY{y}Q{q - 1}I" if q > 1 else f"CY{y - 1}Q4I")


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": f"price-alert-bot {SEC_CONTACT}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def frame(concept, period, get=_get):
    """{cik: value} (empty when the frame does not exist yet)."""
    try:
        return {x["cik"]: x["val"] for x in get(FRAMES + f"{concept}/{period}.json")["data"]}
    except Exception:
        return {}


def prices(tickers, get=None):
    """{ticker: price} via Yahoo spark, 20 symbols per request."""
    out = {}
    for i in range(0, len(tickers), 20):
        url = ("https://query1.finance.yahoo.com/v7/finance/spark?" +
               urllib.parse.urlencode({"symbols": ",".join(tickers[i:i + 20]), "range": "1d", "interval": "1d"}))
        try:
            if get:
                j = get(url)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    j = json.loads(r.read())
            for x in j["spark"]["result"]:
                m = (x.get("response") or [{}])[0].get("meta") or {}
                if m.get("regularMarketPrice"):
                    out[x["symbol"]] = m["regularMarketPrice"]
        except Exception:
            continue
        time.sleep(0.3)
    return out


def sic(cik, get=_get):
    try:
        return int(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("sic") or 0)
    except Exception:
        return 0


def build(today=None, get=_get, price_of=prices, sic_of=None):
    """[{t, name, cap, net, cash, liab, ratio, price}] sorted cheapest first."""
    today = today or date.today()
    sic_of = sic_of or (lambda c: sic(c, get))
    vals = {}
    for period in reversed(quarter(today)):      # the older quarter first, the newer overwrites it
        c, l = frame(CASH, period, get), frame(LIAB, period, get)
        s = {}
        for con in STI:
            for k, v in frame(con, period, get).items():
                s[k] = max(s.get(k, 0), v)       # the same holding can be tagged twice: take the larger, not the sum
        sh = {}
        for con in SHARES:
            for k, v in frame(con, period, get).items():
                sh.setdefault(k, v)
        for k in c.keys() & l.keys() & sh.keys():
            vals[k] = (c[k] + s.get(k, 0), l[k], sh[k])
    ex = get("https://www.sec.gov/files/company_tickers_exchange.json")
    tick = {}
    for cik, name, t, exch in ex["data"]:
        if exch in EXCHANGES and cik not in tick and t and "-" not in t:
            tick[cik] = (t.upper(), name)
    pre = {k: v for k, v in vals.items() if k in tick and v[0] - v[1] >= MIN_NET and v[2] > 0}
    px = price_of([tick[k][0] for k in pre])
    rows = []
    for k, (cash, liab, sh) in pre.items():
        t, name = tick[k]
        p = px.get(t)
        if not p:
            continue
        cap, net = p * sh, cash - liab
        if cap >= MIN_CAP and cap / net <= MAX_RATIO:
            rows.append({"t": t, "name": name, "cik": k, "cap": cap, "net": net, "cash": cash, "liab": liab,
                         "ratio": cap / net, "price": p})
    rows.sort(key=lambda r: r["ratio"])
    out = []
    for r in rows:
        code = sic_of(r["cik"])
        if 6000 <= code <= 6799:
            continue
        out.append(r)
        if len(out) >= LIST_MAX:
            break
    return out


def _m(x):
    return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.0f}M"


def message(rows, before=()):
    if not rows:
        return ""
    lines = ["💰 <b>Cheaper than its cash</b>: the stock market values these companies at less than the cash they "
             "would have left after paying everything they owe (last quarterly report)", "<pre>"]
    lines.append(f"{'Ticker':6} {'Value':>6} {'Cash-all debts':>14} {'Paid':>5}")
    for r in rows:
        mark = "+" if before and r["t"] not in before else " "
        lines.append(f"{r['t'] + mark:<6} {_m(r['cap']):>6} {_m(r['net']):>14} {r['ratio'] * 100:>4.0f}%")
    lines.append("</pre>")
    lines.append("Paid = what the market pays for each $1 of that cash. " + ("+ = new this week. " if before else ""))
    lines.append("<i>Why it matters: buyers of cash shells (e.g. Tang Capital / Concentra, XOMA), activists and "
                 "reverse mergers target these. Why it can stay cheap: most are burning cash (the number shrinks every "
                 "quarter) or failed a key product. Not advice.</i>")
    return "\n".join(lines)


class NetCashScreen:
    def __init__(self, send, build_fn=None, now=None):
        self.send, self.build = send, build_fn or build
        self.now = now or extras.local_now
        self.thread, self.result = None, None

    @staticmethod
    def enabled():
        return ON and bool(SEC_CONTACT)

    def check(self, state):
        st = state.setdefault("_netcash", {})
        now = self.now()
        changed = False
        if self.thread and not self.thread.is_alive():
            res, self.thread = self.result, None
            if res is not None:
                st["prev"] = [r["t"] for r in st.get("rows", [])]
                st["rows"] = [{k: r[k] for k in ("t", "name", "cap", "net", "ratio")} for r in res]
                st["built"] = now.date().isoformat()
                changed = True
                log.info("Net-cash screen: %d companies", len(res))
        built = st.get("built")
        want = not built or (now.weekday() == WEEKDAY and built != now.date().isoformat()
                             and (now.date() - date.fromisoformat(built)).days >= REBUILD_DAYS)
        if want and not self.thread and now.timestamp() - st.get("tried", 0) > 6 * 3600:
            st["tried"] = now.timestamp()
            changed = True

            def run():
                try:
                    self.result = self.build()
                except Exception as e:
                    log.warning("Net-cash screen failed: %s", type(e).__name__)
                    self.result = None
            self.result = None
            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
        week = now.strftime("%G-W%V")
        fresh = st.get("built") == now.date().isoformat()
        due = st.get("rows") is not None and st.get("posted") != week and (
            not st.get("posted_once") or (fresh and now.weekday() == WEEKDAY and now.strftime("%H:%M") >= REPORT_TIME))
        if due:
            text = message(st["rows"], st.get("prev") or ())
            if not text or self.send(text, True):
                st["posted"], st["posted_once"] = week, True
                changed = True
        return changed

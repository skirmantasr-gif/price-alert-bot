"""📊 Earnings tracker: how each watchlist stock moved around its past quarterly reports, posted a few days before
the next one (date from the catalyst calendar, extras._cal).

Past report days come from SEC EDGAR:
  - US companies: 8-K item 2.02 (results of operations) in the submissions list
  - foreign companies (6-K, no item codes): EDGAR full-text search for "financial results" / "earnings release"
A filing is matched to its trading day by volume: the filing day or the next trading day, whichever traded more
(covers releases before the open, after the close, and filings made hours after the press release). Days that
traded < 1.3x the 20-day average are dropped (a 6-K that only mentions results), and within 40 days only the
busiest day is kept (one report per quarter).

For each report: 5 days before, opening gap, report day (previous close -> close), next 5 days, volume ratio.
Past patterns are context, not a forecast: the message says so. GitHub only (needs SEC_CONTACT)."""
import html
import json
import logging
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import extras

log = logging.getLogger("bot")
ON = os.environ.get("EARNINGS_TRACKER", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
LEAD_DAYS = int(os.environ.get("EARNINGS_LEAD_DAYS", "3"))   # post when the report is 0..3 days away
MAX_REPORTS = 8
YEARS = 3
MIN_VOL_RATIO = 1.3
CLUSTER_DAYS = 40
CHECK_SECONDS = 3600
FTS_PHRASES = ('"financial results"', '"earnings release"')
NOT_LISTED = {"DXYZ"}   # closed-end fund: no quarterly results calls


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"price-alert-bot {SEC_CONTACT}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _yget(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- data
def filing_days(cik, since):
    """Dates (YYYY-MM-DD) of results filings since `since`: 8-K item 2.02, else 6-K full-text hits."""
    sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = sub.get("filings", {}).get("recent", {})
    forms, dates, items = r.get("form", []), r.get("filingDate", []), r.get("items", [])
    out = {dates[i] for i in range(len(forms))
           if forms[i] in ("8-K", "8-K/A") and "2.02" in (items[i] if i < len(items) else "") and dates[i] >= since}
    if out or not any(f == "6-K" for f in forms):
        return sorted(out)
    for q in FTS_PHRASES:
        try:
            j = json.loads(_get("https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(
                {"q": q, "forms": "6-K", "ciks": f"{cik:010d}", "dateRange": "custom", "startdt": since,
                 "enddt": date.today().isoformat()})))
            out |= {h["_source"]["file_date"] for h in j.get("hits", {}).get("hits", [])
                    if h.get("_source", {}).get("file_date")}
        except Exception as e:
            log.info("Earnings: full-text search failed (%s)", type(e).__name__)
        time.sleep(0.3)
    return sorted(out)


def daily_bars(ticker):
    """[(YYYY-MM-DD, open, close, volume)] for about YEARS+ years (New York dates)."""
    j = json.loads(_yget("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(ticker) +
                         "?range=5y&interval=1d"))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    off = (j["meta"].get("gmtoffset") or -18000)
    out = []
    for t, o, c, v in zip(j.get("timestamp") or [], q["open"], q["close"], q["volume"]):
        if o and c and v is not None:
            out.append((datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"), o, c, v))
    return out


# ---------------------------------------------------------------- analysis (pure: tested without network)
def reactions(days, bars):
    """Newest first: [{d, pre5, gap, day, next5, vr}] for each report day found among the filing days."""
    idx = {b[0]: i for i, b in enumerate(bars)}
    dates = [b[0] for b in bars]

    def at_or_after(d):
        return next((i for i, x in enumerate(dates) if x >= d), None)

    def ratio(i):
        if i is None or i < 21:
            return 0.0
        avg = statistics.mean(b[3] for b in bars[i - 21:i - 1]) or 1
        return bars[i][3] / avg

    picks = {}
    for d in days:
        i = at_or_after(d)
        if i is None:
            continue
        opts = [i] + ([i + 1] if i + 1 < len(bars) else [])
        best = max(opts, key=ratio)
        if ratio(best) >= MIN_VOL_RATIO and best >= 6:
            picks[best] = ratio(best)
    keep = []
    for i in sorted(picks, key=lambda i: -picks[i]):     # busiest first; drop near-duplicates
        di = datetime.strptime(dates[i], "%Y-%m-%d")
        if all(abs((di - datetime.strptime(dates[j], "%Y-%m-%d")).days) > CLUSTER_DAYS for j in keep):
            keep.append(i)
    out = []
    for i in sorted(keep, reverse=True)[:MAX_REPORTS]:
        prev = bars[i - 1][2]
        out.append({"d": dates[i], "pre5": bars[i - 1][2] / bars[i - 6][2] - 1, "gap": bars[i][1] / prev - 1,
                    "day": bars[i][2] / prev - 1,
                    "next5": bars[i + 5][2] / bars[i][2] - 1 if i + 5 < len(bars) else None,
                    "vr": picks[i]})
    del idx
    return out


def _p(x):
    return "   —  " if x is None else f"{x * 100:+5.1f}%"


def message(ticker, when, rows):
    """Telegram HTML text for the pre-report note."""
    d = datetime.strptime(when, "%Y-%m-%d")
    days = (d.date() - extras.local_now().date()).days
    head = (f"📊 <b>${ticker} reports {'today' if days <= 0 else 'tomorrow' if days == 1 else f'in {days} days'}</b>"
            f" ({d.strftime('%a %b %d')}) — how it moved around its last {len(rows)} report{'s' * (len(rows) != 1)}")
    if not rows:
        return head.split(" — ")[0] + "\n<i>No past report days found in SEC filings — no pattern to show.</i>"
    lines = ["Report      5d before   gap    day   next 5d  volume"]
    for r in rows:
        lines.append(f"{datetime.strptime(r['d'], '%Y-%m-%d').strftime('%b %d %y')}  {_p(r['pre5'])} {_p(r['gap'])} "
                     f"{_p(r['day'])} {_p(r['next5'])}  {r['vr']:4.1f}×")
    dn = [r["day"] for r in rows]
    n5 = [r["next5"] for r in rows if r["next5"] is not None]
    up_before = sum(1 for r in rows if r["pre5"] > 0)
    summary = [f"Report day: down {sum(x < 0 for x in dn)} of {len(dn)} · middle {statistics.median(dn) * 100:+.1f}% · "
               f"biggest move {max(dn, key=abs) * 100:+.1f}%"]
    if n5:
        summary.append(f"Next 5 days: down {sum(x < 0 for x in n5)} of {len(n5)} · middle {statistics.median(n5) * 100:+.1f}%")
    summary.append(f"Rose in the 5 days before: {up_before} of {len(rows)}")
    few = " Few reports — a weak guide." if len(rows) < 6 else ""
    return (head + "\n<pre>" + html.escape("\n".join(lines)) + "</pre>\n" + "\n".join(summary) +
            f"\n<i>Report day = previous close → close of the first trading day after the release.{few} Past "
            "reactions don't predict this one: they show how big the swings usually are. Not advice.</i>")


# ---------------------------------------------------------------- watcher
class EarningsWatch:
    def __init__(self, send, tickers, cik_of):
        self.send = send              # send(text, silent)
        self.tickers = set(tickers)
        self.cik_of = cik_of          # ticker -> SEC CIK (news.sec_cik)
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON and bool(SEC_CONTACT)

    @staticmethod
    def label():
        return f"ON (past reactions posted {LEAD_DAYS} days before each watchlist report)"

    def pattern(self, ticker):
        cik = self.cik_of(ticker)
        if not cik:
            return []
        since = (date.today() - timedelta(days=365 * YEARS)).isoformat()
        return reactions(filing_days(cik, since), daily_bars(ticker))

    def check(self, state):
        if time.monotonic() - self.last < CHECK_SECONDS:
            return False
        self.last = time.monotonic()
        er = state.setdefault("_earn", {})
        sent = er.setdefault("sent", [])
        changed = False
        for tk, iso, kind, _label in extras.calendar_upcoming(state, LEAD_DAYS):
            key = f"{tk}:{iso}"
            if kind != "earnings" or tk not in self.tickers or tk in NOT_LISTED or key in sent:
                continue
            try:
                rows = self.pattern(tk)
            except Exception as e:
                log.warning("Earnings pattern failed: %s", type(e).__name__)
                continue
            if self.send(message(tk, iso, rows), False):
                sent.append(key)
                changed = True
                log.info("Earnings pattern sent (%d past reports)", len(rows))
            break   # one per hour at most: SEC asks for gentle use
        er["sent"] = sent[-60:]
        return changed

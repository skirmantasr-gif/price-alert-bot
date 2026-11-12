"""👥 Insider cluster buys: two or more officers / directors of the same company buying its shares on the open
market within CLUSTER_DAYS. All US companies, from the SEC's live list of new Form 4 filings.

Why only this: open-market purchases with own money (transaction code "P") are the informative kind; sales,
grants and option exercises mostly aren't. Several insiders buying at once is the better-studied version of the
signal. The edge is real but small, so the message says so.

Counted: code P, non-derivative, price >= $1, by an officer or director (a fund that only owns 10% is left out).
Posted when a company reaches 2 different buyers and >= CLUSTER_MIN_USD in total, then again when another insider
joins (max POST_MAX per company per cluster). Loud with 3+ buyers, a CEO/CFO among them, or >= $1M in total.
Watchlist stocks -> main channel, all other stocks -> the contracts channel. GitHub only (needs SEC_CONTACT)."""
import html
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot")
ON = os.environ.get("INSIDER_CLUSTERS", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
SCAN_SECONDS = int(os.environ.get("INSIDER_SCAN_SECONDS", "600"))
CLUSTER_DAYS = 14
CLUSTER_MIN_USD = float(os.environ.get("INSIDER_CLUSTER_MIN_USD", "100000"))
LOUD_USD = 1_000_000
POST_MAX = 3
MAX_POSTS = 8              # clusters posted per scan (a busy window after earnings season)
MAX_PAGES = 8              # 100 feed entries (~50 filings) per page
MAX_FILINGS = 400          # per scan, so a backlog can't hold the thread for long
HIST_MAX = 12              # earlier Form 4s read per company when it first shows a buy (catches clusters that
HIST_COMPANIES = 15        # started before the bot was watching); companies looked up per scan
FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=only&count=100&output=atom&start="
ATOM = "{http://www.w3.org/2005/Atom}"
TOP_ROLE = re.compile(r"\b(CEO|chief executive|CFO|chief financial|president)\b", re.I)


MAX_BYTES = 5_000_000      # filings are written by the companies: never read more than this
SEC_PAUSE = 0.25           # s between SEC requests; other modules use SEC too (limit: 10 per second in total)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"price-alert-bot {SEC_CONTACT}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return data


# ---------------------------------------------------------------- SEC data
def feed_page(start):
    """[(accession, issuer cik)] of Form 4 filings, newest first (one entry per filing: the Issuer line)."""
    root = ET.fromstring(_get(FEED + str(start)))
    out = []
    for e in root.findall(ATOM + "entry"):
        title = e.findtext(ATOM + "title") or ""
        cat = e.find(ATOM + "category")
        if not title.endswith("(Issuer)") or (cat is not None and cat.get("term") != "4"):
            continue
        acc = re.search(r"accession-number=([\d-]+)", e.findtext(ATOM + "id") or "")
        cik = re.search(r"\((\d{10})\) \(Issuer\)", title)
        if acc and cik:
            out.append((acc.group(1), int(cik.group(1))))
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def parse_form4(xml):
    """{ticker, company, owner, role, officer_or_director, plan, buys: [(date, shares, price)]} from Form 4 XML."""
    root = ET.fromstring(xml)
    tk = (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper()
    roles, od = [], False
    for rel in root.findall("reportingOwner/reportingOwnerRelationship"):
        yes = lambda tag: (rel.findtext(tag) or "").strip().lower() in ("1", "true")
        if yes("isOfficer"):
            roles.append(" ".join((rel.findtext("officerTitle") or "Officer").split()))
            od = True
        if yes("isDirector"):
            roles.append("Director")
            od = True
        if yes("isTenPercentOwner"):
            roles.append("10% owner")
    notes = " ".join((f.text or "") for f in root.iter("footnote"))
    buys = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if (t.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        sh = _num(t.findtext("transactionAmounts/transactionShares/value"))
        pr = _num(t.findtext("transactionAmounts/transactionPricePerShare/value"))
        if sh > 0 and pr > 0:
            buys.append(((t.findtext("transactionDate/value") or "")[:10], sh, pr))
    names = [" ".join((o.findtext("reportingOwnerId/rptOwnerName") or "").split()) for o in root.findall("reportingOwner")]
    return {"ticker": tk, "company": (root.findtext("issuer/issuerName") or "").strip(),
            "owner": " & ".join(n for n in names if n) or "insider",
            "role": ", ".join(dict.fromkeys(roles)) or "insider", "officer_or_director": od,
            "plan": (root.findtext("aff10b5One") or "").strip().lower() in ("1", "true") or "10b5-1" in notes,
            "buys": buys}


def recent_form4(cik, days=CLUSTER_DAYS):
    """Accession numbers of the issuer's Form 4 filings in the last `days` (SEC submissions list)."""
    import json
    r = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")).get("filings", {}).get("recent", {})
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [a for a, f, d in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []))
            if f == "4" and d >= since]


def filing_xml(cik, acc):
    """The Form 4 XML from the filing's full text file (one request)."""
    txt = _get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}.txt").decode("utf-8", "replace")
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", txt, re.S)
    if not m:
        raise ValueError("no XML in filing")
    return m.group(1).encode()


def price_now(tk):
    import json
    j = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(tk) + "?range=1d&interval=1d",
        headers={"User-Agent": "Mozilla/5.0 (compatible; price-alert-bot/1.0)"}), timeout=20).read())
    return float(j["chart"]["result"][0]["meta"]["regularMarketPrice"])


def market_cap(tk):
    try:
        import movers
        return movers.market_cap(tk)
    except Exception:
        return None


# ---------------------------------------------------------------- cluster logic (pure)
def add_buys(book, f, acc, today):
    """Record a parsed filing's buys in book {ticker: [[date, owner, role, usd, plan, acc, shares]]}. True if added."""
    if not f["ticker"] or not f["officer_or_director"] or not f["buys"]:
        return False
    usd = sum(sh * pr for _, sh, pr in f["buys"] if pr >= 1.0)
    shares = sum(sh for _, sh, pr in f["buys"] if pr >= 1.0)
    if usd <= 0:
        return False
    rows = book.setdefault(f["ticker"], [])
    if any(r[5] == acc for r in rows):
        return False
    rows.append([max(d for d, _, _ in f["buys"]) or today, f["owner"][:60], f["role"][:60], round(usd),
                 bool(f["plan"]), acc, round(shares)])
    return True


def cluster(rows, today, days=CLUSTER_DAYS):
    """Buys within the window -> (distinct owners, total usd, rows) or None if not a cluster."""
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    win, same = [], {}
    for r in sorted(r for r in rows if r[0] >= since):
        k = (r[0], r[3], r[6] if len(r) > 6 else None)   # a fund + its partner file the SAME purchase jointly
        if k in same:
            same[k][1] = (same[k][1] + " / " + r[1])[:90]
            continue
        same[k] = list(r)
        win.append(same[k])
    owners = {r[1] for r in win}
    total = sum(r[3] for r in win)
    if len(owners) < 2 or total < CLUSTER_MIN_USD:
        return None
    return len(owners), total, sorted(win)


def _usd(v):
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}k"


def message(tk, company, n, total, rows, cap=None, now_px=None, avg_px=None):
    loud = n >= 3 or total >= LOUD_USD or any(TOP_ROLE.search(r[2]) for r in rows)
    first, last = rows[0][0], rows[-1][0]
    span = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days + 1
    lines = [f"👥{'🚨' if loud else ''} <b>${tk}</b> INSIDER CLUSTER BUY — {n} insiders bought {_usd(total)} "
             f"in {span} day{'s' * (span != 1)}", html.escape(company)]
    for d, owner, role, usd, plan, *_ in rows[-8:]:
        lines.append(f"• {datetime.strptime(d, '%Y-%m-%d').strftime('%b %d')} · {html.escape(owner.title())} "
                     f"({html.escape(role)}) · {_usd(usd)}" + (" · pre-planned (10b5-1)" if plan else ""))
    facts = []
    if cap:
        facts.append(f"value {_usd(cap) if cap < 1e9 else f'${cap / 1e9:.1f}B'}")
    if now_px and avg_px:
        facts.append(f"price now ${now_px:,.2f} ({(now_px / avg_px - 1) * 100:+.0f}% vs their average ${avg_px:,.2f})")
    if facts:
        lines.append(" · ".join(facts))
    lines.append(f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;type=4&amp;'
                 f'owner=include&amp;CIK={urllib.parse.quote(tk)}">SEC filings</a>')
    lines.append("<i>Several insiders buying with their own money is a well-studied signal, but the edge is small "
                 "(a few % over months) and many clusters go nowhere. Not advice.</i>")
    return "\n".join(lines), loud


def _score(state, tk, conf, text, price):
    """Save the post as an UP call for the 🎓 review (scored only; paper.NOT_TRADED keeps it out of the practice
    portfolios)."""
    if state is None or not price:
        return
    try:
        import learn
        learn.record(state, "insider", tk, "up", conf, "rules", text, price)
    except Exception as e:
        log.warning("Insider learning record failed: %s", type(e).__name__)


# ---------------------------------------------------------------- watcher
class InsiderWatch:
    def __init__(self, send_main, send_other, watchlist):
        self.send_main, self.send_other = send_main, send_other    # send(text, silent)
        self.watch = set(watchlist)
        self.thread, self.result = None, None
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON and bool(SEC_CONTACT)

    @staticmethod
    def label():
        return (f"ON (all US stocks, every {SCAN_SECONDS // 60} min: 2+ officers/directors buying within "
                f"{CLUSTER_DAYS} days, ≥ {_usd(CLUSTER_MIN_USD)})")

    def _scan(self, seen, hist_done=frozenset()):
        """Background thread: new filings -> [(acc, parsed or None, issuer cik)], plus for each company with a new
        officer/director buy that wasn't looked up lately, its other Form 4s of the last 14 days."""
        new, stop = [], False
        for page in range(MAX_PAGES):
            for acc, cik in feed_page(page * 100):
                if acc in seen:
                    stop = True
                    break
                if all(a != acc for a, _ in new):
                    new.append((acc, cik))
            if stop or len(new) >= MAX_FILINGS:
                break
            time.sleep(0.3)
        out = []
        for acc, cik in new[:MAX_FILINGS]:
            try:
                out.append((acc, parse_form4(filing_xml(cik, acc)), cik))
            except Exception:
                out.append((acc, None, cik))
            time.sleep(SEC_PAUSE)
        have = {a for a, _, _ in out} | set(seen)
        looked = []
        for cik in dict.fromkeys(c for _, f, c in out if f and f["buys"] and f["officer_or_director"]):
            if cik in hist_done or len(looked) >= HIST_COMPANIES:
                continue
            looked.append(cik)
            try:
                accs = [a for a in recent_form4(cik) if a not in have][:HIST_MAX]
            except Exception:
                continue
            time.sleep(SEC_PAUSE)
            for acc in accs:
                try:
                    out.append((acc, parse_form4(filing_xml(cik, acc)), cik))
                    have.add(acc)
                except Exception:
                    pass
                time.sleep(SEC_PAUSE)
        return {"filings": out, "looked": looked}

    def check(self, state):
        st = state.setdefault("_insb", {})
        changed = False
        if self.thread and not self.thread.is_alive():
            res, self.thread, self.result = self.result, None, None
            if res is not None:
                changed |= self._finish(st, res, state)
        if not self.thread and time.monotonic() - self.last >= SCAN_SECONDS:
            self.last = time.monotonic()
            seen = set(st.get("seen", []))
            hist = {int(k) for k in st.get("hist", {})}

            def run():
                try:
                    self.result = self._scan(seen, hist)
                except Exception as e:
                    log.warning("Insider scan failed: %s", type(e).__name__)
                    self.result = None
            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
        return changed

    def _finish(self, st, res, state=None):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        first = not st.get("init")
        st["init"] = True
        seen = st.setdefault("seen", [])
        book = st.setdefault("book", {})
        posted = st.setdefault("posted", {})          # ticker -> [first day of cluster, owners when last posted, posts]
        names = st.setdefault("names", {})
        if isinstance(res, list):   # older shape: [(acc, parsed)]
            res = {"filings": res, "looked": []}
        hist = st.setdefault("hist", {})
        for cik in res.get("looked", []):
            hist[str(cik)] = today
        for k in [k for k, d in hist.items() if d < (datetime.strptime(today, "%Y-%m-%d")
                                                       - timedelta(days=CLUSTER_DAYS)).strftime("%Y-%m-%d")]:
            del hist[k]
        res = res["filings"]
        touched, buys = set(), 0
        for acc, f, *_ in res:
            seen.append(acc)
            if f and add_buys(book, f, acc, today):
                buys += 1
                touched.add(f["ticker"])
                names[f["ticker"]] = f["company"][:60]
        del seen[:-3000]
        sent = 0
        for tk in sorted(touched):
            c = cluster(book[tk], today)
            if not c:
                continue
            n, total, rows = c
            p = posted.get(tk)
            if p and p[0] >= rows[0][0] and (n <= p[1] or p[2] >= POST_MAX):
                continue          # this cluster was already posted with as many buyers (or often enough)
            posted[tk] = [rows[0][0], n, (p[2] + 1) if p and p[0] >= rows[0][0] else 1]
            if first:
                continue          # first scan: only learn
            sh = sum(r[6] for r in rows if len(r) > 6)
            avg = sum(r[3] for r in rows if len(r) > 6) / sh if sh else None
            try:
                px = price_now(tk)
            except Exception:
                px = None
            text, loud = message(tk, names.get(tk, tk), n, total, rows, market_cap(tk), px, avg)
            if sent >= MAX_POSTS:
                log.info("Insider scan: more clusters than %d in one scan; rest skipped", MAX_POSTS)
                break
            (self.send_main if tk in self.watch else self.send_other)(text, not loud)
            sent += 1
            _score(state, tk, "high" if loud else "low", f"{n} insiders bought {_usd(total)}", px)
        cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=CLUSTER_DAYS + 2)).strftime("%Y-%m-%d")
        for tk in list(book):
            book[tk] = [r for r in book[tk] if r[0] >= cut]
            if not book[tk]:
                del book[tk]
        for tk in [t for t, p in posted.items() if p[0] < cut]:
            del posted[tk]
        for tk in [t for t in names if t not in book and t not in posted]:
            del names[tk]
        log.info("Insider scan: %d new filings, %d with open-market buys, %d clusters sent%s", len(res), buys, sent,
                 " (first scan: none sent)" if first else "")
        return True

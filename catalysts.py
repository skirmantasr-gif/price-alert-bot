"""💊 FDA calendar for ALL US stocks + 📼 event results (owner 2026-11-20: "5. do 6. do 7. do").

1. FdaCalendar: SEC full-text search for "PDUFA" / "target action date" in 8-K, 10-Q, 10-K, 6-K, 20-F filings
   (companies restate their decision dates there; Q3 10-Qs were all filed in November). Each new filing is read
   once; dates with a year, in the future, near those words are kept in state _fdacal. A sentence about an
   extension keeps only its latest date, and drops that stock's earlier dates found in older filings.
   First run looks back BACKFILL_DAYS, later runs RECENT_DAYS. Every stock found gets the 📼 options recording
   (options.HistoryWatch reads _fdacal) and a 📼 result post after its decision.
   💊 weekly list (Mondays, and once after the first scan): decisions in the next 14 days.
2. ResultWatch: after each 📼-recorded event (FDA decisions of any stock, watchlist earnings): the reaction day
   (first or second trading day from the event date, whichever traded more), the move vs what options priced
   in the day before, the setup (last 5 days, puts vs calls) and a running scorecard of all events so far.
Network work runs in a background thread (SEC asks for gentle use: ~5 requests a second at most).
"""
import html
import json
import logging
import os
import re
import statistics
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta

import extras

log = logging.getLogger("bot")
ON = os.environ.get("FDA_CALENDAR", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
FTS = "https://efts.sec.gov/LATEST/search-index?"
FTS_QUERIES = ('"PDUFA"', '"target action date"')
FTS_FORMS = "8-K,10-Q,10-K,6-K,20-F"
FTS_PAGES = 5                # 100 hits a page
BACKFILL_DAYS = 45
RECENT_DAYS = 4
SCAN_SECONDS = 6 * 3600
MAX_DOCS = 150               # filings read in one scan
DOC_BYTES = 15_000_000
WINDOW = 350                 # characters around "PDUFA" searched for a date
AHEAD_DAYS = 400
LIST_DAYS = 14
LIST_WEEKDAY, LIST_TIME = 0, "08:30"    # Monday morning, Vilnius time
KEEP_DOCS = 3000
PDUFA_RE = re.compile(r"(?<!potential )(?<!possible )(PDUFA(?!\s+(VI|VII|VIII|IX|reauthori))|target action date|goal date)", re.I)
GENERIC_RE = re.compile(r"\b(failing to meet|failed to meet|miss(es|ed)? (its|their)? ?PDUFA|government shut ?down|"
                        r"reauthori[sz]\w*|for example|such as delays|exclusivity|ahead of (its|the) "
                        r"(PDUFA|target|goal)|approved .{0,80}ahead of)\b", re.I)
AFTER, BEFORE = 160, 90      # a date this close after (or before) the PDUFA words belongs to them
EXTEND_RE = re.compile(r"\bextend\w*|\bextension\b|\bnew (PDUFA|target|goal)\b|\brevised\b|\bmoved\b|\bupdated\b", re.I)
FIRST_PERSON = re.compile(r"\b(we|our|us|the company(['’]s)?)\b", re.I)
CAL_V = 2                    # raise to rebuild _fdacal from scratch (rules changed)
DATE_Y = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                    r"Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b",
                    re.I)
EXT_DAYS = 100              # FDA extensions are 3 months
TICK_RE = re.compile(r"\(([A-Z][A-Z.\-]{0,6})(?:,\s*[A-Z.\-]+)*\)")


def _sec(url):
    import earnings
    return earnings._get(url)


def _text(raw):
    t = raw.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t)


def _sentence(text, i):
    """The sentence around position i, cut to 220 characters before / 260 after (tables have no full stops).
    A bullet (•, ▪) or a full stop starts a new sentence."""
    a = max(max(text.rfind(c, 0, i) for c in (". ", "• ", "▪ ", "; ")), i - 220)
    b = min([x for x in (text.find(c, i) for c in (". ", "• ", "▪ ", "; ")) if x >= 0] or [len(text)])
    b = min(b, i + 260)
    return text[a + 1:b + 1].strip()


def first_word(name):
    """Distinctive first word of a company name ('CAPRICOR THERAPEUTICS, INC.' -> 'capricor')."""
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", name or ""):
        if w.lower() not in ("the", "inc", "corp", "holdings", "group", "therapeutics", "pharmaceuticals"):
            return w.lower()
    return ""


def other_company(snip, own, names):
    """True if the sentence names another company of this scan but not the filer itself (Dyne's 10-Q quoting
    Capricor's PDUFA date, 2026-11-20 dry run)."""
    low = snip.lower()
    if own and re.search(r"\b" + re.escape(own) + r"\b", low):
        return False
    return any(w != own and re.search(r"\b" + re.escape(w) + r"\b", low) for w in names if len(w) >= 5)


def find_dates(text, today=None):
    """[(iso, snippet, extended, sentence)] of future dates with a year right after a PDUFA word (or just before it, as in
    "an NDA with a March 29, 2027, PDUFA date"), skipping risk-factor boilerplate and early approvals (pure).
    "extended from August 22, 2026 to November 22, 2026": the first FUTURE date after the words wins."""
    today = today or date.today()
    out = {}
    for m in PDUFA_RE.finditer(text):
        sent = _sentence(text, m.start())
        if GENERIC_RE.search(sent):
            continue

        def dates(a, b):
            res = []
            for d in DATE_Y.finditer(text, max(0, a), max(0, b)):
                try:
                    dt = date(int(d.group(3)), extras._MON[d.group(1).lower()[:3]], int(d.group(2)))
                except ValueError:
                    continue
                if today <= dt <= today + timedelta(days=AHEAD_DAYS):
                    res.append(dt)
            return res
        start = max(m.start() - BEFORE, text.rfind(sent[:30], 0, m.start() + 1) if sent else 0)
        after = dates(m.end(), m.end() + AFTER)
        dt = after[0] if after else (dates(start, m.start()) or [None])[-1]
        if not dt:
            continue
        ext = bool(EXTEND_RE.search(sent))
        iso = dt.isoformat()
        snip = re.sub(r"\s+", " ", text[max(0, m.start() - 90):m.end() + 130]).strip()
        if iso not in out or (ext and not out[iso][1]):
            out[iso] = (snip, ext, sent)
    return [(k, v[0], v[1], v[2]) for k, v in sorted(out.items())]


def ticker_of(display_names):
    for n in display_names or []:
        m = TICK_RE.search(n)
        if m:
            return m.group(1).split(",")[0].strip(), re.sub(r"\s*\(.*$", "", n).strip()
    return None, None


def merge(cal, t, name, filed, found):
    """Add one filing's dates for ticker t to cal {"T:iso": {...}}; an extension drops older-filing dates."""
    changed = False
    for iso, snip, ext, *_ in found:
        key = f"{t}:{iso}"
        if ext:   # an extension replaces the same stock's earlier date (same or older filing, ≤ EXT_DAYS before it)
            lo = (datetime.strptime(iso, "%Y-%m-%d") - timedelta(days=EXT_DAYS)).strftime("%Y-%m-%d")
            for k, e in list(cal.items()):
                if e["t"] == t and lo <= e["date"] < iso and e["filed"] <= filed and k != key:
                    del cal[k]
                    changed = True
        e = cal.get(key)
        if not e or e["filed"] < filed:
            cal[key] = {"t": t, "name": name[:60], "date": iso, "filed": filed, "snip": snip}
            changed = True
    return changed


class FdaCalendar:
    def __init__(self, send, tickers=()):
        self.send = send              # send(text, silent) -> main channel
        self.watch = set(tickers)
        self.thread, self.result = None, None
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON and bool(SEC_CONTACT)

    @staticmethod
    def label():
        return "ON (SEC filings → 💊 FDA dates of all US stocks, 📼 options recording, list on Mondays)"

    def _job(self, done, days):
        """Background: search + read new filings -> list of (ticker, name, filed, found, doc_key)."""
        today = date.today()
        hits = {}
        for q in FTS_QUERIES:
            for page in range(FTS_PAGES):
                try:
                    j = json.loads(_sec(FTS + urllib.parse.urlencode(
                        {"q": q, "forms": FTS_FORMS, "dateRange": "custom", "from": page * 100,
                         "startdt": (today - timedelta(days=days)).isoformat(), "enddt": today.isoformat()})))
                except Exception as e:
                    log.info("FDA calendar: search failed (%s)", type(e).__name__)
                    break
                rows = j.get("hits", {}).get("hits", [])
                for h in rows:
                    hits.setdefault(h.get("_id", ""), h.get("_source", {}))
                time.sleep(0.3)
                if len(rows) < 100:
                    break
        out, read = [], 0
        for _id, so in sorted(hits.items(), key=lambda x: x[1].get("file_date", ""), reverse=True):
            if not _id or ":" not in _id or _id in done:
                continue
            t, name = ticker_of(so.get("display_names"))
            ciks = so.get("ciks") or []
            if not t or not ciks:
                out.append((None, None, None, [], _id))
                continue
            if read >= MAX_DOCS:
                break
            acc, fn = _id.split(":", 1)
            url = f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{acc.replace('-', '')}/{fn}"
            try:
                raw = _sec(url)
            except Exception as e:
                log.info("FDA calendar: filing unavailable (%s)", type(e).__name__)
                continue
            read += 1
            found = find_dates(_text(raw[:DOC_BYTES]), today)
            if not str(so.get("form") or so.get("file_type") or "").upper().startswith(("8-K", "6-K")):
                # quarterly / annual reports describe rivals' drugs too ("povetacicept … PDUFA date of November 30"
                # in Akebia's and Jade's 10-Qs): only sentences about the company itself count there
                own = first_word(name)
                found = [x for x in found if FIRST_PERSON.search(x[3]) or (own and re.search(
                    r"\b" + re.escape(own) + r"\b", x[3].lower()))]
            out.append((t, name, so.get("file_date", ""), found, _id))
            time.sleep(0.25)
        names = {first_word(n) for _, n, _, _, _ in out if n} - {""}
        out = [(t, n, f, [x[:3] for x in found if not other_company(x[3], first_word(n), names)], k)
               for t, n, f, found, k in out]
        log.info("FDA calendar: %d filings found, %d read", len(hits), read)
        return out

    def check(self, state):
        fc = state.setdefault("_fdacal", {"ev": {}, "docs": []})
        changed = False
        if fc.get("v") != CAL_V:     # rules changed: read the filings again
            keep = {k: fc[k] for k in ("list_week", "listed_once", "warned") if k in fc}
            fc.clear()
            fc.update({"ev": {}, "docs": [], "v": CAL_V, **keep})
            changed = True
        if self.thread and not self.thread.is_alive():
            res, self.thread = self.result, None
            if res is not None:
                before = len(fc["ev"])
                for t, name, filed, found, key in sorted(res, key=lambda r: r[2] or ""):   # oldest first: newer wins
                    if t and found:
                        changed |= merge(fc["ev"], t, name, filed, found)
                    fc["docs"].append(key)
                fc["docs"] = fc["docs"][-KEEP_DOCS:]
                fc["scanned"] = date.today().isoformat()
                changed = True
                log.info("FDA calendar: %d dates known (%+d)", len(fc["ev"]), len(fc["ev"]) - before)
        today = date.today().isoformat()
        for k in [k for k, e in fc["ev"].items() if e["date"] < today]:
            del fc["ev"][k]
            changed = True
        if not self.thread and time.monotonic() - self.last >= SCAN_SECONDS:
            self.last = time.monotonic()
            done, days = set(fc["docs"]), (RECENT_DAYS if fc.get("scanned") else BACKFILL_DAYS)

            def run():
                try:
                    self.result = self._job(done, days)
                except Exception as e:
                    log.warning("FDA calendar job failed: %s", type(e).__name__)
                    self.result = None
            self.result = None
            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
        now = extras.local_now()
        if now.strftime("%H:%M") >= WARN_TIME and trading_day(now.date()):
            warned = fc.setdefault("warned", [])
            evs = [e for e in closed_day_events(state, now.date()) if f"{e[0]}:{e[1]}" not in warned]
            if evs and self.send(closed_day_message(evs, self.watch, now.date()),
                                 not any(t in self.watch for t, _ in evs)):   # loud when a watchlist stock is in it
                fc["warned"] = (warned + [f"{t}:{iso}" for t, iso in evs])[-200:]
                changed = True
                log.info("FDA calendar: closed-day warning sent (%d)", len(evs))
        week = now.strftime("%G-W%V")
        due = fc.get("scanned") and fc.get("list_week") != week and (
            not fc.get("listed_once") or (now.weekday() == LIST_WEEKDAY and now.strftime("%H:%M") >= LIST_TIME))
        if due:
            text = self.list_message(fc["ev"], now.date())
            if text and self.send(text, True):
                fc["list_week"], fc["listed_once"] = week, True
                changed = True
        return changed

    def list_message(self, ev, today):
        end = (today + timedelta(days=LIST_DAYS)).isoformat()
        up = sorted((e for e in ev.values() if today.isoformat() <= e["date"] <= end), key=lambda e: (e["date"], e["t"]))
        if not up:
            return ""
        lines = [f"💊 <b>FDA decisions in the next {LIST_DAYS} days</b> ({len(up)}, from SEC filings)"]
        for e in up[:30]:
            bits = [datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a %b %d"), f"<b>${html.escape(e['t'])}</b>"]
            try:
                import movers
                cap = movers.market_cap(e["t"])
                if cap:
                    bits.append(f"${cap / 1e9:.1f}B" if cap >= 1e9 else f"${cap / 1e6:.0f}M")
            except Exception:
                pass
            try:
                import options
                o = options.get(e["t"])
                if o and o.get("move_pct"):
                    bits.append(f"options ±{o['move_pct']:.0f}% by {options._d(o['exp'])}")
            except Exception:
                pass
            if e["t"] in self.watch:
                bits.append("⭐ watchlist")
            lines.append("• " + " · ".join(bits))
            if e.get("snip"):
                lines.append(f"  <i>{html.escape(e['snip'][:160])}</i>")
        lines.append("<i>Dates as the companies wrote them in SEC filings; the FDA can act earlier or later. "
                     "📼 options are recorded daily for each, results posted after. Not advice.</i>")
        return "\n".join(lines)


# ---------------------------------------------------------------- ⏰ decisions on closed-market days
# Owner 2026-11-20: "if fda approval is on the weekend or non working market day i'll receive it earlier".
# NYSE holidays (full closures); extend each year.
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}
WARN_TIME = "09:00"          # Vilnius time on the last trading day before


def trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def last_trading_before(d):
    x = d - timedelta(days=1)
    while not trading_day(x):
        x -= timedelta(days=1)
    return x


def closed_day_events(state, today):
    """[(ticker, iso)] of FDA dates on a closed-market day whose last trading day before is `today`."""
    evs = {(t, iso) for t, iso, kind, _ in (state.get("_cal") or []) if kind == "fda"}
    evs |= {(e["t"], e["date"]) for e in ((state.get("_fdacal") or {}).get("ev") or {}).values()}
    out = []
    for t, iso in sorted(evs, key=lambda x: (x[1], x[0])):
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        if not trading_day(d) and last_trading_before(d) == today:
            out.append((t, iso))
    return out


def closed_day_message(evs, watch, today):
    def why(d):
        return "a market holiday" if d.weekday() < 5 else "a " + d.strftime("%A")
    lines = []
    for t, iso in evs:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        bits = [f"⏰ <b>${html.escape(t)}</b> FDA decision due <b>{d.strftime('%a %b %d')}</b> — {why(d)}, markets closed"
                + (" ⭐" if t in watch else "")]
        try:
            import options
            o = options.get(t)
            if o and o.get("move_pct"):
                bits.append(f"   🎲 options ±{o['move_pct']:.0f}% by {options._d(o['exp'])}")
        except Exception:
            pass
        lines += bits
    nxt = today + timedelta(days=1)
    while not trading_day(nxt):
        nxt += timedelta(days=1)
    lines.append(f"<b>Today ({today.strftime('%a %b %d')}) is the last trading day before.</b> US market closes "
                 f"23:00 LT, after-hours until 03:00 LT. The FDA often announces on the business day before a "
                 f"weekend or holiday date, so news can come any time today. The bot keeps reading news when markets "
                 f"are closed; trading reopens {nxt.strftime('%a')} 11:00 LT (pre-market).")
    lines.append("<i>Decide today whether to hold through the decision. Not advice.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- 📼 results after the event
def _bars(t):
    """[(YYYY-MM-DD, open, close, volume)] for ~6 months (New York dates)."""
    import earnings
    j = json.loads(earnings._yget("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(t) +
                                  "?range=6mo&interval=1d"))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    off = j["meta"].get("gmtoffset") or -18000
    from datetime import timezone
    return [(datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m-%d"), o, c, v)
            for ts, o, c, v in zip(j.get("timestamp") or [], q["open"], q["close"], q["volume"]) if o and c and v is not None]


def reaction(bars, event, today):
    """(index of the reaction day, volume ratio) or None while not settled (pure)."""
    i = next((k for k, b in enumerate(bars) if b[0] >= event), None)
    if i is None or i < 6:
        return None
    if i + 1 >= len(bars) and today <= (datetime.strptime(bars[i][0], "%Y-%m-%d").date() + timedelta(days=3)):
        return None           # wait for the second trading day (an after-close release moves the next day)

    def vr(k):
        if k >= len(bars):
            return 0.0
        base = statistics.mean(b[3] for b in bars[max(0, k - 21):k - 1]) or 1
        return bars[k][3] / base
    k = max([i] + ([i + 1] if i + 1 < len(bars) else []), key=vr)
    return k, vr(k)


def implied(rec, day):
    """Straddle % of the first expiry on/after `day` in the last snapshot taken before `day`, + put/call OI."""
    snaps = [d for d in sorted(rec.get("snaps", {})) if d < day]
    if not snaps:
        return None, None, None
    s = rec["snaps"][snaps[-1]]
    for e in s.get("exps", []):
        if e[0] >= day and e[2] is not None:
            return e[2], (e[6] / e[5] if e[5] else None), e[0]
    return None, None, None


def scorecard(rows, kind):
    r = [x for x in rows if x["kind"] == kind and x.get("imp")]
    if not r:
        return ""
    big = sum(1 for x in r if abs(x["move"]) * 100 > x["imp"])
    ratio = statistics.median(abs(x["move"]) * 100 / x["imp"] for x in r)
    return (f"📒 Scorecard ({'FDA decisions' if kind == 'fda' else 'earnings'}, {len(r)} so far): moved more than "
            f"options priced in {big} of {len(r)} · typical move = {ratio:.1f}× the priced move")


class ResultWatch:
    def __init__(self, send_main, send_other, tickers, pattern=None, bars=None):
        self.send_main, self.send_other = send_main, send_other
        self.watch = set(tickers)
        self.pattern = pattern        # ticker -> earnings.reactions rows (past reports), optional
        self.bars = bars or _bars
        self.last = float("-inf")

    @staticmethod
    def enabled():
        return ON

    def check(self, state, now=None):
        if now is None and time.monotonic() - self.last < 1800:
            return False
        self.last = time.monotonic()
        today = (now or extras.local_now()).date()
        h = state.get("_opth", {})
        res = state.setdefault("_evres", {"done": [], "rows": []})
        changed = False
        for key, rec in list(h.items()):
            if key in res["done"] or rec["event"] > today.isoformat():
                continue
            if (today - datetime.strptime(rec["event"], "%Y-%m-%d").date()).days > 14:
                res["done"].append(key)          # too late to say anything useful
                changed = True
                continue
            try:
                bars = self.bars(rec["t"])
            except Exception as e:
                log.info("Event result: prices unavailable (%s)", type(e).__name__)
                continue
            r = reaction(bars, rec["event"], today)
            if not r:
                continue
            k, vr = r
            day, move = bars[k][0], bars[k][2] / bars[k - 1][2] - 1
            pre5 = bars[k - 1][2] / bars[k - 6][2] - 1
            imp, pc, exp = implied(rec, day)
            kind = rec.get("kind") or ("earnings" if "earn" in rec.get("label", "").lower() else "fda")
            row = {"t": rec["t"], "kind": kind, "event": rec["event"], "day": day, "move": round(move, 4),
                   "imp": imp, "pc": round(pc, 2) if pc else None, "pre5": round(pre5, 4), "vr": round(vr, 1)}
            res["rows"] = (res["rows"] + [row])[-300:]
            res["done"] = (res["done"] + [key])[-500:]
            changed = True
            text = self.message(rec, row, bars[k - 1][2], bars[k][2], exp, res["rows"])
            (self.send_main if rec["t"] in self.watch else self.send_other)(text, True)
            log.info("Event result sent (%s)", kind)
        return changed

    def message(self, rec, row, before, after, exp, rows):
        what = "earnings" if row["kind"] == "earnings" else "FDA decision"
        d = datetime.strptime(row["day"], "%Y-%m-%d").strftime("%a %b %d")
        ev = datetime.strptime(rec["event"], "%Y-%m-%d").strftime("%a %b %d")
        lines = [f"📼 <b>${html.escape(rec['t'])}</b> after its {what} ({ev}): <b>{row['move'] * 100:+.1f}%</b> on {d} "
                 f"(${before:,.2f} → ${after:,.2f}, {row['vr']:.1f}× normal volume)"]
        if row["imp"]:
            verdict = "bigger than" if abs(row["move"]) * 100 > row["imp"] else "smaller than"
            lines.append(f"🎲 Options had priced ±{row['imp']:.0f}% by {options_day(exp)} → the move was {verdict} priced")
        else:
            lines.append("🎲 No options snapshot from before the event")
        setup = [f"last 5 days before {row['pre5'] * 100:+.0f}%"]
        if row["pc"]:
            setup.append(f"puts vs calls open {row['pc']:.1f}×")
        lines.append("Before: " + " · ".join(setup))
        if row["kind"] == "earnings" and self.pattern:
            try:
                past = [abs(x["day"]) * 100 for x in self.pattern(rec["t"]) if x["d"] < row["day"]]
                if past:
                    lines.append(f"📊 Past report days: middle move ±{statistics.median(past):.0f}% ({len(past)} reports)")
            except Exception:
                pass
        sc = scorecard(rows, row["kind"])
        if sc:
            lines.append(sc)
        lines.append("<i>Recorded to learn how well options price these events. Not advice.</i>")
        return "\n".join(lines)


def options_day(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %b %d") if iso else "?"

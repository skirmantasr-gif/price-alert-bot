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
CAL_V = 3                    # raise to rebuild _fdacal from scratch (rules changed)
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
    """[(iso, snippet, extended, sentence, wider context)] of future dates with a year right after a PDUFA word (or just before it, as in
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
        wide = text[max(0, m.start() - 250):m.end() + 160]      # "Vanda Quimilza … • FDA target action date …"
        if iso not in out or (ext and not out[iso][1]):
            out[iso] = (snip, ext, sent, wide)
    return [(k, v[0], v[1], v[2], v[3]) for k, v in sorted(out.items())]


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
            report = not str(so.get("form") or so.get("file_type") or "").upper().startswith(("8-K", "6-K"))
            out.append((t, name, so.get("file_date", ""), [x + (report,) for x in found], _id))
            time.sleep(0.25)
        names = {first_word(n) for _, n, _, _, _ in out if n} - {""}
        # 1) a sentence (or the words just before it) naming another company of this scan is that company's date
        out = [(t, n, f, [x for x in found if not other_company(x[4], first_word(n), names)], k)
               for t, n, f, found, k in out]
        # 2) quarterly / annual reports describe rivals' drugs too ("povetacicept … PDUFA date of November 30" in
        #    Akebia's and Jade's 10-Qs): there a sentence that isn't about the company itself ("we", its name) only
        #    counts if no other company of this scan has the same date
        claims = {}
        for t, n, f, found, k in out:
            for x in found:
                claims.setdefault(x[0], set()).add(t)

        def own_line(x, n):
            w = first_word(n)
            return bool(FIRST_PERSON.search(x[3]) or (w and re.search(r"\b" + re.escape(w) + r"\b", x[3].lower())))
        out = [(t, n, f, [x[:3] for x in found if not x[5] or own_line(x, n) or claims.get(x[0], set()) <= {t}], k)
               for t, n, f, found, k in out]
        log.info("FDA calendar: %d filings found, %d read", len(hits), read)
        return out

    def check(self, state):
        fc = state.setdefault("_fdacal", {"ev": {}, "docs": []})
        changed = False
        if fc.get("v") != CAL_V:     # rules changed: read the filings again
            keep = {k: fc[k] for k in ("list_week", "listed_once", "warned", "missed") if k in fc}
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
            text = self.list_message(fc["ev"], now.date(), fc.get("missed"))
            if text and self.send(text, True):
                fc["list_week"], fc["listed_once"] = week, True
                changed = True
        return changed

    def list_message(self, ev, today, missed=None):
        missed = missed or {}
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
            miss = missed.get(f"{e['t']}:{e['date']}")
            if miss:
                bits.append("❗ FDA said it will MISS this date")
            lines.append("• " + " · ".join(bits))
            if miss:
                lines.append(f"  <i>{html.escape(miss['title'][:160])}</i>")
            elif e.get("snip"):
                lines.append(f"  <i>{html.escape(e['snip'][:160])}</i>")
        lines.append("<i>Dates as the companies wrote them in SEC filings; the FDA can act earlier or later. "
                     "📼 options are recorded daily for each, results posted after. Not advice.</i>")
        return "\n".join(lines)


# ---------------------------------------------------------------- ❗ the FDA says it will miss a date
# Owner 2026-11-20 ("5. do"): SVRA's Nov 22 date stayed in the calendar after the FDA said it would not meet it.
MISSED_RE = re.compile(r"will not (be able to )?(meet|take action|act)|unable to (meet|complete|take action)|"
                       r"not (be able to )?(meet|complete) (its|the) (review|PDUFA|goal|target)|"
                       r"\bmiss(es|ed|ing)?\b[\w\s'’-]{0,40}?\b(PDUFA|goal date|target action|action date|deadline)|"
                       r"(PDUFA|goal|action) date (is|was) missed", re.I)
MISSED_WINDOW = (-14, 45)    # events from 14 days ago to 45 days ahead can be the one meant


def missed_keys(state):
    return ((state.get("_fdacal") or {}).get("missed") or {})


def mark_missed(state, t, title, today=None):
    """A headline like 'FDA will not meet the PDUFA date' marks the stock's nearby FDA date(s) as missed."""
    if not MISSED_RE.search(title or "") or not re.search(r"\bFDA\b|PDUFA|regulat", title or "", re.I):
        return False
    today = today or date.today()
    evs = {iso for tt, iso, kind, _ in (state.get("_cal") or []) if kind == "fda" and tt == t}
    evs |= {e["date"] for e in ((state.get("_fdacal") or {}).get("ev") or {}).values() if e["t"] == t}
    fc = state.setdefault("_fdacal", {"ev": {}, "docs": []})
    m = fc.setdefault("missed", {})
    changed = False
    for iso in evs:
        delta = (datetime.strptime(iso, "%Y-%m-%d").date() - today).days
        k = f"{t}:{iso}"
        if MISSED_WINDOW[0] <= delta <= MISSED_WINDOW[1] and k not in m:
            m[k] = {"title": title[:200], "at": today.isoformat()}
            changed = True
            log.info("FDA calendar: date marked missed")
    if len(m) > 200:
        for k in sorted(m, key=lambda k: m[k]["at"])[:len(m) - 200]:
            del m[k]
    return changed


# ---------------------------------------------------------------- ⏰ decisions on closed-market days
# Owner 2026-11-20: "if fda approval is on the weekend or non working market day i'll receive it earlier".
# NYSE full closures, computed by the exchange's rules (owner 2026-11-20 "5. do": no hand-typed list to extend).
# A holiday on Saturday is observed Friday, on Sunday Monday — except New Year's Day on a Saturday (NYSE: no closure).


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    return date(y, month, (h + l_ - 7 * m + 114) % 31 + 1)


def _nth(y, month, weekday, n):
    """n-th weekday (0 = Monday) of a month; n = -1 is the last one."""
    if n > 0:
        d = date(y, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y, month + 1, 1) - timedelta(days=1) if month < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d, new_year=False):
    if d.weekday() == 5:
        return None if new_year else d - timedelta(days=1)
    return d + timedelta(days=1) if d.weekday() == 6 else d


def nyse_holidays(y):
    days = [_observed(date(y, 1, 1), new_year=True), _nth(y, 1, 0, 3), _nth(y, 2, 0, 3),
            _easter(y) - timedelta(days=2), _nth(y, 5, 0, -1), _observed(date(y, 7, 4)), _nth(y, 9, 0, 1),
            _nth(y, 11, 3, 4), _observed(date(y, 12, 25))]
    if y >= 2022:
        days.append(_observed(date(y, 6, 19)))
    return {d.isoformat() for d in days if d}


NYSE_HOLIDAYS = set().union(*(nyse_holidays(y) for y in range(2024, 2041)))
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
    missed = missed_keys(state)
    for t, iso in sorted(evs, key=lambda x: (x[1], x[0])):
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        if f"{t}:{iso}" in missed:
            continue          # the FDA already said it will not decide on that day
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
            if kind == "fda" and f"{rec['t']}:{rec['event']}" in missed_keys(state):
                kind = "fda_missed"     # no decision that day: not scored with the real decisions
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
        what = {"earnings": "earnings", "fda_missed": "FDA date (the FDA missed it, no decision yet)"}.get(
            row["kind"], "FDA decision")
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


# ---------------------------------------------------------------- 💊📰 news for FDA-calendar stocks off the watchlist
# Owner 2026-11-20 after SVRA (not on the watchlist): the FDA missed its Nov 22 date and the bot said nothing.
NEWS_BEFORE, NEWS_AFTER = 30, 14          # follow a stock from 30 days before its FDA date to 14 days after
NEWS_SECONDS, SEC_SECONDS = 300, 900
NEWS_MAX_AGE_H = 48
FDA_NEWS = re.compile(r"\bFDA\b|approv\w*|PDUFA|complete response|\bCRL\b|action date|advisory committee|"
                      r"\b(BLA|NDA|sNDA|sBLA)\b|refus\w*|reject\w*|delay\w*|extend\w*|will not (meet|take action)", re.I)
FDA_LOUD = re.compile(r"approv\w*|complete response|\bCRL\b|reject\w*|refus\w*|will not (meet|take action)|"
                      r"delay\w*|miss\w*", re.I)


def _rss(url):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    import earnings
    out = []
    for it in ET.fromstring(earnings._yget(url)).iter("item"):
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "")
        except Exception:
            d = None
        out.append({"title": html.unescape((it.findtext("title") or "").strip()), "link": (it.findtext("link") or "").strip(),
                    "date": d})
    return out


def _hash(x):
    import hashlib
    return hashlib.sha1(x.encode()).hexdigest()[:12]


class FdaNewsWatch:
    """For every FDA-calendar stock that is NOT on the watchlist (news.py covers those), around its date:
    FDA-related Yahoo headlines (loud for a decision, rejection or delay) and new 8-K / 6-K filings (quiet, with
    the 🤖 summary). The first look at a stock only learns what is already out there."""

    def __init__(self, send, tickers, headlines=None, filings_of=None, summarize=None):
        self.send, self.watch = send, set(tickers)
        self.headlines = headlines or (lambda t: _rss("https://feeds.finance.yahoo.com/rss/2.0/headline?" +
                                                      urllib.parse.urlencode({"s": t, "region": "US", "lang": "en-US"})))
        self.filings_of = filings_of
        self.summarize = summarize
        self.last, self.last_sec = float("-inf"), float("-inf")

    @staticmethod
    def enabled():
        return ON

    def tickers(self, state, today):
        out = {}
        for e in ((state.get("_fdacal") or {}).get("ev") or {}).values():
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if e["t"] not in self.watch and -NEWS_AFTER <= (d - today).days <= NEWS_BEFORE:
                out.setdefault(e["t"], e)
        return out

    def check(self, state, now=None):
        mono = time.monotonic()
        if now is None and mono - self.last < NEWS_SECONDS:
            return False
        self.last = mono
        now = now or extras.local_now()
        fn = state.setdefault("_fdanews", {"seen": [], "init": []})
        seen, init = set(fn["seen"]), set(fn["init"])
        changed = False
        do_sec = self.filings_of is not None and (mono - self.last_sec >= SEC_SECONDS or now is not None)
        if do_sec:
            self.last_sec = mono
        from datetime import timezone
        cut = datetime.now(timezone.utc) - timedelta(hours=NEWS_MAX_AGE_H)
        for t, e in self.tickers(state, now.date()).items():
            first = t not in init
            when = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a %b %d")
            try:
                heads = self.headlines(t)
            except Exception as ex:
                log.info("FDA news: headlines unavailable (%s)", type(ex).__name__)
                heads = []
            for h in heads:
                if not FDA_NEWS.search(h["title"]) or (h.get("date") and h["date"] < cut):
                    continue
                k = _hash("n:" + t + ":" + h["title"].lower())
                if k in seen:
                    continue
                seen.add(k)
                changed = True
                if first:
                    continue
                changed |= mark_missed(state, t, h["title"], now.date())
                loud = bool(FDA_LOUD.search(h["title"]))
                link = h["link"] if h["link"].startswith(("https:", "http:")) else ""
                self.send(f"💊{'🚨' if loud else '📰'} <b>${html.escape(t)}</b> FDA news (FDA date {when})\n"
                          f"{html.escape(h['title'])}" + (f'\n<a href="{html.escape(link)}">Read</a>' if link else ""),
                          not loud)
                log.info("FDA news sent")
            if do_sec:
                try:
                    fl = [f for f in self.filings_of(t) if str(f.get("form", "")).upper().startswith(("8-K", "6-K"))]
                except Exception as ex:
                    log.info("FDA news: SEC unavailable (%s)", type(ex).__name__)
                    fl = []
                for f in fl[:5]:
                    k = _hash("s:" + str(f.get("acc")))
                    if k in seen:
                        continue
                    seen.add(k)
                    changed = True
                    if first:
                        continue
                    lines = [f"💊📄 <b>${html.escape(t)}</b> SEC filing {html.escape(str(f.get('form')))} (FDA date {when})"]
                    try:
                        import filings
                        s = self.summarize(dict(f, ticker=t), e.get("name", t)) if self.summarize else None
                        lines += filings.lines(s)
                    except Exception:
                        pass
                    if str(f.get("link", "")).startswith("https:"):
                        lines.append(f'<a href="{html.escape(f["link"])}">Open filing</a>')
                    self.send("\n".join(lines), True)
                    log.info("FDA news: filing sent")
            if first:
                init.add(t)
                changed = True
        if changed:
            fn["seen"] = list(seen)[-1500:]
            fn["init"] = sorted(init)[-300:]
        return changed

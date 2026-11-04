"""
Extra alert features:
  - quiet vs. loud Telegram notifications
  - "possibly related" news attached to price / volume / halt alerts
  - trading halts (Nasdaq Trader feed, covers all US exchanges)
  - unusual volume
  - watchdog: GitHub and the phone check on each other + a daily status message
  - catalyst calendar (earnings / FDA dates found in news headlines)
  - current price of tickers mentioned in forwarded Substack posts

Logs never contain tickers, headlines or chat text (the repo and its logs are public).
"""

import html
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import watchlist

log = logging.getLogger("bot")
UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"


def local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(watchlist.TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc)


def _ago(ts):
    m = int((time.time() - ts) // 60)
    return "just now" if m < 1 else f"{m} min ago" if m < 90 else f"{m // 60} h {m % 60} min ago"


def _strip(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())


# ---------------------------------------------------------------- 1. quiet / loud
# Messages starting with these arrive without sound; everything else (price moves, 🚨, halts, volume,
# insider buys/sales, warnings) rings.
QUIET_PREFIXES = ("📰", "📄", "⚪", "👤", "☀️", "✅")


def is_quiet(text):
    return text.startswith(QUIET_PREFIXES)


# ---------------------------------------------------------------- daily counters
def bump(state, key, n=1):
    st = state.setdefault("_stats", {})
    day = local_now().strftime("%Y-%m-%d")
    d = st.setdefault(day, {})
    d[key] = d.get(key, 0) + n
    for k in sorted(st)[:-4]:
        del st[k]


# ---------------------------------------------------------------- 2. "possibly related" news
RECENT = {}  # ticker -> [(time, important, text, link)], this run only


def remember(ticker, msg, link):
    first = _strip(msg.split("\n")[0])
    first = re.sub(r"^\W+\s*", "", first.replace(f"${ticker} ", "", 1)).strip()
    lst = RECENT.setdefault(ticker, [])
    lst.append((time.time(), not is_quiet(msg), first[:160], link))
    del lst[:-30]


def related(ticker, hours=3, n=2, skip_halts=False):
    """HTML lines with the most relevant news/filings for this ticker from the last few hours."""
    cut = time.time() - hours * 3600
    items = [x for x in RECENT.get(ticker, []) if x[0] >= cut and not (skip_halts and x[2].startswith("TRADING HALTED"))]
    items.sort(key=lambda x: (x[1], x[0]), reverse=True)  # important first, then newest
    out = []
    for ts, _, text, link in items[:n]:
        t = html.escape(text)
        out.append(f"🔎 Possibly related ({_ago(ts)}): " + (f'<a href="{html.escape(link)}">{t}</a>' if link else t))
    return out


# ---------------------------------------------------------------- 3. trading halts
HALT_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
NDAQ = "{http://www.nasdaqtrader.com/}"
HALT_REASONS = {
    "T1": "news pending", "T2": "news released", "T3": "news and resumption times", "T5": "single stock trading pause",
    "T6": "extraordinary market activity", "T7": "quotation-only period", "T8": "ETF halt",
    "T12": "additional information requested by the exchange", "H4": "non-compliance with listing rules",
    "H9": "not current in required filings", "H10": "SEC trading suspension", "H11": "regulatory concern",
    "O1": "operations halt", "IPO1": "IPO not yet trading", "M1": "corporate action", "M2": "quotation not available",
    "LUDP": "volatility pause (limit up / limit down), usually 5 min", "LUDS": "volatility pause (straddle state)",
    "M": "volatility trading pause", "MWC1": "market-wide circuit breaker (level 1)",
    "MWC2": "market-wide circuit breaker (level 2)", "MWC3": "market-wide circuit breaker (level 3)",
    "MWC0": "market-wide circuit breaker carried over", "D": "security deleted from the exchange",
    "C3": "issuer can't meet requirements", "C4": "qualification halt ended", "C11": "trading halt (other)",
}


class HaltWatcher:
    def __init__(self, tickers, send):
        self.tickers = set(tickers)
        self.send = send
        self.fail_since = None

    def check(self, state):
        if not self.tickers:
            return False
        try:
            req = urllib.request.Request(HALT_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
            self.fail_since = None
        except Exception as e:
            log.warning("Halt feed failed: %s", type(e).__name__)
            if self.fail_since is None:
                self.fail_since = time.time()
            elif self.fail_since != "notified" and time.time() - self.fail_since > 3600:
                self.send("⚠️ Trading-halt feed (Nasdaq Trader) failing for 1h+; will keep trying.")
                self.fail_since = "notified"
            return False
        hs = state.setdefault("_halts", {})
        seen = hs.setdefault("seen", {})
        first_time = not hs.get("init")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        changed = first_time
        for it in root.iter("item"):
            f = lambda tag: (it.findtext(NDAQ + tag) or "").strip()
            sym = f("IssueSymbol").upper()
            if sym not in self.tickers:
                continue
            htime, hdate = f("HaltTime").split(".")[0], f("HaltDate")
            resume = f("ResumptionTradeTime")
            code = f("ReasonCode")
            key = f"{sym}|{hdate}|{htime}"
            rec = seen.get(key)
            if rec is None:
                seen[key] = {"d": today, "r": bool(resume)}
                changed = True
                if first_time:
                    continue
                reason = HALT_REASONS.get(code.upper(), "see exchange notice")
                lines = [f"⏸️ <b>${sym}</b> TRADING HALTED at {html.escape(htime)} ET ({html.escape(hdate)})",
                         f"Reason: <b>{html.escape(code or '?')}</b> — {html.escape(reason)}"]
                if resume:
                    lines.append(f"Resumes: {html.escape(resume)} ET")
                lines += related(sym, skip_halts=True)
                lines.append('<a href="https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts">Halt list</a>')
                msg = "\n".join(lines)
                if self.send(msg):
                    remember(sym, msg, "")
                    bump(state, "halts")
                    log.info("Halt alert sent")
                else:
                    del seen[key]
            elif resume and not rec.get("r"):
                if self.send(f"▶️ <b>${sym}</b> trading resumes at {html.escape(resume)} ET "
                             f"(halted {html.escape(htime)}, {html.escape(code)})"):
                    rec["r"] = True
                    changed = True
        hs["init"] = True
        old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        for k in [k for k, v in seen.items() if v["d"] < old]:
            del seen[k]
            changed = True
        return changed


# ---------------------------------------------------------------- 6. unusual volume
# Typical share of a full day's volume traded after N minutes of the regular session (U-shaped day).
CURVE = [(0, 0), (30, .13), (60, .21), (90, .28), (120, .34), (150, .39), (180, .44), (210, .48), (240, .53),
         (270, .58), (300, .63), (330, .70), (360, .80), (390, 1.0)]
VOLUME_LEVELS = (3, 6)          # alert when the pace reaches 3x, again at 6x normal
PREMARKET_SHARE = 0.25          # pre-market volume >= 25% of a normal full day


def _expected_share(minutes):
    for (m0, f0), (m1, f1) in zip(CURVE, CURVE[1:]):
        if minutes <= m1:
            return f0 + (f1 - f0) * (minutes - m0) / (m1 - m0)
    return 1.0


def _short(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else f"{n:.0f}"


class VolumeWatcher:
    def __init__(self, get_json, send):
        self.get_json = get_json
        self.send = send
        self.avg = {}  # ticker -> (day, average daily volume)

    def _avg(self, ticker, day):
        c = self.avg.get(ticker)
        if c and c[0] == day:
            return c[1]
        d = self.get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2mo")
        r = d["chart"]["result"][0]
        off = r["meta"].get("gmtoffset", 0)
        vols = [v for t, v in zip(r.get("timestamp") or [], (r["indicators"]["quote"][0].get("volume") or []))
                if v and datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d") != day][-20:]
        avg = sum(vols) / len(vols) if len(vols) >= 5 else 0
        self.avg[ticker] = (day, avg)
        return avg

    def check(self, ticker, chart, price, prev_close, state):
        meta = chart.get("meta") or {}
        per = meta.get("currentTradingPeriod") or {}
        pre, reg = per.get("pre") or {}, per.get("regular") or {}
        if not reg:
            return False
        now = time.time()
        off = meta.get("gmtoffset", 0)
        day = datetime.fromtimestamp(reg["start"] + off, timezone.utc).strftime("%Y-%m-%d")
        in_pre = pre and pre["start"] <= now < pre["end"]
        in_reg = reg["start"] <= now < reg["end"]
        if not (in_pre or in_reg):
            return False
        try:
            avg = self._avg(ticker, day)
        except Exception as e:
            log.warning("Average volume fetch failed: %s", type(e).__name__)
            return False
        if not avg:
            return False
        vs = state.setdefault("_vol", {})
        rec = vs.get(ticker)
        if not rec or rec[0] != day:
            rec = [day, 0, False]
        stamps = chart.get("timestamp") or []
        vols = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("volume") or []

        def total(a, b):
            return sum(v or 0 for t, v in zip(stamps, vols) if a <= t < b)

        day_chg = f" ({(price / prev_close - 1) * 100:+.1f}% today)" if prev_close else ""
        clock = datetime.fromtimestamp(now + off, timezone.utc).strftime("%H:%M")
        msg = None
        if in_pre and not rec[2]:
            v = total(pre["start"], pre["end"])
            if v >= PREMARKET_SHARE * avg:
                rec[2] = True
                msg = (f"📊 <b>${ticker}</b> HEAVY PRE-MARKET VOLUME\n"
                       f"{_short(v)} shares by {clock} ET = <b>{v / avg * 100:.0f}%</b> of a normal full day "
                       f"({_short(avg)})\nPrice: ${price:,.2f}{day_chg}")
        if in_reg:
            minutes = (now - reg["start"]) / 60
            v = total(reg["start"], reg["end"])
            if minutes >= 15 and v >= 0.1 * avg:
                pace = v / (avg * _expected_share(minutes))
                level = max([lv for lv in VOLUME_LEVELS if pace >= lv] or [0])
                if level > rec[1]:
                    rec[1] = level
                    msg = (f"📊 <b>${ticker}</b> UNUSUAL VOLUME — <b>{pace:.1f}×</b> normal pace\n"
                           f"{_short(v)} shares by {clock} ET (normal full day: {_short(avg)})\n"
                           f"Price: ${price:,.2f}{day_chg}")
        changed = vs.get(ticker) != rec
        vs[ticker] = rec
        if msg:
            msg = "\n".join([msg] + related(ticker))
            if self.send(msg):
                bump(state, "volume")
                log.info("Volume alert sent")
        return changed


# ---------------------------------------------------------------- 7. catalyst calendar
_MON = {m: i for i, ms in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], 1) for m in ms}
DATE_RE = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                     r"Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                     r"(?:,?\s+(20\d\d))?\b", re.I)
EARN_RE = re.compile(r"\b(to|will) (report|host|announce|release|present|hold)\b|\bschedul\w*\b|\bconference call\b|"
                     r"\bwebcast\b", re.I)
QUARTER_RE = re.compile(r"\b(first|second|third|fourth|full[- ]year|Q[1-4])\b(?:\s+(?:quarter|fiscal))?", re.I)
FDA_RE = re.compile(r"\bPDUFA\b|target action date|\bFDA (decision|action) (date|expected)\b", re.I)
ADCOM_RE = re.compile(r"advisory committee", re.I)


def _first_date(text, start, today):
    for m in DATE_RE.finditer(text, start):
        mon, d, y = _MON[m.group(1).lower()[:3]], int(m.group(2)), m.group(3)
        try:
            if y:
                dt = date(int(y), mon, d)
            else:
                dt = date(today.year, mon, d)
                if dt < today - timedelta(days=7):
                    dt = date(today.year + 1, mon, d)
        except ValueError:
            continue
        if today <= dt <= today + timedelta(days=365):
            return dt
        return None
    return None


def extract_events(text, today=None):
    """[(iso_date, kind, label)] of upcoming earnings / FDA dates mentioned in a headline + snippet."""
    today = today or local_now().date()
    out = []
    m = FDA_RE.search(text)
    if m:
        d = _first_date(text, m.start(), today) or _first_date(text, 0, today)
        if d:
            out.append((d.isoformat(), "fda", "FDA decision date (PDUFA)"))
    m = ADCOM_RE.search(text)
    if m and re.search(r"\b(meeting|meet|scheduled|convene)\b", text, re.I):
        d = _first_date(text, m.start(), today) or _first_date(text, 0, today)
        if d:
            out.append((d.isoformat(), "adcom", "FDA advisory committee meeting"))
    m = re.search(r"\b(results|earnings)\b", text, re.I)
    if m and EARN_RE.search(text) and not out:
        d = _first_date(text, 0, today)
        if d:
            q = QUARTER_RE.search(text)
            quarter = q.group(1).upper() if q and q.group(1).upper().startswith("Q") else \
                {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}.get(q.group(1).lower(), "Full-year") if q else ""
            out.append((d.isoformat(), "earnings", f"{quarter} earnings".strip()))
    return out


def calendar_add(state, ticker, events):
    cal = state.setdefault("_cal", [])
    changed = False
    for iso, kind, label in events:
        if any(e[0] == ticker and e[1] == iso and e[2] == kind for e in cal):
            continue
        if kind == "earnings":  # newest announcement wins (rescheduled / next quarter)
            cal[:] = [e for e in cal if not (e[0] == ticker and e[2] == "earnings")]
        cal.append([ticker, iso, kind, label])
        changed = True
        log.info("Calendar: added an event")
    return changed


def calendar_upcoming(state, days=14):
    today = local_now().date()
    cal = state.get("_cal", [])
    cal[:] = [e for e in cal if e[1] >= (today - timedelta(days=1)).isoformat()]
    end = (today + timedelta(days=days)).isoformat()
    return sorted((e for e in cal if today.isoformat() <= e[1] <= end), key=lambda e: e[1])


def _day(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %b %d")


# ---------------------------------------------------------------- 4. watchdog + daily status
HEARTBEAT_SECONDS = 600
STALE_AFTER = 30 * 60
TS_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\dZ)")


def _parse_ts(text):
    m = TS_RE.search(text or "")
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() if m else None


class Heartbeat:
    """GitHub writes its heartbeat into the bot's description, the phone into the bot's short description
    (both use the same Telegram bot), and each side warns when the other has gone quiet."""

    def __init__(self, role, api, send, substack_ok=lambda: True):
        self.role = role
        self.api = api
        self.send = send
        self.substack_ok = substack_ok
        self.last = 0

    def check(self, state):
        if self.role not in ("github", "phone"):
            return False
        changed = False
        if time.time() - self.last >= HEARTBEAT_SECONDS:
            self.last = time.time()
            changed |= self._beat(state)
        if self.role == "github":
            changed |= self._daily(state)
        return changed

    def _beat(self, state):
        iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        if self.role == "github":
            self.api("setMyDescription", {"description": f"Status (auto-updated): GitHub price/news bot OK at {iso}"})
            other = self.api("getMyShortDescription", {}) or {}
            text = other.get("short_description", "")
            name, fix = "Phone", ("Substack chats are probably NOT being forwarded. Check that the phone is on, "
                                  "on Wi-Fi and Termux is running (reboot the phone if unsure).")
        else:
            sub = "OK" if self.substack_ok() else "LOGIN EXPIRED"
            self.api("setMyShortDescription", {"short_description": f"Phone OK {iso} · Substack {sub}"})
            other = self.api("getMyDescription", {}) or {}
            text = other.get("description", "")
            name, fix = "GitHub price/news bot", ("price and news alerts may be down. Check the Actions tab of the "
                                                  "price-alert-bot repo (GitHub outage or a failed run).")
        w = state.setdefault("_watch", {})
        w["other"] = text[:200]
        ts = _parse_ts(text)
        if ts is None:
            return True  # the other side hasn't written a heartbeat yet (e.g. not updated)
        age = time.time() - ts
        if age > STALE_AFTER and not w.get("alerted"):
            if self.send(f"⚠️ <b>{name} hasn't checked in for {int(age // 60)} min</b> — {fix}"):
                w["alerted"] = True
                log.info("Watchdog: other side is quiet, warning sent")
        elif age <= STALE_AFTER and w.get("alerted"):
            if self.send(f"✅ {name} is back online."):
                w["alerted"] = False
        return True

    def _daily(self, state):
        w = state.setdefault("_watch", {})
        now = local_now()
        today = now.strftime("%Y-%m-%d")
        if now.strftime("%H:%M") < watchlist.STATUS_TIME or w.get("status_day") == today:
            return False
        w["status_day"] = today
        lines = [f"☀️ <b>Daily check</b> · {now.strftime('%a %b %d')}", "✅ Price &amp; news bot (GitHub): running"]
        text = w.get("other", "")
        ts = _parse_ts(text)
        if ts is None:
            lines.append("❔ Phone / Substack forwarding: no status yet")
        elif time.time() - ts > STALE_AFTER:
            lines.append(f"❌ Phone / Substack forwarding: offline (last seen {_ago(ts)})")
        else:
            sub = "login OK" if "Substack OK" in text else "⚠️ Substack LOGIN EXPIRED"
            lines.append(f"✅ Phone / Substack forwarding: online ({_ago(ts)}) · {sub}")
        st = state.get("_stats", {})
        y = (now.date() - timedelta(days=1)).isoformat()
        if y in st:
            d = st[y]
            names = [("price", "price moves"), ("news", "news"), ("sec", "SEC filings"), ("halts", "halts"),
                     ("volume", "volume alerts")]
            lines.append("Yesterday: " + ", ".join(f"{d.get(k, 0)} {n}" for k, n in names))
        up = calendar_upcoming(state)
        if up:
            lines.append("📅 <b>Next 14 days</b>")
            lines += [f"• {_day(e[1])}: <b>${e[0]}</b> {html.escape(e[3])}" for e in up]
        lines.append("<i>Dates come from company announcements in the news; check the company's IR page.</i>"
                     if up else "📅 No earnings / FDA dates found in the news for the next 14 days.")
        self.send("\n".join(lines))
        soon = [e for e in up if e[1] <= (now.date() + timedelta(days=1)).isoformat()]
        if soon:
            self.send("📅 <b>Coming up</b>\n" + "\n".join(
                f"• <b>${e[0]}</b> {html.escape(e[3])} — <b>{'TODAY' if e[1] == today else 'TOMORROW'}</b>"
                for e in soon))
        return True


# ---------------------------------------------------------------- 8. prices for tickers in Substack posts
CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
BARE = re.compile(r"(?<![\w$])([A-Z]{3,5})(?![\w])")
STOP = set("""CEO CFO COO CTO CMO FDA SEC USA USD EUR GBP ETF IPO ATH ATL EPS OTC NYSE AMEX GDP CPI PPI FED FOMC
ECB NATO JAV PVM BTC ETH SOL XRP EST CET EET UTC BUY SELL HOLD LONG SHORT CALL CALLS PUTS PUT NEWS FYI ASAP
THE AND FOR NOT BUT ALL ANY NEW NOW TOP LOW HIGH BIG ONE TWO RSI MACD EMA SMA VWAP ATM OTM ITM DTE BLA NDA
PDUFA CRL EBITDA YOY QOQ YTD MTD ROI ROE IRR NAV AUM SPAC IMO IMHO LOL OMG WTF YOLO FOMO HODL TLDR EDIT
UPDATE ALERT TRADE ENTRY EXIT STOP LOSS GAIN PROFIT RISK NOTE INFO TIP TIPS WOW YES GOOD BAD BEST NEXT LAST
WEEK DAY TODAY OPEN CLOSE PRE POST MAX MIN AVG TOTAL PLUS LIKE JUST VERY MUCH MORE LESS HUGE NICE COOL CASH
DEBT BOND BONDS RATE RATES FUND FUNDS IRA LLC INC LTD CORP PLC ADR ADS ARR MRR SAAS API CEO NASDAQ""".split())


def price_context(text, fetch_price, cache):
    """'💲 $ABC $1.23 (+4.5% today)' line for tickers mentioned in a post, or ''. cache: dict kept by caller."""
    cands = list(dict.fromkeys(CASHTAG.findall(text or "") +
                               [w for w in BARE.findall(text or "") if w not in STOP]))
    parts, checked = [], 0
    for t in cands:
        if len(parts) >= 5 or checked >= 10:
            break
        known = cache.get(t)
        if known is False:
            continue
        checked += 1
        try:
            price, prev, session = fetch_price(t)
        except Exception:
            cache[t] = False  # not a ticker
            continue
        cache[t] = True
        chg = f"{(price / prev - 1) * 100:+.1f}% today" if prev else ""
        sess = "" if session == "regular" else ("closed" if session in ("stale", "closed") else session)
        extra = ", ".join(x for x in (chg, sess) if x)
        parts.append(f"<b>${t}</b> ${price:,.2f}" + (f" ({extra})" if extra else ""))
    return ("💲 Now: " + " · ".join(parts)) if parts else ""

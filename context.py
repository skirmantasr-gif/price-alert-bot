"""🔎 Context check run before a message goes to a channel (owner, 2026-11-12: "all of these should be included
into consideration before launching a message"). Six things that often explain a move or hide a trap:

  🌐 market / sector   same-time move of the market (SPY) and the stock's sector ETF -> "mostly the market"
                       makes a price alert quiet (the stock didn't do anything special)
  💧 dilution          quarters of cash left at the recent burn + share-sale filings (S-3 shelf, 424B prospectus
                       supplements) in the last months: small companies that burn cash sell new shares
  🍂 tax-loss season   Nov–Dec: a stock down >= 40% this year faces year-end tax selling; January: the
                       rebound window (a weak, well-known tendency)
  🩳 short selling     FINRA daily short-sale volume, last 5 days (share of all trading that was short sales;
                       ~40–50% is normal because market makers short too; >= 60% = heavy)
  📅 dates             Fed decisions (federalreserve.gov), US jobs report (usually the first Friday 08:30 New
                       York; the official BLS calendar blocks bots), the next ex-dividend day estimated from
                       the past pattern (the price drops by the dividend that morning - not news)
  ⏳ listing deadline  closes under $1: 30 trading days in a row bring a Nasdaq/NYSE notice, then about 180 days
                       to recover; reverse splits often follow
Every fetch is cached and every failure is silent (the alert goes out without the line).
GitHub only (needs SEC_CONTACT for the filings part)."""
import json
import logging
import os
import re
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

log = logging.getLogger("bot")
ON = os.environ.get("CONTEXT_CHECK", "true").lower() == "true"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"
SECTOR_ETF = {"Healthcare": "XBI", "Technology": "QQQ", "Financial Services": "XLF", "Energy": "XLE",
              "Industrials": "XLI", "Consumer Cyclical": "XLY", "Communication Services": "XLC",
              "Real Estate": "XLRE", "Utilities": "XLU", "Basic Materials": "XLB", "Consumer Defensive": "XLP"}
ETF_NAME = {"XBI": "biotech", "QQQ": "tech", "XLF": "financials", "XLE": "energy", "XLI": "industrials",
            "XLY": "consumer", "XLC": "media/telecom", "XLRE": "real estate", "XLU": "utilities", "XLB": "materials",
            "XLP": "staples", "IWM": "small caps", "BTC-USD": "bitcoin"}
MARKET_SHARE = 1.5       # a stock move within 1.5x its sector's same-direction move = "mostly the market"
SHORT_HEAVY = 0.60
TAXLOSS_DROP = -0.40
DIV_DAYS = 5
MAX_BYTES = 20_000_000
_c = {}                  # cache key -> (time, value)


def _cached(key, ttl, fn):
    hit = _c.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        v = fn()
    except Exception as e:
        log.info("Context %s unavailable (%s)", key.split(":")[0], type(e).__name__)
        v = None
        _c[key] = (time.time() - ttl + 600, v)   # retry failures in 10 minutes
        return v
    _c[key] = (time.time(), v)
    return v


def _get(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return data


def _us(sym):
    return bool(sym) and "." not in sym and "=" not in sym and "^" not in sym


# ---------------------------------------------------------------- data
def daily(sym):
    """1 year of daily closes + dividends: {'closes': [(date, close)], 'divs': [(date, amount)]}."""
    def f():
        r = json.loads(_get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
                            + "?range=1y&interval=1d&events=div"))["chart"]["result"][0]
        closes = [(date.fromtimestamp(t), c) for t, c in zip(r.get("timestamp") or [],
                                                              r["indicators"]["quote"][0]["close"]) if c]
        divs = sorted((date.fromtimestamp(int(k)), v["amount"]) for k, v in
                      ((r.get("events") or {}).get("dividends") or {}).items())
        return {"closes": closes, "divs": divs}
    return _cached("daily:" + sym, 6 * 3600, f)


def day_change(sym):
    """Today's % change (fraction) vs the previous close, cached 5 minutes."""
    def f():
        m = json.loads(_get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
                            + "?range=1d&interval=5m"))["chart"]["result"][0]["meta"]
        prev = m.get("chartPreviousClose") or m.get("previousClose")
        return m["regularMarketPrice"] / prev - 1 if prev else None
    return _cached("chg:" + sym, 300, f)


def sector(sym):
    """Yahoo sector name, one attempt, cached a day (the buyout module's lookup retries for ~9 s - too slow
    in front of an alert)."""
    def f():
        j = json.loads(_get("https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode(
            {"q": sym, "quotesCount": 4, "newsCount": 0}), timeout=10))
        return next((x.get("sector") or "" for x in j.get("quotes", []) if (x.get("symbol") or "").upper() == sym), "")
    return _cached("sector:" + sym, 86400, f) or ""


def sector_etf(sym, peer=None):
    return peer or SECTOR_ETF.get(sector(sym), "IWM")


def filings(sym):
    """SEC forms of the last ~year: [(date, form)] (needs SEC_CONTACT)."""
    def f():
        import news
        cik = news.sec_cik(sym)
        if not cik:
            return []
        r = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                            {"User-Agent": f"price-alert-bot {SEC_CONTACT}", "Accept": "application/json"}))
        r = r.get("filings", {}).get("recent", {})
        return [(d, f) for d, f in zip(r.get("filingDate", []), r.get("form", []))]
    return _cached("sec:" + sym, 12 * 3600, f) if SEC_CONTACT else None


def cash_burn(sym):
    """(cash $, quarterly burn $ or 0 if not burning) from Yahoo's public statements data."""
    def f():
        now = int(time.time())
        d = json.loads(_get("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
                            + urllib.parse.quote(sym) + "?" + urllib.parse.urlencode(
                                {"type": "quarterlyCashCashEquivalentsAndShortTermInvestments,quarterlyFreeCashFlow",
                                 "period1": now - 400 * 86400, "period2": now + 86400}), timeout=15))
        fu = {}
        for r in d["timeseries"]["result"]:
            k = r["meta"]["type"][0]
            fu[k] = [(x["asOfDate"], x["reportedValue"]["raw"]) for x in (r.get(k) or []) if x]
        cash = (fu.get("quarterlyCashCashEquivalentsAndShortTermInvestments") or [[0, None]])[-1][1]
        fcf = [v for _, v in (fu.get("quarterlyFreeCashFlow") or [])][-2:]
        burn = -statistics.mean(fcf) if fcf and statistics.mean(fcf) < 0 else 0.0
        return cash, burn
    return _cached("cash:" + sym, 12 * 3600, f)


def short_ratio(sym, days=5):
    """Average share of daily volume that was short sales over the last `days` FINRA files, or None."""
    out = []
    d = date.today()
    tries = 0
    while len(out) < days and tries < days + 6:
        d -= timedelta(days=1)
        tries += 1
        if d.weekday() >= 5:
            continue
        tab = _cached("finra:" + d.isoformat(), 7 * 86400, lambda d=d: _finra(d))
        if tab and sym in tab:
            out.append(tab[sym])
    return statistics.mean(out) if out else None


def _finra(d):
    txt = _get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d.strftime('%Y%m%d')}.txt").decode()
    tab = {}
    for ln in txt.splitlines()[1:]:
        p = ln.split("|")
        if len(p) >= 5:
            try:
                tot = float(p[4])
                if tot > 0:
                    tab[p[1]] = float(p[2]) / tot
            except ValueError:
                pass
    return tab


def fomc_days():
    """Fed decision days (the last day of each meeting), from the Fed's own calendar page."""
    def f():
        t = re.sub(r"<[^>]+>", " ", _get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").decode())
        out = []
        for m in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", t, re.S):
            year = int(m.group(1))
            for mon, a, b in re.findall(r"\b(January|February|March|April|May|June|July|August|September|October|"
                                        r"November|December)(?:/\w+)?\s+(\d{1,2})-(\d{1,2})\*?", m.group(2)):
                try:
                    out.append(datetime.strptime(f"{b} {mon} {year}", "%d %B %Y").date())
                except ValueError:
                    pass
        out = sorted(set(out))
        return [d for i, d in enumerate(out) if not (i + 1 < len(out) and (out[i + 1] - d).days <= 2)]
    return _cached("fomc", 86400, f) or []


def jobs_day(d):
    """First Friday of d's month (the usual US jobs-report day; the BLS sometimes moves it)."""
    first = d.replace(day=1)
    return first + timedelta(days=(4 - first.weekday()) % 7)


# ---------------------------------------------------------------- checks (each returns a line or '')
def market_line(sym, change, peer=None):
    """'🌐 Same time: biotech (XBI) −2.1% · market (SPY) −0.8% → mostly the market', or ''."""
    if change is None or not _us(sym):
        return ""
    etf = sector_etf(sym, peer)
    s, m = day_change(etf), day_change("SPY")
    if s is None and m is None:
        return ""
    parts = [f"{ETF_NAME.get(etf, etf)} ({etf}) {s * 100:+.1f}%" if s is not None else "",
             f"market (SPY) {m * 100:+.1f}%" if m is not None else ""]
    verdict = " → <b>mostly the market</b>" if mostly_market(sym, change, peer) else ""
    return "🌐 Same day: " + " · ".join(p for p in parts if p) + verdict


def mostly_market(sym, change, peer=None):
    """True if the stock moved the same way as its sector and not much more (no stock-specific story)."""
    if change is None or not _us(sym):
        return False
    s = day_change(sector_etf(sym, peer))
    return bool(s) and abs(s) >= 0.01 and (s > 0) == (change > 0) and abs(change) <= MARKET_SHARE * abs(s)


def dilution_line(sym):
    if not _us(sym):
        return ""
    if sector(sym) == "Financial Services":   # lenders / banks: loan-book growth looks like "cash burn"
        return ""
    cb = cash_burn(sym)
    fl = filings(sym) or []
    today = date.today()
    recent = lambda days, forms: [f for d, f in fl if d >= (today - timedelta(days=days)).isoformat()
                                  and f.upper().startswith(forms)]
    sales = recent(90, ("424B",))
    shelf = recent(365, ("S-3", "S-1", "F-3"))
    bits, level = [], 0
    if cb and cb[0] and cb[1]:
        q = cb[0] / cb[1]
        if q < 8:
            bits.append(f"cash for about {q:.0f} quarter{'s' * (round(q) != 1)} at the recent burn")
            level = max(level, 2 if q < 4 else 1)
    if sales:
        bits.append(f"{len(sales)} share-sale filing{'s' * (len(sales) != 1)} (424B) in 90 days")
        level = max(level, 2)
    elif shelf and level:
        bits.append("a registered share-sale program (S-3/S-1) is ready")
    if not level:
        return ""
    return f"💧 Dilution risk {'HIGH' if level == 2 else 'medium'}: " + " · ".join(bits)


def taxloss_line(sym):
    today = date.today()
    if today.month not in (11, 12, 1):
        return ""
    d = daily(sym)
    if not d or len(d["closes"]) < 30:
        return ""
    year = today.year - (1 if today.month == 1 else 0)
    start = [c for dt, c in d["closes"] if dt.year < year]
    end = [c for dt, c in d["closes"] if dt.year == year]
    if not start or not end:
        return ""
    ytd = end[-1] / start[-1] - 1
    if ytd > TAXLOSS_DROP:
        return ""
    if today.month == 1:
        return f"🍂 Down {-ytd * 100:.0f}% last year: January is when tax-loss sellers are done (rebounds are common, not reliable)"
    return f"🍂 Down {-ytd * 100:.0f}% this year: year-end tax-loss selling can weigh until late December"


def short_line(sym):
    if not _us(sym):
        return ""
    r = short_ratio(sym)
    if r is None or r < SHORT_HEAVY:
        return ""
    return f"🩳 Heavy short selling: {r * 100:.0f}% of trading in the last 5 days was short sales (normal ≈ 40–50%)"


def listing_line(sym):
    d = daily(sym)
    if not d or not _us(sym):
        return ""
    n = 0
    for _, c in reversed(d["closes"]):
        if c >= 1:
            break
        n += 1
    if n < 10:
        return ""
    if n < 30:
        return f"⏳ Under $1 for {n} trading days: 30 in a row bring an exchange warning (often a reverse split later)"
    start = d["closes"][-n][0]
    return (f"⏳ Under $1 for {n} trading days: exchange warning likely; about 180 days to recover from "
            f"~{(start + timedelta(days=42)).strftime('%b %d')} → reverse-split risk")


def next_exdiv(sym):
    """Estimated next ex-dividend date from a regular pattern (3 or more payments, steady gap), or None."""
    d = daily(sym)
    if not d or len(d["divs"]) < 3:
        return None
    ds = [x for x, _ in d["divs"][-4:]]
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    g = statistics.median(gaps)
    if not 80 <= g <= 100 and not 170 <= g <= 200 and not 28 <= g <= 35:
        return None                       # irregular payer: no estimate
    nxt = ds[-1] + timedelta(days=g)
    while nxt < date.today() - timedelta(days=3):
        nxt += timedelta(days=g)
    return nxt, d["divs"][-1][1]


def exdiv_line(sym, price=None):
    e = next_exdiv(sym)
    if not e:
        return ""
    nxt, amt = e
    days = (nxt - date.today()).days
    if not -1 <= days <= DIV_DAYS:
        return ""
    pct = f" (~{amt / price * 100:.1f}% of the price)" if price else ""
    return (f"💵 Ex-dividend likely around {nxt.strftime('%a %b %d')} (estimated from past payments): the price "
            f"drops by about ${amt:.2f}{pct} that morning - not news")


# ---------------------------------------------------------------- what callers use
_shown = {}   # ticker -> day the full context was last attached (alerts later that day get the market line only)


def lines(sym, change=None, price=None, peer=None, market=True, once=True):
    """Context lines for one alert (only what is notable), never raises. With once=True the slow-changing
    lines (dilution, listing, tax-loss, short selling, dividend) appear on the stock's first alert of the day."""
    if not ON or not sym:
        return []
    today = date.today().isoformat()
    full = not once or _shown.get(sym) != today
    out = []
    rest = (lambda: dilution_line(sym), lambda: listing_line(sym), lambda: taxloss_line(sym),
            lambda: short_line(sym), lambda: exdiv_line(sym, price)) if full else ()
    for fn in (((lambda: market_line(sym, change, peer)),) if market and change is not None else ()) + rest:
        if fn is None:
            continue
        try:
            x = fn()
        except Exception as e:
            log.info("Context check failed (%s)", type(e).__name__)
            x = ""
        if x:
            out.append(x)
    if full and once and rest:
        _shown[sym] = today
    return out


def calendar_lines(today=None):
    """☀️ daily check: Fed decision / jobs report today or in the next 2 days."""
    today = today or date.today()
    out = []
    for d in fomc_days():
        if 0 <= (d - today).days <= 2:
            out.append(f"📅 Fed rate decision {'TODAY' if d == today else d.strftime('%a %b %d')} "
                       "(20:00/21:00 LT): moves the whole market")
    for d in (jobs_day(today), jobs_day((today.replace(day=1) + timedelta(days=32)))):
        if 0 <= (d - today).days <= 2:
            out.append(f"📅 US jobs report {'TODAY' if d == today else d.strftime('%a %b %d')} 15:30 LT "
                       "(usual first-Friday date; check the BLS calendar)")
    return out


def daily_lines(tickers):
    """☀️ daily check: calendar + one line per watchlist stock with a notable flag."""
    if not ON:
        return []
    out = calendar_lines()
    flags = []
    for t in tickers:
        ls = [x for x in lines(t, market=False, once=False) if x]
        if ls:
            flags.append(f"• ${t}: " + " | ".join(x.split(":")[0] for x in ls))
    if flags:
        out.append("🔎 <b>Context flags</b> (details appear in this stock's alerts):")
        out += flags
    return out


def outlook_input(sym):
    """Short facts for the AI outlook."""
    ls = lines(sym, market=False, once=False)
    return [x.replace("<b>", "").replace("</b>", "") for x in ls] or None

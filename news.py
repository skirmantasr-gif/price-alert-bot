"""
News / information alerts for the watched tickers -> Telegram.

Sources (all free, no keys):
  - Google News RSS search   (aggregates thousands of sites: Reuters, Benzinga, Fierce, press wires, ...)
  - Yahoo Finance RSS        (per-ticker headlines + press releases from GlobeNewswire / PR Newswire / Business Wire)
  - SEC EDGAR filings        (8-K, 6-K, offerings, insider trades, 13D/G...). Needs SEC_CONTACT env var:
                              the SEC requires a contact email in the User-Agent. Off if unset.
  - FDA press releases RSS   (matched against company names)
  - Contract wins            (extra Google News search every 15 min; 🏛️ tag, 🚨 at >= CONTRACT_MIN_USD, default $20M)
  - USAspending.gov          (official US federal contract awards >= CONTRACT_MIN_USD, every 3 h; lags days)

state.json is public, so only short hashes of seen items are stored there (no titles).
"""

import hashlib
import html
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

try:
    import alpaca
except ImportError:  # e.g. an older phone copy without alpaca.py
    class alpaca:  # noqa: N801
        ON = False
        recent = staticmethod(lambda: [])
        selftest = staticmethod(lambda: "OFF (alpaca.py missing)")
import extras
import impact
import insights
try:
    import learn
except ImportError:  # phone copy without learn.py
    learn = None
import watchlist

log = logging.getLogger("bot")

UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
# Tickers that only get 🚨 headlines (too chatty otherwise). Empty = all headlines.
IMPORTANT_ONLY = watchlist.IMPORTANT_ONLY
MAX_AGE_HOURS = 48          # ignore items published longer ago than this
NEWS_MAX_AGE_HOURS = float(os.environ.get("NEWS_MAX_AGE_HOURS", "6"))  # headlines first seen later than this
#   after publishing are stale (Google often lists small outlets a day late): remembered, not sent
SEEN_TTL_DAYS = 10          # forget seen-hashes after this
MAX_SENDS_PER_POLL = 15     # rest waits for the next poll (keeps price checks responsive)

# Per-company search terms / headline match words / federal-contract names come from the WATCHLIST secret
# (see watchlist.py), so the list of stocks is not visible in the public repo.
COMPANIES = watchlist.COMPANIES
GOV_NAMES = watchlist.GOV_NAMES
CONTRACT_MIN = float(os.environ.get("CONTRACT_MIN_USD", "20000000"))   # 🚨 + UP push at/above this
GOOGLE_SECONDS = int(os.environ.get("GOOGLE_NEWS_SECONDS", "300"))  # Google blocks too-frequent automated searches
CONTRACT_SEARCH_SECONDS = 900     # extra "contract / awarded" Google News search per ticker
USASPENDING_SECONDS = 3 * 3600    # federal award database (lags days behind press releases)
USASPENDING_GROUP = 8              # company names per search (too many in one search → HTTP 503)
# Watchlist-only USAspending check: off by default since 2026-11-07 — contracts.py now scans ALL new federal awards
# (any listed US company, watchlist included). USASPENDING=true turns this older check back on.
USASPENDING_ON = os.environ.get("USASPENDING", "").strip().lower() == "true"
CALENDAR_SECONDS = 6 * 3600       # Google News search for announced earnings / FDA dates
SA_SECONDS = int(os.environ.get("SA_SECONDS", "120"))  # Seeking Alpha per-stock feed (Google lists SA items hours late)
LATE_HOURS = 1.0                  # older than this when first seen → "⏰ published Xh ago", arrives silently
RECENT_HOURS = 72                 # per-stock memory of sent / skipped headlines (kept in state, survives restarts)
RECENT_PER_TICKER = 25

CONTRACT = re.compile(
    r"\b(contracts?|task orders?|delivery orders?|IDIQ|BPA|BARDA|Project BioShield|procurement|tender|"
    r"(wins?|won|awarded|secures?|selected for|lands?|receives?) .{0,60}\b(award|deal|order|agreement|funding))\b", re.I)
GOV = re.compile(r"\b(U\.S\.|US|federal|government|Army|Navy|Air Force|Space Force|Marine Corps|Pentagon|DoD|"
                 r"Department of|DARPA|BARDA|HHS|NIH|CDC|DHS|NASA|GSA|state of|city of|county|municipal|"
                 r"transit|authority|DOT|agency|ministry)\b")
AMOUNT = re.compile(r"(?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)\s?(billion|bn|b|million|mln|mn|m)?\b", re.I)
FINANCING = re.compile(r"\b(tranche|funding package|financing|loan|credit facility|private placement|offering|"
                       r"convertible|notes due|equity (investment|raise)|streaming agreement|royalty financing)\b", re.I)


def contract_info(text):
    """(is_contract, is_government, largest $ amount or 0) from a headline/snippet."""
    if not CONTRACT.search(text) or re.search(r"\b(futures?|options?) contracts?\b", text, re.I):
        return False, False, 0
    if FINANCING.search(text) and not GOV.search(text):
        return False, False, 0  # the company raising money (loan tranche, funding package) is not a contract win
    best = 0
    for num, unit in AMOUNT.findall(text):
        v = float(num.replace(",", ""))
        u = (unit or "").lower()
        v *= 1e9 if u in ("billion", "bn", "b") else 1e6 if u in ("million", "mln", "mn", "m") else 1
        best = max(best, v)
    return True, bool(GOV.search(text)), best


# Quote/chart pages and other non-news that Google News indexes
JUNK = re.compile(r"stock price|stock chart|\b[A-Z]{1,5}\d{6}[CP]\d{8}\b|price,? chart|stock quote|\bquote\b|options chain|seasonal returns|historical (prices|data)|"
                  r"\(\w+\.\w{1,3}\) (stock|chart|price)|stock forecast|price prediction|share price &|"
                  r"income statement|balance sheet|cash flow statement|key statistics|company profile|market cap(italization)? ", re.I)

# Headlines with these words get a 🚨
IMPORTANT = re.compile(r"\b(FDA|approv\w*|PDUFA|CRL|complete response|BLA|NDA|sNDA|breakthrough|fast track|"
                       r"orphan|priority review|data|bid price|deficiency|phase\s?(1|2|3|i|ii|iii)\b|topline|top-line|trial|data readout|"
                       r"acqui\w*|merger|merge|buyout|takeover|tender offer|to be acquired|deal|partnership|licens\w*|"
                       r"offering|private placement|dilut\w*|warrants?|reverse split|stock split|"
                       r"earnings|results|guidance|revenue|beats?|miss(es)?|downgrade\w*|upgrade\w*|price target|"
                       r"delist\w*|halt\w*|bankrupt\w*|chapter 11|lawsuit|investigation|subpoena|recall|"
                       r"CEO|CFO|resign\w*|appoint\w*|steps down|activist|stake|13D|short report|"
                       r"dividend|buyback|repurchase|contract|award\w*|record)\b", re.I)
# Look-ahead / opinion pieces: they mention "earnings" etc. but carry no new fact -> 📰 (quiet), not 🚨
PREVIEW = re.compile(r"\b(preview|what to expect|ahead of|expectations?|(set|scheduled|expected) to report|"
                     r"to report (q\d|earnings|results)|should you|is it time|what (are )?(analysts|researchers) (are )?saying|"
                     r"things to watch|what to watch|earnings date)\b", re.I)
# Routine items that rarely move a stock -> 📰 (quiet) even when IMPORTANT matches: law-firm ads, board seats,
# conference talks, list articles. (CEO / CFO changes stay 🚨.)
LAWFIRM = re.compile(r"\b(pomerantz|rosen law|levi & korsinsky|bragar|kahn swick|halper sadeh|bronstein|glancy|faruqi|"
                     r"schall law|gross law|class action|shareholder alert|investors? (who|with) (lost|losses))", re.I)
MINOR = re.compile(r"\b(board of directors|to (its |the )?board\b|(joins|appoints?|elects?|names?) .{0,40}\bdirector\b|"
                   r"conference(?! call)|fireside|to present at|presents? at|investor day|"
                   r"featured highlights|stocks? to (buy|watch)|top \d+ stocks)", re.I)
# ...but a routine-looking headline that carries real news stays 🚨
STRONG = re.compile(r"\b(FDA|approv\w*|topline|top-line|phase\s?(2|3|ii|iii)\b|results|earnings|guidance|revenue|"
                    r"acqui\w*|merger|buyout|takeover|offering|bankrupt\w*|delist\w*)\b", re.I)
EXEC = re.compile(r"\b(CEO|CFO|chief executive|chief financial)\b", re.I)
# Market-wide lists ("… and other big stocks moving lower") tag many symbols; skip unless the title names the company
ROUNDUP_SYMBOLS = 4

FORM_NAMES = {
    "8-K": "8-K (material event)", "6-K": "6-K (foreign issuer report)", "10-Q": "10-Q (quarterly report)",
    "10-K": "10-K (annual report)", "20-F": "20-F (annual report)", "4": "Form 4 (insider trade)",
    "3": "Form 3 (new insider)", "144": "Form 144 (insider plans to sell)",
    "S-1": "S-1 (share registration)", "S-3": "S-3 (shelf registration / possible offering)",
    "S-8": "S-8 (employee shares)", "F-3": "F-3 (shelf registration / possible offering)",
    "S-3ASR": "S-3ASR (shelf registration / possible offering)", "FWP": "FWP (offering term sheet)",
    "SC 13D": "13D (5%+ active stake)", "SC 13G": "13G (5%+ passive stake)",
    "SCHEDULE 13D": "13D (5%+ active stake)", "SCHEDULE 13G": "13G (5%+ passive stake)",
    "DEF 14A": "proxy statement", "DEFM14A": "merger proxy", "SC TO-T": "tender offer", "SC 14D9": "tender offer response",
}
IMPORTANT_FORMS = re.compile(r"^(8-K|S-1|S-3|F-3|F-1|424B\d|SC 13D|SCHEDULE 13D|SC TO|DEFM14A|SC 14D9|25-NSE|15-12)", re.I)
ITEMS_8K = {
    "1.01": "material agreement", "1.02": "agreement terminated", "1.03": "BANKRUPTCY", "1.05": "cybersecurity incident",
    "2.01": "acquisition/sale completed", "2.02": "financial results", "2.03": "new debt", "2.05": "restructuring/layoffs",
    "2.06": "impairment", "3.01": "DELISTING notice", "3.02": "unregistered share sale", "3.03": "shareholder rights changed",
    "4.01": "auditor change", "4.02": "financials can't be relied on", "5.01": "change in control",
    "5.02": "executive/director change", "5.03": "bylaws change", "5.07": "shareholder vote", "7.01": "Reg FD disclosure",
    "8.01": "other events", "9.01": "exhibits",
}
IMPORTANT_ITEMS = {"1.01", "1.02", "1.03", "1.05", "2.01", "2.05", "3.01", "3.02", "4.02", "5.01", "5.02"}


# ---------------------------------------------------------------- helpers
def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# Company merging with its OWN new holding company (redomicile / reincorporation, 1-for-1 shares) files the same
# forms as a real takeover (DEFM14A, 425, S-4, 8-K 1.01). Example: Criteo Nov 2026. Detected from the filing text.
REORG_FORMS = re.compile(r"^(DEFM14|PREM14|DEFA14A|425|S-4|F-4|424B3|8-K)", re.I)
REORG_RE = re.compile(r"re-?domicil|redomesticat|re-?incorporat|change (of|in) (its )?(domicile|jurisdiction of "
                      r"incorporation)|holding company reorgani[sz]ation|internal reorgani[sz]ation", re.I)
OWN_REORG = re.compile(r"\b(its|our|company[’']s|\w+[’']s)( [\w-]+){0,3} (re-?domicil\w*|re-?incorporat\w*|redomesticat\w*)|"
                       r"(re-?domiciliation|re-?incorporation|redomestication) of (the|our) company", re.I)
ONE_FOR_ONE = re.compile(r"one[- ](to|for)[- ]one|1[- ]for[- ]1|1:1 basis|converted into (the right to receive )?one "
                         r"(share|ordinary share|share of common stock)", re.I)
CASH_DEAL = re.compile(r"per share in cash|in cash, without interest|cash consideration|premium of|"
                       r"(?<!issuer )tender offer for|unsolicited (offer|proposal)|(agreed|agrees|will) to be acquired by", re.I)
_reorg_cache = {}


def _sec_text(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": f"price-alert-bot {SEC_CONTACT}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit).decode("utf-8", "ignore")


def sec_reorg(f):
    """True if a merger-type filing is only a company merging into its OWN new holding company (redomicile /
    reincorporation, 1-for-1 shares), not a buyout. Example: Criteo, Nov 2026. Signals: (a) the filing's two
    filers share the family name (Criteo S.A. + Criteo Holdings, Inc.; in a real deal the other filer is the
    buyer), or (b) the text talks about the company's own redomicile / 1-for-1 swap into its own subsidiary.
    Never True if the text mentions a cash price, premium or tender offer. Errors → False (normal alert)."""
    form, items = f.get("form", "").upper(), f.get("items") or []
    if not REORG_FORMS.match(form) or (form.startswith("8-K") and "1.01" not in items) or not f.get("doc"):
        return False
    acc = f.get("acc")
    if acc in _reorg_cache:
        return _reorg_cache[acc]
    try:
        raw = _sec_text(f["link"], 400_000)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())[:150_000]
        family = False
        if f.get("cik"):
            head = _sec_text(f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{acc.replace('-', '')}/{acc}.txt", 8000)
            head = head.split("<DOCUMENT>")[0]
            names = dict(re.findall(r"CENTRAL INDEX KEY:\s*(\d+)", head) and
                         zip(re.findall(r"CENTRAL INDEX KEY:\s*(\d+)", head),
                             re.findall(r"COMPANY CONFORMED NAME:\s*(.+)", head)))
            first = {re.sub(r"[^A-Z0-9]", "", n.split()[0].upper()) for n in names.values() if n.split()}
            family = len(names) >= 2 and len(first) == 1 and len(next(iter(first))) >= 3 \
                and next(iter(first)) not in ("THE", "FIRST", "AMERICAN", "UNITED", "NATIONAL", "GLOBAL")
        own_sub = bool(ONE_FOR_ONE.search(text) and re.search(r"wholly[- ]owned subsidiary", text, re.I)
                       and re.search(r"with and into", text, re.I))
        res = not CASH_DEAL.search(text) and (family or bool(OWN_REORG.search(text)) or own_sub
                                               or bool(REORG_RE.search(text) and ONE_FOR_ONE.search(text)))
        if form.startswith("8-K") and not re.search(r"\bmerger\b|merge with", text, re.I):
            res = False  # e.g. a loan amendment that only mentions the redomicile
    except Exception:
        return False  # not cached: retried next time
    _reorg_cache[acc] = res
    if len(_reorg_cache) > 500:
        _reorg_cache.pop(next(iter(_reorg_cache)))
    return res


def _norm(title):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split())


def _hash(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def _words(title):
    return set(w for w in _norm(title).split() if len(w) > 2)


def _similar(a, b):
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.6


def _recent_add(ns, ticker, text, ts=None):
    """Remember a headline / filing for this stock (repeat detection across runs)."""
    rec = ns.setdefault("recent", {}).setdefault(ticker, [])
    text = " ".join(str(text).split())[:200]
    if text and not any(t == text for _, t in rec):
        rec.append([int(ts or time.time()), text])
        rec.sort()
        del rec[:-RECENT_PER_TICKER]


def _recent(ns, ticker, now=None):
    """[(hours_ago, text)] of the last RECENT_HOURS, oldest first."""
    now = now or time.time()
    return [(round((now - ts) / 3600, 1), t) for ts, t in ns.get("recent", {}).get(ticker, [])
            if now - ts <= RECENT_HOURS * 3600]


def _prune_recent(ns, now=None):
    now = now or time.time()
    rec = ns.get("recent", {})
    for t in list(rec):
        rec[t] = [x for x in rec[t] if now - x[0] <= RECENT_HOURS * 3600]
        if not rec[t]:
            del rec[t]


# A bare ticker right after a foreign exchange name ("TSX: SLS") or with a foreign suffix ("SLS.TO") is another
# company that shares the symbol (2026-11-09: Solaris Resources TSX:SLS was sent as SELLAS $SLS).
FOREIGN_BEFORE = re.compile(r"\b(TSX|TSXV|TSX-V|CSE|NEO|CBOE CA|ASX|LSE|AIM|NSE|BSE|HKEX|SEHK|SGX|JSE|TASE|XETRA|"
                            r"FWB|ETR|Euronext|EPA|BIT|BME|SIX|OSE|TSE|KRX|TWSE|NZX)\s*[:\-]?\s*$", re.I)
FOREIGN_AFTER = re.compile(r"^\.(TO|V|CN|NE|AX|L|HK|NS|BO|SI|DE|F|PA|MI|AS|SW|T|KS|TW|NZ)\b")


def _matches(text, ticker):
    for kw in COMPANIES.get(ticker, {}).get("match", [ticker]):
        flags = re.I if not kw.isupper() or len(kw) > 5 else 0
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])", text, flags):
            if kw.upper() == ticker.upper() and (FOREIGN_BEFORE.search(text[max(0, m.start() - 12):m.start()])
                                                 or FOREIGN_AFTER.match(text[m.end():m.end() + 4])):
                continue  # same symbol on another exchange
            return True
    return False


def _parse_date(s):
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _rss_items(data):
    root = ET.fromstring(data)
    for it in root.iter("item"):
        yield {
            "title": html.unescape((it.findtext("title") or "").strip()),
            "link": (it.findtext("link") or "").strip(),
            "desc": html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or "")),
            "date": _parse_date(it.findtext("pubDate") or ""),
            "source": (it.findtext("source") or "").strip(),
        }


# ---------------------------------------------------------------- sources
def google_news(ticker):
    terms = COMPANIES.get(ticker, {}).get("q", [ticker])
    q = "(" + " OR ".join(terms) + ") when:2d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    out = []
    for it in _rss_items(_get(url)):
        title = it["title"]
        src = it["source"]
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if JUNK.search(title):
            continue  # quote pages, not news
        if not _matches(title, ticker):
            continue
        out.append({**it, "title": title, "source": src or "Google News"})
    return out


def seekingalpha_news(ticker):
    """Seeking Alpha's public per-symbol RSS (news + analysis). Direct, so no Google indexing lag."""
    url = f"https://seekingalpha.com/api/sa/combined/{urllib.parse.quote(ticker)}.xml"
    out = []
    for it in _rss_items(_get(url)):
        if not JUNK.search(it["title"]) and _matches(it["title"] + " " + it["desc"], ticker):
            out.append({**it, "source": "Seeking Alpha"})
    return out


def prnewswire_news(tickers):
    """PR Newswire live list of all new press releases (~20 newest; polled every minute)."""
    out = []
    for it in _rss_items(_get("https://www.prnewswire.com/rss/news-releases-list.rss")):
        for t in tickers:
            if _matches(it["title"] + " " + it["desc"], t):
                out.append({**it, "ticker": t, "source": "PR Newswire"})
    return out


def alpaca_news(tickers):
    """Benzinga real-time news via Alpaca (all symbols, filtered here by the article's own symbol tags)."""
    want = {t.upper() for t in tickers}
    out = []
    for it in alpaca.recent():
        if JUNK.search(it["title"]):
            continue
        for t in want.intersection(it["symbols"]):
            if len(it["symbols"]) >= ROUNDUP_SYMBOLS and not _matches(it["title"], t):
                continue  # roundup of many stocks; nothing specific about this one in the headline
            out.append({"title": it["title"], "link": it["link"], "desc": it["desc"], "date": it["date"],
                        "source": it["source"], "ticker": t})
    return out


def google_contracts(ticker):
    """Second Google News search aimed at contract wins / government awards (only contract headlines kept)."""
    terms = [x for x in COMPANIES.get(ticker, {}).get("q", [ticker]) if x.startswith('"')] or [ticker]
    q = ("(" + " OR ".join(terms) + ') (contract OR awarded OR "task order" OR BARDA OR procurement OR tender) when:2d')
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    out = []
    for it in _rss_items(_get(url)):
        title, src = it["title"], it["source"]
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if JUNK.search(title) or not _matches(title, ticker) or not contract_info(title + " " + it["desc"])[0]:
            continue
        out.append({**it, "title": title, "source": src or "Google News"})
    return out


def google_calendar(ticker):
    """Headlines from the last month that announce an upcoming earnings date / FDA decision date."""
    terms = [x for x in COMPANIES.get(ticker, {}).get("q", [ticker]) if x.startswith('"')] or [ticker]
    q = ("(" + " OR ".join(terms) + ') (PDUFA OR "to report" OR "conference call" OR "advisory committee" '
         'OR "target action date") when:30d')
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return [it for it in _rss_items(_get(url)) if _matches(it["title"] + " " + it["desc"], ticker)]


def usaspending(tickers):
    """Federal contract awards >= CONTRACT_MIN first signed in the last 45 days, for all watched companies
    (USAspending.gov, free, no key; a few searches of USASPENDING_GROUP names each)."""
    names = {n: t for t in tickers for n in GOV_NAMES.get(t, [])}
    if not names:
        return []
    today = datetime.now(timezone.utc).date()
    since = today.fromordinal(today.toordinal() - 45).isoformat()
    # One search with too many names gets HTTP 503 (tested 2026-11-07: 16 names OK, 17 fail), so ask in small groups.
    out, seen_gid, all_names = [], set(), list(names)
    for i in range(0, len(all_names), USASPENDING_GROUP):
        group = all_names[i:i + USASPENDING_GROUP]
        if i:
            time.sleep(1)
        for a in _usaspending_query(group, since, today):
            gid = a.get("generated_internal_id") or a.get("Award ID") or ""
            if gid in seen_gid:
                continue
            seen_gid.add(gid)
            out.extend(_usaspending_award(a, names, since))
    return out


def _usaspending_query(group, since, today):
    import json
    body = {"filters": {"recipient_search_text": group, "award_type_codes": ["A", "B", "C", "D"],
                        "award_amounts": [{"lower_bound": CONTRACT_MIN}],
                        "time_period": [{"start_date": since, "end_date": today.isoformat(),
                                         "date_type": "last_modified_date"}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date", "End Date", "Awarding Agency",
                       "Awarding Sub Agency", "Description", "Last Modified Date", "Base Obligation Date",
                       "generated_internal_id"],
            "limit": 100, "sort": "Last Modified Date", "order": "desc"}
    req = urllib.request.Request("https://api.usaspending.gov/api/v2/search/spending_by_award/",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("results", [])


def _usaspending_award(a, names, since):
    """[] or [award dict] for one search result."""
    rname = a.get("Recipient Name") or ""
    t = next((tk for n, tk in names.items() if re.search(r"\b" + re.escape(n) + r"\b", rname, re.I)), None)
    if not t:
        return []  # fuzzy search can return other companies
    amt = _num(a.get("Award Amount"))
    signed = (a.get("Base Obligation Date") or a.get("Start Date") or "")[:10]
    if amt < CONTRACT_MIN or not signed or signed < since:
        return []  # old award that was only modified
    gid = a.get("generated_internal_id") or a.get("Award ID") or ""
    return [{"kind": "gov", "ticker": t, "award_id": a.get("Award ID") or gid, "gid": gid, "amount": amt,
             "recipient": rname, "agency": a.get("Awarding Agency") or "",
             "sub_agency": a.get("Awarding Sub Agency") or "", "desc": a.get("Description") or "",
             "signed": signed, "start": a.get("Start Date") or "", "end": a.get("End Date") or "", "date": None,
             "link": f"https://www.usaspending.gov/award/{urllib.parse.quote(gid)}" if gid else
                     "https://www.usaspending.gov/search"}]


def yahoo_news(ticker):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode(
        {"s": ticker, "region": "US", "lang": "en-US"})
    out = []
    for it in _rss_items(_get(url)):
        if not JUNK.search(it["title"]) and _matches(it["title"] + " " + it["desc"], ticker):
            out.append({**it, "source": "Yahoo Finance"})
    return out


def fda_news(tickers):
    url = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"
    out = []
    for it in _rss_items(_get(url)):
        for t in tickers:
            if _matches(it["title"] + " " + it["desc"], t):
                out.append({**it, "ticker": t, "source": "FDA.gov"})
    return out


_cik_cache = {}


def sec_cik(ticker):
    if not _cik_cache:
        import json
        data = json.loads(_get("https://www.sec.gov/files/company_tickers.json",
                               {"User-Agent": f"price-alert-bot {SEC_CONTACT}"}))
        for v in data.values():
            _cik_cache[v["ticker"].upper()] = int(v["cik_str"])
    return _cik_cache.get(ticker)


def sec_filings(ticker):
    import json
    cik = sec_cik(ticker)
    if not cik:
        return []
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                           {"User-Agent": f"price-alert-bot {SEC_CONTACT}", "Accept": "application/json"}))
    r = data.get("filings", {}).get("recent", {})
    out = []
    for i in range(min(40, len(r.get("accessionNumber", [])))):
        acc = r["accessionNumber"][i]
        form = r["form"][i]
        accepted = r.get("acceptanceDateTime", [""] * 99)[i] or r["filingDate"][i]
        try:
            d = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            d = None
        items = [x.strip() for x in (r.get("items", [""] * 99)[i] or "").split(",") if x.strip()]
        doc = r.get("primaryDocument", [""] * 99)[i]
        link = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40")
        out.append({"acc": acc, "form": form, "items": items, "date": d, "link": link, "cik": cik, "doc": doc,
                    "desc": r.get("primaryDocDescription", [""] * 99)[i] or ""})
    return out


# ---------------------------------------------------------------- insider filings (Form 3/4/5, 144)
TX_CODES = {
    "P": "Bought (open market)", "S": "Sold (open market)", "A": "Granted / awarded", "M": "Options exercised / RSUs vested",
    "X": "Exercised options", "C": "Converted", "F": "Shares withheld for tax", "G": "Gift", "D": "Returned to company",
    "J": "Other", "W": "Inherited", "I": "Discretionary trade", "K": "Equity swap", "V": "Voluntary report",
    "E": "Short derivative expired", "H": "Long derivative expired", "O": "Exercised (out of the money)", "Z": "Trust deposit/withdrawal",
    "L": "Small acquisition", "U": "Tendered in change of control",
}
ENTITY = re.compile(r"\b(inc|llc|l\.?p|ltd|fund|trust|capital|partners|holdings?|corp|corporation|management|group|"
                    r"advisors?|investments?|ventures|plc|ag|sa|bv|gmbh|foundation|co)\b\.?", re.I)


def _person(name):
    """SEC lists people as 'Last First Middle' (surname first). Reordering breaks double surnames, so keep the
    order and only fix ALL-CAPS."""
    name = " ".join((name or "").split())
    if name.isupper() and not ENTITY.search(name):
        name = " ".join(p.capitalize() for p in name.split(" "))
    return name


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _raw_doc(doc):
    return re.sub(r"^xsl[^/]+/", "", doc or "")


def parse_ownership(xml):
    root = ET.fromstring(xml)
    owners, roles = [], []
    for ro in root.findall("reportingOwner"):
        owners.append(_person(ro.findtext("reportingOwnerId/rptOwnerName")))
        rel = ro.find("reportingOwnerRelationship")
        if rel is not None:
            yes = lambda tag: (rel.findtext(tag) or "").strip().lower() in ("1", "true")
            if yes("isOfficer"):
                roles.append((rel.findtext("officerTitle") or "Officer").strip())
            if yes("isDirector"):
                roles.append("Director")
            if yes("isTenPercentOwner"):
                roles.append("10% owner")
            if yes("isOther"):
                roles.append((rel.findtext("otherText") or "Other").strip())
    notes = " ".join((f.text or "") for f in root.iter("footnote"))
    plan = (root.findtext("aff10b5One") or "").strip().lower() in ("1", "true") or "10b5-1" in notes
    tx = []
    for path, deriv in (("nonDerivativeTable/nonDerivativeTransaction", False),
                        ("derivativeTable/derivativeTransaction", True)):
        for t in root.findall(path):
            sh = _num(t.findtext("transactionAmounts/transactionShares/value"))
            pr = _num(t.findtext("transactionAmounts/transactionPricePerShare/value"))
            tx.append({"code": (t.findtext("transactionCoding/transactionCode") or "?").strip(), "deriv": deriv,
                       "security": (t.findtext("securityTitle/value") or "").strip(),
                       "shares": sh, "price": pr, "value": sh * pr,
                       "after": t.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                       "date": (t.findtext("transactionDate/value") or "")[:10]})
    holds = sum(_num(h.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
                for h in root.findall("nonDerivativeTable/nonDerivativeHolding"))
    return {"type": (root.findtext("documentType") or "4").strip().split("/")[0], "owners": owners,
            "role": ", ".join(dict.fromkeys(" ".join(r.split()) for r in roles)) or "insider", "plan": plan, "tx": tx, "holds": holds}


def parse_144(xml):
    root = ET.fromstring(xml)
    f = {}
    rel = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "relationshipToIssuer":
            rel.append((el.text or "").strip())
        elif el.text and el.text.strip() and tag not in f:
            f[tag] = el.text.strip()
    return {"type": "144", "owners": [_person(f.get("nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold", ""))],
            "role": ", ".join(r for r in rel if r) or "insider", "shares": _num(f.get("noOfUnitsSold")),
            "value": _num(f.get("aggregateMarketValue")), "date": f.get("approxSaleDate", ""),
            "source": f.get("natureOfAcquisitionTransaction", "")}


def insider_details(it):
    """Fetch and parse the XML of a Form 3/4/5/144. Returns a dict or None."""
    form = it["form"].upper().split("/")[0]
    if form not in ("3", "4", "5", "144") or not it.get("doc"):
        return None
    url = f"https://www.sec.gov/Archives/edgar/data/{it['cik']}/{it['acc'].replace('-', '')}/{_raw_doc(it['doc'])}"
    xml = _get(url, {"User-Agent": f"price-alert-bot {SEC_CONTACT}"})
    time.sleep(0.2)
    return parse_144(xml) if form == "144" else parse_ownership(xml)


def _sh(n):
    return f"{n:,.0f}"


def format_insider(it, ins):
    t = it["ticker"]
    who = html.escape(" & ".join(o for o in ins["owners"] if o) or "Insider")
    role = html.escape(ins["role"])
    amended = " (amended)" if "/A" in it["form"].upper() else ""
    if ins["type"] == "144":
        lines = [f"🟠👤 <b>${t}</b> PLANNED INSIDER SALE (Form 144){amended}",
                 f"<b>{who}</b> ({role}) plans to sell <b>{_sh(ins['shares'])} sh</b> (~{impact.money(ins['value'])})"
                 + (f" around {html.escape(ins['date'])}" if ins["date"] else "")]
        if ins.get("source"):
            lines.append(f"Shares came from: {html.escape(ins['source'])}")
        return lines
    if ins["type"] == "3":
        lines = [f"👤 <b>${t}</b> NEW INSIDER (Form 3){amended}", f"<b>{who}</b> ({role})"]
        if ins["holds"]:
            lines.append(f"Holds {_sh(ins['holds'])} sh")
        return lines
    codes = {x["code"] for x in ins["tx"]}
    if "P" in codes and "S" not in codes:
        head = "🟢👤 INSIDER BUY"
    elif "S" in codes and "P" not in codes:
        head = "🔴👤 INSIDER SALE"
    elif "P" in codes and "S" in codes:
        head = "🟡👤 INSIDER BUY + SALE"
    else:
        head = "⚪👤 INSIDER FILING"
    lines = [f"{head} <b>${t}</b> (Form {html.escape(it['form'])}){amended}", f"<b>{who}</b> ({role})"]
    groups = {}
    plain = {x["code"] for x in ins["tx"] if not x["deriv"]}
    for x in ins["tx"]:
        if x["deriv"] and x["code"] in ("M", "X", "C") and x["code"] in plain:
            continue  # mirror of the share-side line

        g = groups.setdefault((x["code"], x["deriv"], x["security"] if x["deriv"] else ""), {"sh": 0, "val": 0, "dates": set()})
        g["sh"] += x["shares"]
        g["val"] += x["value"]
        if x["date"]:
            g["dates"].add(x["date"])
    for (code, deriv, sec), g in groups.items():
        what = TX_CODES.get(code, f"Code {code}")
        unit = html.escape(sec) if deriv and sec else "sh"
        line = f"• {what}: <b>{_sh(g['sh'])} {unit}</b>"
        if g["val"] and g["sh"]:
            line += f" @ ${g['val'] / g['sh']:,.2f} avg = <b>{impact.money(g['val'])}</b>"
        if g["dates"]:
            ds = sorted(g["dates"])
            line += f" ({ds[0]}{'…' + ds[-1] if len(ds) > 1 else ''})"
        lines.append(line)
    after = next((x["after"] for x in reversed(ins["tx"]) if not x["deriv"] and x["after"]), None)
    if after:
        lines.append(f"Now owns: {_sh(_num(after))} sh")
    if ins["plan"]:
        lines.append("📋 Pre-planned trade (Rule 10b5-1)")
    return lines


# ---------------------------------------------------------------- checker
class NewsWatcher:
    def __init__(self, tickers, send):
        self.tickers = [t for t in tickers]
        self.send = send
        self.recent_titles = []   # (ticker, words) of headlines sent/seen this run, for fuzzy dedup
        self.fail = {}            # source -> first failure time
        self.queue = []
        self.last_google = 0.0
        self.last_contract_search = 0.0
        self.last_usaspending = 0.0
        self.last_calendar = 0.0
        self.last_sa = 0.0

    def sources_label(self):
        s = ["Google News", "Yahoo Finance", "FDA"]
        s.append("SEC EDGAR + insider trades" if SEC_CONTACT else "SEC EDGAR off (no SEC_CONTACT)")
        s.append(f"contract wins (🏛️, 🚨 from {impact.money(CONTRACT_MIN)})" +
                 (" + USAspending.gov federal awards" if USASPENDING_ON else ""))
        return ", ".join(s) + ". Impact guesses: " + (f"AI ({impact.AI_MODEL})" if impact.ai_enabled() else "keyword rules")

    def _fetch(self, name, fn, *args):
        try:
            res = fn(*args)
            self.fail.pop(name, None)
            return res
        except Exception as e:
            first = self.fail.setdefault(name, time.time())
            log.warning("News source %s failed: %s", name.split(":")[0],  # no ticker in the public log
                        type(e).__name__ if not hasattr(e, "code") else f"HTTP {e.code}")
            if first != "notified" and time.time() - first > 3600:
                self.send(f"⚠️ News source <b>{html.escape(name.split(':')[0])}</b> failing for 1h+; will keep trying.")
                self.fail[name] = "notified"
            return None

    def check(self, state, fast=False):
        """Fetch all sources, queue new items, send up to MAX_SENDS_PER_POLL. Returns True if state changed.
        fast=True: only the real-time Alpaca feed (run every minute between the full rounds)."""
        ns = state.setdefault("_news", {})
        seen = ns.setdefault("seen", {})
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - MAX_AGE_HOURS * 3600
        news_cutoff = now.timestamp() - NEWS_MAX_AGE_HOURS * 3600
        stale = 0
        today = now.strftime("%Y-%m-%d")
        changed = False
        new = []

        inited = ns.setdefault("init", [])  # sources seen at least once; their backlog is skipped silently

        def run(name, fn, *args):
            res = self._fetch(name, fn, *args)
            if res is None:
                return [], True
            silent = name not in inited
            if silent:
                inited.append(name)
                log.info("News: started watching %s (%d existing items skipped)", name.split(":")[0], len(res))
            return res, silent

        def consider(key, item, silent):
            nonlocal changed, stale
            if key in seen:
                return
            seen[key] = today
            changed = True
            d = item.get("date")
            if silent or (d and d.timestamp() < cutoff):
                return
            if d and item.get("kind") == "news" and item.get("source") != "FDA.gov" and d.timestamp() < news_cutoff:
                stale += 1
                _recent_add(ns, item["ticker"], item["title"], d.timestamp())  # a later copy is then a repeat
                return
            new.append(item)

        if alpaca.ON:
            res, silent = run("Alpaca", alpaca_news, self.tickers)
            for it in res:
                consider(_hash(it["ticker"] + "|" + _norm(it["title"])), {**it, "kind": "news"}, silent)
        mono = time.time()
        if fast:
            mono = float("-inf")  # nothing else is due
        do_contracts = mono - self.last_contract_search >= CONTRACT_SEARCH_SECONDS
        do_usa = USASPENDING_ON and mono - self.last_usaspending >= USASPENDING_SECONDS
        do_google = mono - self.last_google >= GOOGLE_SECONDS
        if do_google:
            self.last_google = mono
        if do_contracts:
            self.last_contract_search = mono
        if do_usa:
            self.last_usaspending = mono
        do_calendar = mono - self.last_calendar >= CALENDAR_SECONDS
        if do_calendar:
            self.last_calendar = mono
        do_sa = mono - self.last_sa >= SA_SECONDS
        if do_sa:
            self.last_sa = mono
        for t in ([] if fast else self.tickers):
            srcs = ([("Google", google_news)] if do_google else []) + [("Yahoo", yahoo_news)] \
                + ([("SeekingAlpha", seekingalpha_news)] if do_sa else [])
            if do_contracts:
                srcs.append(("Google contracts", google_contracts))
            for name, fn in srcs:
                res, silent = run(f"{name}:{t}", fn, t)
                for it in res:
                    key = _hash(t + "|" + _norm(it["title"]))
                    if key not in seen:
                        changed |= extras.calendar_add(state, t, extras.extract_events(
                            it["title"] + " " + (it.get("desc") or "")[:300]))
                    consider(key, {**it, "ticker": t, "kind": "news"}, silent)
            if do_calendar:
                for it in self._fetch(f"Google calendar:{t}", google_calendar, t) or []:
                    changed |= extras.calendar_add(state, t, extras.extract_events(
                        it["title"] + " " + (it.get("desc") or "")[:300]))
            if SEC_CONTACT:
                res, silent = run(f"SEC EDGAR:{t}", sec_filings, t)
                for f in res:
                    consider(_hash("sec|" + f["acc"]), {**f, "ticker": t, "kind": "sec"}, silent)
                time.sleep(0.2)  # SEC asks for <10 requests/second
        if do_usa:
            res, silent = run("USAspending", usaspending, self.tickers)
            for a in res:
                consider(_hash("usa|" + a["gid"]), a, silent)
        if not fast:
            res, silent = run("FDA", fda_news, self.tickers)
            for it in res:
                consider(_hash(it["ticker"] + "|fda|" + _norm(it["title"])), {**it, "kind": "news"}, silent)
            res, silent = run("PRNewswire", prnewswire_news, self.tickers)
            for it in res:
                consider(_hash(it["ticker"] + "|" + _norm(it["title"])), {**it, "kind": "news"}, silent)

        if stale:
            log.info("News: %d stale headline(s) skipped (published > %.0fh before first seen)", stale, NEWS_MAX_AGE_HOURS)
        # merge same story from several sources (within this batch and vs. recently sent)
        reported = ns.setdefault("contracts", [])  # [ticker, $M, date] of contract wins already sent (public data)
        new.sort(key=lambda x: (x["kind"] == "gov", x.get("date") or now))  # official awards after the news
        for it in new:
            if it["kind"] == "news":
                is_c, is_gov, amt = contract_info(it["title"] + " " + (it.get("desc") or "")[:300])
                if is_c and (is_gov or amt):
                    it["contract"] = amt

            if it["kind"] == "news":
                w = _words(it["title"])
                dup = next((q for q in self.queue if q["kind"] == "news" and q["ticker"] == it["ticker"]
                            and _similar(q["words"], w)), None)
                if dup:
                    if it["source"] not in dup["sources"]:
                        dup["sources"].append(it["source"])
                    continue
                if any(t == it["ticker"] and _similar(rw, w) for t, rw in self.recent_titles) or \
                        any(_similar(_words(txt), w) for _, txt in _recent(ns, it["ticker"])):
                    continue  # same story already sent / seen (also in an earlier run)
                it["words"] = w
                it["sources"] = [it["source"]]
            if it["kind"] == "news" and it["ticker"] in IMPORTANT_ONLY and not IMPORTANT.search(it["title"]):
                continue
            self.queue.append(it)

        batch = self.queue[:MAX_SENDS_PER_POLL]
        for it in batch:
            if it["kind"] == "news" and "earlier" not in it:  # for the AI repeat / old-news check
                it["earlier"] = _recent(ns, it["ticker"])[-12:]
            if it["kind"] == "sec" and "insider" not in it:
                it["insider"] = self._fetch("SEC EDGAR insider", insider_details, it)
            if it["kind"] == "sec" and "reorg" not in it and SEC_CONTACT:
                it["reorg"] = sec_reorg(it)
        try:
            impact.assign(batch, {t: (COMPANIES.get(t, {}).get("match") or [t])[0] for t in self.tickers}, self.send)
        except Exception as e:
            log.warning("Impact step failed: %s", type(e).__name__)

        sent = 0
        while self.queue and sent < MAX_SENDS_PER_POLL:
            it = self.queue[0]
            if it["kind"] == "gov" and any(r[0] == it["ticker"] and r[1] and abs(r[1] * 1e6 - it["amount"]) <= 0.2 * it["amount"]
                                           for r in reported):
                log.info("News: federal award already reported; skipped")
                self.queue.pop(0)
                continue
            if it.get("not_about"):
                log.info("News: about another company / a market roundup (AI); skipped")
                self.queue.pop(0)
                continue
            if it.get("repeat"):
                log.info("News: repeat of an earlier story (AI); skipped")
                _recent_add(ns, it["ticker"], it["title"])
                self.queue.pop(0)
                changed = True
                continue
            msg = self.format(it)
            if not self.send(msg):
                log.warning("News: Telegram send failed, will retry next poll")
                break
            self.queue.pop(0)
            extras.remember(it["ticker"], msg, it.get("link", ""))
            insights.note(state, it)
            if learn:
                learn.record_news(state, it)
            extras.bump(state, "sec" if it["kind"] == "sec" else "news")
            _recent_add(ns, it["ticker"], it["title"] if it["kind"] == "news" else extras._strip(msg.split("\n")[0]))
            changed = True
            if it.get("contract") or it["kind"] == "gov":
                reported.append([it["ticker"], round((it.get("contract") or it.get("amount") or 0) / 1e6, 2), today])
                changed = True
            if it["kind"] == "news":
                self.recent_titles.append((it["ticker"], it["words"]))
                self.recent_titles = self.recent_titles[-500:]
            sent += 1
            time.sleep(3)  # Telegram allows ~20 messages/minute per channel
        if sent:
            log.info("News: sent %d item(s), %d queued", sent, len(self.queue))

        # prune old hashes
        old = datetime.fromtimestamp(now.timestamp() - SEEN_TTL_DAYS * 86400, timezone.utc).strftime("%Y-%m-%d")
        ns["contracts"] = [r for r in reported if r[2] >= old]
        _prune_recent(ns)
        for k in [k for k, v in seen.items() if v < old]:
            del seen[k]
            changed = True
        return changed

    @staticmethod
    def _when(d):
        if not d:
            return ""
        return " · " + extras.fmt_local(d)

    def format(self, it):
        msg = self._format(it)
        lab = impact.label(it.get("impact"))
        if lab:
            head, _, tail = msg.rpartition("\n")  # impact goes above the link line
            msg = f"{head}\n{html.escape(lab)}\n{tail}" if head else f"{msg}\n{html.escape(lab)}"
        return msg

    def _format(self, it):
        t = it["ticker"]
        if it["kind"] == "gov":
            amt = it["amount"]
            lines = [f"{'🚨' if amt >= CONTRACT_MIN else ''}🏛️ <b>${t}</b> US FEDERAL CONTRACT: <b>{impact.money(amt)}</b>",
                     "From: " + html.escape(it["agency"] + (f" ({it['sub_agency']})" if it["sub_agency"] and it["sub_agency"] != it["agency"] else ""))]
            if it["recipient"]:
                lines.append(f"To: {html.escape(it['recipient'])}")
            if it["desc"]:
                lines.append(f"What: {html.escape(' '.join(it['desc'].split())[:250])}")
            if it["start"] or it["end"]:
                lines.append(f"Period: {html.escape(it['start'] or '?')} → {html.escape(it['end'] or '?')}")
            if it.get("signed"):
                lines.append(f"Signed: {html.escape(it['signed'])}")
            lines.append("<i>USAspending.gov (official record, usually days after the announcement)</i>")
            lines.append(f'<a href="{html.escape(it["link"])}">Open award</a>')
            return "\n".join(lines)
        if it["kind"] == "sec" and it.get("insider"):
            lines = format_insider(it, it["insider"])
            lines.append(f'<a href="{html.escape(it["link"])}">Open filing</a>{self._when(it.get("date"))}')
            return "\n".join(lines)
        if it["kind"] == "sec":
            form = it["form"]
            label = FORM_NAMES.get(form, FORM_NAMES.get(form.split("/")[0], form))
            if form.startswith("424B"):
                label = f"{form} (prospectus — often an OFFERING)"
            items = [f"{x} {ITEMS_8K[x]}" for x in it["items"] if x in ITEMS_8K and x != "9.01"]
            imp = (bool(IMPORTANT_FORMS.match(form)) or any(x in IMPORTANT_ITEMS for x in it["items"])) and not it.get("reorg")
            lines = [f"{'🚨' if imp else '📄'} <b>${t}</b> SEC filing: <b>{html.escape(label)}</b>"]
            if it.get("reorg"):
                lines.append("🏠 Internal reorganization (company merging into its own new holding company, "
                             "1-for-1 shares) — NOT a buyout")
            if items:
                lines.append(html.escape("; ".join(items)))
            elif it.get("desc") and not re.fullmatch(r"(FORM\s*)?" + re.escape(form), it["desc"].strip(), re.I) \
                    and not it["desc"].upper().startswith(("INITIAL STATEMENT", "STATEMENT OF CHANGES")):
                lines.append(html.escape(it["desc"][:200]))
            lines.append(f'<a href="{html.escape(it["link"])}">Open filing</a>{self._when(it.get("date"))}')
            return "\n".join(lines)
        c = it.get("contract")
        big = c is not None and c >= CONTRACT_MIN
        loud = IMPORTANT.search(it["title"]) and not PREVIEW.search(it["title"]) and not it.get("minor") \
            and not LAWFIRM.search(it["title"]) \
            and not (MINOR.search(it["title"]) and not EXEC.search(it["title"]) and not STRONG.search(it["title"]))
        icon = ("🚨" if big or loud else "📰") + ("🏛️" if c is not None else "")
        srcs = it.get("sources") or [it.get("source", "")]
        src = html.escape(srcs[0]) + (f" +{len(srcs) - 1} more" if len(srcs) > 1 else "")
        extra = ""
        if c is not None:
            extra = f"🏛️ Contract win: <b>{impact.money(c)}</b>\n" if c else "🏛️ Contract news (amount not in headline)\n"
        d = it.get("date")
        age = (datetime.now(timezone.utc) - d).total_seconds() / 3600 if d else 0
        late = age >= LATE_HOURS or it.get("old_event")
        if age >= LATE_HOURS:
            extra += (f"⏰ Published {age:.0f}h ago" if age < 48 else f"⏰ Published {age / 24:.0f} days ago") + \
                     " — the news site listed it late, the market may already know\n"
        elif it.get("old_event"):
            extra += "⏰ Old news — the event itself happened earlier, the market may already know\n"
        if late:
            icon = "⏰" + icon  # late / old news arrives without sound
        return (f"{icon} <b>${t}</b> {html.escape(it['title'])}\n{extra}"
                f'<i>{src}</i>{self._when(it.get("date"))} · <a href="{html.escape(it["link"])}">Read</a>')

"""
News / information alerts for the watched tickers -> Telegram.

Sources (all free, no keys):
  - Google News RSS search   (aggregates thousands of sites: Reuters, Benzinga, Fierce, press wires, ...)
  - Yahoo Finance RSS        (per-ticker headlines + press releases from GlobeNewswire / PR Newswire / Business Wire)
  - SEC EDGAR filings        (8-K, 6-K, offerings, insider trades, 13D/G...). Needs SEC_CONTACT env var:
                              the SEC requires a contact email in the User-Agent. Off if unset.
  - FDA press releases RSS   (matched against company names)

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

log = logging.getLogger("bot")

UA = "Mozilla/5.0 (compatible; price-alert-bot/1.0)"
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
# Comma list of tickers that only get 🚨 headlines (e.g. "ORCL" if Oracle is too chatty). Empty = all headlines.
IMPORTANT_ONLY = {t.strip().upper() for t in os.environ.get("NEWS_IMPORTANT_ONLY", "").split(",") if t.strip()}
MAX_AGE_HOURS = 48          # ignore items published longer ago than this
SEEN_TTL_DAYS = 10          # forget seen-hashes after this
MAX_SENDS_PER_POLL = 15     # rest waits for the next poll (keeps price checks responsive)

# Search names + words that must appear in a headline for it to count as being about that company.
# Tickers that are also ordinary words (ZIM, MIST, SLS...) only match together with a name.
COMPANIES = {
    "GLXY": {"q": ['"Galaxy Digital"', "GLXY"], "match": ["Galaxy Digital", "GLXY", "Novogratz"]},
    "CHRS": {"q": ['"Coherus"', "CHRS"], "match": ["Coherus", "CHRS"]},
    "CAPR": {"q": ['"Capricor"', "CAPR"], "match": ["Capricor", "CAPR", "deramiocel"]},
    "CRTO": {"q": ['"Criteo"', "CRTO"], "match": ["Criteo", "CRTO"]},
    "KLAR": {"q": ['"Klarna"'], "match": ["Klarna", "KLAR"]},
    "ZIM":  {"q": ['"ZIM Integrated"', '"ZIM shipping"', '"ZIM stock"', '"NYSE:ZIM"'],
             "match": ["ZIM Integrated", "ZIM Shipping", "ZIM stock", "ZIM shares", "NYSE: ZIM", "NYSE:ZIM", "(ZIM)", "ZIM's"]},
    "ORCL": {"q": ['"Oracle"', "ORCL"], "match": ["Oracle", "ORCL", "Larry Ellison"]},
    "BZUN": {"q": ['"Baozun"', "BZUN"], "match": ["Baozun", "BZUN"]},
    "DXYZ": {"q": ['"Destiny Tech100"', "DXYZ"], "match": ["Destiny Tech100", "Destiny Tech 100", "DXYZ"]},
    "MIST": {"q": ['"Milestone Pharmaceuticals"', "etripamil", "Cardamyst"],
             "match": ["Milestone Pharma", "etripamil", "Cardamyst", "(MIST)", "NASDAQ: MIST", "NASDAQ:MIST"]},
    "IMRX": {"q": ['"Immuneering"', "IMRX"], "match": ["Immuneering", "IMRX", "atebimetinib"]},
    "VRRM": {"q": ['"Verra Mobility"', "VRRM"], "match": ["Verra Mobility", "VRRM"]},
    "SLS":  {"q": ['"SELLAS"', "galinpepimut"], "match": ["SELLAS", "galinpepimut", "GPS vaccine", "(SLS)", "NASDAQ: SLS", "NASDAQ:SLS"]},
    "VNDA": {"q": ['"Vanda Pharmaceuticals"', "VNDA"],
             "match": ["Vanda", "VNDA", "Fanapt", "Hetlioz", "tradipitant", "Bysanti", "milsaperidone", "imsidolimab", "Nereus"]},
}

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


def _matches(text, ticker):
    for kw in COMPANIES.get(ticker, {}).get("match", [ticker]):
        if re.search(r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])", text, re.I if not kw.isupper() or len(kw) > 5 else 0):
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


def yahoo_news(ticker):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode(
        {"s": ticker, "region": "US", "lang": "en-US"})
    out = []
    for it in _rss_items(_get(url)):
        if not JUNK.search(it["title"]) and _matches(it["title"] + " " + it["desc"], ticker):
            out.append({**it, "source": "Yahoo Finance"})
    return out


def fda_news():
    url = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"
    out = []
    for it in _rss_items(_get(url)):
        for t in COMPANIES:
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
        out.append({"acc": acc, "form": form, "items": items, "date": d, "link": link,
                    "desc": r.get("primaryDocDescription", [""] * 99)[i] or ""})
    return out


# ---------------------------------------------------------------- checker
class NewsWatcher:
    def __init__(self, tickers, send):
        self.tickers = [t for t in tickers]
        self.send = send
        self.recent_titles = []   # (ticker, words) of headlines sent/seen this run, for fuzzy dedup
        self.fail = {}            # source -> first failure time
        self.queue = []

    def sources_label(self):
        s = ["Google News", "Yahoo Finance", "FDA"]
        s.append("SEC EDGAR" if SEC_CONTACT else "SEC EDGAR off (no SEC_CONTACT)")
        return ", ".join(s)

    def _fetch(self, name, fn, *args):
        try:
            res = fn(*args)
            self.fail.pop(name, None)
            return res
        except Exception as e:
            first = self.fail.setdefault(name, time.time())
            log.warning("News source %s failed: %s", name, type(e).__name__ if not hasattr(e, "code") else f"HTTP {e.code}")
            if first != "notified" and time.time() - first > 3600:
                self.send(f"⚠️ News source <b>{html.escape(name.split(':')[0])}</b> failing for 1h+; will keep trying.")
                self.fail[name] = "notified"
            return None

    def check(self, state):
        """Fetch all sources, queue new items, send up to MAX_SENDS_PER_POLL. Returns True if state changed."""
        ns = state.setdefault("_news", {})
        seen = ns.setdefault("seen", {})
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - MAX_AGE_HOURS * 3600
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
            nonlocal changed
            if key in seen:
                return
            seen[key] = today
            changed = True
            d = item.get("date")
            if silent or (d and d.timestamp() < cutoff):
                return
            new.append(item)

        for t in self.tickers:
            for name, fn in (("Google", google_news), ("Yahoo", yahoo_news)):
                res, silent = run(f"{name}:{t}", fn, t)
                for it in res:
                    consider(_hash(t + "|" + _norm(it["title"])), {**it, "ticker": t, "kind": "news"}, silent)
            if SEC_CONTACT:
                res, silent = run(f"SEC EDGAR:{t}", sec_filings, t)
                for f in res:
                    consider(_hash("sec|" + f["acc"]), {**f, "ticker": t, "kind": "sec"}, silent)
                time.sleep(0.2)  # SEC asks for <10 requests/second
        res, silent = run("FDA", fda_news)
        for it in res:
            consider(_hash(it["ticker"] + "|fda|" + _norm(it["title"])), {**it, "kind": "news"}, silent)

        # merge same story from several sources (within this batch and vs. recently sent)
        new.sort(key=lambda x: x.get("date") or now)
        for it in new:
            if it["kind"] == "news":
                w = _words(it["title"])
                dup = next((q for q in self.queue if q["kind"] == "news" and q["ticker"] == it["ticker"]
                            and _similar(q["words"], w)), None)
                if dup:
                    if it["source"] not in dup["sources"]:
                        dup["sources"].append(it["source"])
                    continue
                if any(t == it["ticker"] and _similar(rw, w) for t, rw in self.recent_titles):
                    continue
                it["words"] = w
                it["sources"] = [it["source"]]
            if it["kind"] == "news" and it["ticker"] in IMPORTANT_ONLY and not IMPORTANT.search(it["title"]):
                continue
            self.queue.append(it)

        sent = 0
        while self.queue and sent < MAX_SENDS_PER_POLL:
            it = self.queue[0]
            if not self.send(self.format(it)):
                log.warning("News: Telegram send failed, will retry next poll")
                break
            self.queue.pop(0)
            if it["kind"] == "news":
                self.recent_titles.append((it["ticker"], it["words"]))
                self.recent_titles = self.recent_titles[-500:]
            sent += 1
            time.sleep(3)  # Telegram allows ~20 messages/minute per channel
        if sent:
            log.info("News: sent %d item(s), %d queued", sent, len(self.queue))

        # prune old hashes
        old = datetime.fromtimestamp(now.timestamp() - SEEN_TTL_DAYS * 86400, timezone.utc).strftime("%Y-%m-%d")
        for k in [k for k, v in seen.items() if v < old]:
            del seen[k]
            changed = True
        return changed

    @staticmethod
    def _when(d):
        if not d:
            return ""
        return d.astimezone(timezone.utc).strftime(" · %b %d %H:%M UTC")

    def format(self, it):
        t = it["ticker"]
        if it["kind"] == "sec":
            form = it["form"]
            label = FORM_NAMES.get(form, FORM_NAMES.get(form.split("/")[0], form))
            if form.startswith("424B"):
                label = f"{form} (prospectus — often an OFFERING)"
            items = [f"{x} {ITEMS_8K[x]}" for x in it["items"] if x in ITEMS_8K and x != "9.01"]
            imp = bool(IMPORTANT_FORMS.match(form)) or any(x in IMPORTANT_ITEMS for x in it["items"])
            lines = [f"{'🚨' if imp else '📄'} <b>${t}</b> SEC filing: <b>{html.escape(label)}</b>"]
            if items:
                lines.append(html.escape("; ".join(items)))
            elif it.get("desc") and not re.fullmatch(r"(FORM\s*)?" + re.escape(form), it["desc"].strip(), re.I) \
                    and not it["desc"].upper().startswith(("INITIAL STATEMENT", "STATEMENT OF CHANGES")):
                lines.append(html.escape(it["desc"][:200]))
            lines.append(f'<a href="{html.escape(it["link"])}">Open filing</a>{self._when(it.get("date"))}')
            return "\n".join(lines)
        icon = "🚨" if IMPORTANT.search(it["title"]) else "📰"
        srcs = it.get("sources") or [it.get("source", "")]
        src = html.escape(srcs[0]) + (f" +{len(srcs) - 1} more" if len(srcs) > 1 else "")
        return (f"{icon} <b>${t}</b> {html.escape(it['title'])}\n"
                f'<i>{src}</i>{self._when(it.get("date"))} · <a href="{html.escape(it["link"])}">Read</a>')
